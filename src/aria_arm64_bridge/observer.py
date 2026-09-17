"""ZMQ frame consumer — receives Aria frames and sensors from the FEX-Emu receiver.

Runs natively on ARM64. Decodes the wire protocol (v2 frames + v2.1 sensor
batches on the same socket), stores the latest frame per camera, and keeps short
ring buffers of IMU/mag/baro samples for charting and ego-motion.

Frames arrive as RGB from the Aria SDK and are rotated and converted to BGR to
match the OpenCV convention, so this is a drop-in source for a pipeline that was
written against the SDK's own observer.

Usage::

    observer = AriaBridgeObserver()
    frame = observer.get_frame("rgb")      # numpy BGR uint8 or None
    sensors = observer.get_sensors()       # imu/mag/baro snapshot + rates
    state = observer.get_motion_state()    # "walking" | "stationary" | "unknown"
    observer.stop()
"""

import collections
import struct
import threading
import time
import traceback
from typing import Dict, Any, Optional

import numpy as np
import zmq

from .protocol import (
    HEADER_FORMAT, HEADER_SIZE, HEADER_MAGIC,
    DEFAULT_ZMQ_ENDPOINT, CAM_NAMES,
    SENSOR_HEADER_FORMAT, SENSOR_HEADER_SIZE, SENSOR_MAGIC,
    SENSOR_NAMES, SENSOR_SAMPLE_FORMATS,
    SENSOR_IMU1, SENSOR_MAG, SENSOR_BARO,
)

try:
    from .telemetry import Telemetry
except Exception:
    Telemetry = None  # type: ignore

IMU_DECIMATE = 10  # 1 kHz -> ~100 Hz kept for charting

# Ego-motion thresholds: std of |accel| over a recent window. Below STATIONARY =
# quiet, above WALKING = moving; the band between them is hysteresis (keep the
# previous state) so the estimate doesn't flap at the boundary.
MOTION_STD_STATIONARY = 0.3
MOTION_STD_WALKING = 0.6


def _estimate_motion_state(imu_samples, prev_state="unknown"):
    """Classify walking/stationary from IMU samples (pure, unit-testable).

    imu_samples: iterable of (t, ax, ay, az, gx, gy, gz). Uses the std of the
    accelerometer magnitude as motion energy. Needs >=10 samples, else returns
    prev_state unchanged.
    """
    if len(imu_samples) < 10:
        return prev_state
    mags = [(s[1] ** 2 + s[2] ** 2 + s[3] ** 2) ** 0.5 for s in imu_samples]
    mean = sum(mags) / len(mags)
    std = (sum((m - mean) ** 2 for m in mags) / len(mags)) ** 0.5
    if std < MOTION_STD_STATIONARY:
        return "stationary"
    if std > MOTION_STD_WALKING:
        return "walking"
    return prev_state


class Frame:
    """A single frame from the Aria glasses."""

    __slots__ = ("image", "timestamp", "camera", "shape")

    def __init__(self, image: np.ndarray, timestamp: int, camera: str):
        self.image = image
        self.timestamp = timestamp
        self.camera = camera
        self.shape = image.shape


class AriaBridgeObserver:
    """Receives Aria frames and sensors via ZMQ and exposes them as numpy arrays.

    Implements the interface a pipeline expects from an Aria source:
      - ``get_frame(camera)`` -> Optional[np.ndarray] (BGR uint8)
      - ``get_sensors()`` -> Dict (IMU/mag/baro snapshot)
      - ``get_motion_state()`` -> str
      - ``get_stats()`` -> Dict
      - ``stop()``
    """

    fov_h = 1.919  # ~110 deg horizontal FOV (Aria RGB camera)

    def __init__(self, zmq_endpoint: str = DEFAULT_ZMQ_ENDPOINT,
                 telemetry_pid_fex: Optional[int] = None):
        self._endpoint = zmq_endpoint
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # Frame storage (BGR, post-processed)
        self._frames: Dict[str, Optional[np.ndarray]] = {
            "rgb": None, "eye": None, "slam1": None, "slam2": None,
        }
        self._frame_counts: Dict[str, int] = {k: 0 for k in self._frames}
        self._frame_versions: Dict[str, int] = {k: 0 for k in self._frames}
        self._start_time = time.time()

        # Sensor storage (protocol v2.1): ring buffers for charting
        self._sensor_counts = {k: 0 for k in SENSOR_NAMES.values()}
        self._imu1 = collections.deque(maxlen=600)   # (t_rel, ax,ay,az, gx,gy,gz) ~100 Hz
        self._mag = collections.deque(maxlen=120)    # (t_rel, mx,my,mz)
        self._baro = collections.deque(maxlen=300)   # (t_rel, pressure, temp)
        self._imu_decim = 0
        self._motion_state = "unknown"

        self._telemetry = Telemetry(pid_fex=telemetry_pid_fex) if Telemetry else None

        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"[aria-bridge] observer connected to {zmq_endpoint}")

    # ------------------------------------------------------------------
    # Public API — frames
    # ------------------------------------------------------------------

    def get_frame(self, camera: str = "rgb") -> Optional[np.ndarray]:
        """Most recent frame for *camera*. Returns BGR ``uint8`` or ``None``.

        Returns a read-only view — do not modify the array in place.
        Call ``.copy()`` yourself if you need to write to it.
        """
        with self._lock:
            frame = self._frames.get(camera)
            if frame is None:
                return None
            frame.flags.writeable = False
            return frame

    def get_frame_if_new(self, camera: str = "rgb", last_version: int = -1):
        """Returns ``(frame, version)`` only if the frame is newer than *last_version*.

        Returns ``(None, last_version)`` if nothing new. Use this to avoid
        processing the same frame twice in a tight loop.

        Example::

            version = -1
            while True:
                frame, version = observer.get_frame_if_new("rgb", version)
                if frame is not None:
                    process(frame)
        """
        with self._lock:
            v = self._frame_versions.get(camera, 0)
            if v == last_version:
                return None, last_version
            frame = self._frames.get(camera)
            if frame is None:
                return None, last_version
            frame.flags.writeable = False
            return frame, v

    def get_latest(self, camera: str = "rgb") -> Optional[Frame]:
        """Most recent :class:`Frame` for *camera*, or ``None``."""
        with self._lock:
            img = self._frames.get(camera)
            if img is None:
                return None
            return Frame(img.copy(), int(time.time() * 1e9), camera)

    # ------------------------------------------------------------------
    # Public API — sensors
    # ------------------------------------------------------------------

    def get_sensors(self) -> Dict[str, Any]:
        """Snapshot of sensor data for charting/dashboards.

        Returns dict with: ``imu1`` (list of (t, ax,ay,az, gx,gy,gz)), ``mag``
        (last (mx,my,mz) or None), ``baro`` (last (pressure, temp) or None),
        ``rates`` (Hz per sensor) and ``fps`` (per camera).
        """
        with self._lock:
            elapsed = time.time() - self._start_time
            imu1 = list(self._imu1)[-200:]
            mag = self._mag[-1][1:] if self._mag else None
            baro = self._baro[-1][1:] if self._baro else None
            rates = {k: round(v / elapsed, 1)
                     for k, v in self._sensor_counts.items() if elapsed > 0}
            fps = {k: round(v / elapsed, 1)
                   for k, v in self._frame_counts.items() if elapsed > 0}
        return {"imu1": imu1, "mag": mag, "baro": baro, "rates": rates, "fps": fps}

    def get_motion_state(self) -> str:
        """Ego-motion estimate (``"walking"`` | ``"stationary"`` | ``"unknown"``).

        Lets a tracker ego-compensate apparent approach, so a static obstacle
        ahead doesn't read as a collision while the user walks. Uses the most
        recent ~0.5 s of IMU1.
        """
        with self._lock:
            recent = list(self._imu1)[-50:]
        self._motion_state = _estimate_motion_state(recent, self._motion_state)
        return self._motion_state

    # ------------------------------------------------------------------
    # Public API — lifecycle
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        elapsed = time.time() - self._start_time
        with self._lock:
            return {
                "source": "aria-bridge",
                "frames": dict(self._frame_counts),
                "fps": {k: v / elapsed for k, v in self._frame_counts.items() if v > 0},
                "uptime": elapsed,
                "zmq_endpoint": self._endpoint,
            }

    def stop(self):
        """Stop the background receive thread and telemetry."""
        self._stop_event.set()
        self._thread.join(timeout=2)
        if self._telemetry:
            self._telemetry.stop()
        print("[aria-bridge] observer stopped")

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _receive_loop(self):
        ctx = zmq.Context()
        socket = ctx.socket(zmq.PULL)
        # 64: matches the receiver's SNDHWM. Sensor batches and SLAM pairs arrive
        # in bursts; HWM 2 dropped them whenever the consumer hiccupped.
        socket.setsockopt(zmq.RCVHWM, 64)
        socket.connect(self._endpoint)

        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)

        try:
            while not self._stop_event.is_set():
                events = dict(poller.poll(timeout=100))
                if socket not in events:
                    continue

                parts = socket.recv_multipart(copy=False)
                if len(parts) != 2:
                    continue

                header_buf, payload_buf = parts
                head = bytes(header_buf)

                # Route by magic: ARI2 = frame, ARS1 = sensor batch (v2.1).
                # A consumer that only knows ARI2 skips sensors safely.
                if head[:4] == SENSOR_MAGIC:
                    self._handle_sensor(head, payload_buf)
                    continue

                if len(head) < HEADER_SIZE:
                    continue

                magic, cam_id, timestamp_ns, width, height, channels = struct.unpack(
                    HEADER_FORMAT, head)

                if magic != HEADER_MAGIC:
                    continue

                cam_name = CAM_NAMES.get(cam_id)
                if cam_name is None:
                    continue

                expected_pixels = width * height * channels
                if len(payload_buf) != expected_pixels:
                    continue

                # frombuffer on ZMQ's zero-copy buffer — no extra copy here.
                # _process_frame always calls ascontiguousarray = the one copy.
                shape = (height, width, channels) if channels > 1 else (height, width)
                raw = np.frombuffer(payload_buf, dtype=np.uint8).reshape(shape)

                processed = self._process_frame(cam_name, raw)

                with self._lock:
                    self._frames[cam_name] = processed
                    self._frame_counts[cam_name] += 1
                    self._frame_versions[cam_name] += 1

                total = sum(self._frame_counts.values())  # outside lock, 4 ints

                # Log stats outside the lock — no need to hold it for prints
                if total % 300 == 0:
                    elapsed = time.time() - self._start_time
                    with self._lock:
                        counts = dict(self._frame_counts)
                    fps = {k: v / elapsed for k, v in counts.items() if v > 0}
                    fps_str = " ".join(f"{k}={v:.1f}" for k, v in fps.items())
                    print(f"[aria-bridge] {fps_str} fps (total={total})")
                    if self._telemetry and "rgb" in fps:
                        self._telemetry.record_fps(fps["rgb"])
        except Exception as e:
            print(f"[aria-bridge] ERROR in receive thread: {e}", flush=True)
            traceback.print_exc()
        finally:
            socket.close()
            ctx.term()

    def _handle_sensor(self, head, payload):
        """Parse an ARS1 sensor batch into the ring buffers."""
        if len(head) < SENSOR_HEADER_SIZE:
            return
        try:
            _magic, sensor_id, _n = struct.unpack(SENSOR_HEADER_FORMAT,
                                                  head[:SENSOR_HEADER_SIZE])
            fmt = SENSOR_SAMPLE_FORMATS.get(sensor_id)
            name = SENSOR_NAMES.get(sensor_id)
            if fmt is None or name is None:
                return
            samples = list(struct.iter_unpack(fmt, bytes(payload)))
        except struct.error:
            return  # torn batch — drop it, never kill the thread

        now = time.time() - self._start_time
        with self._lock:
            self._sensor_counts[name] += len(samples)
            if sensor_id == SENSOR_IMU1:  # decimate 1 kHz to ~100 Hz for charts
                for s in samples:
                    self._imu_decim += 1
                    if self._imu_decim % IMU_DECIMATE == 0:
                        self._imu1.append((round(now, 3), *s[1:]))
            elif sensor_id == SENSOR_MAG:
                for s in samples:
                    self._mag.append((round(now, 3), *s[1:]))
            elif sensor_id == SENSOR_BARO:
                for s in samples:
                    self._baro.append((round(now, 3), *s[1:]))
            # imu2 is only counted — charts use imu1

    @staticmethod
    def _process_frame(cam_name: str, raw: np.ndarray) -> np.ndarray:
        """Rotate and colour-convert to match the Aria SDK's standard output (BGR).

        numpy only (no cv2) to avoid a numpy 2.x / OpenCV ABI mismatch. Every path
        produces exactly one contiguous copy — no intermediate arrays.
        """
        if cam_name == "rgb" and raw.ndim == 3:
            # rot90(k=-1) + BGR flip in one ascontiguousarray call
            return np.ascontiguousarray(np.rot90(raw, k=-1)[:, :, ::-1])
        if cam_name == "eye":
            rotated = np.rot90(raw, 2)
            if rotated.ndim == 2:
                return np.ascontiguousarray(np.stack([rotated] * 3, axis=-1))
            return np.ascontiguousarray(rotated)
        if cam_name in ("slam1", "slam2") or cam_name == "rgb":
            # A single-channel "rgb" frame lands here too: a profile can deliver
            # mono, and [:, :, ::-1] on a 2-D array would raise IndexError.
            rotated = np.rot90(raw, k=-1)
            if rotated.ndim == 2:
                return np.ascontiguousarray(np.stack([rotated] * 3, axis=-1))
            return np.ascontiguousarray(rotated)
        return np.ascontiguousarray(raw)

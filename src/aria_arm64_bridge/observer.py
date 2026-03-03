"""ZMQ frame consumer — receives Aria frames from the FEX-Emu receiver.

Runs natively on ARM64. Decodes the wire protocol and stores the latest
frame per camera, with optional rotation/color conversion to match the
Aria SDK's standard output orientation.
"""

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
)


class Frame:
    """A single frame from the Aria glasses."""

    __slots__ = ("image", "timestamp", "camera", "shape")

    def __init__(self, image: np.ndarray, timestamp: int, camera: str):
        self.image = image
        self.timestamp = timestamp
        self.camera = camera
        self.shape = image.shape


class AriaBridgeObserver:
    """Receives Aria frames via ZMQ and makes them available as numpy arrays.

    Frames arrive as RGB from the Aria SDK, are rotated and converted to BGR
    to match the standard OpenCV convention.

    Usage::

        observer = AriaBridgeObserver()
        frame = observer.get_frame("rgb")  # numpy BGR uint8 or None
        observer.stop()
    """

    fov_h = 1.919  # ~110 deg horizontal FOV (Aria RGB camera)

    def __init__(self, zmq_endpoint: str = DEFAULT_ZMQ_ENDPOINT):
        self._endpoint = zmq_endpoint
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._frames: Dict[str, Optional[np.ndarray]] = {
            "rgb": None, "eye": None, "slam1": None, "slam2": None,
        }
        self._frame_counts: Dict[str, int] = {k: 0 for k in self._frames}
        self._frame_versions: Dict[str, int] = {k: 0 for k in self._frames}
        self._start_time = time.time()

        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Public API
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
        """Stop the background receive thread."""
        self._stop_event.set()
        self._thread.join(timeout=2)

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _receive_loop(self):
        ctx = zmq.Context()
        socket = ctx.socket(zmq.PULL)
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

                header_buf, pixel_buf = parts
                if len(header_buf) < HEADER_SIZE:
                    continue

                magic, cam_id, timestamp_ns, width, height, channels = struct.unpack(
                    HEADER_FORMAT, bytes(header_buf))

                if magic != HEADER_MAGIC:
                    continue

                cam_name = CAM_NAMES.get(cam_id)
                if cam_name is None:
                    continue

                expected_pixels = width * height * channels
                if len(pixel_buf) != expected_pixels:
                    continue

                # frombuffer on ZMQ's zero-copy buffer — no extra copy here.
                # _process_frame always calls ascontiguousarray = the one copy.
                shape = (height, width, channels) if channels > 1 else (height, width)
                raw = np.frombuffer(pixel_buf, dtype=np.uint8).reshape(shape)

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
        except Exception as e:
            print(f"[aria-bridge] ERROR in receive thread: {e}", flush=True)
            traceback.print_exc()
        finally:
            socket.close()
            ctx.term()

    @staticmethod
    def _process_frame(cam_name: str, raw: np.ndarray) -> np.ndarray:
        """Rotate and colour-convert to match Aria SDK standard output (BGR).

        All paths produce exactly one contiguous copy — no intermediate arrays.
        """
        if cam_name == "rgb":
            # rot90(k=-1) + BGR flip in one ascontiguousarray call
            return np.ascontiguousarray(np.rot90(raw, k=-1)[:, :, ::-1])
        if cam_name == "eye":
            rotated = np.rot90(raw, 2)
            if rotated.ndim == 2:
                return np.ascontiguousarray(np.stack([rotated] * 3, axis=-1))
            return np.ascontiguousarray(rotated)
        if cam_name in ("slam1", "slam2"):
            rotated = np.rot90(raw, k=-1)
            if rotated.ndim == 2:
                return np.ascontiguousarray(np.stack([rotated] * 3, axis=-1))
            return np.ascontiguousarray(rotated)
        return np.ascontiguousarray(raw)

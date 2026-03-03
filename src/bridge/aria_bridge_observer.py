"""AriaBridgeObserver — drop-in replacement for AriaDemoObserver on Jetson ARM64.

Receives frames from aria_receiver.py via ZMQ instead of using the Aria SDK
directly. Compatible with aria-guard's BaseObserver interface.

Architecture:
    [FEX-Emu x86_64]                    [Native ARM64]
    aria_receiver.py  ---ZMQ--->  AriaBridgeObserver
    (Aria SDK)                    (aria-guard pipeline)

Usage in aria-guard:
    from aria_bridge_observer import AriaBridgeObserver
    observer = AriaBridgeObserver()  # connects to ZMQ on localhost:5555
    rgb = observer.get_frame("rgb")  # numpy uint8 BGR, same as AriaDemoObserver
"""

import struct
import threading
import time
from typing import Dict, Any, Optional

import numpy as np
import zmq

DEFAULT_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"

# Protocol v2 constants (must match aria_receiver.py)
HEADER_FORMAT = "<4sB3xQIII"
HEADER_SIZE = 28
HEADER_MAGIC = b"ARI2"
CAM_NAMES = {0: "rgb", 1: "eye", 2: "slam1", 3: "slam2"}


class AriaBridgeObserver:
    """Observer that receives Aria frames via ZMQ bridge.

    Implements the same interface as aria-guard's BaseObserver:
      - get_frame(camera) -> Optional[np.ndarray]  (BGR uint8)
      - get_stats() -> Dict
      - stop()

    Frames arrive as RGB from Aria, are rotated and converted to BGR
    to match what AriaDemoObserver produces.
    """

    fov_h = 1.919  # ~110° Aria RGB camera (same as AriaDemoObserver)

    def __init__(self, zmq_endpoint: str = DEFAULT_ZMQ_ENDPOINT):
        self._endpoint = zmq_endpoint
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # Frame storage (BGR, post-processed like AriaDemoObserver)
        self._frames = {"rgb": None, "eye": None, "slam1": None, "slam2": None}
        self._frame_counts = {k: 0 for k in self._frames}
        self._frame_versions = {k: 0 for k in self._frames}
        self._start_time = time.time()

        # Start receive thread
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"[BRIDGE] AriaBridgeObserver conectado a {zmq_endpoint}")

    def _receive_loop(self):
        """Background thread: receive frames from ZMQ and store them."""
        ctx = zmq.Context()
        socket = ctx.socket(zmq.PULL)
        socket.setsockopt(zmq.RCVHWM, 2)  # drop oldest frames if consumer is slow
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

                shape = (height, width, channels) if channels > 1 else (height, width)
                raw = np.frombuffer(pixel_buf, dtype=np.uint8).reshape(shape)

                # Post-process to match AriaDemoObserver output (BGR for OpenCV)
                processed = self._process_frame(cam_name, raw)

                with self._lock:
                    self._frames[cam_name] = processed
                    self._frame_counts[cam_name] += 1
                    self._frame_versions[cam_name] += 1

                total = sum(self._frame_counts.values())  # outside lock
                if total % 300 == 0:
                    elapsed = time.time() - self._start_time
                    with self._lock:
                        counts = dict(self._frame_counts)
                    fps = {k: v / elapsed for k, v in counts.items() if v > 0}
                    fps_str = " ".join(f"{k}={v:.1f}" for k, v in fps.items())
                    print(f"[BRIDGE] {fps_str} fps (total={total})")
        except Exception as e:
            print(f"[BRIDGE] ERROR in receive thread: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            socket.close()
            ctx.term()

    @staticmethod
    def _process_frame(cam_name, raw):
        """Apply same transforms as AriaDemoObserver.on_image_received().

        Uses numpy ops only (no cv2) to avoid numpy 2.x / OpenCV ABI mismatch.
        Each path produces exactly one contiguous copy via ascontiguousarray.
        """
        if cam_name == "rgb":
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

    def get_frame(self, camera: str = "rgb") -> Optional[np.ndarray]:
        """Get the most recent frame for a camera. Returns BGR uint8 or None.

        Returns a read-only view — do not modify the array in place.
        Call .copy() yourself if you need to write to it.
        """
        with self._lock:
            frame = self._frames.get(camera)
            if frame is None:
                return None
            frame.flags.writeable = False
            return frame

    def get_frame_if_new(self, camera: str = "rgb", last_version: int = -1):
        """Returns (frame, version) only if the frame is newer than last_version.

        Returns (None, last_version) if nothing new. Avoids processing the
        same frame twice in a tight loop.
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

    def get_stats(self) -> Dict[str, Any]:
        elapsed = time.time() - self._start_time
        with self._lock:
            return {
                "source": "aria-bridge",
                "frames": dict(self._frame_counts),
                "fps": {k: v / elapsed for k, v in self._frame_counts.items() if v > 0},
                "uptime": elapsed,
                "zmq_endpoint": self._endpoint
            }

    def stop(self):
        """Stop the receive thread."""
        self._stop_event.set()
        self._thread.join(timeout=2)
        print("[BRIDGE] AriaBridgeObserver detenido")

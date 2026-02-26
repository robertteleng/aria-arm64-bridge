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
        self._start_time = time.time()

        # Start receive thread
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"[BRIDGE] AriaBridgeObserver conectado a {zmq_endpoint}")

    def _receive_loop(self):
        """Background thread: receive frames from ZMQ and store them."""
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

                data = socket.recv()
                if len(data) < HEADER_SIZE:
                    continue

                magic, cam_id, timestamp_ns, width, height, channels = struct.unpack(
                    HEADER_FORMAT, data[:HEADER_SIZE])

                if magic != HEADER_MAGIC:
                    continue

                cam_name = CAM_NAMES.get(cam_id)
                if cam_name is None:
                    continue

                expected_size = HEADER_SIZE + width * height * channels
                if len(data) != expected_size:
                    continue

                raw = np.frombuffer(data, dtype=np.uint8, offset=HEADER_SIZE).copy()
                if channels > 1:
                    raw = raw.reshape((height, width, channels))
                else:
                    raw = raw.reshape((height, width))

                # Post-process to match AriaDemoObserver output (BGR for OpenCV)
                processed = self._process_frame(cam_name, raw)

                with self._lock:
                    self._frames[cam_name] = processed
                    self._frame_counts[cam_name] += 1

                    # Periodic log
                    total = sum(self._frame_counts.values())
                    if total % 300 == 0:
                        elapsed = time.time() - self._start_time
                        fps = {k: v / elapsed for k, v in self._frame_counts.items() if v > 0}
                        fps_str = " ".join(f"{k}={v:.1f}" for k, v in fps.items())
                        print(f"[BRIDGE] {fps_str} fps (total={total})")
        except Exception as e:
            print(f"[BRIDGE] ERROR in receive thread: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            socket.close()
            ctx.term()

    def _process_frame(self, cam_name, raw):
        """Apply same transforms as AriaDemoObserver.on_image_received().

        Uses numpy ops only (no cv2) to avoid numpy 2.x / OpenCV ABI mismatch.
        """
        if cam_name == "rgb":
            # Aria RGB: rotate 90° CW, convert RGB→BGR
            processed = np.rot90(raw, k=-1)  # 90° CW = rot90 with k=-1
            processed = np.ascontiguousarray(processed[:, :, ::-1])  # RGB→BGR
        elif cam_name == "eye":
            # Eye: rotate 180°, grayscale→BGR
            processed = np.rot90(raw, 2)
            if len(processed.shape) == 2:
                processed = np.stack([processed] * 3, axis=-1)
        elif cam_name in ("slam1", "slam2"):
            # SLAM: rotate 90° CW, grayscale→BGR
            processed = np.rot90(raw, k=-1)
            if len(processed.shape) == 2:
                processed = np.stack([processed] * 3, axis=-1)
        else:
            processed = raw

        return np.ascontiguousarray(processed)

    def get_frame(self, camera: str = "rgb") -> Optional[np.ndarray]:
        """Get the most recent frame for a camera. Returns BGR uint8 or None."""
        with self._lock:
            frame = self._frames.get(camera)
            return frame.copy() if frame is not None else None

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

"""Unit tests for AriaFrameObserver's queue+sender-thread design (Exp 010 fix).

These run NATIVELY (no FEX, no glasses): we stub the ZMQ socket and feed the
observer fake frames/sensor samples, asserting the callback never blocks on the
send and that the dedicated sender thread drains the queue. Strong assertions:
the test fails if the send is moved back into the callback or the thread dies.

Run: .venv/bin/python -m pytest tests/test_observer_sender.py -v
(needs numpy + pyzmq in the native venv; no aria.sdk import — we import the
class directly, guarding the SDK import in the module under test.)
"""

import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

# The module imports `aria.sdk` which only exists under FEX. Stub it so the
# native test can import AriaFrameObserver without the SDK present.
if "aria" not in sys.modules:
    aria_mod = types.ModuleType("aria")
    sdk_mod = types.ModuleType("aria.sdk")
    # _map_camera reads aria.CameraId.{Rgb,Slam1,Slam2,EyeTrack} to build its
    # enum_map. Without these attributes the very first getattr(aria.CameraId, …)
    # raises AttributeError (aria.CameraId itself is missing) BEFORE the default
    # None applies — the default only guards the .Rgb level, not .CameraId. Stub
    # CameraId with the same attribute names the real SDK exposes so the test
    # exercises the production enum path, not just the substring fallback.
    sdk_mod.CameraId = types.SimpleNamespace(
        Rgb="Rgb", Slam1="Slam1", Slam2="Slam2", EyeTrack="EyeTrack")
    aria_mod.sdk = sdk_mod
    sys.modules["aria"] = aria_mod
    sys.modules["aria.sdk"] = sdk_mod

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aria_arm64_bridge.receiver import (  # noqa: E402
    AriaFrameObserver, HEADER_MAGIC, SENSOR_MAGIC, CAM_RGB,
)


class SpySocket:
    """Records every send_multipart call. Optionally blocks to simulate a slow
    consumer — used to prove the SDK callback does NOT wait on the send."""

    def __init__(self, block_event=None):
        self.sent = []
        self._lock = threading.Lock()
        self._block_event = block_event

    def send_multipart(self, parts, flags=0, copy=True):
        if self._block_event is not None:
            self._block_event.wait()  # hold the sender thread, not the callback
        # Materialize parts to stable bytes for assertions.
        with self._lock:
            self.sent.append([bytes(p) for p in parts])

    def count(self):
        with self._lock:
            return len(self.sent)


class FakeRecord:
    def __init__(self, capture_timestamp_ns=123):
        self.capture_timestamp_ns = capture_timestamp_ns
        # Matches stubbed aria.sdk.CameraId.Rgb → _map_camera resolves via the
        # enum_map (production path), returning (CAM_RGB, "rgb").
        self.camera_id = "Rgb"


def _make_observer(sock):
    obs = AriaFrameObserver(sock, bench_drop=False)
    obs.start()
    return obs


def test_frame_is_enqueued_and_sent():
    sock = SpySocket()
    obs = _make_observer(sock)
    try:
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        obs.on_image_received(img, FakeRecord())
        # The sender thread runs async; wait briefly for it to drain.
        deadline = time.monotonic() + 2.0
        while sock.count() < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sock.count() == 1, "frame never reached the socket"
        header, payload = sock.sent[0]
        assert header[:4] == HEADER_MAGIC
        assert header[4] == CAM_RGB
        assert len(payload) == img.nbytes  # full pixel copy forwarded
    finally:
        obs.stop()


def test_callback_does_not_block_on_slow_send():
    # Sender thread is held inside send_multipart; the callback must still
    # return fast because it only copies + enqueues.
    gate = threading.Event()
    sock = SpySocket(block_event=gate)
    obs = _make_observer(sock)
    try:
        img = np.zeros((8, 8, 3), dtype=np.uint8)
        t0 = time.monotonic()
        for _ in range(5):
            obs.on_image_received(img, FakeRecord())
        elapsed = time.monotonic() - t0
        # 5 callbacks while the sender is blocked: must be near-instant.
        # The OLD design (send under shared lock) would block here.
        assert elapsed < 0.5, f"callback blocked on slow send ({elapsed:.2f}s)"
        gate.set()  # release the sender
    finally:
        gate.set()
        obs.stop()


def test_buffer_is_copied_not_referenced():
    # Mutating the source array AFTER the callback returns must not change what
    # was enqueued — proves image.tobytes() made an independent copy.
    sock = SpySocket()
    obs = _make_observer(sock)
    try:
        img = np.ones((4, 4, 3), dtype=np.uint8)  # all 1s
        obs.on_image_received(img, FakeRecord())
        img[:] = 99  # SDK recycles/overwrites the buffer
        deadline = time.monotonic() + 2.0
        while sock.count() < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sock.count() == 1
        _, payload = sock.sent[0]
        assert set(payload) == {1}, "payload reflects post-callback mutation → not copied"
    finally:
        obs.stop()


def test_sensor_batch_enqueued():
    sock = SpySocket()
    obs = _make_observer(sock)
    try:
        obs.on_baro_received(types.SimpleNamespace(
            capture_timestamp_ns=1, pressure=101.3, temperature=20.0))
        deadline = time.monotonic() + 2.0
        while sock.count() < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sock.count() == 1
        header, _ = sock.sent[0]
        assert header[:4] == SENSOR_MAGIC
    finally:
        obs.stop()


def test_queue_full_drops_without_blocking():
    # Hold the sender so the queue fills; callbacks must drop, not block.
    gate = threading.Event()
    sock = SpySocket(block_event=gate)
    obs = AriaFrameObserver(sock, bench_drop=False, send_queue_size=4)
    obs.start()
    try:
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        t0 = time.monotonic()
        for _ in range(50):  # far more than queue size
            obs.on_image_received(img, FakeRecord())
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, "callbacks blocked when queue full instead of dropping"
        assert obs._dropped_enqueue > 0, "expected drops when queue saturated"
        gate.set()
    finally:
        gate.set()
        obs.stop()


def test_bench_drop_never_sends():
    sock = SpySocket()
    obs = AriaFrameObserver(sock, bench_drop=True)
    obs.start()  # no-op in bench_drop
    try:
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        for _ in range(10):
            obs.on_image_received(img, FakeRecord())
        time.sleep(0.2)
        assert sock.count() == 0, "bench_drop must not send anything"
        assert obs._recv_counts["rgb"] == 10  # but it still counts rx
    finally:
        obs.stop()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""Test AriaBridgeObserver — validates it receives frames and converts them correctly.

Usage:
    python3 tests/test_bridge_observer.py
"""

import struct
import sys
import threading
import time

import numpy as np
import zmq

sys.path.insert(0, "src/bridge")
from aria_bridge_observer import AriaBridgeObserver

ZMQ_ENDPOINT = "tcp://127.0.0.1:5559"

# Protocol v2
HEADER_FORMAT = "<4sB3xQIII"
HEADER_MAGIC = b"ARI2"
CAM_RGB = 0


def mock_sender(endpoint, num_frames, width, height):
    """Send RGB frames that the observer should receive and convert to BGR."""
    ctx = zmq.Context()
    socket = ctx.socket(zmq.PUSH)
    socket.bind(endpoint)
    time.sleep(0.3)

    for i in range(num_frames):
        # Create an RGB frame where R=100, G=150, B=200
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, 0] = 100  # R
        frame[:, :, 1] = 150  # G
        frame[:, :, 2] = 200  # B

        timestamp_ns = int(time.monotonic() * 1e9)
        header = struct.pack(HEADER_FORMAT, HEADER_MAGIC, CAM_RGB, timestamp_ns,
                             width, height, 3)
        socket.send(header + frame.tobytes())
        time.sleep(0.02)

    time.sleep(0.3)
    socket.close()
    ctx.term()


def test_observer():
    errors = []
    width, height = 100, 100
    num_frames = 5

    # Start mock sender
    t = threading.Thread(target=mock_sender, args=(ZMQ_ENDPOINT, num_frames, width, height))
    t.start()

    # Create observer
    observer = AriaBridgeObserver(zmq_endpoint=ZMQ_ENDPOINT)
    time.sleep(0.5)  # let it connect and receive some frames

    # Wait for frames
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        frame = observer.get_frame("rgb")
        if frame is not None:
            break
        time.sleep(0.05)

    if frame is None:
        errors.append("No frame received within timeout")
    else:
        # Frame should be rotated 90° CW: (100, 100) → (100, 100) (square, no change)
        # And converted RGB→BGR: so B=200 should be channel 0, R=100 channel 2
        if frame.shape[2] != 3:
            errors.append(f"Expected 3 channels, got {frame.shape[2]}")
        elif frame[0, 0, 0] != 200:  # BGR: blue channel should be 200
            errors.append(f"BGR conversion wrong: channel 0 = {frame[0, 0, 0]}, expected 200 (blue)")
        elif frame[0, 0, 2] != 100:  # BGR: red channel should be 100
            errors.append(f"BGR conversion wrong: channel 2 = {frame[0, 0, 2]}, expected 100 (red)")

    # Check stats
    stats = observer.get_stats()
    if stats["source"] != "aria-bridge":
        errors.append(f"Wrong source: {stats['source']}")

    # Check eye frame is None (not sent)
    eye = observer.get_frame("eye")
    if eye is not None:
        errors.append("Eye frame should be None (not sent)")

    observer.stop()
    t.join(timeout=5)

    # Report
    print(f"Errors: {len(errors)}")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        return False

    print(f"Stats: {stats}")
    print("PASS — AriaBridgeObserver works correctly")
    return True


if __name__ == "__main__":
    success = test_observer()
    exit(0 if success else 1)

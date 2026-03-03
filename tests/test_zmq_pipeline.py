"""Test the ZMQ pipeline: mock_receiver → frame_consumer.

Runs both sides in-process to validate the protocol works.

Usage:
    python3 tests/test_zmq_pipeline.py
"""

import struct
import threading
import time

import numpy as np
import zmq

ZMQ_ENDPOINT = "tcp://127.0.0.1:5556"  # different port to avoid conflicts
NUM_FRAMES = 10
WIDTH, HEIGHT, CHANNELS = 320, 240, 3

# Protocol v2 constants
HEADER_FORMAT = "<4sB3xQIII"
HEADER_SIZE = 28
HEADER_MAGIC = b"ARI2"
CAM_RGB = 0


def sender_thread(endpoint, num_frames):
    """Simulates mock_receiver: sends frames over ZMQ."""
    ctx = zmq.Context()
    socket = ctx.socket(zmq.PUSH)
    socket.bind(endpoint)
    time.sleep(0.2)  # let consumer connect

    for i in range(num_frames):
        timestamp_ns = int(time.monotonic() * 1e9)
        frame = np.full((HEIGHT, WIDTH, CHANNELS), i % 256, dtype=np.uint8)
        header = struct.pack(HEADER_FORMAT, HEADER_MAGIC, CAM_RGB, timestamp_ns,
                             WIDTH, HEIGHT, CHANNELS)
        socket.send_multipart([header, memoryview(frame)], copy=False)
        time.sleep(0.01)

    time.sleep(0.2)  # let consumer drain
    socket.close()
    ctx.term()


def test_pipeline():
    """Send frames and verify they arrive correctly."""
    ctx = zmq.Context()
    socket = ctx.socket(zmq.PULL)
    socket.connect(ZMQ_ENDPOINT)

    t = threading.Thread(target=sender_thread, args=(ZMQ_ENDPOINT, NUM_FRAMES))
    t.start()

    received = 0
    errors = []
    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    while received < NUM_FRAMES:
        events = dict(poller.poll(timeout=2000))
        if socket not in events:
            errors.append(f"Timeout waiting for frame {received}")
            break

        parts = socket.recv_multipart()
        if len(parts) != 2:
            errors.append(f"Frame {received}: expected 2 parts, got {len(parts)}")
            received += 1
            continue

        header_data, pixel_data = parts

        if len(header_data) < HEADER_SIZE:
            errors.append(f"Frame {received}: header too short ({len(header_data)} bytes)")
            received += 1
            continue

        magic, cam_id, ts_ns, w, h, ch = struct.unpack(HEADER_FORMAT, header_data[:HEADER_SIZE])

        if magic != HEADER_MAGIC:
            errors.append(f"Frame {received}: bad magic {magic!r}")
        if cam_id != CAM_RGB:
            errors.append(f"Frame {received}: wrong cam_id {cam_id}")
        if w != WIDTH or h != HEIGHT or ch != CHANNELS:
            errors.append(f"Frame {received}: wrong dimensions {w}x{h}x{ch}")

        expected_pixels = WIDTH * HEIGHT * CHANNELS
        if len(pixel_data) != expected_pixels:
            errors.append(f"Frame {received}: pixel size mismatch {len(pixel_data)} vs {expected_pixels}")
        else:
            frame = np.frombuffer(pixel_data, dtype=np.uint8).reshape((HEIGHT, WIDTH, CHANNELS))
            expected_val = received % 256
            if not np.all(frame == expected_val):
                errors.append(f"Frame {received}: pixel mismatch")

        received += 1

    t.join(timeout=5)
    socket.close()
    ctx.term()

    # Report
    print(f"Frames sent:     {NUM_FRAMES}")
    print(f"Frames received: {received}")
    print(f"Errors:          {len(errors)}")

    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        return False

    print("PASS — ZMQ pipeline works correctly")
    return True


if __name__ == "__main__":
    success = test_pipeline()
    exit(0 if success else 1)

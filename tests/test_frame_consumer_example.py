"""The frame_consumer example must speak the current wire protocol (v2.1 multipart).

It drifted once: it read single-part v1 messages and rejected every frame the
mock receiver sent, while the rest of the suite stayed green.
"""

import importlib.util
import struct
import time
from pathlib import Path

import numpy as np
import pytest
import zmq

from aria_arm64_bridge import protocol

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "frame_consumer.py"


def load_example():
    spec = importlib.util.spec_from_file_location("frame_consumer", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def header(cam_id=0, w=4, h=2, c=3, ts=123):
    return struct.pack(protocol.HEADER_FORMAT, protocol.HEADER_MAGIC, cam_id, ts, w, h, c)


def test_example_constants_match_protocol():
    fc = load_example()
    assert (fc.HEADER_FORMAT, fc.HEADER_SIZE, fc.HEADER_MAGIC) == (
        protocol.HEADER_FORMAT, protocol.HEADER_SIZE, protocol.HEADER_MAGIC)


def test_parse_multipart_frame():
    fc = load_example()
    pixels = np.arange(4 * 2 * 3, dtype=np.uint8).reshape(2, 4, 3)
    cam, ts, frame = fc.parse_frame([header(cam_id=2), pixels.tobytes()])
    assert (cam, ts) == ("slam1", 123)
    assert np.array_equal(frame, pixels)


def test_sensor_batches_are_skipped_and_bad_frames_rejected():
    fc = load_example()
    assert fc.parse_frame([b"ARS1" + b"\x00" * 8, b"\x00" * 16]) is None
    with pytest.raises(ValueError):
        fc.parse_frame([header() + b"extra"])  # single part: the old v1 layout
    with pytest.raises(ValueError):
        fc.parse_frame([header(), b"\x00" * 5])  # payload size mismatch


def test_consumer_receives_frames_from_the_mock_receiver():
    """End to end over loopback: the real mock receiver feeding the example's parser."""
    fc = load_example()
    endpoint = "tcp://127.0.0.1:5561"
    ctx = zmq.Context.instance()
    pull = ctx.socket(zmq.PULL)
    pull.connect(endpoint)

    import subprocess
    import sys
    mock = subprocess.Popen([sys.executable, "-m", "aria_arm64_bridge.mock_receiver", "--zmq-endpoint", endpoint,
                             "--fps", "30", "--width", "64", "--height", "48"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got = 0
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and got < 5:
        if pull.poll(200):
            parsed = fc.parse_frame(pull.recv_multipart())
            if parsed:
                got += 1
    pull.close(linger=0)
    mock.terminate()
    mock.wait(timeout=5)
    assert got >= 5

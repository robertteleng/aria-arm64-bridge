"""Native ARM64 frame consumer — receives frames from the ZMQ bridge.

Runs natively on Jetson (no FEX-Emu). Connects to the receiver's ZMQ
socket and makes frames available for downstream processing.

Usage:
    python3 src/bridge/frame_consumer.py
    python3 src/bridge/frame_consumer.py --zmq-endpoint tcp://127.0.0.1:5555
"""

import argparse
import signal
import struct
import sys
import time

import numpy as np
import zmq

DEFAULT_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"
HEADER_SIZE = 24  # magic(4) + timestamp_ns(8) + width(4) + height(4) + channels(4)
HEADER_MAGIC = b"ARIA"


def parse_frame(data):
    """Parse a frame message from the receiver.

    Returns (timestamp_ns, frame) where frame is a numpy array (H, W, C).
    Raises ValueError on invalid data.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Message too short: {len(data)} bytes")

    magic, timestamp_ns, width, height, channels = struct.unpack("<4sQIII", data[:HEADER_SIZE])

    if magic != HEADER_MAGIC:
        raise ValueError(f"Bad magic: {magic!r}")

    expected_size = HEADER_SIZE + width * height * channels
    if len(data) != expected_size:
        raise ValueError(f"Size mismatch: got {len(data)}, expected {expected_size}")

    frame = np.frombuffer(data, dtype=np.uint8, offset=HEADER_SIZE)
    frame = frame.reshape((height, width, channels)) if channels > 1 else frame.reshape((height, width))

    return timestamp_ns, frame


def run(zmq_endpoint, callback=None):
    """Main consumer loop. Calls callback(timestamp_ns, frame) for each frame.

    If callback is None, just prints stats.
    """
    ctx = zmq.Context()
    socket = ctx.socket(zmq.PULL)
    socket.connect(zmq_endpoint)
    print(f"[consumer] Connected to {zmq_endpoint}")

    frame_count = 0
    start_time = time.monotonic()
    latencies = []

    shutdown = False

    def handle_signal(sig, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    print("[consumer] Waiting for frames...")

    while not shutdown:
        events = dict(poller.poll(timeout=100))
        if socket not in events:
            continue

        data = socket.recv()
        recv_time_ns = int(time.monotonic() * 1e9)

        try:
            timestamp_ns, frame = parse_frame(data)
        except ValueError as e:
            print(f"[consumer] Bad frame: {e}", file=sys.stderr)
            continue

        latency_ms = (recv_time_ns - timestamp_ns) / 1e6
        latencies.append(latency_ms)

        if callback:
            callback(timestamp_ns, frame)

        frame_count += 1
        if frame_count % 30 == 0:
            elapsed = time.monotonic() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0
            avg_latency = sum(latencies[-30:]) / min(len(latencies), 30)
            h, w = frame.shape[:2]
            print(f"[consumer] frames={frame_count} fps={fps:.1f} "
                  f"latency={avg_latency:.1f}ms size={w}x{h}")

    elapsed = time.monotonic() - start_time
    print(f"\n[consumer] Summary:")
    print(f"  Total frames: {frame_count}")
    print(f"  Duration: {elapsed:.1f}s")
    if frame_count > 0:
        print(f"  Average FPS: {frame_count / elapsed:.1f}")
    if latencies:
        print(f"  Latency avg={sum(latencies)/len(latencies):.1f}ms "
              f"min={min(latencies):.1f}ms max={max(latencies):.1f}ms")

    socket.close()
    ctx.term()


def main():
    parser = argparse.ArgumentParser(description="Native ARM64 frame consumer")
    parser.add_argument("--zmq-endpoint", default=DEFAULT_ZMQ_ENDPOINT)
    args = parser.parse_args()

    run(args.zmq_endpoint)


if __name__ == "__main__":
    main()

"""Mock frame receiver — generates fake frames for testing without Aria glasses.

Simulates the same ZMQ protocol as aria_receiver.py but with synthetic data.
Does NOT require FEX-Emu or the Aria SDK.

Usage:
    python3 src/receiver/mock_receiver.py
    python3 src/receiver/mock_receiver.py --fps 15 --width 640 --height 480
"""

import argparse
import signal
import struct
import time

import numpy as np
import zmq

DEFAULT_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"


def run(zmq_endpoint, fps, width, height):
    ctx = zmq.Context()
    socket = ctx.socket(zmq.PUSH)
    socket.setsockopt(zmq.SNDHWM, 2)
    socket.bind(zmq_endpoint)
    print(f"[mock] ZMQ bound to {zmq_endpoint}")
    print(f"[mock] Generating {width}x{height} RGB @ {fps} FPS")

    frame_interval = 1.0 / fps
    frame_count = 0
    channels = 3

    shutdown = False

    def handle_signal(sig, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    start_time = time.monotonic()
    print("[mock] Streaming. Press Ctrl+C to stop.")

    while not shutdown:
        t0 = time.monotonic()
        timestamp_ns = int(t0 * 1e9)

        # Generate a frame with a moving gradient so it's visually distinguishable
        x = np.linspace(0, 1, width, dtype=np.float32)
        y = np.linspace(0, 1, height, dtype=np.float32)
        phase = (frame_count % 60) / 60.0
        r = np.outer(y, np.roll(x, int(phase * width)))
        g = np.outer(np.roll(y, int(phase * height)), x)
        b = np.full((height, width), phase, dtype=np.float32)
        frame = (np.stack([r, g, b], axis=2) * 255).astype(np.uint8)

        header = struct.pack("<4sQIII", b"ARIA", timestamp_ns, width, height, channels)

        try:
            socket.send(header + frame.tobytes(), zmq.NOBLOCK)
        except zmq.Again:
            pass  # consumer too slow, drop frame

        frame_count += 1
        if frame_count % fps == 0:
            elapsed = time.monotonic() - start_time
            actual_fps = frame_count / elapsed if elapsed > 0 else 0
            print(f"[mock] frames={frame_count} fps={actual_fps:.1f}")

        # Rate limit
        elapsed = time.monotonic() - t0
        sleep_time = frame_interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    print(f"[mock] Done. Sent {frame_count} frames.")
    socket.close()
    ctx.term()


def main():
    parser = argparse.ArgumentParser(description="Mock Aria frame sender (no glasses needed)")
    parser.add_argument("--zmq-endpoint", default=DEFAULT_ZMQ_ENDPOINT)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1408, help="Aria RGB camera width")
    parser.add_argument("--height", type=int, default=1408, help="Aria RGB camera height")
    args = parser.parse_args()

    run(args.zmq_endpoint, args.fps, args.width, args.height)


if __name__ == "__main__":
    main()

"""Minimal native drain consumer — receives ZMQ frames and discards them.

Isolates the receiver's tx cost from aria-guard container work: if SLAM rx
drops with this consumer attached but recovers with none, the bottleneck is
the receiver's own ZMQ IO-thread transmission, not the consumer.

Usage:
    python3 tests/drain_consumer.py [--endpoint tcp://127.0.0.1:5555] [--seconds 40]
"""

import argparse
import struct
import time

import zmq

HEADER_MAGIC = b"ARI2"
SENSOR_MAGIC = b"ARS1"
CAM_NAMES = {0: "rgb", 1: "eye", 2: "slam1", 3: "slam2"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="tcp://127.0.0.1:5555")
    parser.add_argument("--seconds", type=float, default=40.0)
    args = parser.parse_args()

    ctx = zmq.Context()
    sock = ctx.socket(zmq.PULL)
    sock.setsockopt(zmq.RCVHWM, 64)
    sock.setsockopt(zmq.RCVTIMEO, 1000)
    sock.connect(args.endpoint)
    print(f"[drain] connected to {args.endpoint}, draining {args.seconds:.0f}s")

    counts = {}
    win_counts = {}
    bytes_total = 0
    start = time.monotonic()
    win_start = start

    while True:
        now = time.monotonic()
        if now - start >= args.seconds:
            break
        try:
            parts = sock.recv_multipart()
        except zmq.Again:
            continue
        head = parts[0]
        if head[:4] == HEADER_MAGIC:
            cam_id = struct.unpack_from("<4sB", head)[1]
            name = CAM_NAMES.get(cam_id, f"cam{cam_id}")
        elif head[:4] == SENSOR_MAGIC:
            name = "sensor"
        else:
            name = "unknown"
        counts[name] = counts.get(name, 0) + 1
        win_counts[name] = win_counts.get(name, 0) + 1
        bytes_total += sum(len(p) for p in parts)

        win = now - win_start
        if win >= 10.0:
            fps = " ".join(f"{k}={v / win:.1f}" for k, v in sorted(win_counts.items()))
            print(f"[drain] {fps} msg/s (win {win:.0f}s)", flush=True)
            win_counts = {}
            win_start = now

    elapsed = time.monotonic() - start
    fps = " ".join(f"{k}={v / elapsed:.1f}" for k, v in sorted(counts.items()))
    print(f"[drain] TOTAL {fps} msg/s over {elapsed:.0f}s, "
          f"{bytes_total / elapsed / 1e6:.1f} MB/s", flush=True)
    sock.close()
    ctx.term()


if __name__ == "__main__":
    main()

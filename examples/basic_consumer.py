#!/usr/bin/env python3
"""Minimal example — stream Aria frames on Jetson ARM64.

Prerequisites:
    1. FEX-Emu installed with Aria SDK (see scripts/setup_fex_emu.sh)
    2. Aria glasses paired (see docs/project/ARIA_CONNECTION_GUIDE.md)
    3. pip install aria-arm64-bridge

Usage:
    python examples/basic_consumer.py
    python examples/basic_consumer.py --interface wifi --device-ip 192.168.1.42
"""

import argparse
import time

from aria_arm64_bridge import AriaBridge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interface", choices=["usb", "wifi"], default="usb")
    parser.add_argument("--device-ip", help="Aria glasses IP (wifi only)")
    args = parser.parse_args()

    with AriaBridge(interface=args.interface, device_ip=args.device_ip) as bridge:
        print("Streaming — press Ctrl+C to stop\n")

        count = 0
        t0 = time.monotonic()

        while bridge.is_running:
            frame = bridge.get_frame("rgb")
            if frame is None:
                time.sleep(0.01)
                continue

            count += 1
            if count % 30 == 0:
                elapsed = time.monotonic() - t0
                fps = count / elapsed
                h, w = frame.shape[:2]
                print(f"  frames={count}  fps={fps:.1f}  shape={w}x{h}")


if __name__ == "__main__":
    main()

"""Aria SDK frame receiver — runs under FEX-Emu (x86_64 emulated).

Connects to Aria glasses via the SDK, receives frames, and pushes
them over ZMQ to the native ARM64 consumer.

Usage:
    FEXBash -c "python3 src/receiver/aria_receiver.py --interface usb"
    FEXBash -c "python3 src/receiver/aria_receiver.py --interface wifi --device-ip 192.168.1.42"
"""

import argparse
import signal
import struct
import sys
import time

import zmq

# These imports only work under FEX-Emu (x86_64)
try:
    import aria.sdk as aria
except ImportError:
    print("ERROR: aria.sdk not found. Run this under FEX-Emu.", file=sys.stderr)
    print("  FEXBash -c \"python3 src/receiver/aria_receiver.py\"", file=sys.stderr)
    sys.exit(1)

DEFAULT_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"
PROFILE_WIFI = "profile18"
PROFILE_USB = "profile28"


class AriaFrameObserver(aria.BaseStreamingClientObserver):
    """Receives frames from Aria SDK and pushes them over ZMQ."""

    def __init__(self, zmq_socket):
        super().__init__()
        self._socket = zmq_socket
        self._frame_count = 0
        self._start_time = time.monotonic()

    def on_image_received(self, image, record):
        timestamp_ns = record.capture_timestamp_ns
        width = image.shape[1] if len(image.shape) >= 2 else 0
        height = image.shape[0] if len(image.shape) >= 2 else 0

        # Header: magic(4) + timestamp_ns(8) + width(4) + height(4) + channels(4)
        channels = image.shape[2] if len(image.shape) == 3 else 1
        header = struct.pack("<4sQIII", b"ARIA", timestamp_ns, width, height, channels)

        self._socket.send(header + image.tobytes(), zmq.NOBLOCK)

        self._frame_count += 1
        if self._frame_count % 30 == 0:
            elapsed = time.monotonic() - self._start_time
            fps = self._frame_count / elapsed if elapsed > 0 else 0
            print(f"[receiver] frames={self._frame_count} fps={fps:.1f} "
                  f"size={width}x{height}x{channels}")


def run(interface, device_ip, zmq_endpoint, profile):
    ctx = zmq.Context()
    socket = ctx.socket(zmq.PUSH)
    socket.setsockopt(zmq.SNDHWM, 2)  # drop old frames if consumer is slow
    socket.bind(zmq_endpoint)
    print(f"[receiver] ZMQ bound to {zmq_endpoint}")

    device_client = aria.DeviceClient()
    print(f"[receiver] Connecting via {interface}...")

    if interface == "usb":
        device = device_client.connect()
    else:
        device = device_client.connect(device_ip)

    streaming_manager = device.streaming_manager
    config = streaming_manager.streaming_config
    resolved_profile = profile or (PROFILE_USB if interface == "usb" else PROFILE_WIFI)
    config.profile_name = resolved_profile
    config.use_ephemeral_certs = True

    print(f"[receiver] Starting streaming (profile={resolved_profile})...")
    streaming_manager.start_streaming()

    observer = AriaFrameObserver(socket)
    streaming_client = streaming_manager.streaming_client
    streaming_client.set_streaming_client_observer(observer)
    streaming_client.subscribe()

    print("[receiver] Streaming active. Press Ctrl+C to stop.")

    shutdown = False

    def handle_signal(sig, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not shutdown:
        time.sleep(0.1)

    print("[receiver] Shutting down...")
    streaming_client.unsubscribe()
    streaming_manager.stop_streaming()
    device_client.disconnect(device)
    socket.close()
    ctx.term()
    print("[receiver] Done.")


def main():
    parser = argparse.ArgumentParser(description="Aria SDK frame receiver (FEX-Emu)")
    parser.add_argument("--interface", choices=["usb", "wifi"], default="usb")
    parser.add_argument("--device-ip", help="Aria glasses IP (required for wifi)")
    parser.add_argument("--zmq-endpoint", default=DEFAULT_ZMQ_ENDPOINT)
    parser.add_argument("--profile", default=None,
                        help="Streaming profile (default: profile28 for USB, profile18 for WiFi)")
    args = parser.parse_args()

    if args.interface == "wifi" and not args.device_ip:
        parser.error("--device-ip is required for wifi interface")

    run(args.interface, args.device_ip, args.zmq_endpoint, args.profile)


if __name__ == "__main__":
    main()

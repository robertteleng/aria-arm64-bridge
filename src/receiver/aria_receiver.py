"""Aria SDK frame receiver — runs under FEX-Emu (x86_64 emulated).

Connects to Aria glasses via the SDK, receives frames, and pushes
them over ZMQ to the native ARM64 consumer.

Usage:
    PYTHONNOUSERSITE=1 FEXBash -c "python3 src/receiver/aria_receiver.py --interface usb"
    PYTHONNOUSERSITE=1 FEXBash -c "python3 src/receiver/aria_receiver.py --interface wifi --device-ip 192.168.1.42"

Protocol (v2):
    Header: magic(4) + camera_id(1) + pad(3) + timestamp_ns(8) + width(4) + height(4) + channels(4)
    Total header: 28 bytes, followed by raw pixel data (uint8)
    Camera IDs: 0=rgb, 1=eye, 2=slam1, 3=slam2
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
    print("  PYTHONNOUSERSITE=1 FEXBash -c \"python3 src/receiver/aria_receiver.py\"", file=sys.stderr)
    sys.exit(1)

DEFAULT_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"
PROFILE_WIFI = "profile18"
PROFILE_USB = "profile28"

# Header format: magic(4s) + camera_id(B) + pad(3x) + timestamp(Q) + w(I) + h(I) + ch(I)
HEADER_FORMAT = "<4sB3xQIII"
HEADER_SIZE = 28
HEADER_MAGIC = b"ARI2"

# Camera ID mapping
CAM_RGB = 0
CAM_EYE = 1
CAM_SLAM1 = 2
CAM_SLAM2 = 3


class AriaFrameObserver(aria.BaseStreamingClientObserver):
    """Receives frames from Aria SDK and pushes them over ZMQ."""

    def __init__(self, zmq_socket):
        super().__init__()
        self._socket = zmq_socket
        self._frame_counts = {"rgb": 0, "eye": 0, "slam1": 0, "slam2": 0}
        self._start_time = time.monotonic()

    def _send_frame(self, cam_id, cam_name, image, timestamp_ns):
        height, width = image.shape[:2]
        channels = image.shape[2] if len(image.shape) == 3 else 1
        header = struct.pack(HEADER_FORMAT, HEADER_MAGIC, cam_id, timestamp_ns,
                             width, height, channels)
        try:
            self._socket.send(header + image.tobytes(), zmq.NOBLOCK)
        except zmq.Again:
            return  # consumer too slow, drop frame

        self._frame_counts[cam_name] += 1
        total = sum(self._frame_counts.values())
        if total % 90 == 0:
            elapsed = time.monotonic() - self._start_time
            fps = {k: v / elapsed for k, v in self._frame_counts.items() if v > 0}
            fps_str = " ".join(f"{k}={v:.0f}" for k, v in fps.items())
            print(f"[receiver] {fps_str} fps (total={total})")

    def on_image_received(self, image, record):
        camera_id = record.camera_id
        timestamp_ns = record.capture_timestamp_ns

        if camera_id == aria.CameraId.Rgb:
            self._send_frame(CAM_RGB, "rgb", image, timestamp_ns)
        elif camera_id == aria.CameraId.EyeTrack:
            self._send_frame(CAM_EYE, "eye", image, timestamp_ns)
        elif camera_id == aria.CameraId.Slam1:
            self._send_frame(CAM_SLAM1, "slam1", image, timestamp_ns)
        elif camera_id == aria.CameraId.Slam2:
            self._send_frame(CAM_SLAM2, "slam2", image, timestamp_ns)


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
        client_config = aria.DeviceClientConfig()
        client_config.ip_v4_address = device_ip
        device_client.set_client_config(client_config)
        device = device_client.connect()

    streaming_manager = device.streaming_manager

    config = aria.StreamingConfig()
    resolved_profile = profile or (PROFILE_USB if interface == "usb" else PROFILE_WIFI)
    config.profile_name = resolved_profile
    if interface == "wifi":
        config.streaming_interface = aria.StreamingInterface.WifiStation
    else:
        config.streaming_interface = aria.StreamingInterface.Usb
    config.security_options.use_ephemeral_certs = True
    streaming_manager.streaming_config = config

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

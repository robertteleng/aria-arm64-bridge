#!/usr/bin/env bash
# Test receiving frames from Aria glasses under FEX-Emu
# Requires: Aria glasses paired and on same WiFi network
set -euo pipefail

echo "=== aria-arm64-bridge: Streaming Test ==="
echo ""
echo "Prerequisites:"
echo "  - Aria glasses powered on and paired"
echo "  - Glasses and Jetson on same WiFi network"
echo "  - test_import.sh passed"
echo ""

DURATION="${1:-10}"
echo "Will attempt to stream for $DURATION seconds..."
echo ""

FEXBash -c "python3 -c '
import time
import aria.sdk as aria

class TestObserver(aria.StreamingClientObserver):
    def __init__(self):
        super().__init__()
        self.frame_count = 0
        self.first_frame_time = None

    def on_image_received(self, image, record):
        self.frame_count += 1
        if self.first_frame_time is None:
            self.first_frame_time = time.time()
            print(f\"First frame received! Camera: {record.camera_id}\")
        if self.frame_count % 30 == 0:
            elapsed = time.time() - self.first_frame_time
            fps = self.frame_count / elapsed if elapsed > 0 else 0
            print(f\"Frames: {self.frame_count}, FPS: {fps:.1f}, Elapsed: {elapsed:.1f}s\")

print(\"Initializing Aria SDK...\")
aria.set_log_level(aria.Level.Info)

# Device client
device_client = aria.DeviceClient()
client_config = aria.DeviceClientConfig()
device_client.set_client_config(client_config)

# Connect
print(\"Connecting to Aria glasses...\")
device = device_client.connect()
print(f\"Connected to: {device}\")

# Start streaming
streaming_manager = device.streaming_manager
streaming_client = streaming_manager.streaming_client

config = aria.StreamingConfig()
config.profile_name = \"profile18\"  # RGB only, low bandwidth
streaming_manager.streaming_config = config

observer = TestObserver()
streaming_client.set_streaming_client_observer(observer)

streaming_manager.start_streaming()
print(f\"Streaming started. Waiting $DURATION seconds...\")

time.sleep($DURATION)

streaming_manager.stop_streaming()
device_client.disconnect(device)

print()
print(f\"=== RESULT ===\")
print(f\"Total frames: {observer.frame_count}\")
if observer.first_frame_time:
    total_time = time.time() - observer.first_frame_time
    print(f\"Average FPS: {observer.frame_count / total_time:.1f}\")
    print(f\"STREAMING TEST: PASS\")
else:
    print(f\"No frames received!\")
    print(f\"STREAMING TEST: FAIL\")
'" || { echo "FAIL: Streaming test crashed"; exit 1; }

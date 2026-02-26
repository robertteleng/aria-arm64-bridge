"""Wire protocol constants for the Aria ARM64 Bridge.

Shared between the FEX-Emu receiver and the native ARM64 consumer.
Protocol v2: 28-byte header + raw pixel data over ZMQ PUSH/PULL.
"""

import struct

HEADER_FORMAT = "<4sB3xQIII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 28 bytes
HEADER_MAGIC = b"ARI2"

DEFAULT_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"

# Camera IDs
CAM_RGB = 0
CAM_EYE = 1
CAM_SLAM1 = 2
CAM_SLAM2 = 3

CAM_NAMES = {CAM_RGB: "rgb", CAM_EYE: "eye", CAM_SLAM1: "slam1", CAM_SLAM2: "slam2"}

# Streaming profiles
PROFILE_STREAMING = "profile12"  # streaming-optimized, no audio, ~11 FPS RGB

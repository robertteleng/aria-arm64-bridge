"""Wire protocol constants for the Aria ARM64 Bridge.

Shared between the FEX-Emu receiver and the native ARM64 consumer.

Protocol v2: 28-byte header + raw pixel data over ZMQ PUSH/PULL.
Protocol v2.1 adds sensor messages (IMU/magnetometer/barometer) with a
distinct magic (``ARS1``) on the same socket; consumers that only know
``ARI2`` skip them safely via the magic check.
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

# ---------------------------------------------------------------------------
# Sensor messages (protocol v2.1)
# ---------------------------------------------------------------------------
# ZMQ multipart: [sensor_header, payload]
#   sensor_header: magic(4s) + sensor_id(B) + pad(3x) + sample_count(I) = 12 bytes
#   payload: sample_count consecutive samples, layout per sensor below.

SENSOR_HEADER_FORMAT = "<4sB3xI"
SENSOR_HEADER_SIZE = struct.calcsize(SENSOR_HEADER_FORMAT)  # 12 bytes
SENSOR_MAGIC = b"ARS1"

SENSOR_IMU1 = 0   # 1000 Hz
SENSOR_IMU2 = 1   # 800 Hz
SENSOR_MAG = 2    # 10 Hz
SENSOR_BARO = 3   # 50 Hz

SENSOR_NAMES = {SENSOR_IMU1: "imu1", SENSOR_IMU2: "imu2",
                SENSOR_MAG: "mag", SENSOR_BARO: "baro"}

# Per-sample layouts (little-endian):
#   IMU:  timestamp_ns(q) + accel xyz m/s^2 (3f) + gyro xyz rad/s (3f) = 32 bytes
#   MAG:  timestamp_ns(q) + field xyz tesla (3f)                       = 20 bytes
#   BARO: timestamp_ns(q) + pressure Pa (f) + temperature C (f)        = 16 bytes
IMU_SAMPLE_FORMAT = "<q6f"
IMU_SAMPLE_SIZE = struct.calcsize(IMU_SAMPLE_FORMAT)
MAG_SAMPLE_FORMAT = "<q3f"
MAG_SAMPLE_SIZE = struct.calcsize(MAG_SAMPLE_FORMAT)
BARO_SAMPLE_FORMAT = "<q2f"
BARO_SAMPLE_SIZE = struct.calcsize(BARO_SAMPLE_FORMAT)

SENSOR_SAMPLE_FORMATS = {
    SENSOR_IMU1: IMU_SAMPLE_FORMAT,
    SENSOR_IMU2: IMU_SAMPLE_FORMAT,
    SENSOR_MAG: MAG_SAMPLE_FORMAT,
    SENSOR_BARO: BARO_SAMPLE_FORMAT,
}


def pack_sensor_header(sensor_id: int, sample_count: int) -> bytes:
    """Build the 12-byte sensor message header."""
    return struct.pack(SENSOR_HEADER_FORMAT, SENSOR_MAGIC, sensor_id, sample_count)


def unpack_sensor_header(buf: bytes):
    """Parse a sensor header. Returns (magic, sensor_id, sample_count)."""
    return struct.unpack(SENSOR_HEADER_FORMAT, buf[:SENSOR_HEADER_SIZE])


def unpack_sensor_samples(sensor_id: int, payload: bytes):
    """Yield decoded sample tuples for *sensor_id* from *payload*.

    IMU: (timestamp_ns, ax, ay, az, gx, gy, gz)
    MAG: (timestamp_ns, mx, my, mz)
    BARO: (timestamp_ns, pressure, temperature)
    """
    fmt = SENSOR_SAMPLE_FORMATS[sensor_id]
    return struct.iter_unpack(fmt, payload)

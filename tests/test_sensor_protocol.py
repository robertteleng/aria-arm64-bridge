"""Tests for protocol v2.1 sensor messages (ARS1)."""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aria_arm64_bridge.protocol import (
    SENSOR_HEADER_SIZE, SENSOR_MAGIC, HEADER_MAGIC,
    SENSOR_IMU1, SENSOR_IMU2, SENSOR_MAG, SENSOR_BARO,
    IMU_SAMPLE_FORMAT, MAG_SAMPLE_FORMAT, BARO_SAMPLE_FORMAT,
    IMU_SAMPLE_SIZE, MAG_SAMPLE_SIZE, BARO_SAMPLE_SIZE,
    pack_sensor_header, unpack_sensor_header, unpack_sensor_samples,
)


class TestSensorHeader:
    def test_roundtrip(self):
        header = pack_sensor_header(SENSOR_IMU1, 25)
        magic, sensor_id, count = unpack_sensor_header(header)
        assert magic == SENSOR_MAGIC
        assert sensor_id == SENSOR_IMU1
        assert count == 25

    def test_header_size(self):
        assert len(pack_sensor_header(SENSOR_BARO, 1)) == SENSOR_HEADER_SIZE

    def test_magic_differs_from_frame_magic(self):
        # Consumers route by magic — they must never collide
        assert SENSOR_MAGIC != HEADER_MAGIC

    def test_zero_samples(self):
        magic, sensor_id, count = unpack_sensor_header(pack_sensor_header(SENSOR_MAG, 0))
        assert count == 0

    def test_max_count(self):
        _, _, count = unpack_sensor_header(pack_sensor_header(SENSOR_IMU2, 2**32 - 1))
        assert count == 2**32 - 1


class TestImuSamples:
    def test_roundtrip_batch(self):
        samples = [
            (1000 + i, 0.1 * i, -9.81, 0.0, 0.01, -0.02, 0.03) for i in range(5)
        ]
        payload = b"".join(struct.pack(IMU_SAMPLE_FORMAT, *s) for s in samples)
        decoded = list(unpack_sensor_samples(SENSOR_IMU1, payload))
        assert len(decoded) == 5
        for orig, dec in zip(samples, decoded):
            assert dec[0] == orig[0]
            assert dec[1:] == pytest.approx(orig[1:], rel=1e-6)

    def test_negative_timestamp_and_extremes(self):
        s = (-1, 3.4e38, -3.4e38, 0.0, 1e-30, -1e-30, 0.0)
        payload = struct.pack(IMU_SAMPLE_FORMAT, *s)
        (dec,) = unpack_sensor_samples(SENSOR_IMU1, payload)
        assert dec[0] == -1
        assert dec[1] == pytest.approx(3.4e38, rel=1e-6)

    def test_empty_payload(self):
        assert list(unpack_sensor_samples(SENSOR_IMU1, b"")) == []

    def test_imu2_uses_same_format(self):
        payload = struct.pack(IMU_SAMPLE_FORMAT, 7, 1, 2, 3, 4, 5, 6)
        (dec,) = unpack_sensor_samples(SENSOR_IMU2, payload)
        assert dec == (7, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

    def test_sample_size(self):
        assert IMU_SAMPLE_SIZE == 8 + 6 * 4


class TestMagBaroSamples:
    def test_mag_roundtrip(self):
        payload = struct.pack(MAG_SAMPLE_FORMAT, 123456789, 2.5e-5, -1.1e-5, 4.8e-5)
        (dec,) = unpack_sensor_samples(SENSOR_MAG, payload)
        assert dec[0] == 123456789
        assert dec[1] == pytest.approx(2.5e-5, rel=1e-6)
        assert MAG_SAMPLE_SIZE == 8 + 3 * 4

    def test_baro_roundtrip(self):
        payload = struct.pack(BARO_SAMPLE_FORMAT, 42, 101325.0, 23.5)
        (dec,) = unpack_sensor_samples(SENSOR_BARO, payload)
        assert dec == pytest.approx((42, 101325.0, 23.5))
        assert BARO_SAMPLE_SIZE == 8 + 2 * 4

    def test_truncated_payload_yields_complete_samples_only(self):
        payload = struct.pack(MAG_SAMPLE_FORMAT, 1, 1.0, 2.0, 3.0)
        truncated = payload + payload[: MAG_SAMPLE_SIZE // 2]
        # struct.iter_unpack requires exact multiples — a torn message must raise,
        # the consumer catches and drops it
        with pytest.raises(struct.error):
            list(unpack_sensor_samples(SENSOR_MAG, truncated))


class TestFrameVsSensorRouting:
    def test_old_consumer_skips_sensor_messages(self):
        """A v2 consumer routes by magic — ARS1 must fail its ARI2 check."""
        header = pack_sensor_header(SENSOR_IMU1, 10)
        assert header[:4] != HEADER_MAGIC

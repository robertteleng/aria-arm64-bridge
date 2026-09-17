"""Unit tests for ego-motion estimation from IMU (pure, no ZMQ/observer).

Validates _estimate_motion_state, which feeds aria-guard's tracker so a static
obstacle ahead doesn't read as a collision while the user walks.

Usage: .venv/bin/python -m pytest tests/test_motion_state.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aria_arm64_bridge.observer import _estimate_motion_state  # noqa: E402


def _imu(mag_base, jitter, n=50):
    """n IMU samples (t, ax, ay, az, gx, gy, gz) with |accel| std == jitter."""
    out = []
    for i in range(n):
        ax = mag_base + (jitter if i % 2 else -jitter)
        out.append((i * 0.01, ax, 0.0, 0.0, 0.0, 0.0, 0.0))
    return out


def test_low_variance_is_stationary():
    # std ~0.05 < 0.3
    assert _estimate_motion_state(_imu(9.81, 0.05), "unknown") == "stationary"


def test_high_variance_is_walking():
    # std ~1.5 > 0.6
    assert _estimate_motion_state(_imu(9.81, 1.5), "unknown") == "walking"


def test_hysteresis_band_keeps_previous_state():
    # std ~0.45 sits in the [0.3, 0.6] hysteresis band → keep previous
    band = _imu(9.81, 0.45)
    assert _estimate_motion_state(band, "walking") == "walking"
    assert _estimate_motion_state(band, "stationary") == "stationary"


def test_too_few_samples_keeps_previous():
    assert _estimate_motion_state([(0, 9.81, 0, 0, 0, 0, 0)] * 5, "walking") == "walking"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

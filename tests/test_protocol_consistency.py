"""The receiver's copy of the wire constants must match protocol.py.

receiver.py deliberately hard-codes the protocol constants instead of importing
them: it also has to run as a plain script inside the FEX-Emu x86_64 rootfs,
where the package may not be installed, and a relative import would break that.
The cost of that decision is drift — two definitions of the same wire format,
and a mismatch would corrupt every frame silently rather than raising.

This test removes the cost. It reads receiver.py with ast instead of importing
it, because importing pulls in aria.sdk (x86-only, absent in CI and on any
machine without the SDK) and the module exits at import time when that fails.
"""
import ast
from pathlib import Path

import pytest

from aria_arm64_bridge import protocol

RECEIVER = Path(__file__).resolve().parents[1] / "src" / "aria_arm64_bridge" / "receiver.py"

# Name in receiver.py -> name in protocol.py. Same value required in both.
SHARED_CONSTANTS = {
    "HEADER_FORMAT": "HEADER_FORMAT",
    "HEADER_SIZE": "HEADER_SIZE",
    "HEADER_MAGIC": "HEADER_MAGIC",
    "DEFAULT_ZMQ_ENDPOINT": "DEFAULT_ZMQ_ENDPOINT",
    "CAM_RGB": "CAM_RGB",
    "CAM_EYE": "CAM_EYE",
    "CAM_SLAM1": "CAM_SLAM1",
    "CAM_SLAM2": "CAM_SLAM2",
    "SENSOR_HEADER_FORMAT": "SENSOR_HEADER_FORMAT",
    "SENSOR_MAGIC": "SENSOR_MAGIC",
    "SENSOR_IMU1": "SENSOR_IMU1",
    "SENSOR_IMU2": "SENSOR_IMU2",
    "SENSOR_MAG": "SENSOR_MAG",
    "SENSOR_BARO": "SENSOR_BARO",
    "IMU_SAMPLE_FORMAT": "IMU_SAMPLE_FORMAT",
    "MAG_SAMPLE_FORMAT": "MAG_SAMPLE_FORMAT",
    "BARO_SAMPLE_FORMAT": "BARO_SAMPLE_FORMAT",
}


def _module_level_constants(path):
    """Literal module-level assignments in *path*, without importing it."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                try:
                    found[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass  # computed value, not a literal — nothing to compare
    return found


@pytest.fixture(scope="module")
def receiver_constants():
    assert RECEIVER.exists(), f"receiver.py not found at {RECEIVER}"
    return _module_level_constants(RECEIVER)


@pytest.mark.parametrize("recv_name,proto_name", sorted(SHARED_CONSTANTS.items()))
def test_receiver_constant_matches_protocol(receiver_constants, recv_name, proto_name):
    assert recv_name in receiver_constants, (
        f"{recv_name} disappeared from receiver.py — if it was renamed, update "
        f"SHARED_CONSTANTS; if it now imports from protocol.py, drop it from the map"
    )
    assert receiver_constants[recv_name] == getattr(protocol, proto_name), (
        f"wire format drift: receiver.py {recv_name}="
        f"{receiver_constants[recv_name]!r} but protocol.py {proto_name}="
        f"{getattr(protocol, proto_name)!r}"
    )


def test_header_size_matches_the_format_string():
    """A 28-byte header is what both sides slice — catch a format/size mismatch."""
    import struct
    assert struct.calcsize(protocol.HEADER_FORMAT) == protocol.HEADER_SIZE == 28
    assert struct.calcsize(protocol.SENSOR_HEADER_FORMAT) == protocol.SENSOR_HEADER_SIZE == 12


def test_magics_are_distinct_and_four_bytes():
    """Routing on the socket depends on the two magics never colliding."""
    assert protocol.HEADER_MAGIC != protocol.SENSOR_MAGIC
    assert len(protocol.HEADER_MAGIC) == len(protocol.SENSOR_MAGIC) == 4

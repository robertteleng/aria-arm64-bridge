"""aria-arm64-bridge — Run Meta Aria glasses on ARM64 (Jetson) via FEX-Emu.

Quick start::

    from aria_arm64_bridge import AriaBridge

    with AriaBridge(interface="usb") as bridge:
        frame = bridge.get_frame("rgb")  # numpy BGR uint8

For lower-level access, use :class:`AriaBridgeObserver` directly
(requires running the receiver separately).
"""

from .bridge import AriaBridge
from .observer import AriaBridgeObserver, Frame
from .protocol import (
    DEFAULT_ZMQ_ENDPOINT,
    PROFILE_STREAMING,
    CAM_RGB, CAM_EYE, CAM_SLAM1, CAM_SLAM2, CAM_NAMES,
)

__version__ = "0.1.0"

__all__ = [
    "AriaBridge",
    "AriaBridgeObserver",
    "Frame",
    "DEFAULT_ZMQ_ENDPOINT",
    "PROFILE_STREAMING",
    "__version__",
]

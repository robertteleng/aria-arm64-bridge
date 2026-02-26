"""High-level API — connect to Aria glasses on ARM64 with one call.

``AriaBridge`` launches the FEX-Emu receiver as a subprocess and exposes
frames through a simple :meth:`get_frame` / :meth:`get_latest` interface.

Example::

    from aria_arm64_bridge import AriaBridge

    bridge = AriaBridge(interface="usb")
    bridge.start()

    while bridge.is_running:
        frame = bridge.get_frame("rgb")
        if frame is not None:
            print(frame.shape)

    bridge.stop()
"""

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional

import numpy as np

from .observer import AriaBridgeObserver, Frame
from .protocol import DEFAULT_ZMQ_ENDPOINT, PROFILE_STREAMING


class AriaBridge:
    """Stream frames from Meta Aria glasses on ARM64 via FEX-Emu.

    Parameters
    ----------
    interface : str
        ``"usb"`` or ``"wifi"``.
    device_ip : str or None
        Aria glasses IP address (required for wifi).
    profile : str
        Streaming profile.  ``"profile12"`` (default) is streaming-optimised
        and delivers ~11 FPS RGB at 1408x1408.
    zmq_endpoint : str
        ZMQ endpoint for the internal bridge.
    receiver_script : str or None
        Path to the receiver script.  ``None`` auto-detects from the
        installed package location.
    """

    def __init__(
        self,
        interface: str = "usb",
        device_ip: Optional[str] = None,
        profile: str = PROFILE_STREAMING,
        zmq_endpoint: str = DEFAULT_ZMQ_ENDPOINT,
        receiver_script: Optional[str] = None,
    ):
        if interface == "wifi" and not device_ip:
            raise ValueError("device_ip is required for wifi interface")

        self._interface = interface
        self._device_ip = device_ip
        self._profile = profile
        self._zmq_endpoint = zmq_endpoint
        self._receiver_script = receiver_script or self._find_receiver()

        self._process: Optional[subprocess.Popen] = None
        self._observer: Optional[AriaBridgeObserver] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, timeout: float = 15.0):
        """Launch the FEX-Emu receiver and start consuming frames.

        Blocks until the first frame arrives or *timeout* seconds elapse.

        Raises
        ------
        RuntimeError
            If FEXBash is not available or the receiver fails to start.
        """
        if self._process is not None:
            raise RuntimeError("Bridge already started")

        self._check_fex_emu()

        # Build receiver command
        cmd = f"python3 {self._receiver_script} --interface {self._interface}"
        cmd += f" --zmq-endpoint {self._zmq_endpoint}"
        cmd += f" --profile {self._profile}"
        if self._device_ip:
            cmd += f" --device-ip {self._device_ip}"

        env = os.environ.copy()
        env["PYTHONNOUSERSITE"] = "1"

        self._process = subprocess.Popen(
            ["FEXBash", "-c", cmd],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Start native observer
        self._observer = AriaBridgeObserver(zmq_endpoint=self._zmq_endpoint)

        # Wait for first frame
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._observer.get_frame("rgb") is not None:
                return
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"Receiver exited with code {self._process.returncode}"
                )
            time.sleep(0.2)

        print("[aria-bridge] Warning: no frames received within timeout, "
              "but receiver is still running")

    def stop(self):
        """Stop the receiver subprocess and observer thread."""
        if self._observer:
            self._observer.stop()
            self._observer = None

        if self._process:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def get_frame(self, camera: str = "rgb") -> Optional[np.ndarray]:
        """Latest frame as a BGR ``uint8`` numpy array, or ``None``."""
        if self._observer is None:
            return None
        return self._observer.get_frame(camera)

    def get_latest(self, camera: str = "rgb") -> Optional[Frame]:
        """Latest :class:`Frame` object, or ``None``."""
        if self._observer is None:
            return None
        return self._observer.get_latest(camera)

    def get_stats(self) -> Dict[str, Any]:
        """Runtime statistics (FPS per camera, uptime, endpoint)."""
        if self._observer is None:
            return {}
        stats = self._observer.get_stats()
        stats["receiver_pid"] = self._process.pid if self._process else None
        stats["interface"] = self._interface
        stats["profile"] = self._profile
        return stats

    @property
    def is_running(self) -> bool:
        """``True`` if the receiver process and observer thread are alive."""
        if self._process is None or self._observer is None:
            return False
        return self._process.poll() is None and self._observer.is_running

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _find_receiver() -> str:
        """Locate the receiver script bundled with the package."""
        pkg_dir = Path(__file__).parent
        receiver = pkg_dir / "receiver.py"
        if receiver.exists():
            return str(receiver)
        # Fallback: old layout
        project_root = pkg_dir.parent.parent
        legacy = project_root / "src" / "receiver" / "aria_receiver.py"
        if legacy.exists():
            return str(legacy)
        raise FileNotFoundError(
            "Cannot find receiver.py. Pass receiver_script= explicitly."
        )

    @staticmethod
    def _check_fex_emu():
        """Verify FEXBash is available."""
        import shutil
        if shutil.which("FEXBash") is None:
            raise RuntimeError(
                "FEXBash not found. Install FEX-Emu first:\n"
                "  See scripts/setup_fex_emu.sh or https://fex-emu.com"
            )

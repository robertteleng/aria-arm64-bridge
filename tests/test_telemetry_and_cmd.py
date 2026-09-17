"""Tests for the pieces that used to be unverifiable off a Jetson.

Telemetry's tegrastats handling and the receiver command string are both things
that only ever ran on device. Parsing and command building are pulled out as
pure functions precisely so they can be tested anywhere.
"""
import shlex

import pytest

from aria_arm64_bridge.bridge import AriaBridge
from aria_arm64_bridge.telemetry import _parse_tegrastats_line, _TegrastatsReader

# A real Orin Nano tegrastats line (JetPack 6, MAXN_SUPER).
TEGRASTATS_LINE = (
    "08-15-2026 14:02:11 RAM 3421/7620MB (lfb 12x4MB) SWAP 0/3810MB (cached 0MB) "
    "CPU [23%@1728,11%@1728,7%@1728,9%@1728,5%@1728,3%@1728] EMC_FREQ 8%@2133 "
    "GR3D_FREQ 18%@[624] NVENC off NVDEC off GPU@50.5C CPU@52.2C VDD_IN 7300mW"
)


class TestTegrastatsParsing:
    def test_parses_a_real_line(self):
        got = _parse_tegrastats_line(TEGRASTATS_LINE)
        assert got["ram_used_mb"] == 3421
        assert got["ram_total_mb"] == 7620
        assert got["gpu_util"] == 18
        # 23+11+7+9+5+3 = 58 over 6 cores
        assert got["cpu_avg"] == pytest.approx(9.7, abs=0.05)

    def test_cpu_average_ignores_the_emc_and_gr3d_percentages(self):
        """EMC_FREQ 8%@2133 and GR3D_FREQ 18%@[624] must not enter the CPU mean.

        They match a naive percent-at-frequency pattern, and counting them would
        quietly bias every CPU number in the CSV.
        """
        got = _parse_tegrastats_line(TEGRASTATS_LINE)
        naive_mean_including_emc = (23 + 11 + 7 + 9 + 5 + 3 + 8) / 7
        assert got["cpu_avg"] != pytest.approx(naive_mean_including_emc, abs=0.05)

    def test_garbage_line_yields_zeros_not_an_exception(self):
        """The reader thread must never die on one malformed line."""
        assert _parse_tegrastats_line("not a tegrastats line at all") == {
            "ram_used_mb": 0, "ram_total_mb": 0, "gpu_util": 0, "cpu_avg": 0.0,
        }

    def test_reader_reports_none_when_tegrastats_is_absent(self, monkeypatch):
        """None means "no reading" — distinct from 0, which means "0% GPU"."""
        monkeypatch.setattr("aria_arm64_bridge.telemetry.shutil.which", lambda _: None)
        reader = _TegrastatsReader()
        assert reader.latest() is None
        reader.stop()  # must be safe with no process

    def test_reader_does_not_spawn_a_process_per_sample(self, monkeypatch):
        """The old code ran tegrastats once per second, polluting its own CPU
        measurement. One long-lived process, or this regresses."""
        calls = []
        monkeypatch.setattr("aria_arm64_bridge.telemetry.shutil.which", lambda _: "/usr/bin/tegrastats")

        class _FakeProc:
            stdout = iter([TEGRASTATS_LINE + "\n"])

            def poll(self):
                return 0

        def _fake_popen(*args, **kwargs):
            calls.append(args[0])
            return _FakeProc()

        monkeypatch.setattr("aria_arm64_bridge.telemetry.subprocess.Popen", _fake_popen)
        reader = _TegrastatsReader(interval_ms=1000)
        for _ in range(5):
            reader.latest()
        assert len(calls) == 1, f"spawned {len(calls)} tegrastats processes, expected 1"
        assert "--interval" in calls[0]


class TestReceiverCommand:
    def _bridge(self, **kw):
        kw.setdefault("receiver_script", "/opt/pkg/receiver.py")
        return AriaBridge(**kw)

    def test_command_round_trips_through_the_shell_parser(self):
        cmd = self._bridge()._build_receiver_cmd()
        argv = shlex.split(cmd)
        assert argv[0] == "/usr/bin/python3"
        assert "/opt/pkg/receiver.py" in argv
        assert argv[argv.index("--interface") + 1] == "usb"

    def test_hostile_device_ip_stays_one_argument(self):
        """An unquoted value here would be executed by the shell FEXBash spawns."""
        evil = "1.2.3.4; touch /tmp/pwned"
        cmd = self._bridge(interface="wifi", device_ip=evil)._build_receiver_cmd()
        argv = shlex.split(cmd)
        assert argv[argv.index("--device-ip") + 1] == evil
        assert "touch" not in [a for a in argv if a != evil]

    def test_profile_with_spaces_stays_one_argument(self):
        cmd = self._bridge(profile="profile12 --streams rgb")._build_receiver_cmd()
        argv = shlex.split(cmd)
        assert argv[argv.index("--profile") + 1] == "profile12 --streams rgb"

    def test_wifi_without_ip_is_rejected_early(self):
        with pytest.raises(ValueError, match="device_ip"):
            AriaBridge(interface="wifi", receiver_script="/opt/pkg/receiver.py")

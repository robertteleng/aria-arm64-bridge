"""Pipeline telemetry — CPU, RAM, GPU and FPS logger.

Runs as a daemon thread. Writes a CSV to logs/ every second.
Zero impact on the frame pipeline — completely independent thread.

Usage::

    from aria_arm64_bridge.telemetry import Telemetry

    t = Telemetry()        # starts immediately, auto-detects log dir
    t.record_fps(12.3)     # call from observer on each stats tick
    t.stop()               # flush and close CSV
"""

import csv
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path


def _find_log_dir() -> Path:
    # Write next to the repo root if possible, otherwise /tmp
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            log_dir = parent / "logs"
            log_dir.mkdir(exist_ok=True)
            return log_dir
    return Path("/tmp")


def _parse_tegrastats_line(line: str) -> dict:
    """Parse one tegrastats line. Pure, so it is unit-testable off a Jetson."""
    ram_m = re.search(r"RAM (\d+)/(\d+)MB", line)
    gpu_m = re.search(r"GR3D_FREQ (\d+)%", line)
    # Only the per-core percentages inside the CPU [...] block. A bare
    # r"(\d+)%@\d+" over the whole line also matches "EMC_FREQ 8%@2133", which
    # silently dragged the memory-controller load into the CPU average.
    cpu_block = re.search(r"CPU \[([^\]]*)\]", line)
    cpu_m = re.findall(r"(\d+)%@\d+", cpu_block.group(1)) if cpu_block else []
    return {
        "ram_used_mb": int(ram_m.group(1)) if ram_m else 0,
        "ram_total_mb": int(ram_m.group(2)) if ram_m else 0,
        "gpu_util": int(gpu_m.group(1)) if gpu_m else 0,
        "cpu_avg": round(sum(int(x) for x in cpu_m) / len(cpu_m), 1) if cpu_m else 0.0,
    }


class _TegrastatsReader:
    """One long-lived tegrastats process, read by a daemon thread.

    The previous version spawned a fresh ``tegrastats`` **every second** with a
    1.2 s timeout and read its first line. That is self-defeating for a tool whose
    job is measuring CPU: process spawn + teardown once per sample lands inside
    the very measurement, and the killed processes can linger. tegrastats already
    emits one line per interval forever, so it is started once and tailed.

    ``latest()`` returns ``None`` when tegrastats is unavailable (any non-Jetson
    machine), which is what lets the caller tell "no GPU data" from "0% GPU".
    """

    def __init__(self, interval_ms: int = 1000):
        self._latest: dict | None = None
        self._lock = threading.Lock()
        self._proc = None
        if shutil.which("tegrastats") is None:
            return  # not a Jetson — stays disabled, latest() keeps returning None
        try:
            self._proc = subprocess.Popen(
                ["tegrastats", "--interval", str(interval_ms)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
        except Exception:
            self._proc = None
            return
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        try:
            for line in self._proc.stdout:
                parsed = _parse_tegrastats_line(line)
                with self._lock:
                    self._latest = parsed
        except Exception:
            pass  # process died or was terminated — latest() just goes stale

    def latest(self) -> dict | None:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def _psutil_snapshot(pid_fex: int | None, pid_obs: int | None) -> dict:
    try:
        import psutil
        fex_cpu, fex_mem = 0.0, 0
        obs_cpu, obs_mem = 0.0, 0
        total_cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        if pid_fex:
            try:
                p = psutil.Process(pid_fex)
                fex_cpu = p.cpu_percent(interval=None)
                fex_mem = p.memory_info().rss // (1024 * 1024)
            except psutil.NoSuchProcess:
                pass
        if pid_obs:
            try:
                p = psutil.Process(pid_obs)
                obs_cpu = p.cpu_percent(interval=None)
                obs_mem = p.memory_info().rss // (1024 * 1024)
            except psutil.NoSuchProcess:
                pass
        return {
            "fex_cpu": fex_cpu, "fex_mem_mb": fex_mem,
            "obs_cpu": obs_cpu, "obs_mem_mb": obs_mem,
            "total_cpu": total_cpu,
            "ram_used_mb": ram.used // (1024 * 1024),
            "ram_free_mb": ram.available // (1024 * 1024),
        }
    except ImportError:
        return {"fex_cpu": 0, "fex_mem_mb": 0, "obs_cpu": 0, "obs_mem_mb": 0,
                "total_cpu": 0, "ram_used_mb": 0, "ram_free_mb": 0}


FIELDS = [
    "timestamp", "elapsed_s",
    "fps_rgb",
    "fex_cpu", "fex_mem_mb",
    "obs_cpu", "obs_mem_mb",
    "total_cpu",
    "ram_used_mb", "ram_free_mb",
    "gpu_util", "soc_cpu_avg",
]


class Telemetry:
    """Daemon thread that writes one CSV row per second to logs/."""

    def __init__(self, interval: float = 1.0, pid_fex: int | None = None):
        self._interval = interval
        self._pid_fex = pid_fex
        self._pid_obs = os.getpid()
        self._stop = threading.Event()
        self._fps_rgb: float = 0.0
        self._lock = threading.Lock()
        self._tegra = _TegrastatsReader(interval_ms=int(interval * 1000))

        log_dir = _find_log_dir()
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._path = log_dir / f"telemetry_{ts}.csv"
        self._file = open(self._path, "w", newline="", buffering=1)
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
        self._writer.writeheader()
        self._start = time.monotonic()

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[telemetry] Logging to {self._path}")

    def record_fps(self, fps: float) -> None:
        """Call from observer stats tick to record current FPS."""
        with self._lock:
            self._fps_rgb = fps

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        self._tegra.stop()
        self._file.close()
        print(f"[telemetry] Closed {self._path}")

    def _loop(self) -> None:
        # Prime psutil cpu_percent (first call always returns 0)
        try:
            import psutil
            psutil.cpu_percent(interval=None)
            if self._pid_fex:
                psutil.Process(self._pid_fex).cpu_percent(interval=None)
            psutil.Process(self._pid_obs).cpu_percent(interval=None)
        except Exception:
            pass

        while not self._stop.wait(self._interval):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            elapsed = round(time.monotonic() - self._start, 1)

            proc = _psutil_snapshot(self._pid_fex, self._pid_obs)
            teg = self._tegra.latest() or {}

            with self._lock:
                fps = self._fps_rgb

            row = {
                "timestamp": ts,
                "elapsed_s": elapsed,
                "fps_rgb": round(fps, 2),
                "fex_cpu": proc["fex_cpu"],
                "fex_mem_mb": proc["fex_mem_mb"],
                "obs_cpu": proc["obs_cpu"],
                "obs_mem_mb": proc["obs_mem_mb"],
                "total_cpu": proc["total_cpu"],
                "ram_used_mb": teg.get("ram_used_mb") or proc["ram_used_mb"],
                "ram_free_mb": proc["ram_free_mb"],
                # Empty, not 0, when tegrastats is absent: 0% GPU and "no GPU
                # reading" are different facts and a CSV that conflates them lies.
                "gpu_util": teg.get("gpu_util", ""),
                # tegrastats' own CPU average across cores. There is no separate
                # GPU RAM figure to report — the Orin has unified memory, which is
                # why the old gpu_ram_used_mb column was just ram_used_mb again.
                "soc_cpu_avg": teg.get("cpu_avg", ""),
            }
            self._writer.writerow(row)

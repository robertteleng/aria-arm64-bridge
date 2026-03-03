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


def _tegrastats_snapshot() -> dict:
    """Read one line from tegrastats. Returns zeros if unavailable."""
    try:
        line = subprocess.check_output(
            ["tegrastats", "--interval", "1000"],
            timeout=1.2,
            stderr=subprocess.DEVNULL,
        ).decode().split("\n")[0]
        # RAM X/YMB
        import re
        ram_m = re.search(r"RAM (\d+)/(\d+)MB", line)
        ram_used = int(ram_m.group(1)) if ram_m else 0
        ram_total = int(ram_m.group(2)) if ram_m else 0
        # GR3D_FREQ X%
        gpu_m = re.search(r"GR3D_FREQ (\d+)%", line)
        gpu_util = int(gpu_m.group(1)) if gpu_m else 0
        # CPU [X%@freq, ...]
        cpu_m = re.findall(r"(\d+)%@\d+", line)
        cpu_avg = sum(int(x) for x in cpu_m) / len(cpu_m) if cpu_m else 0
        return {"ram_used_mb": ram_used, "ram_total_mb": ram_total,
                "gpu_util": gpu_util, "cpu_avg": round(cpu_avg, 1)}
    except Exception:
        return {"ram_used_mb": 0, "ram_total_mb": 0, "gpu_util": 0, "cpu_avg": 0}


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
    "gpu_util", "gpu_ram_used_mb",
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

        use_tegrastats = _tegrastats_snapshot()["gpu_util"] is not None

        while not self._stop.wait(self._interval):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            elapsed = round(time.monotonic() - self._start, 1)

            proc = _psutil_snapshot(self._pid_fex, self._pid_obs)
            teg = _tegrastats_snapshot() if use_tegrastats else {}

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
                "gpu_util": teg.get("gpu_util", 0),
                "gpu_ram_used_mb": teg.get("ram_used_mb", 0),
            }
            self._writer.writerow(row)

"""Native sensor dashboard for the Aria ARM64 Bridge.

Runs natively on the Jetson (no Docker, no CUDA) and visualizes everything
the FEX-Emu receiver pushes over ZMQ: RGB + SLAM cameras, IMU, magnetometer
and barometer.

    .venv/bin/python3 -m src.dashboard.server [--port 5001]

NOTE: ZMQ PUSH/PULL load-balances between consumers — do NOT run this at the
same time as the aria-guard Docker pipeline or they will steal frames from
each other. This dashboard is for sensor inspection sessions.

Lessons from 2026-06-11 baked in: MJPEG streams are rate-limited and only
encode NEW frames (an unthrottled encode loop starved the pipeline via GIL
contention), and JPEG encoding happens outside the frame lock.
"""

import argparse
import collections
import json
import math
import struct
import threading
import time
from io import BytesIO

import numpy as np
import zmq
from flask import Flask, Response, render_template
from PIL import Image

from ..protocol import (
    HEADER_FORMAT, HEADER_SIZE, HEADER_MAGIC, CAM_NAMES,
    SENSOR_HEADER_SIZE, SENSOR_MAGIC, SENSOR_NAMES,
    SENSOR_IMU1, SENSOR_IMU2, SENSOR_MAG, SENSOR_BARO,
    unpack_sensor_header, unpack_sensor_samples,
    DEFAULT_ZMQ_ENDPOINT,
)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_frames = {}          # cam_name -> np.ndarray (display-ready, rotated)
_frame_versions = collections.defaultdict(int)
_counts = collections.defaultdict(int)       # cam/sensor -> total received
_start = time.monotonic()

# Sensor ring buffers (downsampled for charting)
IMU_BUFFER = 600      # ~6s at 100 Hz downsample
_imu1 = collections.deque(maxlen=IMU_BUFFER)   # (t_rel, ax, ay, az, gx, gy, gz)
_imu2 = collections.deque(maxlen=IMU_BUFFER)
_mag = collections.deque(maxlen=120)           # (t_rel, mx, my, mz)
_baro = collections.deque(maxlen=300)          # (t_rel, pressure, temp)
_imu_decim = {SENSOR_IMU1: 0, SENSOR_IMU2: 0}
IMU_DECIMATE = 10     # keep 1 in 10 samples (1 kHz -> 100 Hz for charts)


def _receive_loop(endpoint: str):
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PULL)
    sock.setsockopt(zmq.RCVHWM, 20)
    sock.connect(endpoint)
    poller = zmq.Poller()
    poller.register(sock, zmq.POLLIN)
    print(f"[dashboard] ZMQ connected to {endpoint}")

    while True:
        if not dict(poller.poll(timeout=1000)):
            continue
        parts = sock.recv_multipart(copy=False)
        if len(parts) != 2:
            continue
        head = bytes(parts[0])

        if head[:4] == HEADER_MAGIC and len(head) >= HEADER_SIZE:
            _handle_frame(head, parts[1])
        elif head[:4] == SENSOR_MAGIC and len(head) >= SENSOR_HEADER_SIZE:
            _handle_sensor(head, parts[1])


def _handle_frame(head, pixel_buf):
    _, cam_id, _ts, width, height, channels = struct.unpack(HEADER_FORMAT, head[:HEADER_SIZE])
    cam = CAM_NAMES.get(cam_id)
    if cam is None:
        return
    expected = width * height * channels
    if len(pixel_buf) != expected:
        return
    shape = (height, width, channels) if channels > 1 else (height, width)
    raw = np.frombuffer(pixel_buf, dtype=np.uint8).reshape(shape)
    # Display orientation: same transform the bridge observer applies
    if cam == "eye":
        img = np.ascontiguousarray(np.rot90(raw, 2))
    else:
        img = np.ascontiguousarray(np.rot90(raw, k=-1))
    with _lock:
        _frames[cam] = img
        _frame_versions[cam] += 1
        _counts[cam] += 1


def _handle_sensor(head, payload):
    _, sensor_id, n = unpack_sensor_header(bytes(head))
    name = SENSOR_NAMES.get(sensor_id)
    if name is None:
        return
    now = time.monotonic() - _start
    try:
        samples = list(unpack_sensor_samples(sensor_id, bytes(payload)))
    except struct.error:
        # Torn/malformed batch — drop it, never kill the receive thread
        with _lock:
            _counts[f"{name}_dropped"] += 1
        return
    with _lock:
        _counts[name] += len(samples)
    if sensor_id in (SENSOR_IMU1, SENSOR_IMU2):
        buf = _imu1 if sensor_id == SENSOR_IMU1 else _imu2
        for s in samples:
            _imu_decim[sensor_id] += 1
            if _imu_decim[sensor_id] % IMU_DECIMATE == 0:
                buf.append((now, *s[1:]))
    elif sensor_id == SENSOR_MAG:
        for s in samples:
            _mag.append((now, *s[1:]))
    elif sensor_id == SENSOR_BARO:
        for s in samples:
            _baro.append((now, *s[1:]))


# ---------------------------------------------------------------------------
# MJPEG feeds (rate-limited, only-new-frames)
# ---------------------------------------------------------------------------

def _mjpeg(cam: str, max_fps: float = 12.0, max_height: int = 720):
    interval = 1.0 / max_fps
    last_version = -1
    while True:
        t0 = time.time()
        with _lock:
            v = _frame_versions.get(cam, 0)
            frame = _frames.get(cam) if v != last_version else None
            if frame is not None:
                last_version = v
                frame = frame.copy()
        if frame is None:
            time.sleep(0.05)
            continue

        h = frame.shape[0]
        img = Image.fromarray(frame)
        if h > max_height:
            w = int(frame.shape[1] * max_height / h)
            img = img.resize((w, max_height))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=75)
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.getvalue() + b"\r\n")

        remaining = interval - (time.time() - t0)
        if remaining > 0:
            time.sleep(remaining)


@app.route("/feed/<cam>")
def feed(cam):
    if cam not in ("rgb", "slam1", "slam2", "eye"):
        return "unknown camera", 404
    return Response(_mjpeg(cam), mimetype="multipart/x-mixed-replace; boundary=frame")


# ---------------------------------------------------------------------------
# SSE sensor stream
# ---------------------------------------------------------------------------

def _heading_deg():
    """Compass heading from the latest magnetometer sample (XY plane)."""
    if not _mag:
        return None
    _, mx, my, _mz = _mag[-1]
    return (math.degrees(math.atan2(my, mx)) + 360.0) % 360.0


@app.route("/events")
def events():
    def stream():
        while True:
            with _lock:
                elapsed = time.monotonic() - _start
                counts = dict(_counts)
                imu1 = list(_imu1)[-200:]
                mag = list(_mag)[-1:]
                baro = list(_baro)[-1:]
            rates = {k: round(v / elapsed, 1) for k, v in counts.items() if elapsed > 0}
            payload = {
                "uptime": round(elapsed),
                "rates": rates,
                "counts": counts,
                "imu1": [[round(p, 3) for p in s] for s in imu1],
                "mag": mag[0][1:] if mag else None,
                "heading": _heading_deg(),
                "baro": baro[0][1:] if baro else None,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(0.2)
    return Response(stream(), mimetype="text/event-stream")


@app.route("/")
def index():
    return render_template("index.html")


def main():
    parser = argparse.ArgumentParser(description="Aria bridge sensor dashboard (native)")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--zmq-endpoint", default=DEFAULT_ZMQ_ENDPOINT)
    args = parser.parse_args()

    threading.Thread(target=_receive_loop, args=(args.zmq_endpoint,), daemon=True).start()
    print(f"[dashboard] http://0.0.0.0:{args.port}")
    app.run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()

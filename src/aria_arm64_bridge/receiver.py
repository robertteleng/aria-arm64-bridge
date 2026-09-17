"""Aria SDK frame receiver — runs under FEX-Emu (x86_64 emulated).

Connects to Aria glasses via the SDK, receives frames, and pushes
them over ZMQ to the native ARM64 consumer.

Usage:
    PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 -m aria_arm64_bridge.receiver \
        --interface usb --streams rgb,slam,eye,imu,mag,baro"
    PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 -m aria_arm64_bridge.receiver \
        --interface wifi --device-ip 192.168.1.42"

Protocol (v2 + v2.1):
    Frames:  magic(4) + camera_id(1) + pad(3) + timestamp_ns(8) + width(4) + height(4) + channels(4)
             28-byte header, followed by raw pixel data (uint8). Cameras: 0=rgb, 1=eye, 2=slam1, 3=slam2.
    Sensors: magic(4) + sensor_id(1) + pad(3) + sample_count(4) = 12 bytes, then packed samples.

Why the constants below are duplicated from ``protocol.py`` instead of imported:
this module also has to run as a plain script inside the FEX-Emu x86_64 rootfs,
where the package may not be installed (``python3 <path>/receiver.py``). A relative
import would break that. ``tests/test_protocol_consistency.py`` fails the build if
the two copies ever drift.
"""

import argparse
import queue
import signal
import struct
import sys
import threading
import time

import zmq

# These imports only work under FEX-Emu (x86_64)
try:
    import aria.sdk as aria
except ImportError:
    print("ERROR: aria.sdk not found. Run this under FEX-Emu.", file=sys.stderr)
    print("  PYTHONNOUSERSITE=1 FEXBash -c \"/usr/bin/python3 -m aria_arm64_bridge.receiver\"",
          file=sys.stderr)
    sys.exit(1)

DEFAULT_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"
# profile12 = streaming-optimized, no audio (11 FPS RGB under FEX-Emu)
# profile18 = streaming-optimized, has audio (9 FPS RGB, audio crashes observer)
# profile28 = USB default but NOT streaming-optimized (<2 FPS under FEX-Emu)
PROFILE_WIFI = "profile12"
PROFILE_USB = "profile12"

# Header format: magic(4s) + camera_id(B) + pad(3x) + timestamp(Q) + w(I) + h(I) + ch(I)
HEADER_FORMAT = "<4sB3xQIII"
HEADER_SIZE = 28
HEADER_MAGIC = b"ARI2"

# Camera ID mapping
CAM_RGB = 0
CAM_EYE = 1
CAM_SLAM1 = 2
CAM_SLAM2 = 3

# Sensor messages (protocol v2.1) — see src/aria_arm64_bridge/protocol.py
SENSOR_HEADER_FORMAT = "<4sB3xI"
SENSOR_MAGIC = b"ARS1"
SENSOR_IMU1 = 0
SENSOR_IMU2 = 1
SENSOR_MAG = 2
SENSOR_BARO = 3
IMU_SAMPLE_FORMAT = "<q6f"
MAG_SAMPLE_FORMAT = "<q3f"
BARO_SAMPLE_FORMAT = "<q2f"


class AriaFrameObserver:
    """Receives frames + sensor data from Aria SDK and pushes them over ZMQ.

    Uses plain class (no BaseStreamingClientObserver inheritance) — matches
    the observer pattern validated in Phase 2 streaming tests.
    """

    def __init__(self, zmq_socket, bench_drop=False, send_queue_size=64):
        self._socket = zmq_socket
        # bench_drop: O(1) callback that only counts rx and returns — no lock,
        # no ZMQ send, no struct.pack. Isolates whether OUR callback (shared
        # send-lock + 6MB RGB send blocking SLAM/sensor threads) is what makes
        # the SDK queue and drop. If SLAM holds here but collapses in normal
        # mode, the bottleneck is our tx path, not FastDDS/FEX/publisher.
        self._bench_drop = bench_drop
        # ROOT CAUSE FIX (Exp 010, 2026-06-17): the SDK delivers each data type
        # from its OWN thread. The previous design had every thread share one
        # _send_lock and do the ZMQ send_multipart (a 6 MB RGB frame) inside the
        # callback. While the RGB thread held the lock, SLAM/sensor threads
        # blocked → returned late to the SDK → FastDDS dropped their samples
        # (SLAM collapsed 10→0.8 FPS). bench-drop proved SLAM holds 10 FPS with
        # the send removed. Fix: callbacks only COPY their payload and enqueue;
        # a single dedicated thread drains the queue and does all ZMQ sends, so
        # the socket stays single-threaded (no lock) and no SDK thread ever
        # waits on another's send.
        self._send_queue = queue.Queue(maxsize=send_queue_size)
        self._send_thread = threading.Thread(
            target=self._send_loop, name="zmq-sender", daemon=True)
        self._stop_sender = threading.Event()
        # enqueued = callback put it on the send queue; we report rx (SDK
        # delivered) vs enqueued so the window line still shows whether the
        # queue is dropping under the callback. The sender thread tracks real
        # ZMQ sends separately (HWM drops) but we don't attribute those per-cam.
        self._frame_counts = {"rgb": 0, "eye": 0, "slam1": 0, "slam2": 0}
        self._sensor_counts = {"imu1": 0, "imu2": 0, "mag": 0, "baro": 0}
        self._sent_total = 0  # messages actually pushed over ZMQ by sender thread
        # recv = delivered by SDK callback; enqueued = handed to sender thread.
        # The gap is backpressure: queue full (callback drops at put_nowait).
        # Report both or FPS debugging lies (Exp 010 lesson: enqueued != sent).
        self._recv_counts = {"rgb": 0, "eye": 0, "slam1": 0, "slam2": 0}
        self._win_recv = dict(self._recv_counts)
        self._win_sent = dict(self._frame_counts)
        self._win_start = time.monotonic()
        self._start_time = time.monotonic()
        self._first_frame = True
        self._dropped_enqueue = 0  # frames/sensor batches dropped at the queue
        # INSTRUMENTATION (FEX hypothesis test): per-stream callback duration in ms
        # — from callback entry to right after enqueue. Measures whether the
        # callback returns SLOW to the SDK under FEX (which would make the glasses'
        # DDS publisher throttle the other streams). list.append is atomic under
        # the GIL; we read+clear inside _report_lock each window. p50/p99 reported.
        self._cb_ms = {"rgb": [], "eye": [], "slam1": [], "slam2": []}
        # The 10s window report runs inside _send_frame, which executes on EVERY
        # camera thread (rgb/slam1/slam2/eye). Without this lock two threads can
        # enter the report block at once → duplicated/interleaved log lines and a
        # double window reset (seen in Exp 010 output). Guards ONLY the report,
        # never the send path, so it can't reintroduce cross-thread blocking.
        self._report_lock = threading.Lock()

    def start(self):
        """Start the dedicated ZMQ sender thread. Call before subscribing."""
        if not self._bench_drop:
            self._send_thread.start()

    def stop(self):
        """Stop the sender thread. Pending queued frames are DROPPED, not drained
        — _send_loop exits as soon as _stop_sender is set. Acceptable for live
        streaming (a few trailing frames don't matter). Call after unsubscribe.

        If the join times out the thread is still alive and the caller is about
        to close the socket it owns (use-after-close risk). Sends are NOBLOCK so
        this realistically never happens, but warn loudly if it ever does."""
        if self._bench_drop:
            return
        self._stop_sender.set()
        if self._send_thread.is_alive():
            self._send_thread.join(timeout=2.0)
            if self._send_thread.is_alive():
                print("[receiver] WARNING: zmq-sender thread did not stop within "
                      "2s; socket close may race with an in-flight send", flush=True)

    def _send_loop(self):
        """Single thread: owns the ZMQ socket, drains the queue, sends.

        Because only this thread touches the socket, no lock is needed — which
        is exactly what frees the SDK callback threads from blocking each other.
        """
        while not self._stop_sender.is_set():
            try:
                parts = self._send_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._socket.send_multipart(parts, zmq.NOBLOCK, copy=False)
                self._sent_total += 1
            except zmq.Again:
                pass  # consumer too slow (HWM full), drop this message
            except Exception as e:
                self._log_sensor_error_once("zmq-send", e)

    def _send_sensor(self, sensor_id, sensor_name, payload, n_samples):
        if self._bench_drop:
            self._sensor_counts[sensor_name] += n_samples
            return
        header = struct.pack(SENSOR_HEADER_FORMAT, SENSOR_MAGIC, sensor_id, n_samples)
        # payload here is an immutable bytes built from struct.pack in the
        # caller, so it is safe to hand to the sender thread without copying.
        try:
            self._send_queue.put_nowait([header, payload])
            self._sensor_counts[sensor_name] += n_samples
        except queue.Full:
            self._dropped_enqueue += 1  # sender behind, drop batch (non-blocking)

    _sensor_errors_logged = set()

    def _log_sensor_error_once(self, name, e):
        """Daemon threads die silently — always surface the first failure."""
        key = f"{name}:{type(e).__name__}"
        if key not in self._sensor_errors_logged:
            self._sensor_errors_logged.add(key)
            print(f"[receiver] SENSOR ERROR ({name}): {type(e).__name__}: {e}", flush=True)

    def on_imu_received(self, samples, imu_idx):
        """IMU batch — imu_idx 0 = IMU1 (1 kHz), 1 = IMU2 (800 Hz)."""
        try:
            if not samples:
                return
            sensor_id = SENSOR_IMU1 if imu_idx == 0 else SENSOR_IMU2
            name = "imu1" if imu_idx == 0 else "imu2"
            buf = bytearray()
            count = 0
            for s in samples:
                a, g = s.accel_msec2, s.gyro_radsec
                buf += struct.pack(IMU_SAMPLE_FORMAT, int(s.capture_timestamp_ns),
                                   float(a[0]), float(a[1]), float(a[2]),
                                   float(g[0]), float(g[1]), float(g[2]))
                count += 1
            if count:
                self._send_sensor(sensor_id, name, bytes(buf), count)
        except Exception as e:
            self._log_sensor_error_once("imu", e)

    def on_magneto_received(self, sample):
        try:
            m = sample.mag_tesla
            payload = struct.pack(MAG_SAMPLE_FORMAT, int(sample.capture_timestamp_ns),
                                  float(m[0]), float(m[1]), float(m[2]))
            self._send_sensor(SENSOR_MAG, "mag", payload, 1)
        except Exception as e:
            self._log_sensor_error_once("mag", e)

    def on_baro_received(self, sample):
        try:
            payload = struct.pack(BARO_SAMPLE_FORMAT, int(sample.capture_timestamp_ns),
                                  float(sample.pressure),
                                  float(getattr(sample, "temperature", 0.0)))
            self._send_sensor(SENSOR_BARO, "baro", payload, 1)
        except Exception as e:
            self._log_sensor_error_once("baro", e)

    def on_streaming_client_failure(self, reason, message):
        print(f"[receiver] STREAMING FAILURE: {reason} {message}", flush=True)

    def _send_frame(self, cam_id, cam_name, image, timestamp_ns):
        height, width = image.shape[:2]
        channels = image.shape[2] if len(image.shape) == 3 else 1

        if self._first_frame:
            self._first_frame = False
            print(f"[receiver] First frame! cam={cam_name} shape={image.shape} "
                  f"size={len(image.tobytes())} bytes")

        self._recv_counts[cam_name] += 1
        # t0 AFTER the first-frame log (which does an extra tobytes() we must not
        # measure). Times exactly the real callback work: pack + copy + enqueue.
        t0 = time.perf_counter()
        if self._bench_drop:
            # O(1) path: count rx, skip pack + copy + enqueue entirely, return fast.
            pass
        else:
            header = struct.pack(HEADER_FORMAT, HEADER_MAGIC, cam_id, timestamp_ns,
                                 width, height, channels)
            # The SDK owns `image` and may recycle its buffer once this callback
            # returns. We copy the pixels to bytes here (a few ms in unified RAM)
            # so the sender thread can serialize a stable buffer later. This
            # copy is what bench-drop proved is NOT the bottleneck — the old
            # blocking send-under-shared-lock was. tobytes() is C-contiguous.
            payload = image.tobytes()
            try:
                self._send_queue.put_nowait([header, payload])
                self._frame_counts[cam_name] += 1
            except queue.Full:
                self._dropped_enqueue += 1  # sender behind, drop frame (non-blocking)
        # Record callback duration (ms). This is the FEX-hypothesis probe: if the
        # callback returns slow to the SDK, the glasses throttle the other streams.
        self._cb_ms[cam_name].append((time.perf_counter() - t0) * 1000.0)
        now = time.monotonic()
        if now - self._win_start < 10.0:
            return
        # Only one camera thread should emit/reset the window. Double-check the
        # elapsed time after acquiring the lock so the losers return immediately
        # instead of printing a duplicate line with the window already reset.
        with self._report_lock:
            win = now - self._win_start
            if win < 10.0:
                return
            parts = []
            for k in self._recv_counts:
                r = (self._recv_counts[k] - self._win_recv[k]) / win
                s = (self._frame_counts[k] - self._win_sent[k]) / win
                if r > 0 or s > 0:
                    parts.append(f"{k}={r:.1f}rx/{s:.1f}enq")
            qd = f" qdrop={self._dropped_enqueue}" if self._dropped_enqueue else ""
            print(f"[receiver] {' '.join(parts)} fps (win {win:.0f}s, "
                  f"total rx={sum(self._recv_counts.values())}, "
                  f"sent={self._sent_total}{qd})")
            # Callback duration p50/p99 per stream (ms) — the FEX-hypothesis probe.
            # Snapshot and clear the buffers; a few samples racing in after this
            # read just land in the next window (telemetry, not exact).
            cb_parts = []
            for k, samples in self._cb_ms.items():
                if not samples:
                    continue
                self._cb_ms[k] = []
                vals = sorted(samples)
                p50 = vals[len(vals) // 2]
                p99 = vals[min(len(vals) - 1, int(len(vals) * 0.99))]
                cb_parts.append(f"{k}={p50:.1f}/{p99:.1f}ms(n{len(vals)})")
            if cb_parts:
                print(f"[receiver] callback p50/p99: {' '.join(cb_parts)}")
            self._win_recv = dict(self._recv_counts)
            self._win_sent = dict(self._frame_counts)
            self._win_start = now

    _unknown_cams_logged = set()

    def _map_camera(self, record):
        """Map SDK camera_id to our (cam_id, name). Enum compare first; the
        SDK 2.4 str() format changed and broke substring matching."""
        cid = record.camera_id
        enum_map = {
            getattr(aria.CameraId, "Rgb", None): (CAM_RGB, "rgb"),
            getattr(aria.CameraId, "Slam1", None): (CAM_SLAM1, "slam1"),
            getattr(aria.CameraId, "Slam2", None): (CAM_SLAM2, "slam2"),
            getattr(aria.CameraId, "EyeTrack", None): (CAM_EYE, "eye"),
        }
        mapped = enum_map.get(cid)
        if mapped:
            return mapped
        # Fallback: substring on the string form, then log unknowns once
        s = str(cid).lower()
        for needle, result in (("slam1", (CAM_SLAM1, "slam1")), ("slam-left", (CAM_SLAM1, "slam1")),
                               ("slam2", (CAM_SLAM2, "slam2")), ("slam-right", (CAM_SLAM2, "slam2")),
                               ("eye", (CAM_EYE, "eye")), ("rgb", (CAM_RGB, "rgb"))):
            if needle in s:
                return result
        if s not in self._unknown_cams_logged:
            self._unknown_cams_logged.add(s)
            print(f"[receiver] WARNING: unknown camera_id {cid!r} (str={s!r}), defaulting to rgb")
        return CAM_RGB, "rgb"

    def on_image_received(self, image, record):
        timestamp_ns = getattr(record, "capture_timestamp_ns", int(time.time() * 1e9))
        cam_id, cam_name = self._map_camera(record)
        self._send_frame(cam_id, cam_name, image, timestamp_ns)


def run(interface, device_ip, zmq_endpoint, profile, streams=("rgb",), bench_drop=False):
    ctx = zmq.Context()
    socket = ctx.socket(zmq.PUSH)
    # 64: multi-stream mixes ~120 msgs/s (rgb + slam pairs + IMU batches);
    # HWM 5 dropped SLAM bursts whenever the consumer hiccupped (slam at 1 fps
    # in Docker while rgb kept 7+). Worst case memory ≈ a few RGB frames.
    socket.setsockopt(zmq.SNDHWM, 64)
    socket.bind(zmq_endpoint)
    print(f"[receiver] ZMQ bound to {zmq_endpoint}")

    device_client = aria.DeviceClient()
    client_config = aria.DeviceClientConfig()
    if interface == "wifi" and device_ip:
        client_config.ip_v4_address = device_ip
    device_client.set_client_config(client_config)
    print(f"[receiver] Connecting via {interface}...")
    device = device_client.connect()

    streaming_manager = device.streaming_manager

    config = aria.StreamingConfig()
    resolved_profile = profile or (PROFILE_USB if interface == "usb" else PROFILE_WIFI)
    config.profile_name = resolved_profile
    if interface == "wifi":
        config.streaming_interface = aria.StreamingInterface.WifiStation
    else:
        config.streaming_interface = aria.StreamingInterface.Usb
    config.security_options.use_ephemeral_certs = True
    streaming_manager.streaming_config = config

    print(f"[receiver] Starting streaming (profile={resolved_profile})...")
    try:
        streaming_manager.start_streaming()
    except RuntimeError as e:
        # (940) "Cannot start streaming while a streaming or recording session
        # is in progress." A previous receiver that was killed mid-stream (or
        # whose stop_streaming errored with "Operation canceled") leaves the
        # glasses' session open, so every new launch dies here — the stuck-
        # session death spiral. Recover: stop the stale session and retry once.
        if "940" in str(e) or "in progress" in str(e).lower():
            print("[receiver] Stale streaming session on the glasses — stopping it and retrying...", flush=True)
            try:
                streaming_manager.stop_streaming()
            except Exception as e2:
                print(f"[receiver] stop_streaming during recovery returned: {e2}", flush=True)
            time.sleep(2)
            try:
                streaming_manager.start_streaming()
            except RuntimeError as e3:
                # Still stuck after the retry — surface a clean message instead of
                # the raw SDK error, then propagate (the launcher restarts us).
                print(f"[receiver] Recovery failed, session still in progress: {e3}", flush=True)
                raise
        else:
            raise

    streaming_client = streaming_manager.streaming_client

    # Subscription — NEVER include Audio: crashes under FEX-Emu (free(): invalid size)
    STREAM_TYPES = {
        "rgb": ("Rgb", 10),   # queue=5 starves to 7-8 FPS with 6 streams subscribed (2026-06-12 A/B)
        "slam": ("Slam", 10),
        "eye": ("EyeTrack", 10),
        "imu": ("Imu", 10),
        "mag": ("Magneto", 10),
        "baro": ("Baro", 10),
    }
    sub_config = streaming_client.subscription_config
    data_type = None
    enabled = []
    for name in streams:
        attr, qsize = STREAM_TYPES.get(name, (None, None))
        sdk_type = getattr(aria.StreamingDataType, attr, None) if attr else None
        if sdk_type is None:
            print(f"[receiver] WARNING: stream '{name}' not supported by this SDK, skipping")
            continue
        data_type = sdk_type if data_type is None else (data_type | sdk_type)
        sub_config.message_queue_size[sdk_type] = qsize
        enabled.append(name)
    sub_config.subscriber_data_type = data_type
    streaming_client.subscription_config = sub_config
    print(f"[receiver] Subscribed streams: {', '.join(enabled)}")

    observer = AriaFrameObserver(socket, bench_drop=bench_drop)
    observer.start()  # start dedicated ZMQ sender thread before subscribing
    streaming_client.set_streaming_client_observer(observer)
    streaming_client.subscribe()

    if bench_drop:
        print("[receiver] BENCH-DROP mode: O(1) callback, no ZMQ send (rx-only measurement)")
    print("[receiver] Streaming active. Press Ctrl+C to stop.")

    shutdown = False

    def handle_signal(sig, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not shutdown:
        time.sleep(0.1)

    print("[receiver] Shutting down...")
    streaming_client.unsubscribe()  # stop new callbacks from enqueuing
    streaming_manager.stop_streaming()
    observer.stop()  # join sender thread before closing the socket it owns
    device_client.disconnect(device)
    socket.close()
    ctx.term()
    print("[receiver] Done.")


def main():
    parser = argparse.ArgumentParser(description="Aria SDK frame receiver (FEX-Emu)")
    parser.add_argument("--interface", choices=["usb", "wifi"], default="usb")
    parser.add_argument("--device-ip", help="Aria glasses IP (required for wifi)")
    parser.add_argument("--zmq-endpoint", default=DEFAULT_ZMQ_ENDPOINT)
    parser.add_argument("--profile", default=None,
                        help="Streaming profile (default: profile12 — streaming-optimized, ~11 FPS)")
    parser.add_argument("--streams", default="rgb",
                        help="Comma-separated streams: rgb,slam,imu,mag,baro (default: rgb). "
                             "Audio is never allowed (crashes under FEX-Emu).")
    parser.add_argument("--bench-drop", action="store_true",
                        help="Diagnostic: O(1) callback that only counts rx and returns "
                             "(no ZMQ send, no lock). Isolates whether our tx path throttles SLAM.")
    args = parser.parse_args()

    if args.interface == "wifi" and not args.device_ip:
        parser.error("--device-ip is required for wifi interface")

    streams = tuple(s.strip().lower() for s in args.streams.split(",") if s.strip())
    if "audio" in streams:
        parser.error("audio subscription crashes under FEX-Emu — refusing")

    run(args.interface, args.device_ip, args.zmq_endpoint, args.profile, streams,
        bench_drop=args.bench_drop)


if __name__ == "__main__":
    main()

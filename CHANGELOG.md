# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] — 2026-09-17

Public release as an archived experiment. `projectaria-client-sdk` 2.5.0 (2026-09-04) ships native
Linux aarch64 wheels, so the FEX-Emu layer is no longer needed on a Jetson.

### Changed
- README reframed around what was measured; findings consolidated in `docs/FINDINGS.md`.
- `scripts/launch_pipeline.sh` takes the aria-guard checkout from `ARIA_GUARD_DIR`.

### Fixed
- `examples/frame_consumer.py` still read single-part v1 messages and rejected every frame of the
  v2.1 multipart protocol. It now reads `[header, payload]` and skips sensor batches; new tests run it
  against the mock receiver.

### Removed
- Internal research logs, the native DDS subscriber scaffolding and its probe scripts, and planning
  docs that were out of date. Nothing in the installed package changed.

## [0.2.0] — 2026-08-15

The release that turns a working experiment into something installable. No API
was removed; the package simply grew to match what actually ran on device.

### Added
- **Multi-stream receiver.** `rgb`, `slam`, `eye`, `imu`, `mag` and `baro` can be
  subscribed together (`--streams`). Audio remains refused by design — it crashes
  under FEX-Emu with `free(): invalid size`.
- **Wire protocol v2.1**: IMU, magnetometer and barometer batches travel on the
  same socket under the `ARS1` magic. A consumer that only knows `ARI2` skips
  them safely.
- `AriaBridgeObserver.get_sensors()` and `get_motion_state()` — walking vs
  stationary from IMU variance, for ego-motion compensation downstream.
- **Session self-heal**: a receiver killed mid-stream used to leave the glasses'
  session open, so every later launch died with error 940. `start_streaming()`
  now recovers and retries once.
- `aria_arm64_bridge.mock_receiver` — synthetic frames over the real protocol, so
  the whole pipeline can be exercised with no glasses, no Jetson and no FEX-Emu.
- **`libariabridge`** (`src/libariabridge/`) — native C++20 consumer: header-only
  protocol parsing, an RAII ZMQ consumer returning zero-copy
  `variant<FrameView, SensorView>`, and CMake that resolves ZeroMQ via
  pkg-config, then `find_library`, then by fetching and building it. No root
  required.
- Optional `[dashboard]` extra: a live web view of frames and sensors.
- `scripts/check.sh` and a `pre-push` hook — full local verification with no CI.
- Console scripts: `aria-bridge-receiver`, `aria-bridge-mock`,
  `aria-bridge-dashboard`.

### Changed
- **Receiver callbacks no longer send.** The SDK delivers each data type from its
  own thread; every thread used to share one lock and perform a 6 MB ZMQ send
  inside the callback. Callbacks now copy and enqueue; one dedicated thread owns
  the socket. This was first blamed for SLAM collapsing from 10 to 0.8 FPS, but
  the collapse persisted after the fix: its root cause was a degraded USB-NCM
  link (see `docs/FINDINGS.md`).
- ZMQ high-water marks raised to 64 on both ends. At 2, SLAM pairs and sensor
  bursts were dropped on any consumer hiccup.
- Camera-id mapping compares the SDK enum before falling back to substrings —
  Client SDK 2.4 changed the `str()` form.
- `flask` and `pillow` moved out of the core dependencies. Installing the bridge
  now pulls only `pyzmq` and `numpy`.
- `projectaria-tools` is installed natively instead of inside the emulated x86
  rootfs: 2.2.0 (2026-08-13) publishes Linux aarch64 wheels.
- One package, one copy: `src/receiver/`, `src/bridge/` and `src/dashboard/` are
  gone. Everything ships from `src/aria_arm64_bridge/`.

### Fixed
- `_process_frame` assumed 3 channels on the RGB path, so a single-channel frame
  raised `IndexError` on `[:, :, ::-1]`.
- **Telemetry measured itself.** `tegrastats` was spawned fresh every second with
  a 1.2 s timeout just to read one line — a process spawn and teardown inside the
  measurement of a tool whose job is measuring CPU. One long-lived process is now
  tailed by a daemon thread.
- Telemetry's availability check compared against `None` a value that was always
  `0`, so it detected nothing. `0%` GPU and "no GPU reading" are now distinct.
- The telemetry CPU average counted `EMC_FREQ` as if it were a core: the
  percent-at-frequency pattern matched the memory controller too. Every
  `cpu_avg` logged before this was biased upward.
- Dropped the `gpu_ram_used_mb` column, which was `ram_used_mb` under another
  name — the Orin has unified memory and reports no separate figure.
- The receiver command reached `FEXBash -c` unquoted, so a `device_ip` or
  `profile` containing shell metacharacters was interpreted by the shell.

### Documentation
- Reconciled the earlier FEX-Emu ceiling with the 2026-06-30 audit: measured over USB it looked like
  emulation; over WiFi the same receiver delivers RGB at 20 FPS on `profile9`. The bottleneck was the
  USB-CDC link saturating with six streams.
- README states plainly what is native on ARM64 and what is not.

### Removed
- Internal process docs.

## [0.1.0] — 2026-03-05

Initial release: run the closed-source x86_64 Aria Client SDK on ARM64 under
FEX-Emu and expose RGB frames to native code over ZMQ.

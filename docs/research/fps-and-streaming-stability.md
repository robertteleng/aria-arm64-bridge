# FPS & streaming stability — getting the pipeline to ~10 FPS

> 2026-06-30. Why the full pipeline stalled below 10 FPS and what fixed it.
>
> **Superseded in part.** This note was measured over USB only and attributes the ceiling to FEX-Emu
> and memory contention. The WiFi audit the same day ([aria-streaming-audit-2026-06-30.md](aria-streaming-audit-2026-06-30.md))
> showed the real limit was the USB-CDC link: over WiFi the same receiver delivers RGB at 20 FPS.
> The session self-heal and the lean-streams findings still stand. See [../FINDINGS.md](../FINDINGS.md).
> Measured on device (Jetson Orin Nano, Aria over USB, profile12, FEX-Emu).

## Symptoms

- Full pipeline "didn't reach 10 FPS"; sometimes a Docker container sat at
  **0.2 FPS** while the FEX receiver was dead → starved consumer.
- "SLAM iba regular" — SLAM streams struggled.

## Root causes (two, independent)

### 1. Stuck streaming session (the death spiral)
A receiver killed mid-stream (SIGTERM, or `stop_streaming` erroring with
"Operation canceled") leaves the glasses' streaming session **open**. Every
subsequent launch then dies at `start_streaming()` with:

```
RuntimeError: (940) Cannot start streaming while a streaming or recording
session is in progress.
```

So the receiver never comes up, the Docker guard starves, and you see 0.2 FPS.
**Fix:** `aria_receiver.run()` now catches the 940, calls `stop_streaming()`,
and retries `start_streaming()` once. Verified recovering from a live 940.

### 2. DDS saturation from subscribing to all 6 streams
With `rgb,slam,eye,imu,mag,baro` the FEX receiver can't drain the SDK fast
enough; FastDDS drops samples across **every** topic and RGB caps below 10.

Measured (receiver-only, rx == enqueued, 10 s windows):

| Streams | RGB FPS | DDS sample loss | RGB callback p99 |
|---|---|---|---|
| rgb,slam,eye,imu,mag,baro | **9.3–9.4** | massive (100s/topic) | 21 ms |
| **rgb,eye,imu** (lean) | **9.9–10.1** | none | 11–18 ms |

SLAM (2 grayscale cams) + mag + baro feed **only** the dashboard sensor panels.
Detection uses RGB, gaze uses eye, ego-motion uses IMU. **Fix:** the launch
defaults to `STREAMS=rgb,eye,imu`; SLAM/sensors are opt-in via
`STREAMS=rgb,slam,eye,imu,mag,baro`.

## Secondary optimisation (startup latency)
`launch_pipeline.sh` used to `pip install pyzmq 'numpy<2' --force-reinstall`
(+ piper-tts/onnxruntime) on **every** container start (~30–60 s). These are now
baked into `aria-guard/docker/Dockerfile.jetson`; the launch only installs them
if missing (backwards-compatible with the un-baked image). `numpy<2` is pinned
because the image's cv2 is built against numpy 1.x.

## Net result
RGB source ~10 FPS lean (~9.4 full), no DDS loss, receiver self-recovers from a
stuck session. Full end-to-end pipeline (with SLAM) measured at **7.5–9.1 FPS**.

## The ceiling is input-bound, NOT GPU-bound (measured 2026-06-30)
Fast GPU sampling (tegrastats every 200 ms) under load: **GR3D avg ≈18%, 0% in
~66% of samples, bursts to 99%**; power only ~7.3 W in MAXN_SUPER at ~51 °C.
The GPU does ~22 ms of work per frame then idles ~106 ms waiting for the next —
it has ~5× spare capacity. So the ~8–10 FPS ceiling is **(1) FEX-Emu** emulating
the closed x86 SDK (profile12 ≈10 FPS RGB even standalone) plus **(2) unified-RAM
bandwidth contention** (GPU inference bursts momentarily starve the receiver's DDS,
shaving 9.4→8). A lighter/faster model would NOT help — the GPU is already idle.
Real levers: feed faster (lean streams), reduce GPU↔receiver memory contention,
`jetson_clocks`. ~10 FPS is roughly the architectural ceiling of the USB+FEX path.

## Not done / future
- Rebuild `aria-demo:jetson` to actually bake the deps (the launch falls back to
  runtime install until then).
- If SLAM is ever needed live (e.g. for VIO in aria-nav), it needs its own
  lower-rate sub-pipeline, not the shared 10 FPS subscription.

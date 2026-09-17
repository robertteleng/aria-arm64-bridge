# Findings: running Aria live streaming on a Jetson

What was learned between February and August 2026 while running the Meta Aria Client SDK under
FEX-Emu on a Jetson Orin Nano. **Where the numbers come from:** they were written down at measurement
time in the lab notebook (not published) and in the audit notes in
[`research/`](research/). Raw logs were not kept, so treat each figure as a recorded observation from
one device and one pair of Aria Gen1 glasses, not as a benchmark.

## 1. Why the split architecture

- `projectaria-client-sdk` was closed source and shipped **only Linux x86_64 wheels** (no sdist), so
  on ARM64 the only option was to emulate it.
- **CUDA does not work under emulation.** Inference therefore has to live in a separate native
  process.
- The emulated process only does I/O (receive, copy, enqueue). Raw pixels cross to the native side
  over ZMQ PUSH/PULL on loopback.

## 2. The SLAM collapse: a hypothesis that was right about the code and wrong about the cause

**Symptom.** With RGB and SLAM subscribed over the USB link, RGB held ~9 FPS while SLAM decayed from
10 to **0.8 FPS** within ~30 s of streaming. The receiver was not dropping frames (`rx == tx`).
Raising `rmem_max` from 208 KB to 16 MB did nothing, and neither did rebooting the Jetson.

| Step | Experiment | Result | Conclusion |
|---|---|---|---|
| 1 | Callback that only counts frames: no lock, no send | SLAM **10 FPS** vs 0.8 | Suspect: every SDK thread shared one `_send_lock` and did the 6 MB RGB send inside the callback |
| 2 | Fix: callbacks copy and enqueue, one `zmq-sender` thread owns the socket | RGB+SLAM **still 0.7** SLAM; SLAM alone **10/10, stable** | The fix is correct but **not the root cause**; RGB and SLAM compete for something upstream |
| 3 | Same pair on `profile28` | RGB collapsed to 0.2–0.4 (2,177 samples lost), SLAM rose to 5–7 | The profile decides *which* stream is sacrificed; it rules out our frame copy |
| 4 | Instrumented callback timing | RGB p50 3.8 ms / p99 12–16 ms, SLAM p50 0.4 ms | **FEX-Emu returns fast**: not the bottleneck |
| 5 | After several clean stop/start cycles and USB re-enumerations | RGB+SLAM at **10/10/10** for 16 windows | The collapse was a **transient state**, not structural |
| 6 | Ping over the link | healthy: avg 4.2 ms, mdev 0.4 · degraded: avg 29 ms, mdev 16 | **Root cause: a degraded USB-NCM link** |

The Aria "USB" connection is **USB-NCM** (Ethernet over USB, `192.168.42.x`), not a raw data bus.
It negotiated USB 3.0 (5,000 Mbps), and real use was ~66 MB/s, about 10 % of the bus, so bandwidth was
ruled out. `ethtool -C` is not supported by the `cdc_ncm` driver. What actually worked was a clean
restart: stop streaming and let the USB re-enumerate.

The lock fix stayed. It removes a real contention between SDK threads, and `tests/test_observer_sender.py`
covers it. It just was not what caused this symptom.

## 3. Transport and profiles: WiFi beats USB (audit, 2026-06-30)

Method:
- WiFi, 6 streams (`rgb,slam,eye,imu,mag,baro`);
- **120 s per profile** (~10 windows of 10 s, ramp-up discarded);
- a run only counts if "streaming active" and "first frame" are both seen.

| | USB-CDC | WiFi |
|---|---|---|
| RGB FPS | ~8 | 10–20 |
| SLAM FPS | 1–6 (collapses) | 9–15 |
| DDS `sample lost` | massive | minimal |

| Profile (WiFi, 6 streams, 120 s) | RGB | Eye | SLAM | Samples lost | Verdict |
|---|---|---|---|---|---|
| **9** | **20** (flat) | 10 | 10 | 20 | Best for detection |
| **21** | 15 | **30** | 15 | 9 | Most balanced and cleanest |
| 12 | ~10 | ~10 | ~9 | low | Balanced baseline |
| 15 | 23 → 14 (degrades) | 6 | 18 | 2,875 | Saturates DDS |
| 23 | 28 → 19 (degrades) | 6 | 7 | 1,452 | Saturates DDS |
| 25 | — | — | — | error 954 | USB only |
| 28 | ~6 | 27 | 15 | — | RGB too low for detection |

The high-RGB profiles (15 and 23) looked best in short runs and only degraded over 120 s. A short
test would have picked the wrong profile.

## 4. The pipeline was input-bound, not GPU-bound

With detection running downstream in aria-guard, the GPU averaged **~18 % utilization** (0 % in about
two thirds of the 200 ms samples, bursts to 99 %), at ~7.3 W. It did ~22 ms of work per frame and then
waited. A faster model would not have raised throughput: the feed set the pace.

A correction to earlier notes: the "486 ms → 54 ms" figures in the audit are the **age of the last
detector result** when the pipeline read it. That is not end-to-end latency: it includes neither
capture nor transport.

## 5. Operational lessons

- **Never subscribe to audio under FEX-Emu:** it crashes with `free(): invalid size`.
- **A receiver killed mid-stream leaves the glasses' session open.** Every later launch then fails with
  error **940**. The receiver now stops and retries once.
- **ZMQ high-water marks of 2 dropped SLAM pairs and sensor bursts** on any consumer hiccup; 64 on both
  ends fixed it.
- **Client SDK 2.4 changed the `str()` of camera ids.** Mapping compares the SDK enum first.
- **The telemetry was measuring itself:** it spawned `tegrastats` every second, and its CPU average
  counted the memory controller (`EMC_FREQ`) as a core. Averages logged before the fix are biased upward.
- **Subscribing to all six streams costs RGB** (9.3 vs ~10 FPS over USB). Subscribe only to what the
  consumer uses.

## 6. Why this is now an archived experiment

On **2026-09-04**, `projectaria-client-sdk` **2.5.0** shipped `manylinux_2_34_aarch64` wheels for
Python 3.10–3.12. It installs and imports natively on a Jetson with JetPack 6.2 (glibc 2.35). Its
aarch64 wheel does not bundle `adb`, so a system `adb` is needed. The emulation layer this repo exists
for is no longer required.

What stays useful:
- the wire protocol and the zero-copy C++ consumer;
- the mock receiver, which exercises a consumer without glasses;
- the transport and profile measurements, which apply to any Aria-on-Jetson pipeline, native or not.

An open question this repo cannot answer: how native streaming on the Jetson compares with the numbers
above on the same glasses. That comparison needs a new measurement.

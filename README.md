# aria-arm64-bridge

**Run Meta Aria glasses on ARM64 (Jetson) — no x86 machine required.**

Meta's Aria SDK only ships x86_64 binaries. This library uses [FEX-Emu](https://fex-emu.com) binary translation to run the SDK on ARM64, exposing frames through a simple Python API.

```python
from aria_arm64_bridge import AriaBridge

with AriaBridge(interface="usb") as bridge:
    while bridge.is_running:
        frame = bridge.get_frame("rgb")  # numpy BGR, 1408x1408
        if frame is not None:
            your_model(frame)
```

## Performance

Tested on Jetson Orin Nano (8 GB, JetPack 6.x):

| Metric | Value |
|--------|-------|
| RGB FPS | ~11 (profile12, streaming-optimised) |
| Resolution | 1408 x 1408 x 3 |
| Bridge latency | ~24 ms |
| SLAM FPS | ~49 (2 cameras @ 640x480) |

## How it works

```
┌─────────────┐  USB/WiFi  ┌──────────────────────────────────────┐
│ Aria Glasses │ ─────────> │ Jetson Orin Nano (ARM64)              │
└─────────────┘            │                                       │
                           │  FEX-Emu (x86_64)    Native ARM64     │
                           │  ┌──────────────┐   ┌──────────────┐  │
                           │  │ Aria SDK      │──>│ Your code    │  │
                           │  │ receiver.py   │ZMQ│ YOLO, depth  │  │
                           │  └──────────────┘   │ whatever you  │  │
                           │                     │ want          │  │
                           │                     └──────────────┘  │
                           └──────────────────────────────────────┘
```

The Aria SDK runs under FEX-Emu (x86_64 binary translation). It receives frames and pushes them over ZMQ to your native ARM64 code. The emulation overhead is negligible — the bottleneck is the SDK's DDS protocol, not the CPU.

## Install

```bash
pip install aria-arm64-bridge
```

### Prerequisites

1. **Jetson** (Orin Nano/NX/AGX) with JetPack 6.x
2. **FEX-Emu** with x86_64 rootfs + `projectaria-client-sdk` installed
3. **Aria glasses** paired via `aria auth pair`

Setup scripts are included:

```bash
./scripts/setup_fex_emu.sh    # Install FEX-Emu
./scripts/setup_rootfs.sh     # x86_64 rootfs + Aria SDK
```

See [ARIA_CONNECTION_GUIDE.md](docs/project/ARIA_CONNECTION_GUIDE.md) for pairing and streaming setup.

## Usage

### High-level (recommended)

```python
from aria_arm64_bridge import AriaBridge

# Launches FEX-Emu receiver automatically
bridge = AriaBridge(interface="usb")
bridge.start()

frame = bridge.get_frame("rgb")   # numpy BGR uint8, or None
stats = bridge.get_stats()        # {"fps": {"rgb": 11.2}, "uptime": ...}

bridge.stop()
```

Supports context manager:

```python
with AriaBridge(interface="wifi", device_ip="192.168.1.42") as bridge:
    frame = bridge.get_frame("rgb")
```

### Low-level (run receiver separately)

Terminal 1 — FEX-Emu receiver:
```bash
PYTHONNOUSERSITE=1 FEXBash -c "python3 -m aria_arm64_bridge.receiver --interface usb"
```

Terminal 2 — your code:
```python
from aria_arm64_bridge import AriaBridgeObserver

observer = AriaBridgeObserver()  # connects to ZMQ localhost:5555
frame = observer.get_frame("rgb")
observer.stop()
```

### Available cameras

| Camera | ID | Resolution | Notes |
|--------|----|-----------|-------|
| `"rgb"` | 0 | 1408x1408 | Main camera, ~11 FPS |
| `"slam1"` | 2 | 640x480 | Grayscale, ~25 FPS |
| `"slam2"` | 3 | 640x480 | Grayscale, ~25 FPS |
| `"eye"` | 1 | varies | Eye tracking camera |

## Important notes

- **Use `profile12`** (default) — it's the only streaming-optimised profile that works reliably under FEX-Emu
- **Never subscribe to audio** — causes `free(): invalid size` crash under emulation
- **Always use `PYTHONNOUSERSITE=1`** when running under FEX-Emu
- **11 FPS is the ceiling** for RGB under FEX-Emu with gen1 Aria glasses (DDS protocol limitation, not CPU)

## Project structure

```
src/aria_arm64_bridge/
├── __init__.py      # Public API: AriaBridge, Frame, AriaBridgeObserver
├── bridge.py        # AriaBridge — high-level, manages subprocess + observer
├── observer.py      # AriaBridgeObserver — ZMQ consumer (native ARM64)
├── receiver.py      # Aria SDK receiver (runs under FEX-Emu, x86_64)
└── protocol.py      # Wire protocol constants (header format, camera IDs)
```

## Related

- [FEX-Emu](https://github.com/FEX-Emu/FEX) — x86_64 binary translator for ARM64
- [Project Aria](https://www.projectaria.com/) — Meta's AR research glasses
- [projectaria-client-sdk](https://pypi.org/project/projectaria-client-sdk/) — Official Aria SDK (x86_64 only)

## License

MIT

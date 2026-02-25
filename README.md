# aria-arm64-bridge

## Problem

Meta's Project Aria glasses SDK (`projectaria-client-sdk`) only ships x86_64 Linux binaries. There are no ARM64 wheels, no source distribution, and no way to recompile — it's a 98 MB closed-source monolith with Meta's entire networking stack (FastDDS, Proxygen, QUIC, Folly) statically linked inside.

This blocks running ARIA Guard directly on Jetson Orin Nano (ARM64), forcing a two-machine setup.

## Solution

Use **FEX-Emu** (x86_64 → ARM64 binary translator with JIT) to run *only* the Aria SDK receiver process under emulation. Since the Aria process is pure I/O (receive frames via WiFi, push to queue), the emulation overhead is negligible — the bottleneck is WiFi, not CPU.

```
┌─────────────┐    WiFi     ┌────────────────────────────────────────┐
│ Aria Glasses │ ─────────> │  Jetson Orin Nano (ARM64)              │
│              │            │                                        │
└─────────────┘            │  ┌──────────────────────┐              │
                           │  │ FEX-Emu (x86_64 JIT) │   IPC       │
                           │  │ Python x86_64         │ ─────────>  │
                           │  │ aria-client-sdk       │  ZMQ/SHM   │
                           │  └──────────────────────┘              │
                           │                              │         │
                           │  ┌──────────────────────┐    │         │
                           │  │ Native ARM64          │ <─┘         │
                           │  │ YOLO + Depth + CUDA   │             │
                           │  │ (aria-guard pipeline)  │             │
                           │  └──────────────────────┘              │
                           └────────────────────────────────────────┘
```

### Key Components

| Component | Responsibility |
|-----------|---------------|
| FEX-Emu | Translate x86_64 instructions to ARM64 via JIT |
| x86_64 rootfs | Minimal filesystem with Python + Aria SDK |
| Aria receiver | Emulated process that receives frames from glasses |
| IPC bridge | ZMQ/shared memory transport between emulated and native |
| Native consumer | ARM64 process that feeds frames to aria-guard |

## Tech Stack

- **Platform:** Jetson Orin Nano (JetPack 6.x, ARM64)
- **Emulator:** FEX-Emu (JIT x86_64 binary translation)
- **Language:** Python 3.12, Bash
- **IPC:** ZMQ (or shared memory)

## Getting Started

### Prerequisites

- Jetson Orin Nano with JetPack 6.x
- Meta Aria glasses (Gen1 or Gen2)
- Internet connection (for downloading rootfs and SDK)

### Setup

```bash
# 1. Install FEX-Emu on Jetson
./scripts/setup_fex_emu.sh

# 2. Create x86_64 rootfs with Python + Aria SDK
./scripts/setup_rootfs.sh

# 3. Verify installation
./scripts/test_import.sh
```

### Test Streaming

```bash
# Requires Aria glasses paired and on same network
./scripts/test_streaming.sh
```

## Project Status

See [IMPLEMENTATION_PLAN.md](docs/project/IMPLEMENTATION_PLAN.md) for the full roadmap.

| Phase | Description | Status |
|-------|------------|--------|
| 0 | Foundation (docs, research) | In Progress |
| 1 | FEX-Emu + SDK import test | Pending |
| 2 | Streaming test | Pending |
| 3 | IPC bridge | Pending |
| 4 | aria-guard integration | Pending |

## Documentation

| Doc | Purpose |
|-----|---------|
| [CLAUDE.md](CLAUDE.md) | LLM/AI rules and coding conventions |
| [PAIR_WORKFLOW.md](docs/teamwork/PAIR_WORKFLOW.md) | Engineer + Claude collaboration protocol |
| [IMPLEMENTATION_PLAN.md](docs/project/IMPLEMENTATION_PLAN.md) | Detailed roadmap by phase |
| [RESEARCH.md](docs/project/RESEARCH.md) | SDK internals, FEX-Emu research, alternatives |

## Related

- [aria-guard](https://github.com/robertteleng/aria-guard) — The main ARIA Guard project (collision detection for visually impaired)
- [FEX-Emu](https://github.com/FEX-Emu/FEX) — x86_64 binary translator for ARM64
- [Project Aria](https://www.projectaria.com/) — Meta's AR research glasses

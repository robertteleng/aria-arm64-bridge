# CLAUDE.md — LLM Instructions for aria-arm64-bridge

Read this file completely before writing any code. Read [PAIR_WORKFLOW.md](docs/teamwork/PAIR_WORKFLOW.md) for how we collaborate.

---

## Project Identity

- **What:** Run Meta Aria SDK streaming on ARM64 (Jetson Orin Nano) via binary translation
- **Target user:** ARIA Guard developer (Robert) — needs Aria glasses → Jetson direct pipeline
- **Language/Stack:** Python 3.12, FEX-Emu, Shell scripts, Docker (optional)
- **Stage:** Phase 0 — Research & Proof of Concept

## Critical Constraints

These are non-negotiable. Violating any of these will break the project:

1. `projectaria-client-sdk` is a closed-source x86_64 binary — cannot be recompiled, only emulated
2. The Aria process is I/O only (receive frames, push to queue) — no CUDA, no heavy compute
3. Must work on Jetson Orin Nano (ARM64 Cortex-A78AE, JetPack 6.x)
4. Latency budget: <50ms added overhead from emulation layer (WiFi is the real bottleneck at ~30 FPS)

---

## Architecture Rules

### Patterns — DO

```
# Isolate emulated code from native code
# Emulated x86_64 process communicates via IPC (ZMQ, pipe, shared memory)
[FEX-Emu x86_64] → Queue/ZMQ → [Native ARM64 consumer]
```

### Patterns — DO NOT

```
# Never mix emulated and native Python in the same process
# Never try to use CUDA from the emulated process
import torch  # WRONG — this is the native consumer's job
```

### Anti-patterns to watch for

| Bad | Good |
|-----|------|
| Running full aria-guard under emulation | Only emulate the Aria SDK receiver |
| Pickle for IPC (slow, fragile) | ZMQ with raw bytes or shared memory |
| Hardcoded paths to x86_64 libs | FEX-Emu rootfs with proper lib discovery |

---

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Scripts | snake_case | `test_fex_aria.sh` |
| Python modules | snake_case | `aria_receiver.py` |
| Config files | SCREAMING_SNAKE | `FEX_ROOTFS_PATH` |
| Experiment branches | `exp/description` | `exp/fex-emu-basic` |
| Git branches | `type/description` | `feat/zmq-bridge` |

---

## File Organization

```
aria-arm64-bridge/
├── scripts/               # Setup and test scripts
│   ├── setup_fex_emu.sh   # Install FEX-Emu on Jetson
│   ├── setup_rootfs.sh    # Create x86_64 rootfs with Python + aria SDK
│   └── test_streaming.sh  # End-to-end streaming test
├── src/                   # Python source code
│   ├── receiver/          # x86_64 emulated — Aria SDK frame receiver
│   └── bridge/            # Native ARM64 — IPC bridge to consumer
├── tests/                 # Test scripts and validation
├── docs/                  # Documentation
│   ├── project/           # Plans, research, guides
│   ├── teamwork/          # Collaboration protocol
│   └── DEVELOPER_DIARY.md # Feature diary
└── experiments/           # Raw experiment logs, captures, notes
```

---

## Git & Commits

### Commit Format

```
type(scope): short description
```

**Types:** `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `exp` (experiment)

**No Co-Authored-By trailers.** Keep commits clean.

### Branch Naming

```
exp/experiment-name       # Research experiments
feat/feature-name         # Proven features
docs/what-was-documented  # Documentation only
```

---

## Dependencies

### Required (on Jetson ARM64)

- FEX-Emu (x86_64 binary translator)
- Python 3.12 x86_64 (inside FEX rootfs)
- projectaria-client-sdk (x86_64 wheel, inside rootfs)
- pyzmq or similar IPC mechanism

### Nice to Have

- projectaria-tools (compile from source on ARM64 for data inspection)

### NEVER Use

- QEMU user-mode (too slow, worse syscall coverage than FEX-Emu)
- Box64 (gaming-focused, worse for networking syscalls)

---

## Development Phases

| Phase | What | Status |
|-------|------|--------|
| 0 | Foundation (docs, research, FEX-Emu setup) | In Progress |
| 1 | FEX-Emu + Aria SDK basic test (import, init) | Pending |
| 2 | Streaming test (receive frames from Aria glasses) | Pending |
| 3 | IPC bridge (ZMQ/shared memory to native ARM64) | Pending |
| 4 | Integration with aria-guard | Pending |

**Rule:** Never start Phase N+1 until Phase N is validated with real test.

---

## Documentation Map

| File | What |
|------|------|
| [README.md](README.md) | Problem, solution, architecture, setup |
| [PAIR_WORKFLOW.md](docs/teamwork/PAIR_WORKFLOW.md) | How Engineer + Claude collaborate |
| [IMPLEMENTATION_PLAN.md](docs/project/IMPLEMENTATION_PLAN.md) | Roadmap: what to build and in what order |
| [RESEARCH.md](docs/project/RESEARCH.md) | Research: Aria SDK internals, FEX-Emu, alternatives |
| [DOCUMENTATION_GUIDE.md](docs/project/DOCUMENTATION_GUIDE.md) | Index of all docs and reading order |
| [DEVELOPER_DIARY.md](docs/DEVELOPER_DIARY.md) | Diary of experiments and decisions |

---

## Interaction Rules

1. **Read this file before writing code** — all conventions and constraints are here
2. **Read PAIR_WORKFLOW.md** — follow the collaboration protocol
3. **Don't over-engineer** — do what's needed for the current phase, not future phases
4. **Prefer editing existing files** over creating new ones
5. **Never assume — verify first** — if not 100% certain about FEX-Emu behavior, test it BEFORE writing code
6. **Diagnose before fixing** — understand WHY before changing code
7. **Update DEVELOPER_DIARY.md after each experiment** — fill the 5-question framework entry
8. **Speak to the user in Spanish**
9. **Commit messages in English**

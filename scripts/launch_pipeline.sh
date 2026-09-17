#!/usr/bin/env bash
# Launch the full Aria → aria-guard pipeline.
#
# Reference integration, kept as it ran on device: it needs a local aria-guard
# checkout (ARIA_GUARD_DIR) and the Jetson image that repo builds.
#
# Usage:
#   ./scripts/launch_pipeline.sh              # USB, default
#   ./scripts/launch_pipeline.sh wifi 192.168.1.42
#
# Starts two processes:
#   1. FEX-Emu receiver (Aria SDK → ZMQ)
#   2. Docker aria-guard (ZMQ → YOLO + Depth + Dashboard)
#
# Dashboard: http://<jetson-ip>:5000

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

INTERFACE="${1:-usb}"
DEVICE_IP="${2:-}"
MODE="${3:-all}"
# Streaming profile: 4º argumento o env ARIA_PROFILE.
# Default profile9: medido 2026-06-30 el mejor para detección (RGB ~20 estable y
# limpio, eye/SLAM ~10), elegido tras la auditoría (docs/research/aria-streaming-audit-2026-06-30.md).
PROFILE="${4:-${ARIA_PROFILE:-profile9}}"

# Receiver streams. Default = FULL (SLAM + sensores) para que el dashboard muestre
# todos los paneles. Medido 2026-06-30: el receiver aislado da ~10 FPS lean
# (rgb,eye,imu) vs ~9.4 full, PERO el cuello de botella del pipeline completo es la
# contención GPU↔receiver (YOLO+Depth estrangulan la captura a ~6.8 FPS), no el nº
# de streams — así que llevar SLAM no cuesta FPS e2e. Para un receiver liviano
# (debugging sin GPU): STREAMS=rgb,eye,imu ./launch_pipeline.sh
STREAMS="${STREAMS:-rgb,slam,eye,imu,mag,baro}"
RECEIVER_ARGS="--interface $INTERFACE --streams $STREAMS"
[ -n "$PROFILE" ] && RECEIVER_ARGS="$RECEIVER_ARGS --profile $PROFILE"
if [ "$INTERFACE" = "wifi" ] && [ -n "$DEVICE_IP" ]; then
    RECEIVER_ARGS="$RECEIVER_ARGS --device-ip $DEVICE_IP"
elif [ "$INTERFACE" = "wifi" ] && [ -z "$DEVICE_IP" ]; then
    echo "ERROR: WiFi requires device IP: $0 wifi <IP>"
    exit 1
fi

echo "╔══════════════════════════════════════╗"
echo "║   Aria ARM64 Bridge Pipeline         ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Interface: $INTERFACE"
[ -n "$DEVICE_IP" ] && echo "  Device IP: $DEVICE_IP"
echo "  Mode:      $MODE"
echo ""

# Telemetry: CSV en logs/ con CPU/RAM/GPU por segundo
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
TELEMETRY_LOG="$LOG_DIR/telemetry_$(date +%Y%m%d_%H%M%S).csv"

# Lightweight: avoids stealing CPU from the FEX receiver.
#  - global CPU via two instant /proc/stat snapshots (no blocking `top -bn2 -d0.5`)
#  - tegrastats spawned ONCE to a temp file, we read its latest line (no per-second spawn)
#  - 2s sample interval (less overhead, enough resolution for FPS work)
TEGRA_OUT=""
read_proc_stat_cpu() {
    # echoes busy and total jiffies from first /proc/stat line
    awk '/^cpu /{idle=$5+$6; total=0; for(i=2;i<=NF;i++) total+=$i; print total-idle, total}' /proc/stat
}

telemetry_loop() {
    local receiver_pid=$1
    echo "timestamp,elapsed_s,fex_cpu,fex_mem_mb,total_cpu,ram_used_mb,ram_free_mb,gpu_util,gpu_mem_mb" > "$TELEMETRY_LOG"
    local start prev_busy prev_total
    start=$(date +%s)
    read -r prev_busy prev_total < <(read_proc_stat_cpu)

    # Spawn tegrastats once (1s interval) to a temp file; read its tail each loop
    if command -v tegrastats &>/dev/null; then
        TEGRA_OUT=$(mktemp)
        tegrastats --interval 1000 > "$TEGRA_OUT" 2>/dev/null &
        TEGRA_PID=$!
    fi

    while true; do
        local ts elapsed fex_cpu fex_mem total_cpu ram_used ram_free gpu_util gpu_mem
        local busy total dbusy dtotal teg
        ts=$(date +%Y-%m-%dT%H:%M:%S)
        elapsed=$(( $(date +%s) - start ))

        # FEX receiver CPU+RAM via /proc (cumulative CPU seconds, RSS in MB)
        if [ -d "/proc/$receiver_pid" ]; then
            read -r utime stime rss < <(awk '{print $14,$15,$24}' /proc/$receiver_pid/stat 2>/dev/null || echo "0 0 0")
            fex_cpu=$(awk "BEGIN {printf \"%.1f\", ($utime+$stime)/100}")
            fex_mem=$(awk "BEGIN {printf \"%d\", $rss*$(getconf PAGESIZE)/1048576}" 2>/dev/null || echo 0)
        else
            fex_cpu=0; fex_mem=0
        fi

        # Global CPU% via /proc/stat delta (instant, no blocking)
        read -r busy total < <(read_proc_stat_cpu)
        dbusy=$(( busy - prev_busy )); dtotal=$(( total - prev_total ))
        total_cpu=$(awk "BEGIN {printf \"%.1f\", ($dtotal>0)?100*$dbusy/$dtotal:0}")
        prev_busy=$busy; prev_total=$total

        # RAM global
        read -r _ ram_total ram_used ram_free _ < <(free -m | awk '/^Mem:/{print}')

        # GPU from the latest tegrastats line (no spawn)
        if [ -n "$TEGRA_OUT" ] && [ -s "$TEGRA_OUT" ]; then
            teg=$(tail -1 "$TEGRA_OUT")
            gpu_util=$(echo "$teg" | grep -oP 'GR3D_FREQ \K[0-9]+' 2>/dev/null || echo 0)
            gpu_mem=$(echo "$teg"  | grep -oP 'RAM \K[0-9]+'       2>/dev/null || echo 0)
        else
            gpu_util=0; gpu_mem=0
        fi

        echo "$ts,$elapsed,$fex_cpu,$fex_mem,$total_cpu,$ram_used,$ram_free,$gpu_util,$gpu_mem" >> "$TELEMETRY_LOG"
        sleep 2
    done
}

cleanup() {
    echo ""
    echo "[pipeline] Shutting down..."
    kill $TELEMETRY_PID 2>/dev/null || true
    [ -n "$TEGRA_PID" ] && kill $TEGRA_PID 2>/dev/null || true
    [ -n "$TEGRA_OUT" ] && rm -f "$TEGRA_OUT" 2>/dev/null || true
    kill $RECEIVER_PID 2>/dev/null || true
    kill $GUARD_PID 2>/dev/null || true
    # `docker run --rm` detaches: killing the client PID leaves the container
    # running (orphan that starves once the receiver is gone). Kill by name.
    docker kill aria-guard-live 2>/dev/null || true
    wait 2>/dev/null
    echo "[pipeline] Telemetry saved to $TELEMETRY_LOG"
    echo "[pipeline] Done."
}
trap cleanup EXIT INT TERM

# 1. Start FEX-Emu receiver
echo "[pipeline] Starting FEX-Emu receiver..."
# /usr/bin/python3 explícito: bajo FEXBash, "python3" puede resolver al .venv ARM64 del host (sin SDK)
PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 -u $PROJECT_DIR/src/aria_arm64_bridge/receiver.py $RECEIVER_ARGS" &
RECEIVER_PID=$!
sleep 3

# 2. Start telemetry (host-side, sees all PIDs)
telemetry_loop $RECEIVER_PID &
TELEMETRY_PID=$!
echo "[pipeline] Telemetry → $TELEMETRY_LOG"

# 3. Start aria-guard in Docker
echo "[pipeline] Starting aria-guard in Docker..."
# Audio passthrough: route the container's beeps to the host PulseAudio (and thus
# to BT headphones). The image lacks the ALSA->pulse plugin, so install it + write
# an asound.conf at runtime (until it's baked into Dockerfile.jetson). Beeps are on
# by default now (decoupled from NeMo); set NO_AUDIO=1 to fall back to --no-tts.
UID_="$(id -u)"
AUDIO_FLAG=""; [ "${NO_AUDIO:-0}" = "1" ] && AUDIO_FLAG="--no-tts"

# Voz (TTS): Piper es_ES por defecto si el modelo está presente. VOICE=0 la desactiva.
# El modelo vive en el host (aria-guard/models/piper) y se monta en /app/models/piper.
ARIA_GUARD_DIR="${ARIA_GUARD_DIR:-$HOME/Projects/aria/aria-guard}"
PIPER_VOICE_HOST="$ARIA_GUARD_DIR/models/piper/es_ES-davefx-medium.onnx"
VOICE_ENGINE=""
if [ "${VOICE:-1}" = "1" ] && [ "${NO_AUDIO:-0}" != "1" ] && [ -f "$PIPER_VOICE_HOST" ]; then
    VOICE_ENGINE="piper"
    echo "[pipeline] Voz: Piper es_ES (ARIA_TTS_ENGINE=piper)"
else
    echo "[pipeline] Voz: desactivada (solo beeps). VOICE=1 + modelo en models/piper para activarla."
fi
docker rm -f aria-guard-live 2>/dev/null || true  # clear a stale orphan first
docker run --runtime nvidia --network host --rm --name aria-guard-live \
    -v "$PROJECT_DIR/src":/bridge \
    -v "$ARIA_GUARD_DIR":/app \
    -v "/run/user/$UID_/pulse":"/run/user/$UID_/pulse" \
    -v "$HOME/.config/pulse/cookie":/root/.config/pulse/cookie:ro \
    -e PULSE_SERVER="unix:/run/user/$UID_/pulse/native" \
    -e PULSE_LATENCY_MSEC="${PULSE_LATENCY_MSEC:-40}" \
    -e ARIA_PROFILE="${PROFILE:-profile12}" \
    -e ARIA_YOLO_MODEL="${ARIA_YOLO_MODEL:-yolo26n}" \
    -e ARIA_DEPTH="${ARIA_DEPTH:-1}" \
    -e ARIA_TTS_ENGINE="$VOICE_ENGINE" \
    -e ARIA_PIPER_VOICE="/app/models/piper/es_ES-davefx-medium.onnx" \
    aria-demo:jetson bash -c \
    "(command -v aplay >/dev/null || apt-get update -qq && apt-get install -y -qq libasound2-plugins >/dev/null 2>&1); \
     printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' > /etc/asound.conf; \
     python3 -c 'import zmq, numpy, sys; sys.exit(0 if numpy.__version__[0]==chr(49) else 1)' 2>/dev/null || pip3 install -q pyzmq 'numpy<2' --force-reinstall; \
     if [ -n \"$VOICE_ENGINE\" ]; then python3 -c 'import piper' 2>/dev/null || pip3 install -q piper-tts onnxruntime; fi; \
     cd /app && \
     PYTHONPATH=/bridge:/app python3 -m src.main aria:bridge $MODE $AUDIO_FLAG" &
GUARD_PID=$!

echo "[pipeline] All processes started."
echo "[pipeline] Dashboard: http://$(hostname -I | awk '{print $1}'):5000"
echo "[pipeline] Press Ctrl+C to stop."
echo ""

wait

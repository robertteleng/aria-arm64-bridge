#!/usr/bin/env bash
# Launch the full Aria → aria-guard pipeline.
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

# Receiver args
RECEIVER_ARGS="--interface $INTERFACE"
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

telemetry_loop() {
    local receiver_pid=$1
    echo "timestamp,elapsed_s,fex_cpu,fex_mem_mb,total_cpu,ram_used_mb,ram_free_mb,gpu_util,gpu_mem_mb" > "$TELEMETRY_LOG"
    local start
    start=$(date +%s)
    while true; do
        local ts elapsed fex_cpu fex_mem total_cpu ram_used ram_free gpu_util gpu_mem
        ts=$(date +%Y-%m-%dT%H:%M:%S)
        elapsed=$(( $(date +%s) - start ))

        # FEXBash CPU+RAM via /proc (no deps)
        if [ -d "/proc/$receiver_pid" ]; then
            read -r utime stime _ _ _ _ _ rss _ < <(awk '{print $14,$15,$1,$2,$3,$4,$5,$24,$25}' /proc/$receiver_pid/stat 2>/dev/null || echo "0 0 0 0 0 0 0 0 0")
            fex_cpu=$(awk "BEGIN {printf \"%.1f\", ($utime+$stime)/100}")
            fex_mem=$(awk "BEGIN {printf \"%d\", $rss*4/1024}" 2>/dev/null || echo 0)
        else
            fex_cpu=0; fex_mem=0
        fi

        # RAM global
        read -r _ ram_total ram_used ram_free _ < <(free -m | awk '/^Mem:/{print}')

        # CPU global (1s interval)
        total_cpu=$(top -bn2 -d0.5 | grep 'Cpu(s)' | tail -1 | awk '{print $2}' | tr -d '%us,' 2>/dev/null || echo 0)

        # GPU via tegrastats (Jetson)
        if command -v tegrastats &>/dev/null; then
            local teg
            teg=$(tegrastats --interval 500 2>/dev/null | head -1 || true)
            gpu_util=$(echo "$teg" | grep -oP 'GR3D_FREQ \K[0-9]+' 2>/dev/null || echo 0)
            gpu_mem=$(echo "$teg"  | grep -oP 'RAM \K[0-9]+'       2>/dev/null || echo 0)
        else
            gpu_util=0; gpu_mem=0
        fi

        echo "$ts,$elapsed,$fex_cpu,$fex_mem,$total_cpu,$ram_used,$ram_free,$gpu_util,$gpu_mem" >> "$TELEMETRY_LOG"
        sleep 1
    done
}

cleanup() {
    echo ""
    echo "[pipeline] Shutting down..."
    kill $TELEMETRY_PID 2>/dev/null || true
    kill $RECEIVER_PID 2>/dev/null || true
    kill $GUARD_PID 2>/dev/null || true
    wait 2>/dev/null
    echo "[pipeline] Telemetry saved to $TELEMETRY_LOG"
    echo "[pipeline] Done."
}
trap cleanup EXIT INT TERM

# 1. Start FEX-Emu receiver
echo "[pipeline] Starting FEX-Emu receiver..."
PYTHONNOUSERSITE=1 FEXBash -c "python3 $PROJECT_DIR/src/receiver/aria_receiver.py $RECEIVER_ARGS" &
RECEIVER_PID=$!
sleep 3

# 2. Start telemetry (host-side, sees all PIDs)
telemetry_loop $RECEIVER_PID &
TELEMETRY_PID=$!
echo "[pipeline] Telemetry → $TELEMETRY_LOG"

# 3. Start aria-guard in Docker
echo "[pipeline] Starting aria-guard in Docker..."
docker run --runtime nvidia --network host --rm \
    -v "$PROJECT_DIR/src/bridge":/bridge \
    -v "$HOME/Projects/aria-guard":/app \
    aria-demo:jetson bash -c \
    "pip3 install -q pyzmq 'numpy<2' --force-reinstall && PYTHONPATH=/bridge python3 run.py aria:bridge $MODE --no-tts" &
GUARD_PID=$!

echo "[pipeline] All processes started."
echo "[pipeline] Dashboard: http://$(hostname -I | awk '{print $1}'):5000"
echo "[pipeline] Press Ctrl+C to stop."
echo ""

wait

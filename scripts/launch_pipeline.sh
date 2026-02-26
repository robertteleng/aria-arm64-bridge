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

cleanup() {
    echo ""
    echo "[pipeline] Shutting down..."
    kill $RECEIVER_PID 2>/dev/null || true
    kill $GUARD_PID 2>/dev/null || true
    wait 2>/dev/null
    echo "[pipeline] Done."
}
trap cleanup EXIT INT TERM

# 1. Start FEX-Emu receiver
echo "[pipeline] Starting FEX-Emu receiver..."
PYTHONNOUSERSITE=1 FEXBash -c "python3 $PROJECT_DIR/src/receiver/aria_receiver.py $RECEIVER_ARGS" &
RECEIVER_PID=$!
sleep 3

# 2. Start aria-guard in Docker
echo "[pipeline] Starting aria-guard in Docker..."
docker run --runtime nvidia --network host --rm \
    -v "$PROJECT_DIR/src/bridge":/bridge \
    -v "$HOME/Projects/aria-guard":/app \
    aria-demo:jetson bash -c \
    "pip3 install -q pyzmq 'numpy<2' --force-reinstall && PYTHONPATH=/bridge python3 run.py aria:bridge $MODE --no-tts" &
GUARD_PID=$!

echo "[pipeline] Both processes started."
echo "[pipeline] Dashboard: http://$(hostname -I | awk '{print $1}'):5000"
echo "[pipeline] Press Ctrl+C to stop."
echo ""

wait

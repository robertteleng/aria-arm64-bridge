#!/usr/bin/env bash
# Launch the sensor inspection session: multi-stream receiver + native dashboard.
#
# Usage:
#   ./scripts/launch_sensor_dashboard.sh              # USB, rgb+slam+imu+mag+baro
#   ./scripts/launch_sensor_dashboard.sh wifi 192.168.1.42
#
# Dashboard: http://<jetson-ip>:5001
#
# NOTE: do NOT run launch_pipeline.sh at the same time — ZMQ PUSH/PULL
# load-balances and both consumers would steal frames from each other.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

INTERFACE="${1:-usb}"
DEVICE_IP="${2:-}"
STREAMS="${3:-rgb,slam,imu,mag,baro}"

RECEIVER_ARGS="--interface $INTERFACE --streams $STREAMS"
if [ "$INTERFACE" = "wifi" ]; then
    [ -z "$DEVICE_IP" ] && { echo "ERROR: WiFi requires IP: $0 wifi <IP>"; exit 1; }
    RECEIVER_ARGS="$RECEIVER_ARGS --device-ip $DEVICE_IP"
fi

cleanup() {
    echo ""
    echo "[sensor-dash] Shutting down..."
    kill -INT $RECEIVER_PID 2>/dev/null || true
    sleep 6  # let the receiver stop_streaming() gracefully (kill -9 leaves a stale session on the glasses)
    kill -9 $RECEIVER_PID $DASH_PID 2>/dev/null || true
    pkill -9 -f aria_receiver 2>/dev/null || true
    PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 $PROJECT_DIR/scripts/stop_streaming.py" >/dev/null 2>&1 || true
    echo "[sensor-dash] Done."
}
trap cleanup EXIT INT TERM

echo "[sensor-dash] Starting FEX receiver (streams: $STREAMS)..."
# /usr/bin/python3 explícito + -u: ver notas en launch_pipeline.sh
PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 -u $PROJECT_DIR/src/aria_arm64_bridge/receiver.py $RECEIVER_ARGS" &
RECEIVER_PID=$!

echo "[sensor-dash] Starting native dashboard..."
"$PROJECT_DIR/.venv/bin/python3" -m src.dashboard.server --port 5001 &
DASH_PID=$!

echo "[sensor-dash] Dashboard: http://$(hostname -I | awk '{print $1}'):5001"
echo "[sensor-dash] Press Ctrl+C to stop."
wait $RECEIVER_PID $DASH_PID

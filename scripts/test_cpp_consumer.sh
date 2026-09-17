#!/usr/bin/env bash
# End-to-end check for the native C++ consumer: the Python mock receiver sends,
# libariabridge receives. No glasses, no Jetson, no FEX-Emu.
#
#   ./scripts/test_cpp_consumer.sh [frames]
#
# Configures and builds if needed. ZeroMQ comes from the system when present and
# is fetched and built otherwise, so this never needs root.
set -euo pipefail
cd "$(dirname "$0")/.."

FRAMES="${1:-5}"
PORT="${PORT:-5561}"          # not 5555: don't collide with a real receiver
ENDPOINT="tcp://127.0.0.1:${PORT}"
BUILD_DIR="build/cpp"
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

if [ ! -x "$BUILD_DIR/ariabridge_tests" ]; then
    echo "== configuring and building (first run fetches libzmq) =="
    cmake -S src/libariabridge -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$BUILD_DIR" -j"$(nproc)"
fi

echo "== C++ unit tests =="
"./$BUILD_DIR/ariabridge_tests" --unit

echo
echo "== integration: python mock -> C++ consumer =="
# Small frames keep the run fast; the protocol path is identical at 1408x1408.
$PY -m aria_arm64_bridge.mock_receiver \
    --zmq-endpoint "$ENDPOINT" --fps 30 --width 320 --height 240 &
MOCK_PID=$!
cleanup() {
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 1  # let the mock bind before the consumer starts polling
"./$BUILD_DIR/ariabridge_tests" --integration "$ENDPOINT" "$FRAMES"

#!/usr/bin/env bash
# Clean test of FEX TSO tuning on profile28-all: retry until the USB-NCM link is
# healthy (ping < gate), THEN measure — so the FEX-tuning effect isn't confounded
# by a degraded link (which profile28+RGB tends to produce). Compares against the
# untuned baseline (RGB ~1.4 FPS). All-on, no sudo (FEX env vars only).
set -uo pipefail
cd "$(dirname "$0")/.."

GLASSES_IP="192.168.42.129"
PING_MAX="${PING_MAX:-12}"
HANDSHAKE_S="${HANDSHAKE_S:-35}"
MEASURE_S="${MEASURE_S:-45}"
MAX_TRIES="${MAX_TRIES:-6}"
STREAMS="rgb,slam,imu"
LOG=logs/p25_clean.log

# FEX tuning: memcpy/vector TSO off (FastDDS memcpys payloads), JIT cache, multiblock.

log() { echo "[fextune] $*"; }

stop_all() {
  PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 scripts/stop_streaming.py" >/dev/null 2>&1
  local pids; pids=$(pgrep -f aria_receiver | grep -v pgrep || true)
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  sleep 3
}

log "FEX tuning: MEMCPYTSO=0 VECTORTSO=0 CACHEOBJ=1 MULTIBLOCK=1"
for try in $(seq 1 "$MAX_TRIES"); do
  stop_all
  log "=== attempt $try/$MAX_TRIES — profile25 RGB10+SLAM20 ==="
  rm -f "$LOG"
  PYTHONNOUSERSITE=1 FEXBash -c \
    "/usr/bin/python3 -u src/aria_arm64_bridge/receiver.py --interface usb --profile profile25 --streams $STREAMS" \
    > "$LOG" 2>&1 &
  sleep "$HANDSHAKE_S"

  iface=$(ip -o -4 addr show | awk '/192\.168\.42\./{print $2; exit}')
  if [ -z "$iface" ]; then
    log "  no iface (start failed). $(grep -o '(940)' "$LOG" | head -1)"
    continue
  fi
  ping_line=$(ping -c 8 -i 0.2 "$GLASSES_IP" 2>/dev/null | tail -1)
  avg=$(echo "$ping_line" | awk -F'/' '{print $5}')
  log "  ping avg=${avg}ms (gate ${PING_MAX})"

  if [ -n "$avg" ] && awk "BEGIN{exit !($avg <= $PING_MAX)}"; then
    log "  LINK HEALTHY — measuring ${MEASURE_S}s with FEX tuning"
    sleep "$MEASURE_S"
    log "  === RESULT (FEX-tuned, healthy link ${avg}ms) ==="
    grep "fps (win" "$LOG" | tail -4 | sed 's/^/    /'
    echo "    --- callback ---"
    grep "callback p50" "$LOG" | tail -1 | sed 's/^/    /'
    stop_all
    log "DONE — compare RGB above vs baseline 1.4"
    exit 0
  fi
  log "  link degraded — retry"
done
stop_all
log "GAVE UP: could not get a healthy link in $MAX_TRIES tries (profile28+RGB tends to congest the link — that is itself a finding)"

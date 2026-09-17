#!/usr/bin/env bash
# Systematic profile/stream audit for the Aria → bridge pipeline.
#
# WHY: until now profiles were tested by hand on whatever state the USB-NCM link
# happened to be in (4ms one run, 70ms the next), so results weren't comparable.
# This sweep measures each config under the SAME conditions — every run gates on
# a healthy link (ping < threshold) before trusting the numbers — and emits one
# decision table. Run unattended; it cleans up between configs.
#
# Metrics per config: per-stream FPS (rx/enq/sent), link ping (min/avg/mdev),
# callback p50/p99 ms, queue drops, DDS sample-lost per topic.
#
# Usage:  ./scripts/profile_audit.sh            # focused matrix below
#         PING_MAX=8 MEASURE_S=40 ./scripts/profile_audit.sh
set -uo pipefail

cd "$(dirname "$0")/.."

GLASSES_IP="192.168.42.129"
PING_MAX="${PING_MAX:-12}"        # ms avg above which the link is "degraded" → retry
HANDSHAKE_S="${HANDSHAKE_S:-32}"  # wait after start_streaming for the iface to come up
MEASURE_S="${MEASURE_S:-45}"      # measurement window (≈4 receiver windows of 10s)
MAX_RETRIES="${MAX_RETRIES:-3}"   # re-launch attempts to get a healthy link
OUTDIR="logs/audit_$(date +%Y%m%d_%H%M%S 2>/dev/null || echo run)"
RESULTS="$OUTDIR/results.md"

# --- Focused matrix: "profile|streams|label" -----------------------------------
# Each profile is measured with ALL the sensors it offers (rgb,slam,eye,imu,mag,
# baro) so the table has a column per stream — RGB, SLAM, gaze(eye) AND IMU — not
# just a subset. Plus a couple of SLAM-only rows to see the navigation ceiling.
# Per the official Meta table (all of these carry IMU; eye/gaze fps varies):
#   12: rgb10 slam10 eye10 · 25: rgb10 slam20 (no eye/imu) · 28: rgb30 slam30 eye60
#   15: rgb30 slam30 eye10 · 23: rgb30 slam10 eye10
# Note: IMU/mag/baro are reported in FPS too (rx events/s), so a 1kHz IMU shows high.
ALLS="rgb,slam,eye,imu,mag,baro"
CONFIGS=(
  "profile2|slam,eye,imu|profile2 — SLAM20 + gaze20 (no RGB)"
  "profile18|$ALLS|profile18 — RGB10 SLAM10 gaze10 (like 12, +audio)"
  "profile21|$ALLS|profile21 — RGB15 SLAM15 gaze30"
)

mkdir -p "$OUTDIR"

log() { echo "[audit] $*"; }

stop_all() {
  PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 scripts/stop_streaming.py" >/dev/null 2>&1
  local pids; pids=$(pgrep -f aria_receiver | grep -v pgrep || true)
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  sleep 3
}

link_iface() { ip -o -4 addr show | awk '/192\.168\.42\./{print $2; exit}'; }

# Returns "avg mdev" or empty on no link.
ping_stats() {
  ping -c 8 -i 0.2 "$GLASSES_IP" 2>/dev/null | awk -F'/' '/rtt/{print $5, $7}'
}

run_one() {
  local profile="$1" streams="$2" label="$3"
  local tag="${profile}_${streams//,/+}"
  local rxlog="$OUTDIR/${tag}.log"

  for attempt in $(seq 1 "$MAX_RETRIES"); do
    stop_all
    log "=== $profile | $streams | $label (attempt $attempt) ==="
    PYTHONNOUSERSITE=1 FEXBash -c \
      "/usr/bin/python3 -u src/aria_arm64_bridge/receiver.py --interface usb --profile $profile --streams $streams" \
      > "$rxlog" 2>&1 &
    sleep "$HANDSHAKE_S"

    local iface; iface=$(link_iface)
    if [ -z "$iface" ]; then
      log "  no iface after handshake (start failed?) — retry"
      grep -q "(940)" "$rxlog" && log "  (error 940: stuck session)"
      continue
    fi
    local ps; ps=$(ping_stats)
    local avg="${ps%% *}"
    log "  iface=$iface ping_avg=${avg}ms (max allowed ${PING_MAX})"

    # Gate: if the link is degraded, the numbers aren't comparable → retry.
    if [ -z "$avg" ] || awk "BEGIN{exit !($avg > $PING_MAX)}"; then
      log "  link degraded — discarding and retrying"
      [ "$attempt" -lt "$MAX_RETRIES" ] && continue
      log "  giving up on a healthy link after $MAX_RETRIES tries; measuring anyway (flagged)"
    fi

    log "  link OK — measuring ${MEASURE_S}s"
    sleep "$MEASURE_S"
    parse_and_record "$profile" "$streams" "$label" "$rxlog" "$ps"
    stop_all
    return 0
  done
  # All attempts failed to even stream.
  printf '| %s | %s | %s | FAILED (no stream) | — | — | — | — |\n' \
    "$profile" "$streams" "$label" >> "$RESULTS"
  stop_all
}

parse_and_record() {
  local profile="$1" streams="$2" label="$3" rxlog="$4" ps="$5"
  local ping_avg="${ps%% *}" ping_mdev="${ps##* }"

  # Average the last 3 FPS windows per stream (steady state), from "k=R rx/E enq".
  local fps_summary
  fps_summary=$(grep "fps (win" "$rxlog" | tail -3 | \
    grep -oE '[a-z0-9]+=[0-9.]+rx/[0-9.]+enq' | \
    awk -F'[=/]' '{n=$1; r=$2; sub(/rx/,"",r); cnt[n]++; sum[n]+=r}
                 END{for(k in sum) printf "%s=%.1f ", k, sum[k]/cnt[k]}')
  [ -z "$fps_summary" ] && fps_summary="(no frames)"

  # Last callback p50/p99 line (already per-stream).
  local cb; cb=$(grep "callback p50/p99" "$rxlog" | tail -1 | sed 's/.*p50\/p99: //')
  [ -z "$cb" ] && cb="—"

  # Queue drops (last fps line carries the running qdrop counter if any).
  local qdrop; qdrop=$(grep -oE "qdrop=[0-9]+" "$rxlog" | tail -1)
  [ -z "$qdrop" ] && qdrop="qdrop=0"

  # DDS sample-lost per topic (final totals).
  local lost; lost=$(grep -oE "topic [A-Za-z0-9]+" "$rxlog" | sort | uniq -c | \
    awk '{printf "%s:%s ", $3, $1}')
  [ -z "$lost" ] && lost="none"

  printf '| %s | %s | %s | %s | %s/%s | %s | %s | %s |\n' \
    "$profile" "$streams" "$label" "$fps_summary" "$ping_avg" "$ping_mdev" \
    "$cb" "$qdrop" "$lost" >> "$RESULTS"
  log "  recorded: $fps_summary | ping ${ping_avg}ms"
}

# --- Run ----------------------------------------------------------------------
{
  echo "# Profile Audit — $(date 2>/dev/null || echo)"
  echo
  echo "Gate: ping_avg ≤ ${PING_MAX}ms · measure ${MEASURE_S}s · handshake ${HANDSHAKE_S}s · retries ${MAX_RETRIES}"
  echo
  echo "| Profile | Streams | Label | FPS (rx, last 3 win avg) | Ping avg/mdev ms | Callback p50/p99 | Drops | DDS sample-lost |"
  echo "|---------|---------|-------|--------------------------|------------------|------------------|-------|-----------------|"
} > "$RESULTS"

log "results → $RESULTS"
for cfg in "${CONFIGS[@]}"; do
  IFS='|' read -r p s l <<< "$cfg"
  run_one "$p" "$s" "$l"
done
stop_all
log "DONE. Table:"
cat "$RESULTS"

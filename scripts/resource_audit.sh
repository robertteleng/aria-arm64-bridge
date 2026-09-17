#!/usr/bin/env bash
# Jetson resource audit: capture RAM / SWAP / CPU / GPU / temperature / power
# while the Aria receiver streams, so we know the real system headroom and
# whether the bridge alone is anywhere near saturating the 8 GB / GPU / thermal.
#
# Parses tegrastats (the native Jetson monitor) into a small summary: baseline
# (idle) vs under-streaming, with min/avg/max for each resource. This is the
# "system layer" of the deep audit — independent of aria-scene.
#
# Usage:  ./scripts/resource_audit.sh [profile] [streams] [seconds]
#         ./scripts/resource_audit.sh profile12 rgb,slam 40
set -uo pipefail
cd "$(dirname "$0")/.."

PROFILE="${1:-profile12}"
STREAMS="${2:-rgb,slam}"
SECONDS_RUN="${3:-40}"
OUTDIR="logs/resaudit_$(date +%Y%m%d_%H%M%S 2>/dev/null || echo run)"
mkdir -p "$OUTDIR"
TEGRA="$OUTDIR/tegrastats.log"
RXLOG="$OUTDIR/receiver.log"

log() { echo "[res] $*"; }

stop_all() {
  PYTHONNOUSERSITE=1 FEXBash -c "/usr/bin/python3 scripts/stop_streaming.py" >/dev/null 2>&1
  local pids; pids=$(pgrep -f aria_receiver | grep -v pgrep || true)
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  sleep 3
}

# Summarise one tegrastats numeric field (passed as an awk extraction) → min/avg/max.
summary() { awk '{n++; s+=$1; if(min==""||$1<min)min=$1; if($1>max)max=$1}
                 END{if(n)printf "min %.0f / avg %.0f / max %.0f", min, s/n, max; else print "n/a"}'; }

log "=== baseline (idle, 8s) ==="
tegrastats --interval 1000 > "$OUTDIR/baseline.log" 2>&1 &
TPID=$!; sleep 8; kill "$TPID" 2>/dev/null

log "=== start receiver: $PROFILE | $STREAMS ==="
stop_all
PYTHONNOUSERSITE=1 FEXBash -c \
  "/usr/bin/python3 -u src/aria_arm64_bridge/receiver.py --interface usb --profile $PROFILE --streams $STREAMS" \
  > "$RXLOG" 2>&1 &
sleep 32  # handshake

log "=== capturing tegrastats during streaming (${SECONDS_RUN}s) ==="
tegrastats --interval 1000 > "$TEGRA" 2>&1 &
TPID=$!; sleep "$SECONDS_RUN"; kill "$TPID" 2>/dev/null
stop_all

# --- Parse -------------------------------------------------------------------
# tegrastats line e.g.:
#  RAM 4585/7607MB ... SWAP 439/3804MB ... CPU [10%@729,14%@...] GR3D_FREQ 0% ... tj@47.18C ... VDD_IN 3282mW/...
parse_block() {  # $1 = label, $2 = tegrastats file
  local label="$1" f="$2"
  [ -s "$f" ] || { echo "$label: (no data)"; return; }
  local ram swap gpu temp pwr cpu
  ram=$(grep -oE "RAM [0-9]+/" "$f" | grep -oE "[0-9]+" | summary)
  swap=$(grep -oE "SWAP [0-9]+/" "$f" | grep -oE "[0-9]+" | summary)
  gpu=$(grep -oE "GR3D_FREQ [0-9]+%" "$f" | grep -oE "[0-9]+" | summary)
  temp=$(grep -oE "tj@[0-9.]+C" "$f" | grep -oE "[0-9.]+" | summary)
  pwr=$(grep -oE "VDD_IN [0-9]+mW" "$f" | grep -oE "[0-9]+" | summary)
  # avg CPU across all cores: average the per-core %@ values per line, then summarise
  cpu=$(grep -oE "CPU \[[^]]+\]" "$f" | sed -E 's/CPU \[|\]//g' | \
    awk -F',' '{t=0; for(i=1;i<=NF;i++){split($i,a,"%"); t+=a[1]} print t/NF}' | summary)
  {
    echo "### $label"
    echo "- RAM MB used:   $ram   (of 7607)"
    echo "- SWAP MB used:  $swap   (of 3804)"
    echo "- GPU GR3D %:    $gpu"
    echo "- CPU avg %:     $cpu   (across 6 cores)"
    echo "- Temp tj °C:    $temp"
    echo "- Power VDD_IN mW: $pwr"
    echo
  }
}

REPORT="$OUTDIR/report.md"
{
  echo "# Resource Audit — $PROFILE $STREAMS — $(date 2>/dev/null || echo)"
  echo
  parse_block "Baseline (idle)" "$OUTDIR/baseline.log"
  parse_block "Under streaming ($PROFILE $STREAMS)" "$TEGRA"
  echo "Receiver FPS (last 3 windows):"
  grep "fps (win" "$RXLOG" | tail -3 | sed 's/^/    /'
} > "$REPORT"

log "DONE → $REPORT"
cat "$REPORT"

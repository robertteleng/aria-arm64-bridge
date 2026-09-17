#!/usr/bin/env bash
# Tune the Aria glasses' USB-NCM (Ethernet-over-USB) link to kill the cdc_ncm
# coalescing latency (default timer up to 300ms) that causes the 27-52ms ping
# jitter and intermittent SLAM streaming collapse (Exp 008-013, 2026-06-17).
#
# The enx* interface only exists WHILE a streaming session is active, so this
# must run with the receiver already streaming. Usage:
#   1. Start the receiver (streaming makes 192.168.42.x appear)
#   2. Run this script — it finds the iface, measures ping, applies the fix,
#      measures again.
#
# Safe to re-run; settings reset when the link drops.
set -uo pipefail

GLASSES_IP="192.168.42.129"

find_iface() {
  # The Aria NCM link shows up as enx<mac> on the 192.168.42.x subnet.
  ip -o -4 addr show | awk '/192\.168\.42\./ {print $2; exit}'
}

ping_stats() {
  ping -c 10 -i 0.2 "$GLASSES_IP" 2>/dev/null | tail -1
}

IFACE="$(find_iface)"
if [[ -z "$IFACE" ]]; then
  echo "ERROR: no 192.168.42.x interface found. Is the receiver streaming?" >&2
  exit 1
fi
echo "[tune] Aria NCM interface: $IFACE"

echo "[tune] === BEFORE tuning ==="
echo "[tune] coalescing: $(ethtool -c "$IFACE" 2>/dev/null | grep -iE 'rx-usecs:|rx-frames:' | tr '\n' ' ')"
echo "[tune] ping: $(ping_stats)"

echo "[tune] applying low-latency settings..."
# Kill coalescing (the main culprit): send each frame immediately.
ethtool -C "$IFACE" rx-usecs 0 tx-usecs 0 rx-frames 1 tx-frames 1 2>/dev/null \
  && echo "[tune]   coalescing -> off" \
  || echo "[tune]   WARN: ethtool -C not supported by this driver"
# Disable offloads that batch packets and add latency.
ethtool -K "$IFACE" gro off gso off tso off 2>/dev/null \
  && echo "[tune]   gro/gso/tso -> off" \
  || echo "[tune]   WARN: ethtool -K not supported"

echo "[tune] === AFTER tuning ==="
echo "[tune] coalescing: $(ethtool -c "$IFACE" 2>/dev/null | grep -iE 'rx-usecs:|rx-frames:' | tr '\n' ' ')"
echo "[tune] ping: $(ping_stats)"
echo "[tune] done."

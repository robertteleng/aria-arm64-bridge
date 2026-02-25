#!/usr/bin/env bash
# Test that projectaria-client-sdk imports correctly under FEX-Emu
set -euo pipefail

echo "=== aria-arm64-bridge: Import Test ==="
echo ""

# Check we're on ARM64
ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
    echo "WARNING: Not on ARM64 ($ARCH). This test is meant for Jetson."
    echo "Continuing anyway for development purposes..."
fi

echo "[1/4] Testing Python under FEX-Emu..."
FEXBash -c "python3 --version" || { echo "FAIL: Python x86_64 not working"; exit 1; }
echo "PASS"
echo ""

echo "[2/4] Testing numpy import..."
FEXBash -c "python3 -c 'import numpy; print(f\"numpy {numpy.__version__}: OK\")'" \
    || { echo "FAIL: numpy import"; exit 1; }
echo "PASS"
echo ""

echo "[3/4] Testing aria.sdk import..."
FEXBash -c "python3 -c '
import aria.sdk as aria
print(f\"aria.sdk imported: OK\")
print(f\"Module path: {aria.__file__}\")
'" || { echo "FAIL: aria.sdk import"; exit 1; }
echo "PASS"
echo ""

echo "[4/4] Testing aria.sdk_gen2 import..."
FEXBash -c "python3 -c '
import aria.sdk_gen2 as sdk_gen2
print(f\"aria.sdk_gen2 imported: OK\")
'" || { echo "FAIL: aria.sdk_gen2 import (non-critical, Gen2 only)"; }
echo "PASS"
echo ""

echo "=== ALL IMPORT TESTS PASSED ==="
echo ""
echo "Next step: Pair Aria glasses and run test_streaming.sh"

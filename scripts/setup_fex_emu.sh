#!/usr/bin/env bash
# Setup FEX-Emu on Jetson Orin Nano (JetPack 6.x, ARM64)
# Reference: https://github.com/FEX-Emu/FEX
set -euo pipefail

echo "=== aria-arm64-bridge: FEX-Emu Setup ==="
echo ""

# Check we're on ARM64
ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
    echo "ERROR: This script must run on ARM64 (aarch64). Detected: $ARCH"
    exit 1
fi

echo "Platform: $(uname -m) / $(uname -r)"
echo ""

# --- Step 1: Install build dependencies ---
echo "[1/4] Installing build dependencies..."
sudo apt-get update
sudo apt-get install -y \
    git cmake ninja-build pkg-config ccache clang llvm lld \
    libglfw3-dev libepoxy-dev libsdl2-dev \
    python3 python3-setuptools \
    g++-x86-64-linux-gnu nasm \
    libstdc++-12-dev-i386-cross libstdc++-12-dev-amd64-cross

# --- Step 2: Clone and build FEX-Emu ---
echo "[2/4] Cloning FEX-Emu..."
FEX_DIR="$HOME/fex-emu"
if [ -d "$FEX_DIR" ]; then
    echo "FEX-Emu directory exists, pulling latest..."
    cd "$FEX_DIR" && git pull
else
    git clone --recurse-submodules https://github.com/FEX-Emu/FEX.git "$FEX_DIR"
fi

echo "[3/4] Building FEX-Emu..."
cd "$FEX_DIR"
mkdir -p build && cd build
CC=clang CXX=clang++ cmake -G Ninja .. \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_LTO=ON
ninja -j$(nproc)

echo "[4/4] Installing FEX-Emu..."
sudo ninja install

# Verify
echo ""
echo "=== Verification ==="
FEXInterpreter --help 2>&1 | head -3 || echo "WARNING: FEXInterpreter not found in PATH"
echo ""
echo "FEX-Emu installed. Next step: ./setup_rootfs.sh"

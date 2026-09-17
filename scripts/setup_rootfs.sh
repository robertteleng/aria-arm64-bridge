#!/usr/bin/env bash
# Create x86_64 rootfs with projectaria-client-sdk for FEX-Emu
#
# IMPORTANT: FEX-Emu must be installed BEFORE running this script.
# Uses FEXRootFSFetcher to download a pre-built Ubuntu 22.04 x86_64 rootfs,
# updates libstdc++ for SDK compatibility, then installs the Aria SDK.
#
# Lessons learned while setting this up:
# - FEXRootFSFetcher >> debootstrap (faster, more reliable)
# - SDK wheels must be installed INSIDE the rootfs (dlopen only works there)
# - pip under FEX reports platform as aarch64, must force x86_64
# - rootfs ships gcc-12 libstdc++ but SDK needs GLIBCXX_3.4.31 (gcc-13)
set -euo pipefail

echo "=== aria-arm64-bridge: x86_64 RootFS Setup ==="
echo ""

# Pinned to the version validated on device. Latest on PyPI is 2.4.0 (2026-06-10);
# it is still Linux x86_64 only, so it changes nothing about needing FEX-Emu.
# 2.4 changed the str() form of camera ids — receiver._map_camera compares the enum
# first and only falls back to substrings, so it handles both. Override to try it:
#   ARIA_SDK_VERSION=2.4.0 ./scripts/setup_rootfs.sh
ARIA_SDK_VERSION="${ARIA_SDK_VERSION:-2.2.0}"
LIBSTDCPP_URL="https://ppa.launchpadcontent.net/ubuntu-toolchain-r/test/ubuntu/pool/main/g/gcc-13/libstdc++6_13.1.0-8ubuntu1~22.04_amd64.deb"

# Check FEX-Emu is installed
if ! command -v FEXRootFSFetcher &>/dev/null; then
    echo "ERROR: FEX-Emu not found. Run ./setup_fex_emu.sh first."
    exit 1
fi

# --- Step 1: Download pre-built x86_64 rootfs ---
echo "[1/5] Downloading x86_64 rootfs via FEXRootFSFetcher..."

CURRENT_ROOTFS=$(FEXGetConfig RootFS 2>/dev/null || echo "")
if [ -n "$CURRENT_ROOTFS" ] && [ -e "$CURRENT_ROOTFS" ]; then
    echo "RootFS already configured at: $CURRENT_ROOTFS"
else
    FEXRootFSFetcher -y -x
    CURRENT_ROOTFS=$(FEXGetConfig RootFS 2>/dev/null || echo "")
fi

# Resolve rootfs path (FEXGetConfig may return relative path)
if [[ "$CURRENT_ROOTFS" != /* ]]; then
    ROOTFS_DIR="$HOME/.fex-emu/RootFS/$CURRENT_ROOTFS"
else
    ROOTFS_DIR="$CURRENT_ROOTFS"
fi
echo "RootFS at: $ROOTFS_DIR"

# --- Step 2: Verify FEX-Emu + rootfs ---
echo ""
echo "[2/5] Verifying FEX-Emu + rootfs..."
FEXBash -c "echo 'FEXBash: OK'" || { echo "FAIL: FEXBash not working"; exit 1; }
FEXBash -c "uname -m" || { echo "FAIL: cannot run uname under FEX"; exit 1; }

# --- Step 3: Update libstdc++ (rootfs has gcc-12, SDK needs gcc-13) ---
echo ""
echo "[3/5] Updating libstdc++ in rootfs for GLIBCXX_3.4.31..."

LIBSTDCPP_SO="$ROOTFS_DIR/usr/lib/x86_64-linux-gnu/libstdc++.so.6"
if strings "$LIBSTDCPP_SO" 2>/dev/null | grep -q "GLIBCXX_3.4.31"; then
    echo "libstdc++ already has GLIBCXX_3.4.31, skipping."
else
    echo "Downloading gcc-13 libstdc++6 for Ubuntu 22.04..."
    TMPDIR=$(mktemp -d)
    wget -q "$LIBSTDCPP_URL" -O "$TMPDIR/libstdcpp6.deb"
    dpkg-deb -x "$TMPDIR/libstdcpp6.deb" "$TMPDIR/extracted"
    sudo cp "$TMPDIR/extracted/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.32" \
        "$ROOTFS_DIR/usr/lib/x86_64-linux-gnu/"
    sudo ln -sf libstdc++.so.6.0.32 "$LIBSTDCPP_SO"
    rm -rf "$TMPDIR"
    echo "libstdc++ updated to gcc-13 (GLIBCXX_3.4.31+)."
fi

# --- Step 4: Install pip in rootfs ---
echo ""
echo "[4/5] Setting up pip in rootfs..."

if ! FEXBash -c "python3 -m pip --version" 2>/dev/null; then
    echo "Installing pip via get-pip.py..."
    FEXBash -c "curl -sS https://bootstrap.pypa.io/get-pip.py | python3"
fi
FEXBash -c "python3 -m pip --version"

# --- Step 5: Install Aria SDK inside rootfs ---
echo ""
echo "[5/5] Installing projectaria-client-sdk $ARIA_SDK_VERSION in rootfs..."

# SDK wheels are x86_64 only — pip under FEX sees aarch64, so we force platform
# Must use --target pointing to the FULL rootfs path (not /usr/local/...)
# Must use sudo because rootfs dirs are owned by root
SITE_PACKAGES="$ROOTFS_DIR/usr/local/lib/python3.10/dist-packages"

if [ -d "$SITE_PACKAGES/aria" ]; then
    echo "Aria SDK already installed, skipping. Delete $SITE_PACKAGES/aria to reinstall."
else
    # Only the CLIENT SDK goes in the rootfs — it is the closed x86_64 binary that
    # has to be emulated. projectaria-tools is installed natively below (since
    # 2.2.0 it ships linux aarch64 wheels), so there is no reason to emulate it.
    sudo pip install \
        --platform manylinux2014_x86_64 \
        --only-binary=:all: \
        --no-deps \
        --target "$SITE_PACKAGES" \
        "projectaria-client-sdk==$ARIA_SDK_VERSION"
fi

# Install normal dependencies (numpy, pyzmq) via FEXBash
FEXBash -c "python3 -m pip install numpy pyzmq 2>/dev/null || true"

# --- Step 5b: projectaria-tools, NATIVE ARM64 (no emulation) ---
# projectaria-tools 2.2.0 (2026-08-13) publishes manylinux aarch64 wheels, so the
# offline side (VRS reading, calibration, MPS) is a plain pip install on Jetson.
# Before that it needed a source build with Ocean NEON patches (see Exp 002 in the
# diary) — that is obsolete. NOTE: the NVDEC hardware H.265 path added in 2.2.0 is
# Linux x86_64 only, so decoding on Jetson stays on the CPU.
echo ""
echo "[5b/5] Installing projectaria-tools natively (ARM64, outside the rootfs)..."
if python3 -c "import projectaria_tools" 2>/dev/null; then
    echo "projectaria-tools already present natively, skipping."
else
    pip install "projectaria-tools>=2.2.0" || echo "WARNING: native projectaria-tools install failed (optional — only needed for offline VRS/calibration work)"
fi

echo ""
echo "=== Verification ==="
FEXBash -c "python3 -c 'import aria.sdk; print(\"aria.sdk: OK (emulated x86_64)\")'"
FEXBash -c "python3 -c 'import aria.sdk_gen2; print(\"aria.sdk_gen2: OK (emulated x86_64)\")'"
python3 -c 'from projectaria_tools.core.sensor_data import ImageDataRecord; print("projectaria_tools: OK (native aarch64)")' 2>/dev/null \
    || echo "projectaria_tools: not installed natively (optional)"
echo ""
echo "=== ALL CHECKS PASSED ==="
echo "Next step: ./test_import.sh or ./test_streaming.sh (with Aria glasses)"

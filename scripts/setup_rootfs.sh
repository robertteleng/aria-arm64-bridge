#!/usr/bin/env bash
# Create x86_64 rootfs with Python 3.12 + projectaria-client-sdk
# This rootfs is used by FEX-Emu to run x86_64 binaries
#
# IMPORTANT: FEX-Emu must be installed BEFORE running this script.
set -euo pipefail

echo "=== aria-arm64-bridge: x86_64 RootFS Setup ==="
echo ""

ROOTFS_DIR="${ROOTFS_DIR:-$HOME/.fex-emu/rootfs}"
ARIA_SDK_VERSION="${ARIA_SDK_VERSION:-2.2.0}"

# Check FEX-Emu is installed
if ! command -v FEXInterpreter &>/dev/null; then
    echo "ERROR: FEX-Emu not found. Run ./setup_fex_emu.sh first."
    exit 1
fi

# --- Step 1: Configure FEX-Emu rootfs path (BEFORE anything else) ---
echo "[1/5] Configuring FEX-Emu rootfs path..."
mkdir -p "$HOME/.fex-emu"
mkdir -p "$ROOTFS_DIR"
cat > "$HOME/.fex-emu/Config.json" << EOF
{
    "Config": {
        "RootFS": "$ROOTFS_DIR"
    }
}
EOF
echo "FEX config: RootFS=$ROOTFS_DIR"

# --- Step 2: Create x86_64 rootfs via debootstrap ---
echo "[2/5] Setting up x86_64 rootfs at $ROOTFS_DIR..."

if [ -f "$ROOTFS_DIR/bin/ls" ]; then
    echo "RootFS already exists, skipping debootstrap."
else
    echo "Installing debootstrap..."
    sudo apt-get install -y debootstrap

    # First stage: extract packages (cross-arch, no emulation needed)
    echo "Debootstrap first stage (extracting amd64 packages)..."
    sudo debootstrap --foreign --arch=amd64 jammy "$ROOTFS_DIR" http://archive.ubuntu.com/ubuntu

    # Second stage: run package scripts via FEX-Emu
    echo "Debootstrap second stage (configuring packages via FEX-Emu)..."
    sudo env "FEX_ROOTFS=$ROOTFS_DIR" FEXInterpreter "$ROOTFS_DIR/debootstrap/debootstrap" \
        --second-stage --second-stage-target "$ROOTFS_DIR"
fi

# --- Step 3: Mount filesystems for package installation ---
echo "[3/5] Preparing rootfs environment..."

cleanup() {
    sudo umount "$ROOTFS_DIR/proc" 2>/dev/null || true
    sudo umount "$ROOTFS_DIR/sys" 2>/dev/null || true
    sudo umount "$ROOTFS_DIR/dev" 2>/dev/null || true
}
trap cleanup EXIT

sudo mount --bind /proc "$ROOTFS_DIR/proc" 2>/dev/null || true
sudo mount --bind /sys "$ROOTFS_DIR/sys" 2>/dev/null || true
sudo mount --bind /dev "$ROOTFS_DIR/dev" 2>/dev/null || true
sudo cp /etc/resolv.conf "$ROOTFS_DIR/etc/resolv.conf"

# --- Step 4: Install Python 3.12 inside rootfs ---
echo "[4/5] Installing Python 3.12 in rootfs..."

# Use FEXBash to run commands that see the rootfs
FEXBash -c "
    sudo chroot $ROOTFS_DIR /bin/bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y software-properties-common ca-certificates
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update
        apt-get install -y python3.12 python3.12-venv python3.12-dev python3-pip curl
        python3.12 -m ensurepip --upgrade
    '
"

# --- Step 5: Install Aria SDK in rootfs ---
echo "[5/5] Installing projectaria-client-sdk $ARIA_SDK_VERSION..."
FEXBash -c "python3.12 -m pip install \
    projectaria-client-sdk==$ARIA_SDK_VERSION \
    pyzmq \
    numpy"

echo ""
echo "=== Verification ==="
echo "Testing Python under FEX-Emu..."
FEXBash -c "python3.12 -c 'import sys; print(f\"Python {sys.version} under FEX: OK\")'"
echo ""
echo "RootFS ready at: $ROOTFS_DIR"
echo "Next step: ./test_import.sh"

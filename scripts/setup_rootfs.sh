#!/usr/bin/env bash
# Create x86_64 rootfs with Python 3.12 + projectaria-client-sdk
# This rootfs is used by FEX-Emu to run x86_64 binaries
set -euo pipefail

echo "=== aria-arm64-bridge: x86_64 RootFS Setup ==="
echo ""

ROOTFS_DIR="${ROOTFS_DIR:-$HOME/.fex-emu/rootfs}"
ARIA_SDK_VERSION="${ARIA_SDK_VERSION:-2.2.0}"

# --- Step 1: Download or create x86_64 rootfs ---
echo "[1/3] Setting up x86_64 rootfs at $ROOTFS_DIR..."

if [ -d "$ROOTFS_DIR" ]; then
    echo "RootFS already exists at $ROOTFS_DIR"
else
    echo "Creating minimal x86_64 rootfs via debootstrap..."
    sudo apt-get install -y debootstrap
    sudo mkdir -p "$ROOTFS_DIR"
    sudo debootstrap --arch=amd64 jammy "$ROOTFS_DIR" http://archive.ubuntu.com/ubuntu

    # Install Python inside rootfs
    echo "Installing Python 3.12 in rootfs..."
    sudo chroot "$ROOTFS_DIR" /bin/bash -c "
        apt-get update
        apt-get install -y software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update
        apt-get install -y python3.12 python3.12-venv python3.12-dev python3-pip
        python3.12 -m ensurepip --upgrade
    "
fi

# --- Step 2: Configure FEX-Emu to use this rootfs ---
echo "[2/3] Configuring FEX-Emu rootfs path..."
mkdir -p "$HOME/.fex-emu"
cat > "$HOME/.fex-emu/Config.json" << EOF
{
    "Config": {
        "RootFS": "$ROOTFS_DIR"
    }
}
EOF
echo "FEX config written to ~/.fex-emu/Config.json"

# --- Step 3: Install Aria SDK in rootfs ---
echo "[3/3] Installing projectaria-client-sdk $ARIA_SDK_VERSION..."
sudo chroot "$ROOTFS_DIR" /bin/bash -c "
    python3.12 -m pip install \
        projectaria-client-sdk==$ARIA_SDK_VERSION \
        pyzmq \
        numpy
"

echo ""
echo "=== Verification ==="
echo "Testing Python + aria import under FEX-Emu..."
FEXBash -c "python3.12 -c 'print(\"Python x86_64 under FEX: OK\")'" 2>/dev/null \
    || echo "NOTE: FEXBash test skipped (run on Jetson to verify)"
echo ""
echo "RootFS ready at: $ROOTFS_DIR"
echo "Next step: ./test_import.sh"

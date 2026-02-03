#!/bin/bash
# ScoutCam Installation Script
# Run this on the Raspberry Pi after copying the deploy folder
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEDIAMTX_VERSION="1.16.0"
MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_arm64.tar.gz"

echo "========================================"
echo "  ScoutCam Installation Script"
echo "========================================"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (use sudo)" >&2
    exit 1
fi

# Check architecture
ARCH=$(uname -m)
if [[ "$ARCH" != "aarch64" ]]; then
    echo "Warning: Expected aarch64 (64-bit ARM), got $ARCH"
    echo "This script is designed for Raspberry Pi OS 64-bit"
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "Step 1/7: Updating package lists..."
apt-get update

echo ""
echo "Step 2/7: Installing required packages..."
apt-get install -y \
    rpicam-apps \
    ffmpeg \
    curl \
    jq

echo ""
echo "Step 3/7: Downloading MediaMTX v${MEDIAMTX_VERSION}..."
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

# Download with progress, retries, and proper error detection
echo "Downloading from: $MEDIAMTX_URL"
if ! curl --fail --location --progress-bar --retry 3 --retry-delay 2 \
    -o mediamtx.tar.gz "$MEDIAMTX_URL"; then
    echo "Error: Failed to download MediaMTX" >&2
    echo "Try downloading manually from: $MEDIAMTX_URL" >&2
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Verify download succeeded (file should be ~25MB)
if [[ ! -f mediamtx.tar.gz ]] || [[ $(stat -c%s mediamtx.tar.gz 2>/dev/null || stat -f%z mediamtx.tar.gz 2>/dev/null) -lt 1000000 ]]; then
    echo "Error: MediaMTX download failed or incomplete" >&2
    echo "Try downloading manually from: $MEDIAMTX_URL" >&2
    rm -rf "$TEMP_DIR"
    exit 1
fi

echo "Download complete, extracting..."

# Extract with error handling
if ! tar -xzf mediamtx.tar.gz; then
    echo "Error: Failed to extract MediaMTX archive" >&2
    rm -rf "$TEMP_DIR"
    exit 1
fi

if [[ ! -f mediamtx ]]; then
    echo "Error: mediamtx binary not found after extraction" >&2
    rm -rf "$TEMP_DIR"
    exit 1
fi

mv mediamtx /usr/local/bin/
chmod +x /usr/local/bin/mediamtx
rm -rf "$TEMP_DIR"
echo "MediaMTX installed to /usr/local/bin/mediamtx"

echo ""
echo "Step 4/7: Installing configuration files..."
mkdir -p /etc/scoutcam/profiles
cp -v "$SCRIPT_DIR/etc/scoutcam/config.env" /etc/scoutcam/
cp -v "$SCRIPT_DIR/etc/scoutcam/mediamtx.yml" /etc/scoutcam/
cp -v "$SCRIPT_DIR/etc/scoutcam/profiles/"*.env /etc/scoutcam/profiles/
chmod 644 /etc/scoutcam/*.env /etc/scoutcam/*.yml
chmod 644 /etc/scoutcam/profiles/*.env

echo ""
echo "Step 5/7: Installing CLI tool..."
cp -v "$SCRIPT_DIR/bin/scoutcam" /usr/local/bin/
chmod +x /usr/local/bin/scoutcam

echo ""
echo "Step 6/7: Installing systemd services..."
cp -v "$SCRIPT_DIR/systemd/"*.service /etc/systemd/system/
cp -v "$SCRIPT_DIR/systemd/"*.mount /etc/systemd/system/
chmod 644 /etc/systemd/system/scoutcam-*.service
chmod 644 /etc/systemd/system/mnt-usb.mount

# Install udev rules
cp -v "$SCRIPT_DIR/udev/"*.rules /etc/udev/rules.d/
chmod 644 /etc/udev/rules.d/99-scoutcam-usb.rules

# Create mount point
mkdir -p /mnt/usb

# Reload systemd and udev
systemctl daemon-reload
udevadm control --reload-rules
udevadm trigger

echo ""
echo "Step 7/7: Enabling services..."
systemctl enable scoutcam-stream.service
systemctl enable scoutcam-record.service
# Note: mnt-usb.mount is triggered by udev, not enabled directly

echo ""
echo "========================================"
echo "  Installation Complete!"
echo "========================================"
echo ""
echo "Quick start:"
echo "  scoutcam start     - Start streaming"
echo "  scoutcam status    - Check status"
echo "  scoutcam profile   - Change profile"
echo ""
echo "Stream URL will be:"
echo "  rtsp://$(hostname -I | awk '{print $1}'):8554/cam"
echo ""
echo "For local recording, format a USB drive with:"
echo "  sudo mkfs.ext4 -L SCOUTCAM /dev/sdX1"
echo ""
echo "Then plug it in - recording will start automatically."
echo ""

# Check camera
echo "Checking camera..."
if rpicam-hello --list-cameras 2>&1 | grep -q "imx477"; then
    echo "OK: Pi HQ Camera (IMX477) detected"
else
    echo "WARNING: Pi HQ Camera (IMX477) not detected"
    echo "Make sure the camera is connected and the ribbon cable is seated properly"
fi

echo ""
echo "Ready! Run 'scoutcam start' to begin streaming."

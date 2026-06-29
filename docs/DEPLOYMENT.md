# ScoutCam Deployment Guide

Deploy one or more Pi cameras for FRC competition use. Each Pi runs independently
and streams via RTSP over a PoE switch — no internet required.

## Prerequisites

| Item | Notes |
|------|-------|
| Raspberry Pi 4B | One per camera |
| IMX477 HQ Camera + ribbon cable | Verify cable orientation before closing case |
| PoE HAT (e.g., Waveshare PoE HAT-F) | Powers Pi over Ethernet — no separate PSU needed |
| microSD card (16 GB+) | Class 10 / A1 minimum |
| TP-Link SG1005P (or any PoE switch) | Unmanaged is fine — mDNS uses L2 multicast |
| Ethernet cables | One per Pi + one for the laptop |
| Laptop with Python 3.10+ and OpenCV | Receives all streams |

## Step 1: Flash SD Card

1. Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi OS Lite (64-bit)** — no desktop needed.
3. Click the **gear icon** (OS Customisation) before writing:
   - **Hostname**: `scoutcam-blue` (use hyphens, not underscores — see naming table below)
   - **Enable SSH**: Yes, password authentication
   - **Username**: `pi`
   - **Password**: `2491`
   - **WiFi**: Optional — useful for initial setup at home, not needed at competition
   - **Locale**: Set your timezone and keyboard layout
4. Write to the SD card.

## Step 2: First Boot

1. Insert SD card into the Pi, connect Ethernet (or WiFi if configured), and power on.
2. Wait ~60 seconds for first boot to complete.
3. Verify connectivity:
   ```bash
   ping scoutcam-blue.local
   ```
   If mDNS doesn't resolve, check your network or try the Pi's IP from your router.
4. SSH in:
   ```bash
   ssh pi@scoutcam-blue.local
   # Password: 2491
   ```

## Step 3: Deploy and Install

From your **development machine** (where this repo is cloned):

```bash
# Copy the deploy folder to the Pi
scp -r deploy/ pi@scoutcam-blue.local:/tmp/scoutcam-deploy/

# SSH in and run the installer
ssh pi@scoutcam-blue.local
cd /tmp/scoutcam-deploy
sudo bash install.sh
```

The installer will:
- Install required packages (rpicam-apps, ffmpeg, curl)
- Download and install MediaMTX
- Copy config files to `/etc/scoutcam/`
- Install and enable the systemd service
- Check for the IMX477 camera
- Validate the hostname

## Step 4: Verify Camera and Stream

On the Pi:
```bash
# Check camera is detected
rpicam-hello --list-cameras

# Start the stream
scoutcam start

# Check status
scoutcam status
```

From your laptop:
```bash
# Quick test with ffprobe
ffprobe -rtsp_transport tcp rtsp://scoutcam-blue.local:8554/cam

# Or use the viewer
python receiver/multicam_viewer.py --hosts scoutcam-blue.local --no-discover
```

## Step 5: Configure Static IPs (Competition Setup)

At competition there's no DHCP server — the PoE switch is just a dumb switch.
Each device needs a static IP.

### On each Pi

SSH in and edit `/etc/dhcpcd.conf`:
```bash
sudo nano /etc/dhcpcd.conf
```

Add at the bottom:
```
interface eth0
static ip_address=10.0.0.11/24
```

Use the IP from the naming table below. Then reboot:
```bash
sudo reboot
```

### On the Laptop

Set the Ethernet adapter to a static IP:
- **IP**: `10.0.0.1`
- **Subnet mask**: `255.255.255.0`
- **Gateway**: (leave blank)
- **DNS**: (leave blank)

### Verify

After all devices have static IPs:
```bash
ping 10.0.0.11    # scoutcam-blue
ping 10.0.0.12    # scoutcam-red
```

mDNS (`.local`) still works alongside static IPs — avahi-daemon handles both.

## Naming Convention

| Hostname | Static IP | Color | Use Case |
|----------|-----------|-------|----------|
| `scoutcam-blue` | `10.0.0.11` | Blue | Primary / blue alliance (Pi 4B, 1 cam) |
| `scoutcam-red` | `10.0.0.12` | Red | Red alliance (Pi 4B, 1 cam) |
| `scoutcam-pi5` | `10.0.0.13` | — | Pi 5 dual camera: two IMX708 on `/cam0` + `/cam1` |
| Laptop | `10.0.0.1` | — | Viewer |

> **Pi 5 dual-camera node (`10.0.0.13`):** one MediaMTX instance serves two paths,
> `rtsp://10.0.0.13:8554/cam0` and `/cam1`, selected via `rpiCameraCamID` 0/1. The Pi 5
> has **no hardware H.264 encoder**, so both streams are software-encoded (`rpiCameraCodec:
> auto` → OpenH264) — set `CAMERA_COUNT=2` in `config.env` and view with
> `view_dual.bat 10.0.0.13` (or `multicam_viewer.py --hosts 10.0.0.13 --paths /cam0 /cam1`).
> Verified on Raspberry Pi OS (Debian 13 trixie, aarch64), MediaMTX v1.16.0, login user `mfg`.

### Pi 5 dual-camera throughput (measured)

Two IMX708 Camera Module 3 streaming simultaneously, delivered fps measured at the PC,
CPU = total across the Pi 5's 4 cores. Fixed 6 ms shutter (so exposure never caps fps):

| Profile (per camera) | Delivered fps (cam0 / cam1) | Pi 5 CPU | Temp | Verdict |
|----------------------|------------------------------|----------|------|---------|
| 720p30  | 28 / 28 | 28% | 50 °C | trivial |
| **720p60** (default) | **57 / 57** | **42%** | 54 °C | **recommended for tracking** |
| 1080p30 | 30 / 30 | 49% | 55 °C | comfortable, max detail |
| 1080p50 | 50 / 50 | 79% | 59 °C | works, near the CPU limit |
| 1080p60 | 27 / 51 | 64% | 59 °C | unstable — one camera starves; avoid |

The encoder, not the camera, is the only ceiling: the IMX708 raw-captures 30/60/120 fps
fine, and software OpenH264 leaves >50% CPU idle through 1080p30. The camera index→MIPI
port mapping was stable across a reboot (`cam0`→`i2c@88000`, `cam1`→`i2c@80000`).

## Quick-Clone Checklist (5 minutes per Pi)

For each additional Pi after you've done the first one:

1. Flash SD with Pi Imager — set hostname (e.g., `scoutcam-red`), SSH, user `pi`, password `2491`
2. Boot, wait 60s, then: `scp -r deploy/ pi@scoutcam-red.local:/tmp/scoutcam-deploy/`
3. SSH in: `ssh pi@scoutcam-red.local`
4. Install: `cd /tmp/scoutcam-deploy && sudo bash install.sh`
5. Set static IP in `/etc/dhcpcd.conf` (see table above)
6. Reboot: `sudo reboot`
7. Verify: `ping 10.0.0.12` and `ffprobe rtsp://scoutcam-red.local:8554/cam`

## Troubleshooting

### Camera not detected
```
WARNING: Pi HQ Camera (IMX477) not detected
```
- Check the ribbon cable is fully seated at both ends (camera and Pi CSI port)
- Make sure the cable isn't backwards — the contacts face the PCB on the Pi
- Run `rpicam-hello --list-cameras` — if no cameras listed, it's a hardware issue
- Try `sudo reboot` after reseating the cable

### mDNS (.local) not resolving
- **Windows**: Install [Bonjour Print Services](https://support.apple.com/kb/DL999) if not already present (iTunes installs it too)
- **Linux**: Install `avahi-utils`: `sudo apt install avahi-utils`
- Check the Pi is on the same network segment as the laptop
- Fall back to the static IP: `ping 10.0.0.11`

### Encoder errors
```
encoder_create(): unable to activate output stream
```
- You're exceeding the Pi 4B hardware encoder limits
- Use `720p60` (default) or `1080p30` — these are the verified-safe profiles
- Do NOT use 1080p50 or 720p120 — the V4L2 encoder can't keep up

### Stream connects but no video
- Check `scoutcam status` on the Pi — make sure the service is running
- Try `scoutcam restart`
- Check logs: `scoutcam logs 100`
- Verify the RTSP port isn't blocked: `ss -tln | grep 8554`

### Multiple Pis with same hostname
- Each Pi MUST have a unique hostname — mDNS will conflict otherwise
- The installer warns if the hostname is still `raspberrypi`
- Fix with: `sudo hostnamectl set-hostname scoutcam-blue && sudo reboot`

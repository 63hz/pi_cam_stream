# ScoutCam - Pi Camera Streaming System

RTSP streaming system for Raspberry Pi 4B with Pi HQ Camera (IMX477), designed for FRC game piece tracking.

## Features

- **RTSP streaming** - Works with ffmpeg, VLC, OBS, OpenCV
- **Multiple profiles** - 1080p50, 720p120, 720p60
- **Local recording** - Auto-records to USB SSD
- **Low latency** - Hardware H.264 encoding
- **Auto-start** - Streaming starts on boot

## Quick Start

### 1. Prepare the Pi

Flash Raspberry Pi OS Lite 64-bit to SD card, enable SSH, set hostname to `scoutcam`.

### 2. Deploy

From Windows PowerShell:
```powershell
scp -r deploy/* pi@scoutcam.local:~/scoutcam-setup/
ssh pi@scoutcam.local
```

On the Pi:
```bash
cd ~/scoutcam-setup
sudo ./install.sh
```

### 3. Start Streaming

```bash
scoutcam start
```

### 4. View Stream (Windows)

Double-click `receiver/view_stream.bat` or run:
```cmd
ffplay -fflags nobuffer rtsp://scoutcam.local:8554/cam
```

## Profiles

| Profile | Resolution | FPS | Bitrate | Use Case |
|---------|-----------|-----|---------|----------|
| 1080p50 | 2028x1080 | 50 | 8 Mbps | Default, wide HD |
| 1080p40 | 2028x1080 | 40 | 10 Mbps | Low-light (longer exposure) |
| 720p120 | 1280x720 | 120 | 6 Mbps | Fast motion tracking |
| 720p60 | 1280x720 | 60 | 4 Mbps | Balanced, lower bandwidth |

Switch profile:
```bash
scoutcam profile 720p120
```

## CLI Commands

```
scoutcam start      - Start streaming
scoutcam stop       - Stop streaming
scoutcam status     - Show status
scoutcam profile X  - Switch profile
scoutcam health     - Quick health check
scoutcam logs       - View logs
scoutcam config     - Show configuration
```

## Local Recording

For automatic recording to USB:

1. Format drive with label "SCOUTCAM":
   ```bash
   sudo mkfs.ext4 -L SCOUTCAM /dev/sdX1
   ```

2. Plug in drive - recording starts automatically

3. Recordings saved to `/mnt/usb/recordings/`

## Windows Receiver

### View Stream
```cmd
receiver\view_stream.bat [PI_IP]
```

### Record Stream
```cmd
receiver\record_stream.bat [PI_IP]
```

### OpenCV/Python
```cmd
pip install opencv-python numpy
python receiver\opencv_example.py [PI_IP]
```

## Configuration

Edit `/etc/scoutcam/config.env` on the Pi:

```bash
PROFILE=1080p50        # Active profile
RTSP_PORT=8554         # RTSP server port
RECORDING_ENABLED=true # Enable local recording
```

## Troubleshooting

**Stream won't start:**
```bash
scoutcam status
journalctl -u scoutcam-stream -f
```

**Camera not detected:**
```bash
rpicam-hello --list-cameras
```

**Can't connect from Windows:**
- Verify Pi IP: `hostname -I`
- Check firewall allows port 8554
- Test: `ping scoutcam.local`

**VLC shows 404 or "no stream available":**
Check if all processes are running:
```bash
ps aux | grep -E 'rpicam|ffmpeg|mediamtx'
```
You should see 3 processes: mediamtx, rpicam-vid, and ffmpeg. If only mediamtx is running, the pipeline failed - check logs with `journalctl -u scoutcam-stream -n 100`.

**"encoder_create(): unable to activate output stream" errors:**
This is MediaMTX's built-in rpiCamera failing. Our setup bypasses this by using rpicam-vid + ffmpeg instead. If you see this error, the config is wrong - ensure `/etc/scoutcam/mediamtx.yml` has `source: publisher` (not `source: rpiCamera`), then restart: `scoutcam restart`

**VLC uses wrong RTSP dialect:**
If VLC logs show "only real/helix rtsp servers supported", force TCP transport:
```cmd
vlc rtsp://192.168.0.232:8554/cam --rtsp-tcp
```

## Architecture

```
[Pi HQ Camera] → [rpicam-vid] → pipe → [ffmpeg] → RTSP publish → [MediaMTX] → [Clients]
                   H.264 encode           wrap for RTSP            RTSP server
```

**Why this architecture?**
MediaMTX has a built-in `rpiCamera` source that *should* work directly, but it fails on Pi 4B with IMX477 with "encoder_create(): unable to activate output stream". The rpicam-vid + ffmpeg pipeline bypasses MediaMTX's encoder entirely - rpicam-vid does the H.264 encoding via the Pi's hardware encoder, ffmpeg wraps the stream for RTSP, and MediaMTX just acts as a simple RTSP server in "publisher" mode.

## File Structure

```
/etc/scoutcam/
├── config.env           # Main configuration
├── mediamtx.yml         # RTSP server config
└── profiles/            # Resolution profiles

/usr/local/bin/
└── scoutcam             # CLI tool

/mnt/usb/
└── recordings/          # MP4 recordings
```

## Hardware Requirements

- Raspberry Pi 4B (4GB+ recommended)
- Pi HQ Camera (IMX477)
- MicroSD card (16GB+)
- USB SSD for recording (optional)
- Ethernet or WiFi connection

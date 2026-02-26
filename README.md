# ScoutCam - Pi Camera Streaming System

RTSP streaming system for Raspberry Pi 4B with Pi HQ Camera (IMX477), designed for FRC game piece tracking.

## Features

- **RTSP streaming** - Works with ffmpeg, VLC, OBS, OpenCV
- **50fps delivered** - 720p60 profile with threaded capture hits 50fps with detection on
- **Multiple profiles** - 720p60 (default), 1080p30, plus legacy profiles
- **Local recording** - Auto-records to USB SSD
- **Single-process streaming** - MediaMTX rpiCamera source (no rpicam-vid/ffmpeg needed)
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

```powershell
ffplay -fflags nobuffer rtsp://scoutcam.local:8554/cam
```

Or use the debug harness with detection overlay:
```powershell
pip install opencv-python numpy
python receiver/rtsp_ingest_debug.py --detect
```

## Profiles

| Profile | Resolution | FPS | Bitrate | Use Case |
|---------|-----------|-----|---------|----------|
| **720p60** | 1280x720 | 60 | 6 Mbps | Default - game piece tracking (delivers 50fps to app) |
| **1080p30** | 1920x1080 | 30 | 8 Mbps | Maximum detail, robot identification |
| 1080p50 | 2028x1080 | 50 | 8 Mbps | Legacy - Pi 4B encoder can't sustain 50fps |
| 1080p40 | 2028x1080 | 40 | 10 Mbps | Legacy - Pi 4B encoder can't sustain 40fps |
| 720p120 | 1280x720 | 120 | 6 Mbps | Legacy - Pi 4B encoder can't sustain 120fps |

Switch profile:
```bash
scoutcam profile 720p60
```

## CLI Commands

```
scoutcam start      - Start streaming
scoutcam stop       - Stop streaming
scoutcam restart    - Restart streaming
scoutcam status     - Show status
scoutcam profile X  - Switch profile
scoutcam profiles   - List available profiles
scoutcam health     - Quick health check
scoutcam logs       - View logs
scoutcam config     - Show configuration
```

## Debug Harness (receiver/rtsp_ingest_debug.py)

The main receiver with timing diagnostics, threaded capture, and detection overlay:

```powershell
# Default: threaded capture + GUI
python receiver/rtsp_ingest_debug.py --detect

# Probe mode: test what the Pi actually delivers (no OpenCV)
python receiver/rtsp_ingest_debug.py --probe

# Headless benchmarking
python receiver/rtsp_ingest_debug.py --headless --detect --max-seconds 30

# With NVIDIA GPU decode (for higher resolutions)
python receiver/rtsp_ingest_debug.py --hwaccel cuda --detect

# Sequential mode for A/B comparison
python receiver/rtsp_ingest_debug.py --no-threaded --detect
```

### Console output explained
```
src_fps= 50.0  app_fps= 50.0  read=20.0ms  proc=2.4ms  gui=2.6ms  total=5.5ms  grab=730 drop=19(3%)
```
- `src_fps` - Rate frames arrive from Pi (capture thread)
- `app_fps` - Rate the main loop processes frames
- `read` - Per-frame decode time (in threaded mode, from capture thread)
- `proc` - Detection processing time
- `gui` - Display + waitKey time
- `grab/drop` - Total frames grabbed and dropped by threaded capture

## Architecture

```
[Pi HQ Camera] -> [MediaMTX rpiCamera source] -> RTSP :8554/cam -> [Windows PC]
                  (libcamera + V4L2 H.264 hw encode)               (OpenCV + ThreadedCapture)
```

MediaMTX v1.16.0 talks directly to the camera via libcamera and uses the Pi's V4L2 hardware H.264 encoder. Single process, no pipes, honest timestamps.

### Why not rpicam-vid + ffmpeg?

The previous architecture piped rpicam-vid -> ffmpeg -> MediaMTX (publisher mode). This added ~11fps overhead from process/pipe switching and used `ffmpeg -fflags +genpts` which generated fake timestamps that masked the real frame rate. The stream claimed 50fps but only delivered 33fps.

## Pi 4B Hardware Encoder Limits

The BCM2835 V4L2 H.264 hardware encoder has these verified limits:

| Resolution | Max Honest FPS | Notes |
|-----------|---------------|-------|
| 1920x1080 | 30fps | MediaMTX rpiCamera, V4L2 limit |
| 1280x720 | 60fps | MediaMTX rpiCamera, V4L2 limit |

These are hard limits - changing H.264 profile (high/main/baseline), level, or bitrate does not help.
The IMX477 sensor itself can do ~47fps at 1080p, but the encoder is the bottleneck.

## Local Recording

For automatic recording to USB:

1. Format drive with label "SCOUTCAM":
   ```bash
   sudo mkfs.ext4 -L SCOUTCAM /dev/sdX1
   ```

2. Plug in drive - recording starts automatically

3. Recordings saved to `/mnt/usb/recordings/`

## Configuration

Edit `/etc/scoutcam/config.env` on the Pi:

```bash
PROFILE=720p60         # Active profile
RTSP_PORT=8554         # RTSP server port
RECORDING_ENABLED=true # Enable local recording
```

## File Structure

```
deploy/
├── bin/scoutcam                    # CLI tool (generates mediamtx.yml, runs MediaMTX)
├── etc/scoutcam/
│   ├── config.env                  # Main configuration
│   └── profiles/
│       ├── 720p60.env              # Recommended for tracking
│       ├── 1080p30.env             # Recommended for detail
│       ├── 1080p50.env             # Legacy (can't hit target)
│       ├── 1080p40.env             # Legacy
│       └── 720p120.env             # Legacy
├── systemd/
│   ├── scoutcam-stream.service
│   └── scoutcam-record.service
└── ...

receiver/
├── rtsp_ingest_debug.py            # Main debug harness
├── view_stream.bat
└── record_stream.bat
```

On the Pi after deployment:
```
/etc/scoutcam/           # Configuration + profiles
/usr/local/bin/scoutcam  # CLI tool
/mnt/usb/recordings/     # MP4 recordings
```

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed diagnosis steps, or [TOOLBOX.md](TOOLBOX.md) for a quick-reference command cheat sheet.

**Stream won't start:**
```bash
scoutcam status
journalctl -u scoutcam-stream -f
```

**"encoder_create(): unable to activate output stream":**
You're requesting a resolution/fps combo above the V4L2 encoder limit. Use `720p60` or `1080p30`.

**FPS lower than expected:**
```powershell
python receiver/rtsp_ingest_debug.py --probe
```

**Can't connect from Windows:**
- Verify Pi IP: `hostname -I` (on Pi)
- Check firewall allows port 8554
- Test: `ping scoutcam.local`

## Hardware Requirements

- Raspberry Pi 4B (4GB+ recommended)
- Pi HQ Camera (IMX477)
- MicroSD card (16GB+)
- USB SSD for recording (optional)
- Ethernet connection (recommended) or WiFi

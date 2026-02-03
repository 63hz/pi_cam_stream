# ScoutCam Handoff Document

## Project Overview
Building an RTSP streaming system for Raspberry Pi 4B with Pi HQ Camera (IMX477) for FRC game piece tracking.

## Current Architecture
```
[Pi HQ Camera] → [MediaMTX with rpiCamera source] → [RTSP stream]
```

MediaMTX has built-in Raspberry Pi camera support (`source: rpiCamera`) which eliminates the need for rpicam-vid or ffmpeg in the pipeline.

## Hardware Confirmed Working
- Raspberry Pi 4B running Raspberry Pi OS Lite 64-bit (Bookworm)
- Pi HQ Camera (IMX477) detected and working
- Camera test passed: `rpicam-hello --list-cameras` shows:
  ```
  0 : imx477 [4056x3040 12-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx477@1a)
      Modes: 'SRGGB10_CSI2P' : 1332x990 [120.05 fps]
             'SRGGB12_CSI2P' : 2028x1080 [50.03 fps]
                               2028x1520 [40.01 fps]
                               4056x3040 [10.00 fps]
  ```

## Current Problem
**MediaMTX download is failing.** The install script tries to download from GitHub but gets a redirect or 404.

Current URL in install.sh:
```
https://github.com/bluenviron/mediamtx/releases/download/v1.16.0/mediamtx_v1.16.0_linux_arm64.tar.gz
```

Previous attempt with `linux_arm64v8` also failed. Need to verify the correct download URL for MediaMTX ARM64 binary.

## Key Technical Decisions Made

1. **rpicam-apps not libcamera-apps** - Raspberry Pi OS Bookworm renamed the camera tools
2. **Native sensor modes** - Profiles use actual IMX477 sensor modes:
   - 1080p50: 2028x1080 @ 50fps
   - 720p120: 1332x990 @ 120fps
   - 1520p40: 2028x1520 @ 40fps
3. **MediaMTX rpiCamera** - Using MediaMTX's built-in Pi camera support instead of piping rpicam-vid through ffmpeg

## File Structure
```
pi_cam_stream/
├── deploy/                         # Copy to Pi
│   ├── install.sh                  # Main installer (NEEDS FIX for MediaMTX URL)
│   ├── bin/scoutcam                # CLI tool
│   ├── etc/scoutcam/
│   │   ├── config.env              # Main config
│   │   ├── mediamtx.yml            # Generated at runtime by scoutcam
│   │   └── profiles/
│   │       ├── 1080p50.env
│   │       ├── 720p120.env
│   │       └── 1520p40.env
│   ├── systemd/
│   │   ├── scoutcam-stream.service
│   │   ├── scoutcam-record.service
│   │   └── mnt-usb.mount
│   └── udev/
│       └── 99-scoutcam-usb.rules
├── receiver/                       # Windows client tools
│   ├── view_stream.bat
│   ├── record_stream.bat
│   └── opencv_example.py
└── README.md
```

## How the System Works

1. `scoutcam start` calls systemd to start `scoutcam-stream.service`
2. Service runs `scoutcam _stream` which:
   - Reads profile from `/etc/scoutcam/config.env`
   - Generates `/etc/scoutcam/mediamtx.yml` with camera settings
   - Runs MediaMTX which accesses camera directly via `source: rpiCamera`
3. Stream available at `rtsp://PI_IP:8554/cam`

## MediaMTX Config Generated
```yaml
paths:
  cam:
    source: rpiCamera
    rpiCameraWidth: 2028
    rpiCameraHeight: 1080
    rpiCameraFPS: 50
    rpiCameraBitrate: 8000000
    rpiCameraCodec: h264
    rpiCameraIDRPeriod: 25
```

## What Needs to Be Fixed

1. **MediaMTX download URL** - Find the correct URL for v1.16.0 (or latest) ARM64 Linux binary
2. **Test the full pipeline** - Once MediaMTX installs, run `scoutcam start` and verify streaming works
3. **Test from Windows** - Use `receiver/view_stream.bat` or ffplay to verify stream

## Pi Details
- Hostname: scoutcam
- IP: 192.168.0.232
- User: pi
- Deploy path: `~/scoutcam-setup/`

## Useful Commands on Pi
```bash
# Check camera
rpicam-hello --list-cameras

# Manual MediaMTX test (after fixing install)
/usr/local/bin/mediamtx /etc/scoutcam/mediamtx.yml

# Service logs
journalctl -u scoutcam-stream -f

# Stop the restart loop if service keeps crashing
sudo systemctl stop scoutcam-stream
```

## Sources
- [MediaMTX GitHub](https://github.com/bluenviron/mediamtx)
- [MediaMTX Releases](https://github.com/bluenviron/mediamtx/releases)
- [Raspberry Pi Camera Software Docs](https://www.raspberrypi.com/documentation/computers/camera_software.html)

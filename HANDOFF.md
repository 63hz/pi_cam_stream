# ScoutCam Handoff Document

## Project Overview
RTSP streaming system for Raspberry Pi 4B with Pi HQ Camera (IMX477) for FRC game piece tracking.

## Current Architecture
```
[Pi HQ Camera] -> [MediaMTX rpiCamera source] -> RTSP :8554/cam -> [Windows PC OpenCV]
```

MediaMTX v1.16.0 has built-in Raspberry Pi camera support (`source: rpiCamera`) which talks to
libcamera and the V4L2 hardware H.264 encoder directly. Single process, no pipes.

## Hardware
- Raspberry Pi 4B (4GB) running Raspberry Pi OS Lite 64-bit (Bookworm)
- Pi HQ Camera (IMX477)
- Windows desktop PC with NVIDIA GPU (for receiving)

## Working Configuration
- **Default profile:** 720p60 (1280x720 @ 60fps, 6 Mbps)
- **Delivers:** 50fps to PC application with detection overlay enabled
- **Alternative:** 1080p30 (1920x1080 @ 30fps, 8 Mbps) for maximum detail

## Pi 4B Encoder Limits (Verified)
The V4L2 hardware H.264 encoder maxes out at:
- 1920x1080 @ 30fps
- 1280x720 @ 60fps

These are hard limits. Profile, level, bitrate changes don't help. Legacy profiles
(1080p50, 720p120) exceed these limits and only work with the old rpicam-vid pipeline
(which delivers fewer fps than claimed due to fake timestamps).

## Pi Access
- Hostname: `scoutcam.local`
- User: `pi`
- SSH key: `~/.ssh/id_ed25519` (key auth configured)
- Password: `2491`
- SSH command: `ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local`

## Key Files
- `deploy/bin/scoutcam` - CLI script (generates mediamtx.yml, runs `exec mediamtx`)
- `deploy/etc/scoutcam/config.env` - Main config
- `deploy/etc/scoutcam/profiles/` - Resolution profiles
- `receiver/rtsp_ingest_debug.py` - PC receiver with ThreadedCapture, probe mode, hwaccel
- `CLAUDE.md` - Instructions for Claude Code instances
- `TOOLBOX.md` - Quick-reference commands for the user

## History
1. Initial build used MediaMTX `source: rpiCamera` but hit encoder errors at 1080p50
2. Switched to rpicam-vid + ffmpeg pipeline as workaround
3. Discovered rpicam-vid only produced 36fps (not 50fps), ffmpeg `+genpts` faked timestamps
4. Diagnosed Pi 4B V4L2 encoder limits through systematic testing
5. Switched back to MediaMTX `source: rpiCamera` at supported resolutions (720p60, 1080p30)
6. Now delivering honest 50fps to PC application

## Useful Resources
- [MediaMTX GitHub](https://github.com/bluenviron/mediamtx)
- [Raspberry Pi Camera Docs](https://www.raspberrypi.com/documentation/computers/camera_software.html)

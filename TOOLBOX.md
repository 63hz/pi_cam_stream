# ScoutCam Toolbox - Quick Reference

Commands you'll actually use, copy-paste ready for PowerShell.

## SSH into the Pi

```powershell
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local
```

## Service Control (run on Pi via SSH)

```bash
# Start / stop / restart
sudo systemctl start scoutcam-stream.service
sudo systemctl stop scoutcam-stream.service
sudo systemctl restart scoutcam-stream.service

# Status and logs
scoutcam status
journalctl -u scoutcam-stream -f          # live tail
journalctl -u scoutcam-stream -n 50       # last 50 lines

# Switch profile
scoutcam profile 720p60
scoutcam profile 1080p30
scoutcam profiles                          # list all available
```

## Test Stream from PC (PowerShell)

```powershell
# Quick view with ffplay (lowest latency)
ffplay -fflags nobuffer rtsp://scoutcam.local:8554/cam

# VLC (force TCP)
vlc rtsp://scoutcam.local:8554/cam --rtsp-tcp

# Debug harness with detection overlay
python receiver/rtsp_ingest_debug.py --detect

# Debug harness headless (console stats only)
python receiver/rtsp_ingest_debug.py --headless --max-seconds 30

# Without threading (for A/B comparison)
python receiver/rtsp_ingest_debug.py --no-threaded --detect
```

## Diagnose FPS Issues

### From PC: probe what the Pi is actually delivering
```powershell
python receiver/rtsp_ingest_debug.py --probe
```
This runs software decode, CUDA, and D3D11VA back-to-back and reports which can keep up.
Look at `speed=` — anything >= 0.95x is real-time.

### From Pi: test encoder throughput directly
```bash
# How many frames does rpicam-vid actually produce? (-t is milliseconds!)
rpicam-vid -t 10000 --width 1280 --height 720 --framerate 60 --codec h264 --profile high --level 4.2 --bitrate 6000000 --intra 30 --inline -o /dev/null -v 2>&1 | grep -E "displayed|dropped"

# Camera sensor max FPS (no encoding)
rpicam-hello -t 10000 --width 1280 --height 720 --framerate 60 -v 2>&1 | grep -E "displayed|dropped"

# CPU usage while streaming
top -bn1 | head -15

# What's running?
ps aux | grep -E 'rpicam|ffmpeg|mediamtx' | grep -v grep
```

### Key numbers to look for
| Metric | Healthy | Problem |
|--------|---------|---------|
| `src_fps` | ~50 | < 40 = Pi can't deliver |
| `app_fps` | ~50 | < src_fps = PC can't keep up |
| `read` (threaded) | ~20ms | > 30ms = decode bottleneck |
| `drop%` (steady) | < 5% | > 20% = main loop too slow |
| Probe `speed=` | >= 0.95x | < 0.8x = can't keep up |

## Network Diagnostics

```powershell
# Can you reach the Pi?
ping scoutcam.local

# Is RTSP port open? (from Pi)
# ssh in, then:
ss -tln | grep 8554

# Raw ffprobe of stream
ffprobe -v error -select_streams v -show_entries stream=r_frame_rate,avg_frame_rate,codec_name,width,height -rtsp_transport tcp rtsp://scoutcam.local:8554/cam
```

## Deploy Updated Files to Pi (PowerShell)

```powershell
# Deploy scoutcam script
scp -o IdentityFile=~/.ssh/id_ed25519 deploy/bin/scoutcam pi@scoutcam.local:/tmp/scoutcam_new
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo cp /tmp/scoutcam_new /usr/local/bin/scoutcam && sudo chmod +x /usr/local/bin/scoutcam"

# Deploy config
scp -o IdentityFile=~/.ssh/id_ed25519 deploy/etc/scoutcam/config.env pi@scoutcam.local:/tmp/config.env
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo cp /tmp/config.env /etc/scoutcam/config.env"

# Deploy a profile
scp -o IdentityFile=~/.ssh/id_ed25519 deploy/etc/scoutcam/profiles/720p60.env pi@scoutcam.local:/tmp/720p60.env
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo cp /tmp/720p60.env /etc/scoutcam/profiles/720p60.env"

# Restart after deploying
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo systemctl restart scoutcam-stream.service"
```

## Camera Tuning (on Pi)

```bash
# List camera capabilities
rpicam-hello --list-cameras

# Test specific shutter speed (in microseconds)
rpicam-hello -t 5000 --width 1280 --height 720 --shutter 1000

# Current config
scoutcam config
cat /etc/scoutcam/config.env
cat /etc/scoutcam/mediamtx.yml
```

## Recording (from PC)

```powershell
# Start receiver with recording
python receiver/rtsp_ingest_debug.py --record-on-start --detect

# Controls while running: r=toggle recording, s=screenshot, d=toggle detection, q=quit
```

## Emergency Recovery

```bash
# Kill everything and restart clean (on Pi)
sudo systemctl stop scoutcam-stream.service
sudo pkill -9 mediamtx
sudo pkill -9 rpicam
sleep 2
sudo systemctl start scoutcam-stream.service

# If the camera is "busy"
sudo pkill -9 rpicam
sudo pkill -9 libcamera
sleep 2
sudo systemctl restart scoutcam-stream.service
```

## Available Profiles

| Profile | Resolution | FPS | Bitrate | Notes |
|---------|-----------|-----|---------|-------|
| **720p60** | 1280x720 | 60 | 6 Mbps | Default, best for tracking |
| **1080p30** | 1920x1080 | 30 | 8 Mbps | Max detail |
| 1080p50 | 2028x1080 | 50 | 8 Mbps | Legacy, Pi 4B can't hit 50fps |
| 1080p40 | 2028x1080 | 40 | 10 Mbps | Legacy, Pi 4B can't hit 40fps |
| 720p120 | 1280x720 | 120 | 6 Mbps | Legacy, Pi 4B can't hit 120fps |

The legacy profiles still exist for reference but the Pi 4B's hardware encoder can't sustain their target framerates. Use **720p60** or **1080p30**.

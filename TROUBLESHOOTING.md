# ScoutCam Troubleshooting & Recovery Guide

## Architecture Overview

The current pipeline uses MediaMTX's built-in `rpiCamera` source:
```
[IMX477 Camera] -> [MediaMTX rpiCamera] -> RTSP -> [PC OpenCV]
                   (single process)
```

You should see **one process** (`mediamtx`) and no rpicam-vid or ffmpeg for streaming.

## Quick Health Check

```bash
# On the Pi:
scoutcam status
scoutcam health

# Is MediaMTX running?
ps aux | grep mediamtx | grep -v grep

# Is RTSP port listening?
ss -tln | grep 8554

# Recent logs
journalctl -u scoutcam-stream -n 30 --no-pager
```

From the PC:
```powershell
# Probe actual delivery FPS
python receiver/rtsp_ingest_debug.py --probe

# Quick stream test
ffplay -fflags nobuffer rtsp://scoutcam.local:8554/cam
```

---

## Common Issues

### "encoder_create(): unable to activate output stream"

**Cause:** The profile requests a resolution/fps combo above the V4L2 hardware encoder's limit.

**Pi 4B encoder limits:**
- 1920x1080 @ 30fps max
- 1280x720 @ 60fps max

**Fix:** Switch to a supported profile:
```bash
scoutcam profile 720p60     # 1280x720 @ 60fps
scoutcam profile 1080p30    # 1920x1080 @ 30fps
```

The legacy profiles (1080p50, 720p120, 1080p40) exceed these limits and will fail with `rpiCamera` source.

### Stream starts but FPS is low

Run the probe from your PC:
```powershell
python receiver/rtsp_ingest_debug.py --probe
```

Look at `speed=` in the output:
- `speed >= 0.95x` = Pi is delivering in real-time, PC can keep up
- `speed < 0.8x` = Pi can't deliver fast enough (encoder bottleneck)

If the Pi is slow, check:
```bash
# Is the profile too aggressive?
scoutcam config

# CPU usage
top -bn1 | head -15

# Direct encoder throughput test (bypass network)
rpicam-vid -t 10000 --width 1280 --height 720 --framerate 60 --codec h264 --profile high --level 4.2 --bitrate 6000000 --intra 30 --inline -o /dev/null -v 2>&1 | grep -E "displayed|dropped"
```

### Camera not detected

```bash
rpicam-hello --list-cameras
```

Should show:
```
0 : imx477 [4056x3040 12-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx477@1a)
```

If not:
- Check camera ribbon cable is seated properly
- Check `dtoverlay=imx477` in `/boot/firmware/config.txt`
- Reboot

### Camera device busy

```bash
sudo pkill -9 mediamtx
sudo pkill -9 rpicam
sudo pkill -9 libcamera
sleep 2
sudo systemctl restart scoutcam-stream.service
```

### Can't connect from Windows

```powershell
# Can you reach the Pi?
ping scoutcam.local

# If mDNS doesn't work, find the Pi's IP:
# On the Pi: hostname -I
# Then use the IP directly:
ffplay -fflags nobuffer rtsp://192.168.x.x:8554/cam
```

Check firewall allows port 8554 on both Pi and PC.

### VLC shows errors

Force TCP transport:
```cmd
vlc rtsp://scoutcam.local:8554/cam --rtsp-tcp
```

### OpenCV ThreadedCapture crashes (pthread_frame.c assertion)

This happens if `cv2.VideoCapture` is created in one thread and `read()` is called from another. The `ThreadedCapture` class in `rtsp_ingest_debug.py` avoids this by creating the capture inside the reader thread. If you see this error, make sure you're using the latest version of the script.

---

## Fresh Install / Recovery

### From Scratch

1. Flash Pi OS Lite 64-bit, set hostname `scoutcam`, enable SSH
2. From PowerShell:
   ```powershell
   scp -r deploy/* pi@scoutcam.local:~/scoutcam-setup/
   ssh pi@scoutcam.local "cd ~/scoutcam-setup && chmod +x install.sh && sudo ./install.sh"
   ```
3. Start:
   ```bash
   scoutcam start
   ```

### Re-deploy from repo

```powershell
scp -o IdentityFile=~/.ssh/id_ed25519 deploy/bin/scoutcam pi@scoutcam.local:/tmp/scoutcam_new
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo cp /tmp/scoutcam_new /usr/local/bin/scoutcam && sudo chmod +x /usr/local/bin/scoutcam && sudo systemctl restart scoutcam-stream.service"
```

---

## Key Files

| File | Purpose |
|------|---------|
| `/usr/local/bin/scoutcam` | Main CLI script |
| `/etc/scoutcam/mediamtx.yml` | MediaMTX config (auto-generated, `source: rpiCamera`) |
| `/etc/scoutcam/config.env` | Main settings (profile, port, etc) |
| `/etc/scoutcam/profiles/*.env` | Resolution/framerate profiles |
| `/etc/systemd/system/scoutcam-stream.service` | Systemd service |

---

## Diagnostic Commands Reference

See [TOOLBOX.md](TOOLBOX.md) for a complete copy-paste-ready command reference.

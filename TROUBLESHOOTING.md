# ScoutCam Troubleshooting & Recovery Guide

## The Big Gotcha: MediaMTX rpiCamera vs rpicam-vid

**TL;DR:** MediaMTX's built-in `rpiCamera` source does NOT work reliably with Pi 4B + IMX477. We use `rpicam-vid` + `ffmpeg` instead.

### Symptoms of the Wrong Setup
- VLC connects but gets 404 error
- MediaMTX logs show: `encoder_create(): unable to activate output stream`
- `ps aux | grep mediamtx` shows mediamtx running, but no rpicam-vid or ffmpeg

### Symptoms of the Correct Setup
- Three processes running: mediamtx, rpicam-vid, ffmpeg
- MediaMTX logs show: `[path cam] stream is available and online, 1 track (H264)`
- VLC connects and shows video

### How to Verify
```bash
# Check processes - you need all 3
ps aux | grep -E 'rpicam|ffmpeg|mediamtx' | grep -v grep

# Check MediaMTX config - must say "source: publisher"
grep "source:" /etc/scoutcam/mediamtx.yml

# Check logs for success message
journalctl -u scoutcam-stream -n 20 | grep "stream is available"
```

### How to Fix
```bash
# 1. Stop the service
sudo systemctl stop scoutcam-stream

# 2. Verify config has publisher mode (not rpiCamera)
cat /etc/scoutcam/mediamtx.yml | grep source
# Should show: source: publisher

# 3. If it shows "source: rpiCamera", fix it:
sudo sed -i 's/source: rpiCamera/source: publisher/' /etc/scoutcam/mediamtx.yml

# 4. Restart
sudo systemctl restart scoutcam-stream

# 5. Verify all 3 processes are running
sleep 3 && ps aux | grep -E 'rpicam|ffmpeg|mediamtx' | grep -v grep
```

---

## Fresh Install / Recovery

### From Scratch on a New Pi

1. **Flash Pi OS Lite 64-bit** to SD card
   - Use Raspberry Pi Imager
   - Set hostname: `scoutcam`
   - Enable SSH
   - Configure WiFi if needed

2. **Copy files from Windows:**
   ```powershell
   scp -r deploy/* pi@scoutcam.local:~/scoutcam-setup/
   ```

3. **Run installer on Pi:**
   ```bash
   ssh pi@scoutcam.local
   cd ~/scoutcam-setup
   chmod +x install.sh
   sudo ./install.sh
   ```

4. **Start streaming:**
   ```bash
   scoutcam start
   ```

5. **Test from Windows:**
   ```cmd
   vlc rtsp://scoutcam.local:8554/cam --rtsp-tcp
   ```

### Recovering a Broken Pi

If streaming stops working:

```bash
# Full reset
scoutcam stop
sudo systemctl restart scoutcam-stream
sleep 3
scoutcam status
```

If that doesn't work:
```bash
# Re-deploy from Windows
scp -r deploy/* pi@scoutcam:~/scoutcam-setup/
ssh pi@scoutcam "cd ~/scoutcam-setup && sudo ./install.sh && scoutcam restart"
```

---

## Common Issues

### "No stream is available on path 'cam'"
The rpicam-vid + ffmpeg pipeline isn't running. Check:
```bash
ps aux | grep rpicam    # Should show rpicam-vid process
ps aux | grep ffmpeg    # Should show ffmpeg process
```

If missing, check the full logs:
```bash
journalctl -u scoutcam-stream -n 100
```

### Camera Device Busy
If you see "Failed to queue buffer" errors, another process has the camera:
```bash
# Kill any stray camera processes
sudo pkill -9 rpicam
sudo pkill -9 libcamera
# Restart
scoutcam restart
```

### VLC "only real/helix rtsp servers supported"
VLC is using the wrong RTSP module. Force TCP:
```cmd
vlc rtsp://192.168.0.232:8554/cam --rtsp-tcp
```

### H.264 Decode Errors ("error while decoding MB", "concealing errors")
If you see decode errors when playing or capturing the stream, the pipeline may be missing critical flags. The correct pipeline includes:
- `--inline` on rpicam-vid: Repeats SPS/PPS headers with every keyframe for decoder recovery
- `-fflags +genpts` on ffmpeg: Generates proper timestamps for raw H.264
- `-r $FRAMERATE` on ffmpeg input: Ensures correct timestamp intervals
- `-rtsp_transport tcp` on ffmpeg output: Reliable delivery to MediaMTX

---

## Key Files

| File | Purpose |
|------|---------|
| `/usr/local/bin/scoutcam` | Main CLI script |
| `/etc/scoutcam/mediamtx.yml` | MediaMTX config - **must have `source: publisher`** |
| `/etc/scoutcam/config.env` | Main settings (profile, port, etc) |
| `/etc/scoutcam/profiles/*.env` | Resolution/framerate profiles |
| `/etc/systemd/system/scoutcam-stream.service` | Systemd service |

---

## Quick Diagnostic Commands

```bash
# Is the service running?
systemctl is-active scoutcam-stream

# What processes are running?
ps aux | grep -E 'rpicam|ffmpeg|mediamtx' | grep -v grep

# Recent logs
journalctl -u scoutcam-stream -n 50 --no-pager

# Is RTSP port open?
ss -tln | grep 8554

# Is camera detected?
rpicam-hello --list-cameras

# Current config
scoutcam config
```

---

## The Pipeline Explained

```
rpicam-vid -t 0 --width W --height H --framerate F --codec h264 --inline ... -o - |
ffmpeg -fflags +genpts -r F -f h264 -i - -c copy -f rtsp -rtsp_transport tcp rtsp://localhost:8554/cam
```

1. **rpicam-vid**: Captures from camera, encodes to H.264 using Pi's hardware encoder, outputs to stdout
   - `--inline`: Repeats SPS/PPS headers with every keyframe (critical for decoder recovery after any glitch)
2. **ffmpeg**: Reads H.264 stream, wraps it in RTSP, publishes to MediaMTX
   - `-fflags +genpts`: Generates proper PTS/DTS timestamps for raw H.264 input
   - `-r F`: Tells ffmpeg the input frame rate for correct timestamp intervals
   - `-rtsp_transport tcp`: Ensures reliable delivery to MediaMTX (no packet loss)
3. **MediaMTX**: Simple RTSP server in "publisher" mode - just relays the stream to clients

This bypasses MediaMTX's broken rpiCamera implementation entirely.

# ScoutCam - Claude Code Instructions

## Project Overview

RTSP streaming system: Pi 4B (IMX477 HQ Camera) -> MediaMTX (rpiCamera source) -> RTSP -> Windows PC OpenCV.
Used for FRC (FIRST Robotics Competition) game piece tracking.

## SSH Access to Pi

```bash
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local
```

- Hostname: `scoutcam.local` (or check for IP if mDNS fails)
- User: `pi`
- Auth: Ed25519 key at `~/.ssh/id_ed25519` (key auth configured)
- Password fallback: `2491`

## Architecture

```
[IMX477 Camera] -> [MediaMTX rpiCamera source] -> [RTSP :8554/cam] -> [Windows PC OpenCV]
                   (libcamera + V4L2 H.264 hw encode, single process)
```

MediaMTX talks directly to the camera via libcamera and uses the Pi's V4L2 hardware H.264 encoder.
There is NO rpicam-vid or ffmpeg in the streaming pipeline. The `_stream()` function in
`deploy/bin/scoutcam` just runs `exec mediamtx config.yml`.

### Previous architecture (deprecated, DO NOT revert to)

The old pipeline used rpicam-vid piped to ffmpeg piped to MediaMTX in publisher mode.
This was replaced because:
- rpicam-vid + ffmpeg added ~11fps overhead from pipe/process switching
- ffmpeg's `+genpts` flag generated fake timestamps, masking the real frame rate
- The 3-process pipeline delivered only 33fps when the stream claimed 50fps

## Pi 4B Hardware Encoder Limits (VERIFIED)

The V4L2 H.264 encoder (bcm2835-codec-encode at /dev/video11) has hard limits:

| Resolution | Max FPS (MediaMTX rpiCamera) | Max FPS (rpicam-vid, fake timestamps) |
|-----------|------------------------------|---------------------------------------|
| 1920x1080 | **30fps** | ~36fps (but timestamps lie about 50fps) |
| 1280x720 | **60fps** | ~68fps (but timestamps lie about 120fps) |

- H.264 profile (high/main/baseline) makes NO difference to throughput
- Width (2028 vs 1920) makes negligible difference
- The IMX477 sensor can do ~47fps at 1080p (ISP-limited), but the encoder is the bottleneck
- MediaMTX rpiCamera fails with "encoder_create(): unable to activate output stream" above these limits

## Key Files

### Pi-side (deployed to /etc/scoutcam/ and /usr/local/bin/)
- `deploy/bin/scoutcam` - CLI script, generates mediamtx.yml, runs MediaMTX
- `deploy/etc/scoutcam/config.env` - Main config (default profile: 720p60)
- `deploy/etc/scoutcam/profiles/720p60.env` - Recommended profile for tracking
- `deploy/etc/scoutcam/profiles/1080p30.env` - Max detail profile
- `deploy/systemd/scoutcam-stream.service` - Systemd service

### PC-side
- `receiver/rtsp_ingest_debug.py` - Main receiver/debug harness with:
  - `ThreadedCapture` class (creates VideoCapture inside reader thread)
  - `FFmpegPipeCapture` class (subprocess ffmpeg pipe for hwaccel)
  - `--probe` mode for diagnosing FPS issues
  - `--hwaccel cuda` for NVIDIA GPU decode (uses FFmpegPipeCapture)
  - `--threaded` (default on) / `--no-threaded` for A/B testing

## Important Technical Notes

- pip `opencv-python` bundles FFmpeg WITHOUT hwaccel support.
  `OPENCV_FFMPEG_CAPTURE_OPTIONS=hwaccel;d3d11va` silently falls back to software.
  Use `FFmpegPipeCapture` (system ffmpeg subprocess) for real GPU decode.

- OpenCV's FFmpeg backend crashes with `pthread_frame.c async_lock` assertion when
  `VideoCapture` is created in one thread and `read()` called from another.
  `ThreadedCapture` avoids this by creating the capture inside its reader thread.

- The PC has an NVIDIA GPU. Software decode handles 720p60 fine (~50fps with detection).
  CUDA hwaccel is available but unnecessary at 720p.

## Deploying Changes to Pi

```bash
# From this repo's working directory:
scp -o IdentityFile=~/.ssh/id_ed25519 deploy/bin/scoutcam pi@scoutcam.local:/tmp/scoutcam_new
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo cp /tmp/scoutcam_new /usr/local/bin/scoutcam && sudo chmod +x /usr/local/bin/scoutcam"

scp -o IdentityFile=~/.ssh/id_ed25519 deploy/etc/scoutcam/config.env pi@scoutcam.local:/tmp/config.env
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo cp /tmp/config.env /etc/scoutcam/config.env"

# Deploy profiles
for f in 720p60.env 1080p30.env; do
  scp -o IdentityFile=~/.ssh/id_ed25519 "deploy/etc/scoutcam/profiles/$f" "pi@scoutcam.local:/tmp/$f"
  ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo cp /tmp/$f /etc/scoutcam/profiles/$f"
done

# Restart service
ssh -o IdentityFile=~/.ssh/id_ed25519 pi@scoutcam.local "sudo systemctl restart scoutcam-stream.service"
```

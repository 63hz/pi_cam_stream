You are a senior embedded/streaming engineer. Help me implement the Raspberry Pi 4B side of a computer-vision capture system using a Raspberry Pi HQ Camera (IMX477) and libcamera on Raspberry Pi OS Lite 64-bit.

Before writing new code, first check whether an existing, standard solution already meets the requirements using off-the-shelf tools (libcamera-vid, ffmpeg, gstreamer, systemd services, etc.). If something already exists, propose the simplest “glue” + configuration approach rather than reinventing a streaming server.

Goals / Requirements:
1) The Pi should provide a low-latency, CV-friendly live video stream over the network that can be consumed by common toolchains on a laptop (ffmpeg, OpenCV, OBS, GStreamer).
   - Prefer standard protocols and formats (RTSP or SRT are OK; UDP RTP is OK if it’s the simplest; avoid exotic one-off solutions).
   - The stream should be H.264 (not H.265) unless you can justify otherwise.
   - Constant frame rate is required (no VFR surprises).
   - Minimize “stream breaks” and avoid solutions that require a web UI in a browser.

2) Avoid “sneakernet”: no SD-card swapping. The laptop should be able to connect and receive the stream over wired Ethernet (preferred).
   - It’s fine if the Pi streams to a fixed target IP/port OR hosts a server that clients connect to—choose the approach that is most robust and simplest.

3) If CPU and I/O allow, the Pi should also record a local backup copy to a USB 3.0 attached SSD/HDD while streaming.
   - If simultaneous stream+record is too costly or fragile, propose fallback modes (stream-only, record-only, or record with delayed upload).
   - Recording should be in a format easy to use later (MP4 preferred if feasible; raw .h264 acceptable if that’s more robust; if raw, include an automatic conversion step).

4) Provide a clean, operator-friendly way to change capture parameters (resolution, framerate, shutter/gain/awb, etc.) without re-imaging the Pi.
   - “On the fly” can mean: edit a config file and restart a service; or a simple CLI command; or a minimal local web API (only if truly worth it).
   - Keep it reliable for use in noisy event conditions.

5) Provide a production-ish setup:
   - A single install script (or documented commands) that sets up packages, creates directories, and installs systemd services.
   - A systemd service that auto-starts on boot and restarts on failure.
   - Logging that is easy to inspect (journalctl).
   - A “health check” and a “status” command.

6) Include an explicit performance and reliability plan:
   - Explain expected CPU usage on Pi 4B for chosen streaming+record approach.
   - Explain how you avoid dropped frames and how to tune bitrate/GOP/keyframe interval.
   - Explain what happens if the USB drive disappears or fills up: how the system fails safely.

Technical constraints / preferences:
- Raspberry Pi 4B, Pi HQ camera, long lens, tight scoring-zone view.
- Worst-case camera-to-subject distance 120–130 ft; lighting may be gym lighting.
- Prefer wired Ethernet and a standard “travel router” LAN, but don’t hard-require internet.
- If you use gstreamer, keep the pipeline readable and avoid fragile plugin dependencies.
- If you use ffmpeg, show the exact commands and the rationale.
- Avoid anything that requires proprietary capture hardware.
- Keep security simple (private LAN). Do not overcomplicate with TLS unless it’s trivial.

Deliverables:
A) Recommendation: pick ONE primary approach (RTSP server vs SRT vs RTP/UDP) and briefly justify why it’s the most robust for this use case.
B) Concrete implementation:
   - exact libcamera-vid / ffmpeg / gstreamer commands
   - config file format (e.g., /etc/scoutcam/config.env)
   - systemd unit file(s)
   - helper scripts: start/stop/status, change profile, list profiles
C) A small “profiles” concept:
   - e.g., Profile 1: 1080p50 (more detail), Profile 2: 720p120 (less blur), Profile 3: 720p60 (fallback)
   - ability to switch profiles quickly
D) A “receiver example” for a laptop:
   - one-liners to receive and record with ffmpeg
   - optionally a small OpenCV snippet showing how to open the stream

Stop me / ask clarifying questions only if absolutely necessary. Otherwise, make reasonable assumptions and produce a complete, minimal, robust solution. Prefer boring reliability over cleverness.

#!/usr/bin/env python3
"""
RTSP ingest / debug harness (full rewrite)

Key goals:
  - Measure where time is going: cap.read (network+decode), processing, GUI, total
  - Use sliding-window FPS that reflects the REAL loop cadence (includes GUI)
  - Avoid "sped up" recordings by tagging output FPS to measured cadence (or user override)
  - Provide headless mode + display rate limiting to isolate GUI/compositor bottlenecks
  - Threaded capture for frame freshness + hwaccel decode to hit 50+ FPS

Controls (when display enabled):
  q  quit
  s  screenshot
  r  toggle recording
  d  toggle detection overlay

Examples:
  python rtsp_ingest_debug.py
  python rtsp_ingest_debug.py --pi scoutcam.local
  python rtsp_ingest_debug.py --hwaccel cuda --detect
  python rtsp_ingest_debug.py --hwaccel d3d11va --threaded --detect
  python rtsp_ingest_debug.py --no-threaded --headless --max-seconds 30
  python rtsp_ingest_debug.py --probe
  python rtsp_ingest_debug.py --display-fps 15
  python rtsp_ingest_debug.py --record-on-start
  python rtsp_ingest_debug.py --proc-scale 0.5 --detect
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Threaded capture: always holds the LATEST frame, drops stale ones
# ---------------------------------------------------------------------------
class ThreadedCapture:
    """Continuously grabs frames in a background thread, keeping only the latest.

    This decouples network+decode from the main processing loop so the main
    thread always gets the freshest frame without blocking.

    IMPORTANT: The VideoCapture is opened inside the reader thread to avoid
    cross-thread FFmpeg assertion failures (pthread_frame.c async_lock).
    The caller provides a factory function; the capture is created and used
    entirely within the reader thread.
    """

    def __init__(self, cap_factory):
        """cap_factory: callable that returns (cap, width, height, fps)."""
        self._cap_factory = cap_factory
        self._lock = threading.Lock()
        self._frame = None
        self._ret = False
        self._running = False
        self._ready = threading.Event()

        # Stream props (filled after thread opens the capture)
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._opened = False

        # Stats
        self.frames_grabbed = 0
        self.frames_dropped = 0
        self.last_read_dt = 0.0
        self._read_dts = deque(maxlen=240)

        # Source FPS tracking (rate frames arrive from network)
        self._src_times = deque()
        self._src_fps = 0.0

    def start(self):
        self._running = True
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
        # Wait for the capture to open (up to 15s for RTSP)
        self._ready.wait(timeout=15)
        return self

    def _reader(self):
        cap = self._cap_factory()
        with self._lock:
            self._opened = cap.isOpened()
            if self._opened:
                self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
                self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
                self._fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._ready.set()

        if not self._opened:
            return

        while self._running:
            t0 = time.perf_counter()
            ret, frame = cap.read()
            dt = time.perf_counter() - t0

            if not ret:
                time.sleep(0.01)
                continue

            now = time.perf_counter()
            with self._lock:
                if self._frame is not None:
                    self.frames_dropped += 1
                self._frame = frame
                self._ret = ret
                self.frames_grabbed += 1
                self.last_read_dt = dt
                self._read_dts.append(dt)

                # Track source arrival rate
                self._src_times.append(now)
                cutoff = now - 2.0
                while self._src_times and self._src_times[0] < cutoff:
                    self._src_times.popleft()
                if len(self._src_times) >= 2:
                    self._src_fps = (len(self._src_times) - 1) / (
                        self._src_times[-1] - self._src_times[0]
                    )

        cap.release()

    def read(self):
        """Return (ret, frame) — the latest frame, or (False, None) if none yet."""
        with self._lock:
            frame = self._frame
            ret = self._ret
            self._frame = None  # consumed
            self._ret = False
        return ret, frame

    @property
    def src_fps(self):
        with self._lock:
            return self._src_fps

    @property
    def avg_read_dt(self):
        with self._lock:
            if self._read_dts:
                return sum(self._read_dts) / len(self._read_dts)
            return 0.0

    def stop(self):
        self._running = False

    def release(self):
        self.stop()

    def isOpened(self):
        with self._lock:
            return self._opened

    def get(self, prop):
        with self._lock:
            if prop == cv2.CAP_PROP_FRAME_WIDTH:
                return float(self._width)
            elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
                return float(self._height)
            elif prop == cv2.CAP_PROP_FPS:
                return self._fps
        return 0.0


# ---------------------------------------------------------------------------
# FFmpeg pipe capture: spawn real ffmpeg.exe with explicit hwaccel flags
# ---------------------------------------------------------------------------
class FFmpegPipeCapture:
    """Capture via ffmpeg subprocess pipe — bypasses OpenCV's bundled FFmpeg.

    This uses the system ffmpeg binary which supports GPU-accelerated decode
    (cuda/cuvid, d3d11va, etc.) that pip's opencv-python typically lacks.
    Produces raw BGR24 frames on stdout that we reshape into numpy arrays.
    """

    def __init__(self, url: str, hwaccel: str = "cuda", transport: str = "tcp"):
        self._url = url
        self._hwaccel = hwaccel
        self._transport = transport
        self._proc = None
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._frame_size = 0
        self._opened = False

        # Probe stream dimensions first
        self._probe_stream()
        if self._width > 0 and self._height > 0:
            self._frame_size = self._width * self._height * 3
            self._start_ffmpeg()

    def _probe_stream(self):
        """Use ffprobe to get width, height, fps from the stream."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-rtsp_transport", self._transport,
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,r_frame_rate",
                    "-of", "csv=p=0",
                    self._url,
                ],
                capture_output=True, text=True, timeout=15,
            )
            if result.stdout.strip():
                parts = result.stdout.strip().split(",")
                if len(parts) >= 3:
                    self._width = int(parts[0])
                    self._height = int(parts[1])
                    # r_frame_rate is like "50/1"
                    fps_parts = parts[2].strip().split("/")
                    if len(fps_parts) == 2 and int(fps_parts[1]) > 0:
                        self._fps = int(fps_parts[0]) / int(fps_parts[1])
                    else:
                        self._fps = float(fps_parts[0])
                    print(f"FFmpegPipe: probed {self._width}x{self._height} @ {self._fps:.1f}fps")
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as e:
            print(f"FFmpegPipe: ffprobe failed: {e}")

    def _start_ffmpeg(self):
        """Launch ffmpeg with hwaccel, piping raw BGR24 to stdout."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

        # Hardware acceleration setup
        if self._hwaccel == "cuda":
            cmd += ["-hwaccel", "cuda", "-c:v", "h264_cuvid"]
        elif self._hwaccel == "d3d11va":
            cmd += ["-hwaccel", "d3d11va"]
        elif self._hwaccel == "dxva2":
            cmd += ["-hwaccel", "dxva2"]
        # else: software decode (no hwaccel flags)

        cmd += [
            "-rtsp_transport", self._transport,
            "-i", self._url,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-an",  # no audio
            "pipe:1",
        ]

        print(f"FFmpegPipe: {' '.join(cmd)}")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=self._frame_size * 4,
            )
            self._opened = True
        except FileNotFoundError:
            print("ERROR: ffmpeg not found in PATH")
            self._opened = False

    def read(self):
        """Read one raw frame from the ffmpeg pipe. Returns (ret, frame)."""
        if not self._opened or self._proc is None:
            return False, None

        raw = self._proc.stdout.read(self._frame_size)
        if len(raw) != self._frame_size:
            return False, None

        frame = np.frombuffer(raw, dtype=np.uint8).reshape(
            (self._height, self._width, 3)
        )
        return True, frame

    def isOpened(self):
        return self._opened and self._proc is not None and self._proc.poll() is None

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        elif prop == cv2.CAP_PROP_FPS:
            return self._fps
        return 0.0

    def set(self, prop, val):
        pass  # not supported for pipe capture

    def release(self):
        self._opened = False
        if self._proc is not None:
            try:
                self._proc.stdout.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def open_stream(url: str, buffersize: int = 1) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    # Not always honored for RTSP, but worth attempting
    cap.set(cv2.CAP_PROP_BUFFERSIZE, buffersize)
    return cap


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


def mean(dq: deque) -> float:
    return (sum(dq) / len(dq)) if dq else 0.0


# ---------------------------------------------------------------------------
# Probe mode: pure ffmpeg frame-count test (bypasses OpenCV)
# ---------------------------------------------------------------------------
def run_probe(rtsp_url: str, duration: int = 10) -> int:
    print(f"Probing stream for {duration}s (pure ffmpeg, no OpenCV)...")
    print(f"URL: {rtsp_url}")
    print()

    # First: ffprobe for reported framerate
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v",
                "-show_entries", "stream=r_frame_rate,avg_frame_rate,codec_name,width,height",
                "-of", "default=noprint_wrappers=1",
                "-rtsp_transport", "tcp",
                rtsp_url,
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip():
            print("ffprobe reports:")
            for line in result.stdout.strip().splitlines():
                print(f"  {line}")
            print()
    except FileNotFoundError:
        print("WARN: ffprobe not found in PATH, skipping stream info query")
        print()
    except subprocess.TimeoutExpired:
        print("WARN: ffprobe timed out")
        print()

    # Second: ffmpeg decode-to-null for N seconds (software, then hwaccel)
    tests = [
        ("Software decode", []),
        ("NVIDIA CUDA hwaccel", ["-hwaccel", "cuda", "-c:v", "h264_cuvid"]),
        ("D3D11VA hwaccel", ["-hwaccel", "d3d11va"]),
    ]

    for label, extra_args in tests:
        print(f"--- {label}: decoding {duration}s to /dev/null ---")
        cmd = ["ffmpeg", "-hide_banner"] + extra_args + [
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-t", str(duration),
            "-f", "null", "-",
        ]
        print(f"  cmd: {' '.join(cmd)}")
        try:
            t0 = time.time()
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=duration + 30,
            )
            wall = time.time() - t0
            output = result.stderr

            # Show progress lines
            for line in output.splitlines():
                if "frame=" in line:
                    print(f"  {line.strip()}")

            # Parse final frame count
            frames = _parse_frame_count(output)
            if frames is not None:
                actual_fps = frames / duration
                speed = duration / wall if wall > 0 else 0
                print(f"  => {frames} frames in {duration}s stream = {actual_fps:.1f} fps "
                      f"(wall={wall:.1f}s, speed={speed:.2f}x)")
                if speed >= 0.95:
                    print(f"  PASS: {label} can keep up with real-time")
                else:
                    print(f"  SLOW: {label} only {speed:.2f}x real-time")
            else:
                # Check if it failed entirely (e.g. unsupported hwaccel)
                if result.returncode != 0:
                    for line in output.splitlines()[-3:]:
                        print(f"  {line.strip()}")
                    print(f"  SKIP: {label} not available (ffmpeg returned {result.returncode})")
                else:
                    print("  Could not parse frame count")
            print()
        except FileNotFoundError:
            print("ERROR: ffmpeg not found in PATH")
            return 1
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT: {label} took too long, skipping")
            print()

    return 0


def _parse_frame_count(stderr_output: str):
    """Parse the final frame count from ffmpeg stderr."""
    for line in reversed(stderr_output.splitlines()):
        if "frame=" not in line:
            continue
        # Try "frame=NNN" (no space) first
        for part in line.split():
            if part.startswith("frame=") and len(part) > 6:
                try:
                    return int(part[6:])
                except ValueError:
                    pass
        # Try "frame= NNN" (space after =)
        try:
            idx = line.index("frame=")
            after = line[idx + 6:].strip().split()[0]
            return int(after)
        except (ValueError, IndexError):
            pass
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="RTSP ingest debug harness (OpenCV).")
    parser.add_argument("--pi", default="scoutcam.local", help="Pi hostname/IP (default: scoutcam.local)")
    parser.add_argument("--path", default="/cam", help="RTSP path (default: /cam)")
    parser.add_argument("--port", type=int, default=8554, help="RTSP port (default: 8554)")
    parser.add_argument("--buffersize", type=int, default=1, help="CAP_PROP_BUFFERSIZE to request (default: 1)")

    parser.add_argument("--headless", action="store_true", help="Disable all GUI (imshow/waitKey)")
    parser.add_argument("--display-fps", type=float, default=0.0,
                        help="Limit display updates to this FPS (0 = unlimited).")
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="Auto-exit after N seconds (0 = run until quit).")

    parser.add_argument("--window-seconds", type=float, default=2.0,
                        help="Sliding window size for FPS(win) (default: 2.0s).")

    parser.add_argument("--record-on-start", action="store_true", help="Start recording immediately.")
    parser.add_argument("--record-fps", type=float, default=0.0,
                        help="Override output file FPS (0 = auto from measured cadence).")
    parser.add_argument("--record-fourcc", default="mp4v",
                        help="FourCC for recording (default: mp4v).")

    parser.add_argument("--detect", action="store_true", help="Start with detection overlay on.")

    # --- New flags ---
    parser.add_argument("--hwaccel", default=None,
                        choices=["d3d11va", "dxva2", "cuda", "none"],
                        help="Hardware-accelerated decode backend (default: none/software).")
    parser.add_argument("--transport", default="tcp", choices=["tcp", "udp"],
                        help="RTSP transport (default: tcp).")
    parser.add_argument("--threaded", action="store_true", default=True,
                        help="Use threaded capture for frame freshness (default: on).")
    parser.add_argument("--no-threaded", action="store_true",
                        help="Disable threaded capture (sequential read).")
    parser.add_argument("--proc-scale", type=float, default=1.0,
                        help="Scale factor for detection processing (e.g. 0.5 for half-res). Default: 1.0")
    parser.add_argument("--probe", action="store_true",
                        help="Probe mode: run pure ffmpeg frame-count test and exit.")

    args = parser.parse_args()

    if args.no_threaded:
        args.threaded = False

    rtsp_url = f"rtsp://{args.pi}:{args.port}{args.path}"

    # --- Probe mode: just test source FPS and exit ---
    if args.probe:
        return run_probe(rtsp_url)

    use_ffpipe = args.hwaccel and args.hwaccel != "none"

    print("=" * 72)
    print("RTSP ingest / debug harness")
    print("=" * 72)
    print(f"URL:       {rtsp_url}")
    print(f"HW accel:  {args.hwaccel or 'none (software decode)'}")
    print(f"Backend:   {'ffmpeg pipe (system ffmpeg)' if use_ffpipe else 'OpenCV FFmpeg'}")
    print(f"Transport: {args.transport}")
    print(f"Threaded:  {args.threaded}")
    if args.proc_scale != 1.0:
        print(f"Proc scale: {args.proc_scale}")
    print()
    if not args.headless:
        print("Controls: q quit | s screenshot | r record | d detect overlay")
    else:
        print("Headless mode: GUI disabled")
    print()

    # Set transport for OpenCV's FFmpeg backend
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{args.transport}"

    # Build a capture factory for the chosen backend
    def make_opencv_cap():
        return open_stream(rtsp_url, buffersize=args.buffersize)

    def make_ffpipe_cap():
        return FFmpegPipeCapture(rtsp_url, hwaccel=args.hwaccel, transport=args.transport)

    cap_factory = make_ffpipe_cap if use_ffpipe else make_opencv_cap

    # Open capture — threaded mode creates cap inside its own thread to avoid
    # cross-thread FFmpeg assertion failures (pthread_frame.c async_lock)
    tcap = None
    if args.threaded:
        tcap = ThreadedCapture(cap_factory).start()
        if not tcap.isOpened():
            print("ERROR: Failed to open stream (threaded).")
            print("Checks:")
            print("  - Can you ping the Pi?")
            print("  - Is the RTSP server running on the Pi?")
            return 1
        width = int(tcap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        height = int(tcap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        reported_fps = float(tcap.get(cv2.CAP_PROP_FPS) or 0.0)
        print(f"Stream: {width}x{height} @ {reported_fps:.1f}fps")
        print("Threaded capture started (latest-frame mode)")
        print()
    else:
        cap = cap_factory()
        if not cap.isOpened():
            print("ERROR: Failed to open stream.")
            print("Checks:")
            print("  - Can you ping the Pi?")
            print("  - Is the RTSP server running on the Pi?")
            if use_ffpipe:
                print(f"  - Does your GPU/driver support {args.hwaccel}?")
                print("  - Is ffmpeg in your PATH?")
            return 1
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        reported_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        print(f"Stream: {width}x{height} @ {reported_fps:.1f}fps")
        print()

    # Detection example thresholds
    ORANGE_LOWER = np.array([5, 100, 100])
    ORANGE_UPPER = np.array([25, 255, 255])

    show_detection = bool(args.detect)

    # Recording state
    recording = False
    writer = None
    recording_filename = None
    recording_fps_tagged = 0.0

    def start_recording(tag_fps: float):
        nonlocal recording, writer, recording_filename, recording_fps_tagged
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        recording_filename = f"recording_{timestamp}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*args.record_fourcc)
        # Ensure sane fps
        tag_fps = tag_fps if tag_fps > 1.0 else (reported_fps if reported_fps > 1.0 else 30.0)
        writer = cv2.VideoWriter(recording_filename, fourcc, float(tag_fps), (int(width), int(height)))
        if not writer.isOpened():
            print(f"ERROR: Failed to open VideoWriter for {recording_filename}")
            writer = None
            recording = False
            recording_filename = None
            recording_fps_tagged = 0.0
            return
        recording = True
        recording_fps_tagged = float(tag_fps)
        print(f"Recording started: {recording_filename} (tagged fps={recording_fps_tagged:.2f})")

    def stop_recording():
        nonlocal recording, writer, recording_filename, recording_fps_tagged
        recording = False
        if writer is not None:
            writer.release()
            writer = None
        if recording_filename:
            print(f"Recording stopped: {recording_filename}")
        else:
            print("Recording stopped")
        recording_filename = None
        recording_fps_tagged = 0.0

    # Timing windows
    window_seconds = float(args.window_seconds)
    frame_times_total = deque()  # total-loop cadence timestamps (includes GUI)

    # Per-stage timing (sliding averages)
    timing_window_frames = 240
    read_dts = deque(maxlen=timing_window_frames)
    proc_dts = deque(maxlen=timing_window_frames)
    gui_dts = deque(maxlen=timing_window_frames)
    total_dts = deque(maxlen=timing_window_frames)

    # Bookkeeping
    frame_count = 0
    start_wall = time.time()
    last_display_t = 0.0
    last_print_t = 0.0

    # If display-fps is set, compute min interval
    display_min_dt = (1.0 / args.display_fps) if args.display_fps and args.display_fps > 0 else 0.0

    # Autostart recording? We'll tag fps after a brief warmup unless overridden
    pending_autorecord = bool(args.record_on_start)

    # Proc scale precompute
    proc_scale = clamp(args.proc_scale, 0.1, 1.0)

    while True:
        t_total0 = time.perf_counter()

        # 1) Read/decode/network time
        t_read0 = t_total0
        if tcap is not None:
            ret, frame = tcap.read()
            t_read1 = time.perf_counter()
            # In threaded mode, read() is near-instant (just a lock+swap).
            # The real decode time is tracked by the capture thread.
            read_dts.append(tcap.last_read_dt)
        else:
            ret, frame = cap.read()
            t_read1 = time.perf_counter()
            read_dts.append(t_read1 - t_read0)

        if not ret or frame is None:
            if tcap is not None:
                # Threaded mode: no frame ready yet, spin briefly
                time.sleep(0.001)
                continue
            else:
                print("WARN: Lost stream frame. Reconnecting...")
                cap.release()
                time.sleep(0.25)
                cap = cap_factory()
                continue

        frame_count += 1

        # 2) Processing time (your CV work)
        t_proc0 = time.perf_counter()

        # Only copy when we need to draw on the frame
        need_overlay = not args.headless and (show_detection or True)  # HUD always drawn
        if need_overlay:
            display_frame = frame.copy()
        else:
            display_frame = frame

        if show_detection:
            # Optional scale-down for faster detection at high resolutions
            if proc_scale < 1.0:
                small_h = int(frame.shape[0] * proc_scale)
                small_w = int(frame.shape[1] * proc_scale)
                small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv, ORANGE_LOWER, ORANGE_UPPER)
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                inv_scale = 1.0 / proc_scale
                for contour in contours:
                    area = cv2.contourArea(contour)
                    # Scale area threshold down proportionally
                    if area > 500 * (proc_scale ** 2):
                        x, y, w, h = cv2.boundingRect(contour)
                        # Scale coordinates back to full resolution
                        x = int(x * inv_scale)
                        y = int(y * inv_scale)
                        w = int(w * inv_scale)
                        h = int(h * inv_scale)
                        if need_overlay:
                            cv2.rectangle(display_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                            cx, cy = x + w // 2, y + h // 2
                            cv2.circle(display_frame, (cx, cy), 5, (0, 0, 255), -1)
                            cv2.putText(display_frame, f"({cx}, {cy})", (x, y - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            else:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv, ORANGE_LOWER, ORANGE_UPPER)
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > 500:
                        x, y, w, h = cv2.boundingRect(contour)
                        if need_overlay:
                            cv2.rectangle(display_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                            cx, cy = x + w // 2, y + h // 2
                            cv2.circle(display_frame, (cx, cy), 5, (0, 0, 255), -1)
                            cv2.putText(display_frame, f"({cx}, {cy})", (x, y - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        t_proc1 = time.perf_counter()
        proc_dts.append(t_proc1 - t_proc0)

        # 3) Compute FPS(win) based on TOTAL cadence (includes GUI)
        now = time.perf_counter()
        frame_times_total.append(now)
        cutoff = now - window_seconds
        while frame_times_total and frame_times_total[0] < cutoff:
            frame_times_total.popleft()

        if len(frame_times_total) >= 2:
            fps_win = (len(frame_times_total) - 1) / (frame_times_total[-1] - frame_times_total[0])
        else:
            fps_win = 0.0

        # Source FPS (from threaded capture, or same as fps_win in sequential mode)
        src_fps = tcap.src_fps if tcap is not None else fps_win

        # If user asked to record-on-start and we have a reasonable fps estimate, start now.
        # (Or immediately if record_fps override is provided.)
        if pending_autorecord:
            if args.record_fps and args.record_fps > 1.0:
                start_recording(args.record_fps)
                pending_autorecord = False
            elif fps_win > 1.0 and frame_count > 30:
                start_recording(fps_win)
                pending_autorecord = False

        # 4) Overlay HUD (small cost; included in proc path for display)
        if not args.headless:
            # Estimates from sliding averages (stage-specific)
            avg_read = tcap.avg_read_dt if tcap is not None else mean(read_dts)
            avg_proc = mean(proc_dts)
            avg_gui = mean(gui_dts)
            avg_total = mean(total_dts)

            read_fps_est = (1.0 / avg_read) if avg_read > 0 else 0.0
            total_fps_est = (1.0 / avg_total) if avg_total > 0 else 0.0

            if tcap is not None:
                grabbed = tcap.frames_grabbed
                dropped = tcap.frames_dropped
                drop_pct = (dropped / grabbed * 100) if grabbed > 0 else 0.0
                hud = (
                    f"src:{src_fps:4.0f}fps  app:{fps_win:4.0f}fps  "
                    f"dec:{avg_read*1000:5.1f}ms  "
                    f"proc:{avg_proc*1000:5.1f}ms  "
                    f"gui:{avg_gui*1000:5.1f}ms  "
                    f"drop:{drop_pct:3.0f}%"
                )
            else:
                hud = (
                    f"FPS(win): {fps_win:5.1f}  "
                    f"read_dt:{avg_read*1000:6.1f}ms(~{read_fps_est:4.0f})  "
                    f"proc_dt:{avg_proc*1000:6.1f}ms  "
                    f"gui_dt:{avg_gui*1000:6.1f}ms  "
                    f"tot_dt:{avg_total*1000:6.1f}ms(~{total_fps_est:4.0f})"
                )
            flags = []
            if recording:
                flags.append("REC")
            if show_detection:
                flags.append("DET")
            if args.display_fps and args.display_fps > 0:
                flags.append(f"DISP<={args.display_fps:g}")
            if args.hwaccel and args.hwaccel != "none":
                flags.append(args.hwaccel.upper())
            if args.threaded:
                flags.append("THR")
            if flags:
                hud += "  [" + " ".join(flags) + "]"

            cv2.putText(display_frame, hud, (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

        # 5) Recording: writes 1 frame per loop iteration
        if recording and writer is not None:
            writer.write(frame)

        # 6) GUI timing (imshow + waitKey) — often the culprit
        t_gui0 = time.perf_counter()
        key = 255  # default "no key"
        if not args.headless:
            do_display = True
            if display_min_dt > 0.0:
                # Rate-limit display updates
                if (time.perf_counter() - last_display_t) < display_min_dt:
                    do_display = False

            if do_display:
                cv2.imshow("RTSP Debug", display_frame)
                last_display_t = time.perf_counter()
                # waitKey pumps window messages; keep it small
                key = cv2.waitKey(1) & 0xFF
            else:
                # Still pump the window occasionally so it stays responsive
                # (This is cheap vs full imshow, but still helps with OS event handling)
                key = cv2.waitKey(1) & 0xFF
        t_gui1 = time.perf_counter()
        gui_dts.append(t_gui1 - t_gui0)

        # 7) Total timing (includes EVERYTHING)
        t_total1 = time.perf_counter()
        total_dts.append(t_total1 - t_total0)

        # Keyboard controls
        if not args.headless:
            if key == ord("q"):
                break
            elif key == ord("s"):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                fn = f"screenshot_{ts}.png"
                cv2.imwrite(fn, frame)
                print(f"Screenshot saved: {fn}")
            elif key == ord("d"):
                show_detection = not show_detection
                print(f"Detection overlay: {'ON' if show_detection else 'OFF'}")
            elif key == ord("r"):
                if recording:
                    stop_recording()
                else:
                    # If user overrides record fps, use it; otherwise use fps_win (measured cadence)
                    tag = args.record_fps if args.record_fps and args.record_fps > 1.0 else fps_win
                    start_recording(tag)

        # Periodic console print (useful in headless mode)
        # Print about once per second
        if (time.time() - last_print_t) >= 1.0:
            last_print_t = time.time()
            avg_read = tcap.avg_read_dt if tcap is not None else mean(read_dts)
            avg_proc = mean(proc_dts)
            avg_gui = mean(gui_dts)
            avg_total = mean(total_dts)

            read_fps_est = (1.0 / avg_read) if avg_read > 0 else 0.0
            total_fps_est = (1.0 / avg_total) if avg_total > 0 else 0.0

            extra = ""
            if recording:
                extra += f" REC(tag={recording_fps_tagged:.2f})"

            if tcap is not None:
                grabbed = tcap.frames_grabbed
                dropped = tcap.frames_dropped
                drop_pct = (dropped / grabbed * 100) if grabbed > 0 else 0.0
                print(
                    f"src_fps={src_fps:5.1f}  "
                    f"app_fps={fps_win:5.1f}  "
                    f"read={avg_read*1000:6.1f}ms(~{read_fps_est:4.0f})  "
                    f"proc={avg_proc*1000:6.1f}ms  "
                    f"gui={avg_gui*1000:6.1f}ms  "
                    f"total={avg_total*1000:6.1f}ms(~{total_fps_est:4.0f})  "
                    f"grab={grabbed} drop={dropped}({drop_pct:.0f}%)"
                    f"{extra}"
                )
            else:
                print(
                    f"fps_win={fps_win:5.1f}  "
                    f"read={avg_read*1000:6.1f}ms(~{read_fps_est:4.0f})  "
                    f"proc={avg_proc*1000:6.1f}ms  "
                    f"gui={avg_gui*1000:6.1f}ms  "
                    f"total={avg_total*1000:6.1f}ms(~{total_fps_est:4.0f})"
                    f"{extra}"
                )

        # Auto-exit
        if args.max_seconds and args.max_seconds > 0.0:
            if (time.time() - start_wall) >= args.max_seconds:
                break

    # Cleanup
    if tcap is not None:
        tcap.release()
    else:
        cap.release()
    if writer is not None:
        writer.release()
    if not args.headless:
        cv2.destroyAllWindows()

    elapsed = time.time() - start_wall
    avg_fps = (frame_count / elapsed) if elapsed > 0 else 0.0
    print()
    print(f"Frames processed: {frame_count}, wall time: {elapsed:.2f}s, avg app fps: {avg_fps:.2f}")
    if tcap is not None:
        grabbed = tcap.frames_grabbed
        dropped = tcap.frames_dropped
        drop_pct = (dropped / grabbed * 100) if grabbed > 0 else 0.0
        print(f"Capture thread: {grabbed} grabbed, {dropped} dropped ({drop_pct:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

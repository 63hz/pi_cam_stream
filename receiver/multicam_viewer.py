#!/usr/bin/env python3
"""
Multi-camera viewer for ScoutCam systems.

Auto-discovers scoutcam-* hosts via mDNS (zeroconf) or probes known hostnames,
then displays all feeds in an auto-sizing grid with status overlays.

Controls:
  q  quit
  f  toggle fullscreen

Examples:
  python multicam_viewer.py                                    # auto-discover
  python multicam_viewer.py --hosts scoutcam-blue.local        # specific camera
  python multicam_viewer.py --hosts scoutcam-blue.local scoutcam-red.local
  python multicam_viewer.py --no-discover --hosts scoutcam-blue.local
  # Dual-camera Pi 5 (two streams from one host):
  python multicam_viewer.py --no-discover --hosts 10.0.0.13 --paths /cam0 /cam1
"""

import argparse
import math
import os
import socket
import sys
import threading
import time
from collections import deque

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# ThreadedCapture: copied from rtsp_ingest_debug.py (proven pattern)
# Creates VideoCapture inside reader thread to avoid FFmpeg pthread assertion.
# ---------------------------------------------------------------------------
class ThreadedCapture:
    """Continuously grabs frames in a background thread, keeping only the latest.

    IMPORTANT: The VideoCapture is opened inside the reader thread to avoid
    cross-thread FFmpeg assertion failures (pthread_frame.c async_lock).
    """

    def __init__(self, cap_factory):
        """cap_factory: callable that returns a cv2.VideoCapture."""
        self._cap_factory = cap_factory
        self._lock = threading.Lock()
        self._frame = None
        self._ret = False
        self._running = False
        self._ready = threading.Event()

        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._opened = False

        self.frames_grabbed = 0
        self.frames_dropped = 0
        self._src_times = deque()
        self._src_fps = 0.0

    def start(self):
        self._running = True
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
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
            ret, frame = cap.read()
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
        with self._lock:
            frame = self._frame
            ret = self._ret
            self._frame = None
            self._ret = False
        return ret, frame

    @property
    def src_fps(self):
        with self._lock:
            return self._src_fps

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
# CameraFeed: wraps ThreadedCapture with reconnection and status tracking
# ---------------------------------------------------------------------------
class CameraFeed:
    """Manages a single camera connection with auto-reconnection."""

    # Status constants
    CONNECTING = "CONNECTING"
    LIVE = "LIVE"
    NO_SIGNAL = "NO_SIGNAL"

    RECONNECT_INTERVAL = 3.0  # seconds between reconnect attempts

    def __init__(self, hostname, port=8554, path="/cam", transport="tcp"):
        self.hostname = hostname
        self.port = port
        self.path = path
        self.transport = transport
        self.rtsp_url = f"rtsp://{hostname}:{port}{path}"

        self._lock = threading.Lock()
        self._status = self.CONNECTING
        self._capture = None  # type: ThreadedCapture | None
        self._last_frame = None
        self._fps = 0.0
        self._running = False
        self._thread = None

    @property
    def status(self):
        with self._lock:
            return self._status

    @property
    def fps(self):
        with self._lock:
            return self._fps

    @property
    def display_name(self):
        """Short name for overlay (strip .local; append path for dual-cam hosts)."""
        name = self.hostname
        if name.endswith(".local"):
            name = name[:-6]
        p = self.path.lstrip("/")
        if p and p != "cam":
            name = f"{name}/{p}"
        return name

    def start(self):
        """Start the feed manager thread."""
        self._running = True
        self._thread = threading.Thread(target=self._manager_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the feed and release resources."""
        self._running = False
        with self._lock:
            if self._capture is not None:
                self._capture.release()
                self._capture = None

    def read(self):
        """Return the latest frame, or None if unavailable."""
        with self._lock:
            if self._capture is not None and self._capture.isOpened():
                ret, frame = self._capture.read()
                if ret and frame is not None:
                    self._last_frame = frame
                    self._fps = self._capture.src_fps
                    self._status = self.LIVE
                    return frame
            return self._last_frame if self._status == self.LIVE else None

    def _manager_loop(self):
        """Background loop: connect, monitor, reconnect on failure."""
        while self._running:
            with self._lock:
                self._status = self.CONNECTING

            cap = self._try_connect()
            if cap is None:
                with self._lock:
                    self._status = self.NO_SIGNAL
                # Wait before retrying
                self._sleep(self.RECONNECT_INTERVAL)
                continue

            with self._lock:
                self._capture = cap
                self._status = self.LIVE

            # Monitor the connection
            while self._running:
                time.sleep(0.5)
                with self._lock:
                    if self._capture is None or not self._capture.isOpened():
                        break
                    # Check if we're still getting frames
                    if self._capture.frames_grabbed > 0 and self._capture.src_fps < 0.5:
                        # Stream stalled
                        break

            # Clean up dead connection
            with self._lock:
                if self._capture is not None:
                    self._capture.release()
                    self._capture = None
                self._status = self.NO_SIGNAL
                self._fps = 0.0

            if self._running:
                self._sleep(self.RECONNECT_INTERVAL)

    def _try_connect(self):
        """Attempt to connect to the RTSP stream. Returns ThreadedCapture or None."""
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{self.transport}"

        url = self.rtsp_url

        def factory():
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap

        try:
            cap = ThreadedCapture(factory).start()
            if cap.isOpened():
                return cap
            cap.release()
        except Exception:
            pass
        return None

    def _sleep(self, duration):
        """Interruptible sleep."""
        end = time.monotonic() + duration
        while self._running and time.monotonic() < end:
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# CameraDiscovery: find scoutcam-* hosts via mDNS or hostname probing
# ---------------------------------------------------------------------------
class CameraDiscovery:
    """Discovers scoutcam-* hosts on the local network."""

    # Default hostnames to probe if zeroconf is unavailable
    DEFAULT_HOSTNAMES = [
        "scoutcam.local",
        "scoutcam-blue.local",
        "scoutcam-red.local",
        "scoutcam-green.local",
    ]

    def __init__(self):
        self._lock = threading.Lock()
        self._discovered = {}  # hostname -> True
        self._zeroconf = None
        self._browser = None

    def start(self):
        """Start discovery in a background thread."""
        t = threading.Thread(target=self._discover, daemon=True)
        t.start()

    def get_hosts(self):
        """Return list of discovered hostnames."""
        with self._lock:
            return list(self._discovered.keys())

    def stop(self):
        """Stop discovery and clean up."""
        if self._browser is not None:
            self._browser.cancel()
            self._browser = None
        if self._zeroconf is not None:
            self._zeroconf.close()
            self._zeroconf = None

    def _discover(self):
        """Try zeroconf first, fall back to hostname probing."""
        # Try zeroconf-based mDNS discovery
        if self._try_zeroconf():
            return

        # Fallback: probe known hostnames
        print("[discovery] zeroconf not available, probing known hostnames...")
        self._probe_hostnames(self.DEFAULT_HOSTNAMES)

    def _try_zeroconf(self):
        """Attempt zeroconf mDNS discovery. Returns True if zeroconf is available."""
        try:
            from zeroconf import ServiceBrowser, Zeroconf

            print("[discovery] Starting mDNS discovery via zeroconf...")
            self._zeroconf = Zeroconf()

            class Listener:
                def __init__(self, parent):
                    self._parent = parent

                def add_service(self, zc, svc_type, name):
                    self._check_name(name)

                def update_service(self, zc, svc_type, name):
                    self._check_name(name)

                def remove_service(self, zc, svc_type, name):
                    pass

                def _check_name(self, name):
                    # mDNS service names look like "scoutcam-blue._workstation._tcp.local."
                    # Extract the hostname part
                    hostname_part = name.split(".")[0]
                    if hostname_part.startswith("scoutcam"):
                        fqdn = f"{hostname_part}.local"
                        with self._parent._lock:
                            if fqdn not in self._parent._discovered:
                                self._parent._discovered[fqdn] = True
                                print(f"[discovery] Found: {fqdn}")

            self._browser = ServiceBrowser(
                self._zeroconf, "_workstation._tcp.local.", Listener(self)
            )
            return True

        except ImportError:
            print(
                "[discovery] WARNING: zeroconf package not installed. "
                "Install with: pip install zeroconf"
            )
            return False
        except Exception as e:
            print(f"[discovery] WARNING: zeroconf failed: {e}")
            return False

    def _probe_hostnames(self, hostnames):
        """Probe a list of hostnames via DNS resolution."""
        for hostname in hostnames:
            try:
                socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
                with self._lock:
                    if hostname not in self._discovered:
                        self._discovered[hostname] = True
                        print(f"[discovery] Found (probe): {hostname}")
            except socket.gaierror:
                pass


# ---------------------------------------------------------------------------
# MultiCamViewer: main viewer class
# ---------------------------------------------------------------------------
class MultiCamViewer:
    """Displays multiple camera feeds in an auto-sizing grid."""

    WINDOW_NAME = "ScoutCam Multi-Camera Viewer"

    # Colors (BGR)
    COLOR_GREEN = (0, 255, 0)
    COLOR_RED = (0, 0, 255)
    COLOR_GRAY = (128, 128, 128)
    COLOR_WHITE = (255, 255, 255)
    COLOR_BG = (30, 30, 30)

    def __init__(self, args):
        self.args = args
        self.feeds = {}  # hostname -> CameraFeed
        self.discovery = None
        self._fullscreen = args.fullscreen
        self._running = True

    def run(self):
        """Main loop: discover cameras, manage feeds, render grid."""
        # Set up window
        cv2.namedWindow(self.WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW_NAME, self.args.width, self.args.height)
        if self._fullscreen:
            cv2.setWindowProperty(
                self.WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
            )

        # Start with explicit hosts (one feed per host x path)
        if self.args.hosts:
            for host in self.args.hosts:
                for path in self.args.paths:
                    self._add_feed(host, path)

        # Start auto-discovery
        if not self.args.no_discover:
            self.discovery = CameraDiscovery()
            self.discovery.start()

        print(f"Window: {self.args.width}x{self.args.height}")
        print("Controls: q = quit, f = toggle fullscreen")
        print()

        try:
            while self._running:
                # Check for newly discovered cameras
                if self.discovery is not None:
                    for host in self.discovery.get_hosts():
                        for path in self.args.paths:
                            if self._feed_key(host, path) not in self.feeds:
                                self._add_feed(host, path)

                # Render the grid
                canvas = self._render_grid()
                cv2.imshow(self.WINDOW_NAME, canvas)

                # Handle keyboard
                key = cv2.waitKey(16) & 0xFF  # ~60fps render loop
                if key == ord("q"):
                    break
                elif key == ord("f"):
                    self._toggle_fullscreen()

        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()

    @staticmethod
    def _feed_key(hostname, path):
        """Unique key per feed so two paths on the same host don't collide."""
        return f"{hostname}{path}"

    def _add_feed(self, hostname, path):
        """Add a new camera feed for a given host + RTSP path."""
        key = self._feed_key(hostname, path)
        if key in self.feeds:
            return
        print(f"[viewer] Adding feed: {hostname}{path}")
        feed = CameraFeed(
            hostname,
            port=self.args.port,
            path=path,
            transport=self.args.transport,
        )
        feed.start()
        self.feeds[key] = feed

    def _render_grid(self):
        """Render all feeds into a single canvas."""
        # Get window size
        try:
            rect = cv2.getWindowImageRect(self.WINDOW_NAME)
            win_w, win_h = rect[2], rect[3]
        except Exception:
            win_w, win_h = self.args.width, self.args.height

        if win_w <= 0 or win_h <= 0:
            win_w, win_h = self.args.width, self.args.height

        canvas = np.full((win_h, win_w, 3), self.COLOR_BG, dtype=np.uint8)

        feed_list = list(self.feeds.values())
        n = len(feed_list)

        if n == 0:
            # No cameras — show waiting message
            self._draw_centered_text(canvas, "Waiting for cameras...", self.COLOR_GRAY)
            return canvas

        # Compute grid layout: cols = ceil(sqrt(n)), rows = ceil(n/cols)
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        tile_w = win_w // cols
        tile_h = win_h // rows

        for i, feed in enumerate(feed_list):
            row = i // cols
            col = i % cols
            x0 = col * tile_w
            y0 = row * tile_h
            self._render_tile(canvas, feed, x0, y0, tile_w, tile_h)

        return canvas

    def _render_tile(self, canvas, feed, x0, y0, tile_w, tile_h):
        """Render a single camera tile onto the canvas."""
        frame = feed.read()
        status = feed.status

        if frame is not None:
            # Fit frame into tile preserving aspect ratio
            fh, fw = frame.shape[:2]
            scale = min(tile_w / fw, tile_h / fh)
            new_w = int(fw * scale)
            new_h = int(fh * scale)
            resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

            # Center in tile
            pad_x = (tile_w - new_w) // 2
            pad_y = (tile_h - new_h) // 2
            canvas[y0 + pad_y : y0 + pad_y + new_h, x0 + pad_x : x0 + pad_x + new_w] = resized
        else:
            # No frame — draw status text
            sub = canvas[y0 : y0 + tile_h, x0 : x0 + tile_w]
            if status == CameraFeed.CONNECTING:
                self._draw_centered_text(sub, "CONNECTING...", self.COLOR_GRAY)
            else:
                self._draw_centered_text(sub, "NO SIGNAL", self.COLOR_RED)

        # Draw hostname label (top-left)
        label = feed.display_name
        self._draw_text_bg(
            canvas, label, (x0 + 8, y0 + 28), 0.7, self.COLOR_WHITE
        )

        # Draw FPS counter (top-right) — only when live
        if status == CameraFeed.LIVE and feed.fps > 0:
            fps_text = f"{feed.fps:.0f} fps"
            text_size = cv2.getTextSize(fps_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
            fps_x = x0 + tile_w - text_size[0] - 12
            self._draw_text_bg(
                canvas, fps_text, (fps_x, y0 + 28), 0.6, self.COLOR_GREEN
            )

        # Draw border between tiles
        cv2.rectangle(canvas, (x0, y0), (x0 + tile_w - 1, y0 + tile_h - 1), (60, 60, 60), 1)

    def _draw_centered_text(self, img, text, color, scale=1.0):
        """Draw text centered in the image."""
        h, w = img.shape[:2]
        text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0]
        tx = (w - text_size[0]) // 2
        ty = (h + text_size[1]) // 2
        cv2.putText(
            img, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2
        )

    def _draw_text_bg(self, img, text, pos, scale, color):
        """Draw text with a dark background rectangle for readability."""
        font = cv2.FONT_HERSHEY_SIMPLEX
        thickness = 2
        text_size, baseline = cv2.getTextSize(text, font, scale, thickness)
        x, y = pos
        # Background rectangle
        cv2.rectangle(
            img,
            (x - 4, y - text_size[1] - 4),
            (x + text_size[0] + 4, y + baseline + 4),
            (0, 0, 0),
            cv2.FILLED,
        )
        # Text
        cv2.putText(img, text, (x, y), font, scale, color, thickness)

    def _toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            cv2.setWindowProperty(
                self.WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
            )
        else:
            cv2.setWindowProperty(
                self.WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL
            )

    def _cleanup(self):
        """Stop all feeds and close window."""
        print("\nShutting down...")
        for feed in self.feeds.values():
            feed.stop()
        if self.discovery is not None:
            self.discovery.stop()
        cv2.destroyAllWindows()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Multi-camera viewer for ScoutCam systems."
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        default=[],
        help="Explicit hostnames (e.g., scoutcam-blue.local scoutcam-red.local)",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Disable auto-discovery, use --hosts only",
    )
    parser.add_argument(
        "--port", type=int, default=8554, help="RTSP port (default: 8554)"
    )
    parser.add_argument(
        "--path", default="/cam", help="RTSP path for single-camera hosts (default: /cam)"
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=None,
        help="Multiple RTSP paths per host for dual-camera Pis, e.g. /cam0 /cam1. "
        "Overrides --path; each host gets one feed per path.",
    )
    parser.add_argument(
        "--width", type=int, default=1280, help="Window width (default: 1280)"
    )
    parser.add_argument(
        "--height", type=int, default=720, help="Window height (default: 720)"
    )
    parser.add_argument(
        "--transport",
        default="tcp",
        choices=["tcp", "udp"],
        help="RTSP transport (default: tcp)",
    )
    parser.add_argument(
        "--fullscreen", action="store_true", help="Start in fullscreen mode"
    )

    args = parser.parse_args()

    # Normalize paths: --paths (dual-cam) overrides --path (single-cam default)
    args.paths = args.paths or [args.path]

    if not args.hosts and args.no_discover:
        print("ERROR: --no-discover requires --hosts to specify at least one camera.")
        return 1

    print("=" * 60)
    print("  ScoutCam Multi-Camera Viewer")
    print("=" * 60)
    if args.hosts:
        print(f"  Hosts: {', '.join(args.hosts)}")
    if not args.no_discover:
        print("  Auto-discovery: enabled")
    else:
        print("  Auto-discovery: disabled")
    print(f"  RTSP: port={args.port} paths={' '.join(args.paths)} transport={args.transport}")
    print()

    viewer = MultiCamViewer(args)
    viewer.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Real-time pose comparison demo: RF-DETR vs YOLO26, side-by-side on one feed.

Two brand-new 2026 pose models run on the SAME live input and are drawn with one
consistent skeleton style, each labeled with its own live FPS so the speed and
accuracy differences are visible at a glance.

  Left  : RF-DETR  (rfdetr.RFDETRKeypointPreview)
  Right : YOLO26   (ultralytics yolo26*-pose)

Input (--source) accepts a fleet camera name (from cameras.json), an rtsp:// URL,
a webcam index, or a video file. Pose tracks people (COCO-17 keypoints), so aim
the camera at a person.

  python pose_compare.py                         # default: pi5-cam0
  python pose_compare.py --source 0              # laptop webcam
  python pose_compare.py --source blue --yolo yolo26m-pose.pt --imgsz 512

Keys:  q quit   s save side-by-side snapshot
"""

import argparse
import os
import sys
import threading
import time
from collections import deque

import cv2
import numpy as np

# Reuse the crash-safe latest-frame RTSP capture and the fleet registry.
from multicam_viewer import ThreadedCapture
import calib

import supervision as sv

import warnings
import logging
warnings.filterwarnings("ignore")           # quiet supervision/torch deprecation spam
logging.getLogger("rf-detr").setLevel(logging.ERROR)

# COCO-17 skeleton (edges between keypoint indices) for consistent drawing.
COCO_EDGES = [
    (0, 1), (0, 2), (1, 3), (2, 4), (0, 5), (0, 6), (5, 6), (5, 7), (7, 9),
    (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
]


# --------------------------------------------------------------------------
# source resolution
# --------------------------------------------------------------------------
def build_source(spec, transport="tcp"):
    """Return (label, cap_factory) for a fleet name / rtsp url / webcam index / file."""
    # webcam index
    if spec.isdigit():
        idx = int(spec)
        return f"webcam:{idx}", lambda: cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    # local video file
    if os.path.exists(spec):
        return os.path.basename(spec), lambda: cv2.VideoCapture(spec)
    # fleet camera name -> url, else assume it's already a url
    cam = calib.get_camera(spec)
    if cam:
        url, label = cam["url"], spec
    else:
        url, label = spec, spec
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}"

    def factory():
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    return label, factory


# --------------------------------------------------------------------------
# shared latest-frame hub: one reader, many consumers
# --------------------------------------------------------------------------
class FrameHub:
    """Continuously pulls the latest frame from a ThreadedCapture so multiple
    model workers can each grab the most recent frame without stealing it."""

    def __init__(self, tcap):
        self.tcap = tcap
        self._lock = threading.Lock()
        self._frame = None
        self._running = True

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def _loop(self):
        while self._running:
            ret, f = self.tcap.read()
            if ret and f is not None:
                with self._lock:
                    self._frame = f
            else:
                time.sleep(0.005)

    def get(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def stop(self):
        self._running = False
        try:
            self.tcap.release()
        except Exception:
            pass


# --------------------------------------------------------------------------
# pose worker: one model, its own thread, publishes annotated frame + fps
# --------------------------------------------------------------------------
class PoseWorker(threading.Thread):
    def __init__(self, name, hub, infer_fn, annotate_fn, every_n=1):
        super().__init__(daemon=True)
        self.name = name
        self.hub = hub
        self.infer_fn = infer_fn
        self.annotate_fn = annotate_fn
        self.every_n = max(1, every_n)
        self._lock = threading.Lock()
        self._out = None
        self._inf_ts = deque(maxlen=30)
        self._last_kp = None
        self._i = 0
        self._running = True
        self.ready = threading.Event()
        self.error = None

    def run(self):
        while self._running:
            frame = self.hub.get()
            if frame is None:
                time.sleep(0.005)
                continue
            self._i += 1
            try:
                if self.every_n > 1 and self._i % self.every_n != 0 and self._last_kp is not None:
                    kp = self._last_kp  # reuse last result on skipped frames
                else:
                    kp = self.infer_fn(frame)
                    self._last_kp = kp
                    self._inf_ts.append(time.perf_counter())
                annotated = self.annotate_fn(frame, kp)
            except Exception as e:  # keep the demo alive; surface once
                if self.error is None:
                    self.error = repr(e)
                    print(f"[{self.name}] inference error: {e}")
                annotated = frame
            with self._lock:
                self._out = annotated
            self.ready.set()

    @property
    def fps(self):
        ts = self._inf_ts
        if len(ts) < 2:
            return 0.0
        return (len(ts) - 1) / (ts[-1] - ts[0])

    def latest(self):
        with self._lock:
            return self._out

    def stop(self):
        self._running = False


# --------------------------------------------------------------------------
# model setup
# --------------------------------------------------------------------------
def _keypoint_conf(kp):
    c = getattr(kp, "keypoint_confidence", None)
    if c is None:
        c = getattr(kp, "confidence", None)
    return c


def make_annotator(conf_thr=0.5):
    """Confidence-filtered COCO skeleton drawer, identical style for both models.

    Both RF-DETR and YOLO return sv.KeyPoints with .xy (N,17,2) and per-keypoint
    confidence; filtering low-confidence joints avoids stray lines to off-screen
    (e.g. cropped-off legs) and keeps the comparison fair (same drawing for both).
    """

    def annotate(frame, kp):
        if not isinstance(kp, sv.KeyPoints) or kp.xy is None or len(kp) == 0:
            return frame  # RF-DETR returns sv.Detections when no person is found
        out = frame.copy()
        conf = _keypoint_conf(kp)
        for p in range(len(kp.xy)):
            pts = kp.xy[p]
            c = conf[p] if conf is not None else np.ones(len(pts))
            for a, b in COCO_EDGES:
                if a < len(pts) and b < len(pts) and c[a] >= conf_thr and c[b] >= conf_thr:
                    cv2.line(out, (int(pts[a][0]), int(pts[a][1])),
                             (int(pts[b][0]), int(pts[b][1])), (0, 230, 0), 2, cv2.LINE_AA)
            for i in range(len(pts)):
                if c[i] >= conf_thr:
                    cv2.circle(out, (int(pts[i][0]), int(pts[i][1])), 4, (255, 80, 0), -1, cv2.LINE_AA)
        return out

    return annotate


def load_rfdetr(conf, device):
    from rfdetr import RFDETRKeypointPreview

    # NOTE: do NOT call optimize_for_inference() — for the keypoint-preview model
    # it drops the keypoint head and predict() returns boxes (Detections) only.
    # The plain model returns real KeyPoints (~14 fps on this GPU).
    model = RFDETRKeypointPreview()

    def infer(bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return model.predict(rgb, threshold=conf)

    return infer


def load_yolo(weights, imgsz, conf, device):
    from ultralytics import YOLO

    model = YOLO(weights)
    try:
        model.to(device)
    except Exception:
        pass

    def infer(bgr):
        r = model.predict(bgr, imgsz=imgsz, conf=conf, device=device, verbose=False)[0]
        return sv.KeyPoints.from_ultralytics(r)

    return infer


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def draw_banner(img, text, sub):
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, 0), (w, 40), (0, 0, 0), -1)
    cv2.putText(img, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    tw = cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0][0]
    cv2.putText(img, sub, (w - tw - 12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
    return img


def fit_height(img, target_h):
    h, w = img.shape[:2]
    scale = target_h / h
    return cv2.resize(img, (int(w * scale), target_h), interpolation=cv2.INTER_LINEAR)


def compose(left, left_lbl, left_fps, right, right_lbl, right_fps, target_h, device):
    left = fit_height(left, target_h).copy()
    right = fit_height(right, target_h).copy()
    draw_banner(left, left_lbl, f"{left_fps:4.1f} fps")
    draw_banner(right, right_lbl, f"{right_fps:4.1f} fps")
    divider = np.full((target_h, 4, 3), (40, 40, 40), np.uint8)
    canvas = np.hstack([left, divider, right])
    tag = f"GPU: {device}"
    cv2.putText(canvas, tag, (12, canvas.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1, cv2.LINE_AA)
    return canvas


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="RF-DETR vs YOLO26 pose, side-by-side.")
    ap.add_argument("--source", default="pi5-cam0",
                    help="fleet name / rtsp url / webcam index / video file (default: pi5-cam0)")
    ap.add_argument("--yolo", default="yolo26s-pose.pt", help="YOLO26 pose weights")
    ap.add_argument("--imgsz", type=int, default=640, help="YOLO inference size")
    ap.add_argument("--conf", type=float, default=0.4, help="detection confidence threshold")
    ap.add_argument("--kp-conf", type=float, default=0.5,
                    help="min per-keypoint confidence to draw a joint/edge")
    ap.add_argument("--rfdetr-every-n", type=int, default=1,
                    help="run RF-DETR every Nth frame (hold last skeleton) if it lags")
    ap.add_argument("--height", type=int, default=720, help="display tile height")
    ap.add_argument("--transport", default="tcp", choices=["tcp", "udp"])
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    import torch
    device = args.device if (args.device != "cuda" or torch.cuda.is_available()) else "cpu"
    if device == "cpu":
        print("WARNING: CUDA not available - running on CPU (slow). See plan Step 1.")
    dev_name = torch.cuda.get_device_name(0) if device == "cuda" else "cpu"
    print(f"Device: {device} ({dev_name})")

    label, factory = build_source(args.source, args.transport)
    print(f"Source: {label}")

    print("Loading models (first run downloads weights)...")
    annotate = make_annotator(args.kp_conf)
    rf_infer = load_rfdetr(args.conf, device)
    print("  RF-DETR ready.")
    yolo_infer = load_yolo(args.yolo, args.imgsz, args.conf, device)
    print("  YOLO26 ready.")

    tcap = ThreadedCapture(factory).start()
    if not tcap.isOpened():
        sys.exit(f"ERROR: could not open source: {label}")
    hub = FrameHub(tcap).start()

    rf_worker = PoseWorker("RF-DETR", hub, rf_infer, annotate, every_n=args.rfdetr_every_n)
    yolo_worker = PoseWorker("YOLO26", hub, yolo_infer, annotate)
    rf_worker.start()
    yolo_worker.start()

    win = "Pose: RF-DETR (left) vs YOLO26 (right)  -  q quit, s snapshot"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    print("Running. Aim the camera at a person. q quit, s snapshot.")
    try:
        while True:
            base = hub.get()
            li = rf_worker.latest()
            ri = yolo_worker.latest()
            if li is None:
                li = base
            if ri is None:
                ri = base
            if li is None or ri is None:
                if cv2.waitKey(30) & 0xFF == ord("q"):
                    break
                continue
            canvas = compose(li, "RF-DETR  (KeypointPreview)", rf_worker.fps,
                              ri, f"YOLO26  ({os.path.basename(args.yolo)})", yolo_worker.fps,
                              args.height, dev_name)
            cv2.imshow(win, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pose_snapshot.png")
                cv2.imwrite(p, canvas)
                print(f"saved {p}")
    finally:
        rf_worker.stop()
        yolo_worker.stop()
        hub.stop()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())

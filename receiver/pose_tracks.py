#!/usr/bin/env python3
"""
YOLO26 pose tracking demo - unique per-person skeletons + fading motion trails
+ a cumulative usage heatmap. Built toward mapping a manufacturing floor:
traffic patterns and high/low-usage areas.

  - Persistent tracking (ByteTrack) gives each person a stable ID and color.
  - A fading trail follows each person's anchor point (oldest fades first).
  - Press 'h' to reveal a cumulative heatmap of where people have been -
    the "floor usage" map.

Anchor point (the tracked "position") - see --anchor:
  chest (default) : center of the upper torso (just below the shoulder line).
                    Stable, high-confidence, and well clear of the waistline.
  shoulders       : midpoint of the two shoulders (base of the neck).
  feet            : midpoint of the two ankles -> falls back to box bottom-center.
                    This is the FLOOR-CONTACT point; use it when projecting onto a
                    floor plan (it's where the person actually stands).
  hips            : midpoint of the two hips (true body centroid, but low).
  bbox            : bottom-center of the bounding box (robust floor proxy).

  python pose_tracks.py                          # default: pi5-cam0
  python pose_tracks.py --source 0 --anchor feet # webcam, floor-contact anchor

Keys:  q quit | s snapshot | h heatmap | t trails | c clear | b boxes
"""

import argparse
import math
import os
import sys
import time
from collections import deque

import cv2
import numpy as np

from multicam_viewer import ThreadedCapture
from pose_compare import build_source, COCO_EDGES  # reuse capture + skeleton edges

import warnings
import logging
warnings.filterwarnings("ignore")
logging.getLogger("ultralytics").setLevel(logging.ERROR)


def id_color(i):
    """Distinct, repeatable BGR color per track id (golden-ratio hue)."""
    h = (int(i) * 0.61803398875) % 1.0
    bgr = cv2.cvtColor(np.uint8([[[int(h * 179), 200, 255]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def anchor_point(mode, xy, conf, box, kp_thr):
    """Return (x, y) anchor for a person. xy:(17,2) conf:(17,) box:(x1,y1,x2,y2)."""
    x1, y1, x2, y2 = box

    def mid(a, b):
        if conf[a] >= kp_thr and conf[b] >= kp_thr:
            return np.array([(xy[a][0] + xy[b][0]) / 2.0, (xy[a][1] + xy[b][1]) / 2.0])
        return None

    shoulders = mid(5, 6)
    hips = mid(11, 12)

    if mode == "chest":
        # center of upper torso: shoulders, nudged ~1/3 toward hips (the sternum).
        if shoulders is not None and hips is not None:
            return tuple(shoulders + 0.33 * (hips - shoulders))
        if shoulders is not None:
            return tuple(shoulders)
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    if mode == "shoulders":
        return tuple(shoulders) if shoulders is not None else ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    if mode == "hips":
        return tuple(hips) if hips is not None else ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    if mode == "feet":
        feet = mid(15, 16)
        return tuple(feet) if feet is not None else ((x1 + x2) / 2.0, y2)
    return ((x1 + x2) / 2.0, y2)  # bbox bottom-center


def draw_skeleton(img, xy, conf, color, kp_thr):
    for a, b in COCO_EDGES:
        if conf[a] >= kp_thr and conf[b] >= kp_thr:
            cv2.line(img, (int(xy[a][0]), int(xy[a][1])), (int(xy[b][0]), int(xy[b][1])),
                     color, 2, cv2.LINE_AA)
    for i in range(len(xy)):
        if conf[i] >= kp_thr:
            cv2.circle(img, (int(xy[i][0]), int(xy[i][1])), 3, color, -1, cv2.LINE_AA)


def colorize_heat(heat):
    """Colorized, additive-friendly heat layer (low-res, uint8).

    Intensity-scaled so unused areas are ~black (add nothing) and hot areas glow;
    this lets the composite be a single fast cv2.add instead of a masked blend.
    """
    mx = float(np.log1p(heat).max())
    if mx <= 0:
        return np.zeros((*heat.shape, 3), np.uint8)
    norm = np.log1p(heat) / mx
    cm = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    return (cm * norm[..., None]).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description="YOLO26 pose tracking + trails + heatmap.")
    ap.add_argument("--source", default="pi5-cam0",
                    help="fleet name / rtsp url / webcam index / video file (default: pi5-cam0)")
    ap.add_argument("--yolo", default="yolo26s-pose.pt", help="YOLO26 pose weights")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.4, help="detection confidence")
    ap.add_argument("--kp-conf", type=float, default=0.5, help="min keypoint confidence to draw")
    ap.add_argument("--anchor", default="chest",
                    choices=["chest", "shoulders", "hips", "feet", "bbox"],
                    help="tracked position point (default chest; feet/bbox = floor contact)")
    ap.add_argument("--trail-seconds", type=float, default=6.0, help="trail fade time")
    ap.add_argument("--overlay-scale", type=float, default=0.5,
                    help="resolution scale for the trail/heatmap layers (lower = faster)")
    ap.add_argument("--heat-refresh", type=int, default=5,
                    help="recolorize heatmap every N frames (accumulation stays per-frame)")
    ap.add_argument("--tracker", default="bytetrack.yaml")
    ap.add_argument("--transport", default="tcp", choices=["tcp", "udp"])
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    import torch
    device = args.device if (args.device != "cuda" or torch.cuda.is_available()) else "cpu"
    dev_name = torch.cuda.get_device_name(0) if device == "cuda" else "cpu"
    print(f"Device: {device} ({dev_name})")

    label, factory = build_source(args.source, args.transport)
    print(f"Source: {label}  | anchor: {args.anchor}")

    from ultralytics import YOLO
    model = YOLO(args.yolo)
    model.to(device)
    print(f"Loaded {args.yolo}.")

    cap = ThreadedCapture(factory).start()
    if not cap.isOpened():
        sys.exit(f"ERROR: could not open source: {label}")

    win = "YOLO26 tracking - traffic / floor usage   (q s h t c b)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)

    tau = max(0.1, args.trail_seconds / 3.0)   # fade so a point is ~5% after trail_seconds
    trail = None               # low-res uint8 trail layer
    heat = None                # low-res float32 accumulator
    heat_vis = None            # cached colorized heat (recomputed every --heat-refresh)
    ow = oh = heat_r = 0
    sx = sy = 1.0
    prev = {}                  # id -> (x, y) last anchor in OVERLAY coords
    seen = {}                  # id -> last loop index (for pruning prev)
    show_heat, show_trails, show_boxes = False, True, False
    fps_t = deque(maxlen=30)
    last = time.perf_counter()
    loop = 0
    print("Running. q quit | s snapshot | h heatmap | t trails | c clear | b boxes")
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                if cv2.waitKey(15) & 0xFF == ord("q"):
                    break
                time.sleep(0.005)
                continue
            loop += 1
            now = time.perf_counter()
            dt = now - last
            last = now
            fps_t.append(now)

            H, W = frame.shape[:2]
            if trail is None:
                ow, oh = max(1, int(W * args.overlay_scale)), max(1, int(H * args.overlay_scale))
                sx, sy = ow / W, oh / H
                trail = np.zeros((oh, ow, 3), np.uint8)
                heat = np.zeros((oh, ow), np.float32)
                heat_r = max(3, int(16 * args.overlay_scale))
            if show_trails:
                # SIMD exponential fade on a small uint8 layer (releases the GIL)
                trail = cv2.convertScaleAbs(trail, alpha=math.exp(-dt / tau))

            res = model.track(frame, persist=True, tracker=args.tracker, imgsz=args.imgsz,
                              conf=args.conf, device=device, verbose=False)[0]

            people = []
            if res.boxes is not None and res.boxes.id is not None and res.keypoints is not None:
                ids = res.boxes.id.int().cpu().tolist()
                boxes = res.boxes.xyxy.cpu().numpy()
                kxy = res.keypoints.xy.cpu().numpy()
                kconf = (res.keypoints.conf.cpu().numpy() if res.keypoints.conf is not None
                         else np.ones((len(ids), 17)))
                for i, tid in enumerate(ids):
                    color = id_color(tid)
                    ax, ay = anchor_point(args.anchor, kxy[i], kconf[i], boxes[i], args.kp_conf)
                    ax, ay = int(ax), int(ay)
                    people.append((tid, color, kxy[i], kconf[i], boxes[i], (ax, ay)))
                    seen[tid] = loop
                    # trail + heat are drawn in the low-res overlay space
                    oax, oay = int(ax * sx), int(ay * sy)
                    if 0 <= oax < ow and 0 <= oay < oh:
                        if tid in prev:
                            cv2.line(trail, prev[tid], (oax, oay), color, 2, cv2.LINE_AA)
                        prev[tid] = (oax, oay)
                        cv2.circle(heat, (oax, oay), heat_r, 1.0, -1)

            # prune stale prev entries (left the frame a while ago)
            for tid in [t for t, l in seen.items() if loop - l > 60]:
                prev.pop(tid, None)
                seen.pop(tid, None)

            # compose: frame -> trails (additive) -> heatmap -> skeletons -> HUD.
            # Overlays are upscaled from the small layer and added with one SIMD op each.
            display = frame
            if show_trails:
                display = cv2.add(display, cv2.resize(trail, (W, H), interpolation=cv2.INTER_LINEAR))
            if show_heat:
                if heat_vis is None or loop % max(1, args.heat_refresh) == 0:
                    heat_vis = colorize_heat(heat)
                display = cv2.add(display, cv2.resize(heat_vis, (W, H), interpolation=cv2.INTER_LINEAR))
            for tid, color, xy, conf, box, (ax, ay) in people:
                if show_boxes:
                    x1, y1, x2, y2 = box.astype(int)
                    cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                draw_skeleton(display, xy, conf, color, args.kp_conf)
                cv2.circle(display, (ax, ay), 6, (255, 255, 255), -1, cv2.LINE_AA)
                cv2.circle(display, (ax, ay), 6, color, 2, cv2.LINE_AA)
                cv2.putText(display, f"ID {tid}", (ax + 8, ay - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

            fps = (len(fps_t) - 1) / (fps_t[-1] - fps_t[0]) if len(fps_t) > 1 else 0.0
            hud = (f"YOLO26  people:{len(people)}  {fps:4.1f} fps  anchor:{args.anchor}"
                   f"  [trails:{'on' if show_trails else 'off'}  heat:{'on' if show_heat else 'off'}]")
            cv2.rectangle(display, (0, 0), (W, 34), (0, 0, 0), -1)
            cv2.putText(display, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)

            cv2.imshow(win, display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("h"):
                show_heat = not show_heat
            elif key == ord("t"):
                show_trails = not show_trails
            elif key == ord("b"):
                show_boxes = not show_boxes
            elif key == ord("c"):
                trail[:] = 0; heat[:] = 0; heat_vis = None; prev.clear()
                print("cleared trails + heatmap")
            elif key == ord("s"):
                p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracks_snapshot.png")
                cv2.imwrite(p, display)
                print(f"saved {p}")
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
ScoutCam per-camera calibration tool.

Computes each camera's intrinsics + lens distortion so frames can be
undistorted (and, later, registered onto a common plane). Built to scale: add
a camera to cameras.json and calibrate it by name, or point the tool at any
--url / --image-dir for a one-off.

Workflow
--------
  1. Print a board:   python calibrate_camera.py make-board
  2. Capture views:   python calibrate_camera.py capture --camera blue
       Hold the printed checkerboard so it fills different parts of the frame
       (corners, edges, center) and tilt it; the tool auto-grabs ~20 good,
       well-spread views. SPACE = grab now, u = undo, c = calibrate now, q = quit.
  3. Calibrate:       python calibrate_camera.py calibrate --camera blue
       (auto-runs after capture if you press 'c'). For wide lenses (IMX708
       wide) add --rational, or --model fisheye for the strongest distortion.
  4. Verify:          python calibrate_camera.py verify --camera blue
       Side-by-side raw vs. undistorted on the live stream.
  5. Status:          python calibrate_camera.py list

Results are saved to receiver/calibration/<name>.json (one file per camera).
Calibration is resolution-specific; recalibrate if you change a camera's profile.
"""

import argparse
import os
import sys
import time

import cv2
import numpy as np

import calib
from multicam_viewer import ThreadedCapture  # reuse the crash-safe capture


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def resolve_board(args) -> calib.BoardSpec:
    base = calib.default_board()
    return calib.BoardSpec(
        cols=args.cols if args.cols else base.cols,
        rows=args.rows if args.rows else base.rows,
        square_size_mm=args.square if args.square else base.square_size_mm,
    )


def resolve_source(args):
    """Return (name, url) from --camera (registry) or --url."""
    if getattr(args, "url", None):
        name = getattr(args, "name", None) or "adhoc"
        return name, args.url
    if getattr(args, "camera", None):
        cam = calib.get_camera(args.camera)
        if not cam:
            sys.exit(f"ERROR: camera '{args.camera}' not in cameras.json (use --url for ad-hoc)")
        return cam["name"], cam["url"]
    sys.exit("ERROR: specify --camera <name> or --url <rtsp-url>")


def open_stream(url, transport="tcp"):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}"

    def factory():
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    tcap = ThreadedCapture(factory).start()
    if not tcap.isOpened():
        sys.exit(f"ERROR: could not open stream: {url}")
    return tcap


def board_centroid_area(corners, shape):
    pts = corners.reshape(-1, 2)
    h, w = shape[:2]
    c = pts.mean(axis=0)
    hull = cv2.convexHull(pts.astype(np.float32))
    area = cv2.contourArea(hull) / float(w * h)
    return (c[0] / w, c[1] / h), area


def is_novel(cn, area, captured, min_dist=0.07, min_area_ratio=0.35):
    for pcn, parea in captured:
        d = ((cn[0] - pcn[0]) ** 2 + (cn[1] - pcn[1]) ** 2) ** 0.5
        if d < min_dist and abs(area - parea) < min_area_ratio * max(area, parea):
            return False
    return True


def draw_text(img, text, org, color=(255, 255, 255), scale=0.6):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def fov_degrees(K, size):
    w, h = size
    fx, fy = K[0, 0], K[1, 1]
    fx_d = np.degrees(2 * np.arctan2(w, 2 * fx))
    fy_d = np.degrees(2 * np.arctan2(h, 2 * fy))
    return fx_d, fy_d


# --------------------------------------------------------------------------
# make-board
# --------------------------------------------------------------------------
def cmd_make_board(args):
    board = resolve_board(args)
    sq = args.square_px
    cols_sq, rows_sq = board.cols + 1, board.rows + 1
    margin = sq
    cap_h = 60
    W = cols_sq * sq + 2 * margin
    H = rows_sq * sq + 2 * margin + cap_h
    img = np.full((H, W), 255, np.uint8)
    for r in range(rows_sq):
        for c in range(cols_sq):
            if (r + c) % 2 == 0:
                y0, x0 = margin + r * sq, margin + c * sq
                img[y0 : y0 + sq, x0 : x0 + sq] = 0
    caption = (
        f"{board.cols}x{board.rows} inner corners | {board.square_size_mm:g}mm squares "
        f"| PRINT AT 100% then measure a square and pass --square <mm> if it differs"
    )
    cv2.putText(img, caption, (margin, H - cap_h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1, cv2.LINE_AA)
    out = args.out or os.path.join(calib.CALIB_DIR, "checkerboard.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv2.imwrite(out, img)
    print(f"Wrote {out}  ({board.cols}x{board.rows} inner corners, {board.square_size_mm:g}mm)")
    print("Print at 100% scale (no 'fit to page'), mount on something rigid and flat.")


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------
def cmd_capture(args):
    name, url = resolve_source(args)
    board = resolve_board(args)
    out_dir = os.path.join(calib.CAPTURE_DIR, name)
    os.makedirs(out_dir, exist_ok=True)
    if args.fresh:
        for f in os.listdir(out_dir):
            if f.endswith(".png"):
                os.remove(os.path.join(out_dir, f))

    print(f"Capturing for '{name}' from {url}")
    print(f"Board: {board.cols}x{board.rows} inner corners. Target: {args.count} views.")
    print("Keys: SPACE=grab  u=undo  c=calibrate now  q=quit")
    tcap = open_stream(url, args.transport)
    win = f"calibrate:capture [{name}]"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    captured = []          # (centroid, area) of saved views
    saved_files = []
    stable_corners = None
    stable_count = 0
    do_calibrate = args.then_calibrate
    try:
        while True:
            ret, frame = tcap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners = calib.find_corners(gray, board)
            view = frame.copy()

            grabbed_this_frame = False
            if corners is not None:
                cv2.drawChessboardCorners(view, board.size, corners, True)
                # stability tracking
                if stable_corners is not None and stable_corners.shape == corners.shape:
                    motion = float(np.mean(np.linalg.norm(corners - stable_corners, axis=2)))
                else:
                    motion = 999.0
                stable_corners = corners
                stable_count = stable_count + 1 if motion < 2.0 else 0

                cn, area = board_centroid_area(corners, frame.shape)
                novel = is_novel(cn, area, captured)
                steady = stable_count >= 3
                ready = novel and steady
                status = "READY" if ready else ("hold steady" if novel else "move board")
                draw_text(view, f"board: {status}", (10, 60),
                          (0, 255, 0) if ready else (0, 200, 255))
                if args.auto and ready and len(saved_files) < args.count:
                    _save_view(out_dir, frame, captured, cn, area, saved_files)
                    grabbed_this_frame = True
            else:
                stable_corners, stable_count = None, 0
                draw_text(view, "board: not found", (10, 60), (0, 0, 255))

            draw_text(view, f"{name}  captured {len(saved_files)}/{args.count}", (10, 30))
            _draw_coverage(view, captured)
            if grabbed_this_frame:
                cv2.rectangle(view, (0, 0), (view.shape[1] - 1, view.shape[0] - 1),
                              (0, 255, 0), 8)
            cv2.imshow(win, view)

            key = cv2.waitKey(15) & 0xFF
            if key == ord("q"):
                break
            elif key == ord(" ") and corners is not None:
                cn, area = board_centroid_area(corners, frame.shape)
                _save_view(out_dir, frame, captured, cn, area, saved_files)
            elif key == ord("u") and saved_files:
                last = saved_files.pop()
                os.remove(last)
                captured.pop()
                print(f"  undo -> {len(saved_files)} views")
            elif key == ord("c"):
                do_calibrate = True
                break
            if args.auto and len(saved_files) >= args.count:
                print(f"Reached {args.count} views.")
                if not args.no_stop_at_target:
                    break
    finally:
        tcap.release()
        cv2.destroyAllWindows()

    print(f"Saved {len(saved_files)} views to {out_dir}")
    if do_calibrate and saved_files:
        args.image_dir = out_dir
        cmd_calibrate(args)


def _save_view(out_dir, frame, captured, cn, area, saved_files):
    idx = len(saved_files)
    path = os.path.join(out_dir, f"view_{idx:03d}.png")
    cv2.imwrite(path, frame)
    saved_files.append(path)
    captured.append((cn, area))
    print(f"  + view {idx} (total {len(saved_files)})")


def _draw_coverage(view, captured):
    """Mini 3x3 grid showing which thirds of the frame have been covered."""
    h, w = view.shape[:2]
    mw, mh = 120, 90
    x0, y0 = w - mw - 10, 10
    cv2.rectangle(view, (x0, y0), (x0 + mw, y0 + mh), (40, 40, 40), -1)
    cells = set()
    for (cx, cy), _ in captured:
        cells.add((min(2, int(cx * 3)), min(2, int(cy * 3))))
    for gx in range(3):
        for gy in range(3):
            cx = x0 + int((gx + 0.5) * mw / 3)
            cy = y0 + int((gy + 0.5) * mh / 3)
            col = (0, 255, 0) if (gx, gy) in cells else (90, 90, 90)
            cv2.circle(view, (cx, cy), 6, col, -1)


# --------------------------------------------------------------------------
# calibrate
# --------------------------------------------------------------------------
def cmd_calibrate(args):
    board = resolve_board(args)
    if getattr(args, "image_dir", None):
        name = getattr(args, "name", None) or os.path.basename(os.path.normpath(args.image_dir))
        img_dir = args.image_dir
    else:
        name = args.camera or args.name
        if not name:
            sys.exit("ERROR: specify --camera <name> (or --image-dir / --name)")
        img_dir = os.path.join(calib.CAPTURE_DIR, name)
    if not os.path.isdir(img_dir):
        sys.exit(f"ERROR: no captured views at {img_dir} (run 'capture' first)")

    files = sorted(f for f in os.listdir(img_dir) if f.lower().endswith((".png", ".jpg", ".jpeg")))
    if not files:
        sys.exit(f"ERROR: no images in {img_dir}")

    print(f"Calibrating '{name}' from {len(files)} images ({board.cols}x{board.rows}, "
          f"model={args.model}{' +rational' if args.rational else ''})")
    image_points, used, image_size = [], [], None
    for f in files:
        img = cv2.imread(os.path.join(img_dir, f))
        if img is None:
            continue
        h, w = img.shape[:2]
        if image_size is None:
            image_size = (w, h)
        elif image_size != (w, h):
            print(f"  ! {f}: size {(w, h)} != {image_size}, skipping")
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners = calib.find_corners(gray, board)
        if corners is None:
            print(f"  - {f}: board not detected")
            continue
        image_points.append(corners)
        used.append(f)
    print(f"Detected board in {len(image_points)}/{len(files)} images.")
    if len(image_points) < 5:
        sys.exit("ERROR: need >=5 good views (aim for 15-25 well-spread).")

    result, per_view = calib.calibrate(
        image_points, board, image_size, name, model=args.model, rational=args.rational
    )

    if args.auto_prune and len(image_points) > 8:
        thresh = max(1.0, np.mean(per_view) + 1.5 * np.std(per_view))
        keep = [i for i, e in enumerate(per_view) if e <= thresh]
        if 8 <= len(keep) < len(image_points):
            print(f"Auto-prune: dropping {len(image_points) - len(keep)} high-error views "
                  f"(>{thresh:.2f}px) and re-running.")
            image_points = [image_points[i] for i in keep]
            result, per_view = calib.calibrate(
                image_points, board, image_size, name, model=args.model, rational=args.rational
            )

    fx_d, fy_d = fov_degrees(np.asarray(result.camera_matrix), image_size)
    print("\n=== Calibration result ===")
    print(f"  model:           {result.model}")
    print(f"  image size:      {image_size[0]}x{image_size[1]}")
    print(f"  views used:      {result.num_views}")
    print(f"  RMS reproj err:  {result.rms_reproj_error:.3f} px  "
          f"({'good' if result.rms_reproj_error < 1.0 else 'high - recapture for tighter'})")
    print(f"  focal (px):      fx={result.camera_matrix[0,0]:.1f} fy={result.camera_matrix[1,1]:.1f}")
    print(f"  est. FOV:        {fx_d:.1f}deg x {fy_d:.1f}deg")
    worst = int(np.argmax(per_view))
    print(f"  worst view:      {used[worst] if worst < len(used) else worst} ({max(per_view):.2f}px)")

    path = result.save()
    print(f"\nSaved -> {path}")
    print("Verify with:  python calibrate_camera.py verify "
          f"{'--camera ' + name if calib.get_camera(name) else '--image ' + os.path.join(img_dir, files[0])}")


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
def cmd_verify(args):
    name = args.camera or args.name
    cal = calib.load_calibration(args.calib or name)

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            sys.exit(f"ERROR: cannot read {args.image}")
        _show_pair(cal, img, args, still=True)
        return

    _, url = resolve_source(args)
    tcap = open_stream(url, args.transport)
    win = f"calibrate:verify [{cal.name}]  (q quit, s save)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    try:
        while True:
            ret, frame = tcap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue
            combo = _make_pair(cal, frame, args.alpha)
            cv2.imshow(win, combo)
            key = cv2.waitKey(15) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                p = os.path.join(calib.CALIB_DIR, f"{cal.name}_verify.png")
                cv2.imwrite(p, combo)
                print(f"saved {p}")
    finally:
        tcap.release()
        cv2.destroyAllWindows()


def _make_pair(cal, frame, alpha):
    h, w = frame.shape[:2]
    if (w, h) != tuple(cal.image_size):
        frame = cv2.resize(frame, tuple(cal.image_size))
    und = calib.undistort_image(frame, cal, alpha=alpha)
    draw_text(frame, "RAW", (10, 30), (0, 0, 255))
    draw_text(und, f"UNDISTORTED ({cal.model})", (10, 30), (0, 255, 0))
    return np.hstack([frame, und])


def _show_pair(cal, img, args, still=False):
    combo = _make_pair(cal, img, args.alpha)
    win = f"verify [{cal.name}] - any key to close"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.imshow(win, combo)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------
def cmd_list(args):
    fleet = calib.load_fleet()
    cams = fleet.get("cameras", [])
    b = fleet.get("board", {})
    print(f"Board: {b.get('cols','?')}x{b.get('rows','?')} inner corners, "
          f"{b.get('square_size_mm','?')}mm squares\n")
    print(f"{'CAMERA':10} {'PROFILE':9} {'CALIBRATED':10} {'MODEL':8} {'SIZE':10} {'RMSpx':6} URL")
    for cam in cams:
        name = cam["name"]
        if calib.has_calibration(name):
            c = calib.load_calibration(name)
            sz = f"{c.image_size[0]}x{c.image_size[1]}"
            status, model, rms = "yes", c.model, f"{c.rms_reproj_error:.2f}"
        else:
            status, model, sz, rms = "NO", "-", "-", "-"
        print(f"{name:10} {cam.get('profile','-'):9} {status:10} {model:8} {sz:10} {rms:6} {cam['url']}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        description="ScoutCam per-camera calibration tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_board_opts(sp):
        sp.add_argument("--cols", type=int, default=0, help="inner corners across (default from cameras.json)")
        sp.add_argument("--rows", type=int, default=0, help="inner corners down")
        sp.add_argument("--square", type=float, default=0.0, help="square size in mm")

    mb = sub.add_parser("make-board", help="generate a printable checkerboard PNG")
    add_board_opts(mb)
    mb.add_argument("--square-px", type=int, default=100, help="square size in pixels (print res)")
    mb.add_argument("--out", default="", help="output PNG path")
    mb.set_defaults(func=cmd_make_board)

    cap = sub.add_parser("capture", help="capture calibration views from a camera")
    add_board_opts(cap)
    cap.add_argument("--camera", help="fleet camera name (from cameras.json)")
    cap.add_argument("--url", help="ad-hoc RTSP url instead of --camera")
    cap.add_argument("--name", help="name to save under (with --url)")
    cap.add_argument("--count", type=int, default=20, help="target number of views")
    cap.add_argument("--transport", default="tcp", choices=["tcp", "udp"])
    cap.add_argument("--no-auto", dest="auto", action="store_false", help="manual capture only")
    cap.add_argument("--fresh", action="store_true", help="clear existing captures first")
    cap.add_argument("--no-stop-at-target", action="store_true", help="keep going past --count")
    cap.add_argument("--then-calibrate", action="store_true", help="calibrate immediately after")
    cap.add_argument("--model", default="pinhole", choices=["pinhole", "fisheye"])
    cap.add_argument("--rational", action="store_true")
    cap.add_argument("--auto-prune", action="store_true")
    cap.set_defaults(func=cmd_capture, auto=True)

    cal = sub.add_parser("calibrate", help="compute intrinsics from captured views")
    add_board_opts(cal)
    cal.add_argument("--camera", help="fleet camera name")
    cal.add_argument("--name", help="name to save under")
    cal.add_argument("--image-dir", help="folder of captured images (default: captures/<name>)")
    cal.add_argument("--model", default="pinhole", choices=["pinhole", "fisheye"])
    cal.add_argument("--rational", action="store_true", help="add k4-k6 (wide lenses)")
    cal.add_argument("--auto-prune", action="store_true", help="drop high-error views and re-run")
    cal.set_defaults(func=cmd_calibrate)

    vf = sub.add_parser("verify", help="show raw vs undistorted")
    vf.add_argument("--camera", help="fleet camera name")
    vf.add_argument("--url", help="ad-hoc RTSP url")
    vf.add_argument("--name", help="calibration name")
    vf.add_argument("--calib", help="explicit calibration json path/name")
    vf.add_argument("--image", help="verify on a still image instead of live")
    vf.add_argument("--alpha", type=float, default=0.0, help="0 crop to valid, 1 keep all pixels")
    vf.add_argument("--transport", default="tcp", choices=["tcp", "udp"])
    vf.set_defaults(func=cmd_verify)

    ls = sub.add_parser("list", help="show fleet + calibration status")
    ls.set_defaults(func=cmd_list)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

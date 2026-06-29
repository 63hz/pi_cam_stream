# Camera Calibration

Each camera's lens bends straight lines (distortion) and has its own field of
view. **Calibration** measures this — the intrinsic matrix `K` (focal length +
principal point) and the distortion coefficients — so frames can be
**undistorted**. This is the per-camera groundwork for registering multiple
cameras onto a common plane/map.

Tooling lives in `receiver/`:

| File | Purpose |
|------|---------|
| `calibrate_camera.py` | CLI: `make-board`, `capture`, `calibrate`, `verify`, `list` |
| `calib.py` | shared library (load/save, undistort) — reused by other tools |
| `cameras.json` | the fleet registry (name → RTSP url + board spec) |
| `calibrate.bat` | Windows launcher (forwards args) |
| `calibration/<name>.json` | the saved result for each camera |

Zero extra dependencies — just the `opencv-python` + `numpy` you already use.

## Quick start

```bat
REM 1. Print a checkerboard (default 9x6 inner corners)
calibrate.bat make-board
REM   -> receiver/calibration/checkerboard.png. Print SCALED TO FIT the page
REM      (landscape uses Letter/A4 better); keep the whole board + white border
REM      un-clipped, then mount it on something rigid and FLAT (foam board, clipboard).

REM 2. Capture ~20 views from a camera (live window with guidance)
calibrate.bat capture --camera blue

REM 3. Compute and save intrinsics
calibrate.bat calibrate --camera blue
REM   wide lens (IMX708 wide -> pi5-cam0/pi5-cam1): add --rational, or
calibrate.bat calibrate --camera pi5-cam0 --rational

REM 4. Check it worked (raw vs undistorted, side by side)
calibrate.bat verify --camera blue

REM 5. See the whole fleet's status
calibrate.bat list
```

(`python calibrate_camera.py <cmd>` works too if you prefer.)

> **Board size doesn't matter for undistortion.** Intrinsics (K + distortion) are
> independent of the physical square size, so just fit the board to the page —
> don't clip it. The square size only sets *metric* scale (extrinsic translation);
> if you need that later, measure one printed square and pass `--square <mm>`. What
> *does* matter: the board is flat/rigid, fully in frame, and detected.

## Capturing good views

The live capture window shows detected corners, a counter, and a 3×3 coverage
map. It **auto-grabs** a frame when the board is detected, held steady, and in a
new position. To get a tight calibration:

- Fill **different parts of the frame** — center, all four corners, the edges
  (the coverage dots turn green as you cover each third).
- **Tilt** the board toward/away and left/right (not just flat-on); vary the
  distance. Tilt is what lets the solver separate focal length from distortion.
- Keep the whole board in frame and in focus; avoid motion blur.
- ~15–25 views is plenty.

Keys: `SPACE` grab now · `u` undo last · `c` calibrate now · `q` quit.

## Reading the result

`calibrate` prints (and saves) the model, image size, views used, **RMS
reprojection error** (aim for **< 1.0 px** — under ~0.5 px is great), focal
length, and estimated FOV. If the error is high, recapture with more spread and
tilt, or add `--auto-prune` to drop the worst views and re-fit.

## Lens models

- **pinhole** (default) — standard model (k1,k2,p1,p2,k3). Good for the HQ-cam
  Pi 4Bs and moderate lenses.
- **pinhole + `--rational`** — adds k4,k5,k6 for wide lenses; try first for the
  IMX708 **wide** cameras (`pi5-cam0`, `pi5-cam1`).
- **`--model fisheye`** — the fisheye model, for the strongest (≳120°)
  distortion if `--rational` still leaves curvature.

## Important: calibration is resolution-specific

A calibration captured at 1280×720 is only valid for 1280×720 frames. If you
change a camera's `scoutcam` profile (e.g. 720p60 → 1080p30), **recalibrate**.
`verify`/`undistort_image` resize to the calibrated size, but the geometry is
only exact at the resolution you calibrated. The `profile` field in
`cameras.json` is a reminder of each camera's current resolution.

## Adding a new camera (as the fleet grows)

Append an entry to `receiver/cameras.json`:

```json
{ "name": "pit-cam", "url": "rtsp://10.0.0.14:8554/cam", "sensor": "imx708", "lens": "wide-noir", "profile": "720p60" }
```

then `calibrate.bat capture --camera pit-cam` etc. For a one-off camera not in
the registry, skip the registry and pass `--url rtsp://… --name pit-cam`
directly to any subcommand.

## Reusing calibration in code

```python
import calib
cal = calib.load_calibration("blue")          # -> Calibration
undistorted = calib.undistort_image(frame, cal, alpha=0.0)   # alpha 0=crop, 1=keep all
# or precompute remap tables for a live loop:
map1, map2, newK = calib.undistort_maps(cal, alpha=0.0)
```

This is the entry point the common-plane mapping work will build on.

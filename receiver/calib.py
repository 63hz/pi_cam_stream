#!/usr/bin/env python3
"""
Shared camera-calibration library for ScoutCam.

Small, dependency-light (opencv-python + numpy) helpers used by
`calibrate_camera.py` and reusable by downstream tools (the multi-camera
mapping proof-of-concept will undistort frames with `undistort_image`).

A "calibration" captures everything about how one camera at one resolution
morphs the image: the intrinsic matrix K (focal length + principal point) and
the lens distortion coefficients, plus the model used and a quality metric
(RMS reprojection error). Calibrations are stored one JSON file per camera in
`receiver/calibration/<name>.json` so they are easy to read, diff, and reuse.

Calibration is resolution-specific: a calibration captured at 1280x720 is only
valid for 1280x720 frames. `image_size` is stored and checked on use.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

# --- paths -----------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CALIB_DIR = os.path.join(HERE, "calibration")
CAPTURE_DIR = os.path.join(CALIB_DIR, "captures")
FLEET_FILE = os.path.join(HERE, "cameras.json")


# --- board -----------------------------------------------------------------
@dataclass
class BoardSpec:
    """A checkerboard, described by its INNER-corner counts (not square counts).

    A board printed with 10x7 squares has 9x6 inner corners -> cols=9, rows=6.
    square_size_mm only affects extrinsic scale (translation units); intrinsics
    and distortion do not depend on it, but we store it for future pose work.
    """

    cols: int = 9          # inner corners across (width)
    rows: int = 6          # inner corners down (height)
    square_size_mm: float = 25.0

    @property
    def size(self) -> tuple[int, int]:
        return (self.cols, self.rows)

    def object_points(self) -> np.ndarray:
        """(cols*rows, 3) float32 grid of corner coords on the z=0 plane."""
        objp = np.zeros((self.rows * self.cols, 3), np.float32)
        objp[:, :2] = np.mgrid[0 : self.cols, 0 : self.rows].T.reshape(-1, 2)
        objp *= float(self.square_size_mm)
        return objp


# --- calibration result ----------------------------------------------------
@dataclass
class Calibration:
    name: str
    camera_matrix: np.ndarray            # 3x3
    dist_coeffs: np.ndarray              # 1xN
    image_size: tuple[int, int]          # (width, height)
    model: str = "pinhole"               # "pinhole" | "fisheye"
    rms_reproj_error: float = 0.0
    board: Optional[BoardSpec] = None
    num_views: int = 0
    created: str = ""
    notes: str = ""

    # ---- serialization ----
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "model": self.model,
            "image_size": list(self.image_size),
            "camera_matrix": np.asarray(self.camera_matrix).tolist(),
            "dist_coeffs": np.asarray(self.dist_coeffs).reshape(-1).tolist(),
            "rms_reproj_error": float(self.rms_reproj_error),
            "num_views": int(self.num_views),
            "board": (
                None
                if self.board is None
                else {
                    "cols": self.board.cols,
                    "rows": self.board.rows,
                    "square_size_mm": self.board.square_size_mm,
                }
            ),
            "created": self.created,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        board = None
        if d.get("board"):
            board = BoardSpec(**d["board"])
        return cls(
            name=d["name"],
            camera_matrix=np.array(d["camera_matrix"], dtype=np.float64),
            dist_coeffs=np.array(d["dist_coeffs"], dtype=np.float64).reshape(1, -1),
            image_size=tuple(d["image_size"]),
            model=d.get("model", "pinhole"),
            rms_reproj_error=d.get("rms_reproj_error", 0.0),
            board=board,
            num_views=d.get("num_views", 0),
            created=d.get("created", ""),
            notes=d.get("notes", ""),
        )

    def save(self, path: Optional[str] = None) -> str:
        path = path or calib_path(self.name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path


def calib_path(name: str) -> str:
    return os.path.join(CALIB_DIR, f"{name}.json")


def load_calibration(name_or_path: str) -> Calibration:
    path = name_or_path if name_or_path.endswith(".json") else calib_path(name_or_path)
    with open(path) as f:
        return Calibration.from_dict(json.load(f))


def has_calibration(name: str) -> bool:
    return os.path.isfile(calib_path(name))


# --- corner detection ------------------------------------------------------
def find_corners(gray: np.ndarray, board: BoardSpec) -> Optional[np.ndarray]:
    """Locate inner corners robustly. Returns (N,1,2) float32 or None.

    Uses findChessboardCornersSB (handles lens distortion / uneven lighting
    far better than the legacy detector); falls back to the classic detector
    plus sub-pixel refinement if SB is unavailable.
    """
    if hasattr(cv2, "findChessboardCornersSB"):
        flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
        ok, corners = cv2.findChessboardCornersSB(gray, board.size, flags=flags)
        if ok:
            return corners.astype(np.float32)
        return None
    ok, corners = cv2.findChessboardCorners(
        gray, board.size, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    )
    if not ok:
        return None
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    return cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria).astype(np.float32)


# --- calibration -----------------------------------------------------------
def calibrate(
    image_points: list[np.ndarray],
    board: BoardSpec,
    image_size: tuple[int, int],
    name: str,
    model: str = "pinhole",
    rational: bool = False,
) -> tuple[Calibration, list[float]]:
    """Run calibration over detected corner sets. Returns (Calibration, per_view_rms).

    model="pinhole" uses the standard model (k1,k2,p1,p2,k3); pass rational=True
    to add k4,k5,k6 for very wide lenses. model="fisheye" uses the fisheye model
    (recommended for >120deg lenses).
    """
    objp = board.object_points()
    object_points = [objp.copy() for _ in image_points]

    if model == "fisheye":
        return _calibrate_fisheye(object_points, image_points, board, image_size, name)
    return _calibrate_pinhole(object_points, image_points, board, image_size, name, rational)


def _calibrate_pinhole(object_points, image_points, board, image_size, name, rational):
    flags = cv2.CALIB_RATIONAL_MODEL if rational else 0
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        object_points, image_points, image_size, None, None, flags=flags
    )
    per_view = []
    for objp, imgp, rvec, tvec in zip(object_points, image_points, rvecs, tvecs):
        proj, _ = cv2.projectPoints(objp, rvec, tvec, K, dist)
        # per-view RMS (norm/sqrt(N)) so it is comparable to the overall RMS
        err = cv2.norm(imgp, proj.astype(np.float32), cv2.NORM_L2) / np.sqrt(len(proj))
        per_view.append(float(err))
    calib = Calibration(
        name=name,
        camera_matrix=K,
        dist_coeffs=dist,
        image_size=tuple(image_size),
        model="pinhole",
        rms_reproj_error=float(rms),
        board=board,
        num_views=len(image_points),
        created=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    return calib, per_view


def _calibrate_fisheye(object_points, image_points, board, image_size, name):
    n = board.cols * board.rows
    objp = [op.reshape(1, n, 3).astype(np.float64) for op in object_points]
    imgp = [ip.reshape(1, n, 2).astype(np.float64) for ip in image_points]
    K = np.zeros((3, 3))
    D = np.zeros((4, 1))
    flags = (
        cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
        | cv2.fisheye.CALIB_FIX_SKEW
        | cv2.fisheye.CALIB_CHECK_COND
    )
    rvecs = [np.zeros((1, 1, 3)) for _ in objp]
    tvecs = [np.zeros((1, 1, 3)) for _ in objp]
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-6)
    rms, K, D, rvecs, tvecs = cv2.fisheye.calibrate(
        objp, imgp, image_size, K, D, rvecs, tvecs, flags, criteria
    )
    per_view = []
    for op, ip, rvec, tvec in zip(objp, imgp, rvecs, tvecs):
        proj, _ = cv2.fisheye.projectPoints(op, rvec, tvec, K, D)
        # per-view RMS (norm/sqrt(N)) comparable to the overall RMS
        err = cv2.norm(ip, proj, cv2.NORM_L2) / np.sqrt(proj.shape[1])
        per_view.append(float(err))
    calib = Calibration(
        name=name,
        camera_matrix=K,
        dist_coeffs=D,
        image_size=tuple(image_size),
        model="fisheye",
        rms_reproj_error=float(rms),
        board=board,
        num_views=len(image_points),
        created=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    return calib, per_view


# --- undistortion (reusable by downstream tools) ---------------------------
def undistort_maps(calib: Calibration, alpha: float = 0.0, new_size=None):
    """Precompute remap tables. alpha=0 crops to valid pixels, 1 keeps all.

    Returns (map1, map2, new_camera_matrix).
    """
    w, h = calib.image_size
    size = new_size or (w, h)
    K = np.asarray(calib.camera_matrix)
    D = np.asarray(calib.dist_coeffs)
    if calib.model == "fisheye":
        newK = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
            K, D.reshape(4, 1), (w, h), np.eye(3), balance=alpha, new_size=size
        )
        m1, m2 = cv2.fisheye.initUndistortRectifyMap(
            K, D.reshape(4, 1), np.eye(3), newK, size, cv2.CV_16SC2
        )
        return m1, m2, newK
    newK, _ = cv2.getOptimalNewCameraMatrix(K, D, (w, h), alpha, size)
    m1, m2 = cv2.initUndistortRectifyMap(K, D, None, newK, size, cv2.CV_16SC2)
    return m1, m2, newK


def undistort_image(img: np.ndarray, calib: Calibration, alpha: float = 0.0) -> np.ndarray:
    """Undistort a single frame. Frame size must match calib.image_size."""
    m1, m2, _ = undistort_maps(calib, alpha)
    return cv2.remap(img, m1, m2, cv2.INTER_LINEAR)


# --- fleet registry --------------------------------------------------------
def load_fleet() -> dict:
    """Load cameras.json -> {'cameras': [...], 'board': {...}}; empty if absent."""
    if not os.path.isfile(FLEET_FILE):
        return {"cameras": [], "board": {}}
    with open(FLEET_FILE) as f:
        return json.load(f)


def get_camera(name: str) -> Optional[dict]:
    for cam in load_fleet().get("cameras", []):
        if cam.get("name") == name:
            return cam
    return None


def default_board() -> BoardSpec:
    b = load_fleet().get("board") or {}
    return BoardSpec(
        cols=b.get("cols", 9),
        rows=b.get("rows", 6),
        square_size_mm=b.get("square_size_mm", 25.0),
    )

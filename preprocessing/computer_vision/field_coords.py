"""
Common field coordinate frame + per-camera pixel->field transforms.

The field is a flat 40 x 20 ft paddock. Each camera maps image pixels -> field
centimetres using EITHER:
  - a 3x3 homography  (oblique cams CH3/CH4, nadir cams CH5/CH6), or
  - a 2nd-order polynomial  (180 deg Duo3 panoramas CH1/CH2, whose distortion a
    single homography can't capture).

This module owns:
  - the common-frame definition + unit conversions (cm <-> inch, for WISER),
  - the field layout (pole grid + shelters + camera mounts) in configs/field_layout.json,
  - resolving a landmark name (e.g. "L2", "left_NE") to its field cm,
  - loading a saved per-camera calibration and applying it (pixel <-> field).

Pure numpy, all transforms elementwise (no BLAS gemm) so it's light and robust.

Per-camera calibration JSON (written by calibration.py) -> configs/<CH>_calib.json:
    { "channel":"CH3", "type":"homography", "forward":[[..3x3..]], "inverse":[[..]], ... }
    { "channel":"CH1", "type":"poly",       "forward":[[..2x6..]], "inverse":[[..2x6..]], ... }
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Sequence

import numpy as np

# ---- field constants ----
FT_TO_CM = 30.48
IN_TO_CM = 2.54
# Axes: x = 40 ft length, y = 20 ft width (origin at corner pole A0).
FIELD_X_CM = 1219.2     # 40 ft (x, length)
FIELD_Y_CM = 609.6      # 20 ft (y, width)
# deprecated aliases = x-extent / y-extent (kept so older callers keep working)
FIELD_W_CM = FIELD_X_CM
FIELD_L_CM = FIELD_Y_CM

CONFIG_DIR = Path(__file__).resolve().parent / "configs"
LAYOUT_PATH = CONFIG_DIR / "field_layout.json"


# ============================ unit helpers ============================
def cm_to_inch(a):
    return np.asarray(a, dtype=float) / IN_TO_CM


def inch_to_cm(a):
    return np.asarray(a, dtype=float) * IN_TO_CM


# ============================ field layout ============================
def load_layout(path: Path | str = LAYOUT_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))  # tolerate editor BOM


CORNER_KEYS = ("XloYlo", "XhiYlo", "XhiYhi", "XloYhi")   # by FIELD direction (±x, ±y)


def shelter_corners(shelter: dict) -> dict:
    """4 corners keyed by FIELD direction: 'XloYlo','XhiYlo','XhiYhi','XloYhi'
    (toward lower/higher x and y). The key reflects the corner's ACTUAL field
    position, so it stays correct no matter how the shelter is rotated."""
    cx, cy = shelter["center_cm"]
    long_cm, short_cm = shelter["size_cm"]            # [62.55, 45.72]
    hx, hy = long_cm / 2.0, short_cm / 2.0
    th = math.radians(shelter.get("orientation_deg", 0))
    c, s = math.cos(th), math.sin(th)
    out = {}
    for dx, dy in [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]:
        X = cx + dx * c - dy * s
        Y = cy + dx * s + dy * c
        key = ("Xhi" if X > cx else "Xlo") + ("Yhi" if Y > cy else "Ylo")
        out[key] = [round(X, 2), round(Y, 2)]
    return out


def corner_label(key: str) -> str:
    """'XhiYlo' -> '+x,-y' (human direction tag)."""
    return ("+x" if "Xhi" in key else "-x") + "," + ("+y" if "Yhi" in key else "-y")


def resolve_landmark(name: str, layout: dict | None = None) -> list:
    """field cm (x,y, ground) of a landmark: a pole key ('L2') or shelter corner ('left_NE')."""
    layout = layout or load_layout()
    if name in layout.get("poles", {}):
        return layout["poles"][name]
    if name in layout.get("markers", {}):              # temporary ground markers (calibration aids)
        return layout["markers"][name]
    if "_" in name:
        shelter, corner = name.rsplit("_", 1)
        sh = layout.get("shelters", {}).get(shelter)
        if sh:
            corners = shelter_corners(sh)
            if corner in corners:
                return corners[corner]
    raise KeyError(f"Unknown landmark '{name}' (not a pole key or <shelter>_<{'|'.join(CORNER_KEYS)}>)")


def resolve_world(name: str, layout: dict | None = None) -> list:
    """3D world point [x,y,z] cm. '<pole>_top' = that pole at the wall top (z=wall_height);
    everything else is on the ground (z=0)."""
    layout = layout or load_layout()
    if name.endswith("_top"):
        xy = resolve_landmark(name[:-4], layout)
        return [xy[0], xy[1], float(layout.get("wall_height_cm", 0.0))]
    xy = resolve_landmark(name, layout)
    return [xy[0], xy[1], 0.0]


# ============================ transforms ============================
def _apply_homography(H: np.ndarray, pts) -> np.ndarray:
    """3x3 homography on Nx2 points, elementwise (no gemm)."""
    H = np.asarray(H, dtype=float)
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    x, y = p[:, 0], p[:, 1]
    u = H[0, 0] * x + H[0, 1] * y + H[0, 2]
    v = H[1, 0] * x + H[1, 1] * y + H[1, 2]
    w = H[2, 0] * x + H[2, 1] * y + H[2, 2]
    w = np.where(w == 0, np.finfo(float).eps, w)
    return np.stack([u / w, v / w], axis=1)


def poly_features(x, y) -> np.ndarray:
    """Design matrix for a 2nd-order 2D polynomial: [1, x, y, x^2, xy, y^2]. Nx6."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)


def _apply_poly(coeffs: np.ndarray, pts) -> np.ndarray:
    """Apply a 2x6 polynomial map to Nx2 points, elementwise (no gemm)."""
    coeffs = np.asarray(coeffs, dtype=float)
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    F = poly_features(p[:, 0], p[:, 1])               # N x 6
    out_x = (F * coeffs[0]).sum(axis=1)
    out_y = (F * coeffs[1]).sum(axis=1)
    return np.stack([out_x, out_y], axis=1)


# ---- PnP (full camera pose) transforms: pixel <-> ground plane (z=0), elementwise ----
def _mat3_apply(M, vecs) -> np.ndarray:
    """Linear 3x3 * Nx3 -> Nx3 (no homogeneous divide, no gemm)."""
    M = np.asarray(M, float)
    v = np.asarray(vecs, float).reshape(-1, 3)
    x, y, z = v[:, 0], v[:, 1], v[:, 2]
    return np.stack([
        M[0, 0] * x + M[0, 1] * y + M[0, 2] * z,
        M[1, 0] * x + M[1, 1] * y + M[1, 2] * z,
        M[2, 0] * x + M[2, 1] * y + M[2, 2] * z,
    ], axis=1)


def backproject_ground(K, R, t, pts_px) -> np.ndarray:
    """Camera pose (K, R world->cam, t) -> intersect each pixel's ray with z=0. Nx2 cm."""
    K = np.asarray(K, float); R = np.asarray(R, float); t = np.asarray(t, float).reshape(3)
    Kinv = np.linalg.inv(K); Rt = R.T
    C = -_mat3_apply(Rt, t.reshape(1, 3))[0]                 # camera centre in world
    p = np.asarray(pts_px, float).reshape(-1, 2)
    homog = np.stack([p[:, 0], p[:, 1], np.ones(len(p))], axis=1)
    ray = _mat3_apply(Rt, _mat3_apply(Kinv, homog))         # ray directions in world
    rz = np.where(ray[:, 2] == 0, np.finfo(float).eps, ray[:, 2])
    s = -C[2] / rz
    g = C[None, :] + ray * s[:, None]
    return g[:, :2]


def project_ground(K, R, t, pts_cm) -> np.ndarray:
    """Project ground points (z=0) to pixels via K[R|t]. Nx2 px (for overlays)."""
    K = np.asarray(K, float); R = np.asarray(R, float); t = np.asarray(t, float).reshape(3)
    p = np.asarray(pts_cm, float).reshape(-1, 2)
    world = np.stack([p[:, 0], p[:, 1], np.zeros(len(p))], axis=1)
    Xc = _mat3_apply(R, world) + t[None, :]
    uvw = _mat3_apply(K, Xc)
    w = np.where(uvw[:, 2] == 0, np.finfo(float).eps, uvw[:, 2])
    return np.stack([uvw[:, 0] / w, uvw[:, 1] / w], axis=1)


# ============================ calibration I/O ============================
def calib_path(channel: str, config_dir: Path | str = CONFIG_DIR) -> Path:
    return Path(config_dir) / f"{channel}_calib.json"


def intrinsics_path(channel: str, config_dir: Path | str = CONFIG_DIR) -> Path:
    return Path(config_dir) / f"{channel}_intrinsics.json"


def _ref_still_size(channel: str, config_dir: Path | str = CONFIG_DIR):
    """(w, h) of the calibration reference still if present, else None."""
    p = Path(config_dir) / f"{channel}_reference.png"
    if p.exists():
        import cv2
        im = cv2.imread(str(p))
        if im is not None:
            return (im.shape[1], im.shape[0])
    return None


def load_intrinsics(channel: str, config_dir: Path | str = CONFIG_DIR) -> dict | None:
    """Lens intrinsics from intrinsics.py, or None. {model, K, D, image_size}."""
    p = intrinsics_path(channel, config_dir)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8-sig"))
    return {"model": d["model"], "K": np.asarray(d["K"], float),
            "D": np.asarray(d["D"], float), "image_size": tuple(d.get("image_size", ()))}


def undistort_px(pts_px, intr: dict) -> np.ndarray:
    """Raw pixel -> undistorted pixel in the SAME K (so a homography stays in px units)."""
    import cv2
    K = np.asarray(intr["K"], float)
    D = np.asarray(intr["D"], float).ravel()
    p = np.asarray(pts_px, float).reshape(-1, 1, 2)
    if intr["model"] == "fisheye":
        out = cv2.fisheye.undistortPoints(p, K, D.reshape(-1, 1), R=np.eye(3), P=K)
    else:
        out = cv2.undistortPoints(p, K, D, P=K)
    return np.asarray(out, float).reshape(-1, 2)


def homography_path(channel: str, config_dir: Path | str = CONFIG_DIR) -> Path:
    return Path(config_dir) / f"{channel}_homography.json"


def load_calib(channel: str, config_dir: Path | str = CONFIG_DIR) -> dict:
    """Load a per-camera calibration. Returns {channel,type,forward,inverse}.

    Prefers <CH>_calib.json (homography OR poly); falls back to the older
    <CH>_homography.json (homography only).
    """
    p = calib_path(channel, config_dir)
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        # distortion: if the fit was done in undistorted-pixel space, undistort before applying
        undistort = bool(d.get("undistort"))
        intr = load_intrinsics(channel, config_dir) if undistort else None
        if d["type"] == "pnp":
            f = d["forward"]
            fwd = {"K": np.asarray(f["K"], float), "R": np.asarray(f["R"], float),
                   "t": np.asarray(f["t"], float)}
            out = {"channel": channel, "type": "pnp", "forward": fwd, "inverse": None}
        else:
            fwd = np.asarray(d["forward"], dtype=float)
            if d["type"] == "homography":
                inv = np.asarray(d.get("inverse"), dtype=float) if d.get("inverse") else np.linalg.inv(fwd)
            else:
                inv = np.asarray(d["inverse"], dtype=float)
            out = {"channel": channel, "type": d["type"], "forward": fwd, "inverse": inv}
        out["undistort"] = undistort
        out["intrinsics"] = intr
        isz = d.get("image_size")          # pixel space the calib was fit in (for scaling)
        out["image_size"] = tuple(isz) if isz else _ref_still_size(channel, config_dir)
        return out
    hp = homography_path(channel, config_dir)
    if hp.exists():
        H = np.asarray(json.loads(hp.read_text(encoding="utf-8-sig"))["H"], dtype=float)
        return {"channel": channel, "type": "homography", "forward": H, "inverse": np.linalg.inv(H),
                "undistort": False, "intrinsics": None,
                "image_size": _ref_still_size(channel, config_dir)}
    raise FileNotFoundError(f"No calibration for {channel} ({p} or {hp}). Run calibration.py.")


def to_field(channel: str, pts_px, config_dir: Path | str = CONFIG_DIR,
             calib: dict | None = None, src_size=None) -> np.ndarray:
    """Map image pixels -> field cm using the channel's saved calibration.

    `src_size` = (w, h) the pixels are in; if it differs from the calibration's
    image_size, pixels are scaled first so a clip at any resolution maps correctly.
    If the calibration was fit in undistorted-pixel space (`undistort`), raw pixels
    are undistorted with the lens intrinsics first.
    """
    c = calib or load_calib(channel, config_dir)
    pts = np.asarray(pts_px, float).reshape(-1, 2)
    isz = c.get("image_size")
    if src_size and isz and tuple(src_size) != tuple(isz):     # scale to the calib resolution
        pts = pts * np.array([isz[0] / src_size[0], isz[1] / src_size[1]], float)
    if c.get("undistort") and c.get("intrinsics") is not None:
        pts = undistort_px(pts, c["intrinsics"])
    if c["type"] == "homography":
        return _apply_homography(c["forward"], pts)
    if c["type"] == "pnp":
        f = c["forward"]
        return backproject_ground(f["K"], f["R"], f["t"], pts)
    return _apply_poly(c["forward"], pts)


def to_pixel(channel: str, pts_cm, config_dir: Path | str = CONFIG_DIR,
             calib: dict | None = None) -> np.ndarray:
    """Map field cm -> image pixels (for overlays) using the inverse calibration."""
    c = calib or load_calib(channel, config_dir)
    if c["type"] == "homography":
        return _apply_homography(c["inverse"], pts_cm)
    if c["type"] == "pnp":
        f = c["forward"]
        return project_ground(f["K"], f["R"], f["t"], pts_cm)
    return _apply_poly(c["inverse"], pts_cm)


# ---- backward-compatible thin wrappers (used by older callers/tests) ----
def load_homography(channel: str, config_dir: Path | str = CONFIG_DIR) -> np.ndarray:
    return load_calib(channel, config_dir)["forward"]


def pixel_to_field(H: np.ndarray, pts_px: Sequence) -> np.ndarray:
    return _apply_homography(H, pts_px)


def field_to_pixel(H: np.ndarray, pts_cm: Sequence) -> np.ndarray:
    return _apply_homography(np.linalg.inv(H), pts_cm)


def in_field(pts_cm: Sequence, margin_cm: float = 0.0) -> np.ndarray:
    p = np.asarray(pts_cm, dtype=float).reshape(-1, 2)
    return ((p[:, 0] >= -margin_cm) & (p[:, 0] <= FIELD_W_CM + margin_cm) &
            (p[:, 1] >= -margin_cm) & (p[:, 1] <= FIELD_L_CM + margin_cm))


# ============================ CLI ============================
def _cli() -> None:
    ap = argparse.ArgumentParser(description="Transform an image pixel to field cm, or look up a landmark.")
    ap.add_argument("--channel", required=True, help="e.g. CH3")
    ap.add_argument("--px", nargs=2, type=float, metavar=("X", "Y"), help="image pixel to transform")
    ap.add_argument("--landmark", help="print field cm of a layout landmark (e.g. L2, left_NE)")
    ap.add_argument("--config-dir", default=str(CONFIG_DIR))
    args = ap.parse_args()

    if args.landmark:
        xy = resolve_landmark(args.landmark)
        print(f"landmark {args.landmark} -> field cm ({xy[0]:.1f}, {xy[1]:.1f})")
        return
    if not args.px:
        raise SystemExit("Provide --px X Y or --landmark NAME")

    c = load_calib(args.channel, args.config_dir)
    xy = to_field(args.channel, args.px, calib=c)[0]
    inside = bool(in_field([xy])[0])
    print(f"channel      : {args.channel}  (calib type: {c['type']})")
    print(f"pixel        : ({args.px[0]:.1f}, {args.px[1]:.1f})")
    print(f"field (cm)   : ({xy[0]:.1f}, {xy[1]:.1f})")
    print(f"field (inch) : ({xy[0]/IN_TO_CM:.1f}, {xy[1]/IN_TO_CM:.1f})  # WISER units")
    print(f"inside field : {inside}  (field x={FIELD_X_CM:.1f} (40ft) by y={FIELD_Y_CM:.1f} (20ft) cm)")


if __name__ == "__main__":
    _cli()

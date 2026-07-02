"""
Per-camera calibration: image pixels -> field cm, anchored on the EXISTING field
references (pole grid @ 10 ft + shelter rectangles) defined in configs/field_layout.json.

You click each landmark in the camera's still; its field-cm is looked up from the
layout automatically (no typing coordinates). Two model types (auto-selected from
field_layout camera_mounts[ch].mapping, or via --type):

  homography  (CH3/CH4 oblique, CH5/CH6 nadir) - needs >=4 points
  poly        (CH1/CH2 180 deg Duo3 panoramas) - 2nd-order map, needs >=6 points

Outputs configs/<CH>_calib.json + a grid-overlay PNG (reprojected 10-ft grid on the
still) and prints the reprojection RMSE (cm).

Usage
-----
  python extract_clip.py --channel CH3 --frame          # get the still first
  python calibration.py  --channel CH3 --pick           # click the listed landmarks
  # non-interactive (testing): supply correspondences directly
  python calibration.py  --channel CH3 --points pts.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np

import field_coords as fc

CONFIG_DIR = Path(__file__).resolve().parent / "configs"


# ---- default landmark sets per camera (edit configs/<CH>_points.json after) ----
def default_landmarks(channel: str, layout: dict) -> list:
    mount = layout.get("camera_mounts", {}).get(channel, {})
    if mount.get("calib_landmarks"):                      # explicit per-camera list (e.g. markers)
        return list(mount["calib_landmarks"])
    poles = list(layout.get("poles", {}).keys())
    shelters = [s for s in layout.get("shelters", {}) if not s.startswith("_")]
    shelter_corners = [f"{s}_{k}" for s in shelters for k in fc.CORNER_KEYS]
    if mount.get("shelter"):                              # nadir shelter cam
        s = mount["shelter"]
        # its own 4 corners = a full homography; poles appended as optional extras
        return [f"{s}_{k}" for k in fc.CORNER_KEYS] + poles
    if mount.get("mapping") == "poly":                   # pano -> all poles + any ground markers
        return poles + list(layout.get("markers", {}).keys())
    if mount.get("mapping") == "pnp":                    # few/collinear ground poles + wall
        out = []                                          # each pole: base then wall-top crossing
        for p in poles:
            out += [p, f"{p}_top"]
        return out
    # oblique side cam (homography): offer poles + shelter corners
    return poles + shelter_corners


# ---- model fitting ----
def _ccw_order(p):
    """Indices that put 4 quad points in convex (counter-clockwise) order."""
    c = np.asarray(p, float).mean(0)
    return np.argsort(np.arctan2(np.asarray(p)[:, 1] - c[1], np.asarray(p)[:, 0] - c[0]))


def fit_homography(image_px, field_cm):
    import cv2
    image_px = np.asarray(image_px, float)
    field_cm = np.asarray(field_cm, float)
    if len(image_px) < 4:
        raise ValueError("homography needs >=4 points")
    if len(image_px) == 4:
        # 4-corner case (e.g. a shelter): pair by WINDING order, not click order, or the
        # homography maps the rectangle to a self-intersecting quad -> vanishing line through
        # the interior -> interior points blow up. Sort both quads to a common convex order.
        image_px = image_px[_ccw_order(image_px)]
        field_cm = field_cm[_ccw_order(field_cm)]
        H, _ = cv2.findHomography(image_px, field_cm, method=0)        # exact for 4 pts
    else:
        H, _ = cv2.findHomography(image_px, field_cm, method=cv2.RANSAC, ransacReprojThreshold=10.0)
    if H is None:
        raise RuntimeError("findHomography failed (degenerate points?)")
    proj = fc._apply_homography(H, image_px)
    rmse = float(np.sqrt(np.mean(np.sum((proj - field_cm) ** 2, axis=1))))
    return H, np.linalg.inv(H), rmse


def intrinsics_from_specs(channel, img_w, img_h, layout) -> np.ndarray:
    """Approximate pinhole K from the camera's FOV (camera_specs.json) and still size."""
    import math
    specs = json.loads((CONFIG_DIR / "camera_specs.json").read_text(encoding="utf-8-sig"))
    model = layout["camera_mounts"][channel]["model"]
    sp = specs[model]
    fx = (img_w / 2.0) / math.tan(math.radians(sp["fov_h_deg"] / 2.0))
    fy = (img_h / 2.0) / math.tan(math.radians(sp["fov_v_deg"] / 2.0))
    return np.array([[fx, 0, img_w / 2.0], [0, fy, img_h / 2.0], [0, 0, 1.0]], float)


def _ground_rmse(K, R, t, image_px, world):
    base = np.abs(world[:, 2]) < 1e-6
    if not base.any():
        base = np.ones(len(world), bool)        # no z=0 pts: score all (rare)
    bp = fc.backproject_ground(K, R, t, image_px[base])
    return float(np.sqrt(np.mean(np.sum((bp - world[base, :2]) ** 2, axis=1))))


def _planar_pnp(image_px, world, K):
    """Pose from a flat target whose points are coplanar (e.g. the paddock WALL:
    pole bases z=0 + wall-tops z=wall_height all lie in one vertical plane x=const).
    A general PnP is degenerate here; IPPE is built for planar targets. We express the
    points in a local 2D frame (z'=0), solve IPPE, then map the pose back to world."""
    import cv2
    o = world.mean(axis=0)
    Q = world - o
    _, sv, vt = np.linalg.svd(Q, full_matrices=True)
    if sv[1] < 1e-3:                              # points are a LINE, not a plane
        raise ValueError("planar PnP needs the points to span a plane, not a line "
                         "(add wall-top clicks so the wall has height, or a 2nd column).")
    u, v = vt[0], vt[1]                           # in-plane axes; vt[2] = plane normal
    R_lw = np.stack([u, v, vt[2]])               # world-delta -> local (a, b, ~0)
    obj2d = Q @ np.stack([u, v]).T
    obj3d = np.hstack([obj2d, np.zeros((len(obj2d), 1))]).astype(np.float64)
    n, rvecs, tvecs, _ = cv2.solvePnPGeneric(obj3d, image_px, K, np.zeros(5),
                                             flags=cv2.SOLVEPNP_IPPE)
    if not n:
        raise RuntimeError("planar solvePnP (IPPE) failed")
    best, best_rmse = None, 1e18
    for rv, tv in zip(rvecs, tvecs):             # IPPE returns up to 2 poses; pick best
        R_lc, _ = cv2.Rodrigues(rv)
        R = R_lc @ R_lw
        t = (tv.reshape(3) - R @ o)
        r = _ground_rmse(K, R, t, image_px, world)
        if r < best_rmse:
            best, best_rmse = (R, t), r
    return best[0], best[1], best_rmse


def fit_pnp(image_px, world_xyz, K):
    """Solve full camera pose from >=4 (ideally >=6) 3D points (mix of ground bases
    z=0 and wall-top crossings z=wall_height). Auto-detects whether the points are
    coplanar (the wall-only case for CH03/CH04, where every visible reference lies in
    one vertical plane) and uses IPPE for that; otherwise a general iterative PnP.
    Returns (K, R, t, rmse_cm); RMSE is ground reprojection error on the z=0 points."""
    import cv2
    image_px = np.asarray(image_px, float)
    world = np.asarray(world_xyz, float)
    if len(image_px) < 4:
        raise ValueError("PnP needs >=4 points (>=6 recommended: each pole's base + wall-top)")
    # coplanarity test: smallest singular value of centred points ~ 0
    Q = world - world.mean(axis=0)
    coplanar = np.linalg.svd(Q, compute_uv=False)[2] < 1.0   # cm
    if coplanar:
        R, t, rmse = _planar_pnp(image_px, world, K)
        return K, R, t, rmse
    ok, rvec, tvec = cv2.solvePnP(world, image_px, K, np.zeros(5), flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise RuntimeError("solvePnP failed")
    R, _ = cv2.Rodrigues(rvec)
    t = tvec.reshape(3)
    rmse = _ground_rmse(K, R, t, image_px, world)
    return K, R, t, rmse


def fit_affine(image_px, field_cm):
    """3+ point affine fallback (no perspective). Stored as a 3x3 so it loads like a
    homography. Less accurate for oblique cams across depth - use only when <4 points."""
    import cv2
    ipx = np.asarray(image_px, np.float32).reshape(-1, 1, 2)   # cv2 wants float32 Nx1x2
    fcm = np.asarray(field_cm, np.float32).reshape(-1, 1, 2)
    if len(ipx) < 3:
        raise ValueError("affine needs >=3 points")
    M, _ = cv2.estimateAffine2D(ipx, fcm)
    if M is None:
        raise RuntimeError("estimateAffine2D failed")
    H = np.vstack([M, [0.0, 0.0, 1.0]])
    flat = np.asarray(image_px, float).reshape(-1, 2)
    proj = fc._apply_homography(H, flat)
    rmse = float(np.sqrt(np.mean(np.sum((proj - np.asarray(field_cm, float).reshape(-1, 2)) ** 2, axis=1))))
    return H, np.linalg.inv(H), rmse


def fit_poly(image_px, field_cm):
    image_px = np.asarray(image_px, float)
    field_cm = np.asarray(field_cm, float)
    if len(image_px) < 6:
        raise ValueError("poly (2nd order) needs >=6 points")
    F = fc.poly_features(image_px[:, 0], image_px[:, 1])          # img -> field
    cx, *_ = np.linalg.lstsq(F, field_cm[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(F, field_cm[:, 1], rcond=None)
    fwd = np.stack([cx, cy])                                       # 2x6
    G = fc.poly_features(field_cm[:, 0], field_cm[:, 1])           # field -> img (for overlay)
    ix, *_ = np.linalg.lstsq(G, image_px[:, 0], rcond=None)
    iy, *_ = np.linalg.lstsq(G, image_px[:, 1], rcond=None)
    inv = np.stack([ix, iy])
    proj = fc._apply_poly(fwd, image_px)
    rmse = float(np.sqrt(np.mean(np.sum((proj - field_cm) ** 2, axis=1))))
    return fwd, inv, rmse


def save_calib(channel, mtype, forward, inverse, rmse, names, image_px, world,
               config_dir: Path = CONFIG_DIR, undistort: bool = False, image_size=None) -> Path:
    if isinstance(forward, dict):                          # pnp: {K,R,t}
        fwd_ser = {k: np.asarray(v).tolist() for k, v in forward.items()}
    else:
        fwd_ser = np.asarray(forward).tolist()
    if image_size is None:                                 # default: the reference still's size
        image_size = fc._ref_still_size(channel, config_dir)
    out = fc.calib_path(channel, config_dir)
    out.write_text(json.dumps({
        "channel": channel,
        "type": mtype,
        "image_size": list(image_size) if image_size else None,   # px space the calib is in
        "undistort": bool(undistort),       # if True, raw px are undistorted (CHxx_intrinsics.json) before mapping
        "forward": fwd_ser,
        "inverse": (np.asarray(inverse).tolist() if inverse is not None else None),
        "units": "cm",
        "field_cm": [fc.FIELD_W_CM, fc.FIELD_L_CM],
        "reproj_rmse_cm": round(rmse, 3),
        "n_points": int(len(image_px)),
        "landmarks": list(names),
        "image_px": np.asarray(image_px).tolist(),     # RAW pixels (pre-undistort), so --refit can redo
        "world_points": np.asarray(world).tolist(),
        "created": datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    return out


def write_grid_overlay(channel, still_png, step_cm: float = fc.FT_TO_CM,
                       config_dir: Path = CONFIG_DIR) -> Path:
    import cv2
    img = cv2.imread(str(still_png))
    if img is None:
        raise FileNotFoundError(still_png)
    c = fc.load_calib(channel, config_dir)
    if c.get("undistort") and c.get("intrinsics") is not None:
        # calib maps UNDISTORTED px -> field, so draw the grid on the undistorted image
        intr = c["intrinsics"]; K = np.asarray(intr["K"]); D = np.asarray(intr["D"]).ravel()
        if intr["model"] == "fisheye":
            img = cv2.fisheye.undistortImage(img, K, D.reshape(-1, 1), Knew=K)
        else:
            img = cv2.undistort(img, K, D)
    h, w = img.shape[:2]

    def line(cm_pts):
        px = fc.to_pixel(channel, cm_pts, calib=c).astype(np.int32)
        cv2.polylines(img, [px.reshape(-1, 1, 2)], False, (0, 255, 0), 1, cv2.LINE_AA)

    for x in np.arange(0, fc.FIELD_W_CM + 1e-6, step_cm):
        line([[x, y] for y in np.linspace(0, fc.FIELD_L_CM, 60)])
    for y in np.arange(0, fc.FIELD_L_CM + 1e-6, step_cm):
        line([[x, y] for x in np.linspace(0, fc.FIELD_W_CM, 60)])
    out = config_dir / f"{channel}_grid_overlay.png"
    cv2.imwrite(str(out), img)
    return out


# ---- interactive picking ----
def pick_points(channel, still_png, names, layout):
    import cv2
    img = cv2.imread(str(still_png))
    if img is None:
        raise FileNotFoundError(still_png)
    poles = set(layout.get("poles", {}))
    markers = set(layout.get("markers", {}))
    targets = [(n, fc.resolve_world(n, layout)) for n in names]   # 3D world [x,y,z]
    clicked, used = [], []
    i = {"k": 0}
    win = f"{channel}: calibration"

    def what_to_click(n: str) -> str:
        if n.endswith("_top"):
            return "where this pole CROSSES THE TOP OF THE WALL"
        if n in markers:
            return f"the CENTRE of marker {n} on the grass"
        if n in poles:
            return "the POLE BASE (where it meets the ground)"
        if "_" in n:
            corner = n.rsplit("_", 1)[1]
            if corner in fc.CORNER_KEYS:
                return f"the shelter corner toward {fc.corner_label(corner)} (field x,y)"
        return "this point on the ground"

    def banner(d, text, y, color):
        # dark strip behind text for readability over grass
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(d, (5, y - th - 6), (5 + tw + 8, y + 6), (0, 0, 0), -1)
        cv2.putText(d, text, (9, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    def redraw():
        d = img.copy()
        for (n, _), (cx, cy) in zip(used, clicked):
            cv2.circle(d, (int(cx), int(cy)), 5, (0, 255, 0), -1)
            cv2.putText(d, n, (int(cx) + 6, int(cy)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        if i["k"] < len(targets):
            n, cm = targets[i["k"]]
            banner(d, f"CLICK {n}: {what_to_click(n)}", 28, (0, 200, 255))
            banner(d, f"field ({cm[0]:.0f},{cm[1]:.0f}) cm   [{len(clicked)} placed]   "
                      f"s=skip (not visible)  u=undo  q=done", 56, (255, 255, 255))
        else:
            banner(d, f"DONE - {len(clicked)} points placed. Press q.", 28, (0, 255, 0))
        cv2.imshow(win, d)

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN and i["k"] < len(targets):
            clicked.append([float(x), float(y)]); used.append(targets[i["k"]]); i["k"] += 1; redraw()

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    redraw()
    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s") and i["k"] < len(targets):      # skip current landmark
            i["k"] += 1; redraw()
        if key == ord("u") and clicked:
            clicked.pop(); used.pop(); i["k"] -= 1; redraw()
    cv2.destroyAllWindows()
    names_out = [n for n, _ in used]
    field_cm = [cm for _, cm in used]
    return np.array(clicked), np.array(field_cm), names_out


# ---- CLI ----
def _cli() -> None:
    ap = argparse.ArgumentParser(description="Calibrate a camera (pole grid + shelters -> field cm).")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--pick", action="store_true", help="interactive landmark picking")
    ap.add_argument("--points", help="JSON: {image_px:[],field_cm:[]} or {landmarks:[],image_px:[]}")
    ap.add_argument("--refit", action="store_true",
                    help="re-fit from the SAVED clicks in <CH>_calib.json (no re-clicking) — "
                         "use after intrinsics.py to undistort + fit a homography")
    ap.add_argument("--type", choices=["homography", "poly", "affine"], help="override mapping model")
    ap.add_argument("--no-undistort", action="store_true",
                    help="ignore <CH>_intrinsics.json even if present")
    ap.add_argument("--still", help="reference still (default configs/<CH>_reference.png)")
    ap.add_argument("--config-dir", default=str(CONFIG_DIR))
    args = ap.parse_args()

    cfg = Path(args.config_dir)
    layout = fc.load_layout()
    mount = layout.get("camera_mounts", {}).get(args.channel, {})
    mtype = args.type or mount.get("mapping", "homography")
    still = Path(args.still) if args.still else cfg / f"{args.channel}_reference.png"

    if args.points:
        spec = json.loads(Path(args.points).read_text(encoding="utf-8-sig"))
        image_px = np.asarray(spec["image_px"], float)
        if "world_cm" in spec:
            world = np.asarray(spec["world_cm"], float)
            names = spec.get("landmarks", [])
        elif "field_cm" in spec:
            xy0 = np.asarray(spec["field_cm"], float)
            world = np.hstack([xy0, np.zeros((len(xy0), 1))])
            names = spec.get("landmarks", [])
        else:
            names = spec["landmarks"]
            world = np.asarray([fc.resolve_world(n, layout) for n in names], float)
    elif args.pick:
        pj = cfg / f"{args.channel}_points.json"
        if not pj.exists():                                  # auto-generate a default list to trim
            cand = default_landmarks(args.channel, layout)
            pj.write_text(json.dumps({"channel": args.channel, "landmarks": cand,
                                      "_comment": "names from field_layout; '<pole>_top'=wall-top crossing; skip unseen with 's'"},
                                     indent=2), encoding="utf-8")
            print(f"created {pj} with {len(cand)} candidate landmarks (skip unseen ones while picking)")
        names = json.loads(pj.read_text(encoding="utf-8-sig"))["landmarks"]
        if not still.exists():
            raise SystemExit(f"No still {still}. Run: python extract_clip.py --channel {args.channel} --frame")
        image_px, world, names = pick_points(args.channel, still, names, layout)
    elif args.refit:
        prev = json.loads(fc.calib_path(args.channel, cfg).read_text(encoding="utf-8-sig"))
        image_px = np.asarray(prev["image_px"], float)         # RAW saved clicks
        names = prev.get("landmarks", [])
        world = (np.asarray([fc.resolve_world(nm, layout) for nm in names], float)
                 if names else np.asarray(prev["world_points"], float))
        print(f"refit: reusing {len(image_px)} saved clicks from {fc.calib_path(args.channel, cfg).name}")
    else:
        raise SystemExit("Use --pick (interactive), --points <json>, or --refit.")

    n = len(image_px)
    xy = np.asarray(world, float)[:, :2] if n else np.empty((0, 2))

    # lens distortion: if intrinsics exist, undistort pixels and fit a clean homography
    # (after undistortion the ground->image map is projective, so a homography generalises).
    intr = None if args.no_undistort else fc.load_intrinsics(args.channel, cfg)
    undistort = intr is not None and mtype != "pnp"
    fit_px = fc.undistort_px(image_px, intr) if undistort else image_px
    if undistort and mtype in ("poly", "affine"):
        print(f"intrinsics ({intr['model']}) found -> undistorting pixels and fitting HOMOGRAPHY "
              f"(overrides '{mtype}').")
        mtype = "homography"

    if mtype == "pnp":
        if not still.exists():
            raise SystemExit(f"PnP needs the still for image size. Run: python extract_clip.py --channel {args.channel} --frame")
        import cv2
        ih, iw = cv2.imread(str(still)).shape[:2]
        K = intrinsics_from_specs(args.channel, iw, ih, layout)
        K, R, t, rmse = fit_pnp(fit_px, world, K)
        fwd, inv = {"K": K, "R": R, "t": t}, None
    elif mtype == "poly":
        if n < 6:
            raise SystemExit(f"poly (CH01/CH02) needs >=6 points; only {n} clicked. Click more poles.")
        fwd, inv, rmse = fit_poly(fit_px, xy)
    elif mtype == "affine":
        fwd, inv, rmse = fit_affine(fit_px, xy); mtype = "homography"
        print("note: affine (3-pt) model used — OK for near-nadir; less accurate for oblique across depth.")
    else:  # homography
        if n >= 4:
            fwd, inv, rmse = fit_homography(fit_px, xy)
        elif n == 3:
            print("WARNING: only 3 points -> falling back to AFFINE (homography needs >=4).")
            fwd, inv, rmse = fit_affine(fit_px, xy)
        else:
            raise SystemExit(f"need >=4 points for homography (or >=3 for affine); only {n} clicked.")

    out = save_calib(args.channel, mtype, fwd, inv, rmse, names, image_px, world, cfg,
                     undistort=undistort)
    print(f"saved {out}  type={mtype}  undistort={undistort}  points={n}  reproj RMSE={rmse:.2f} cm")
    if still.exists():
        ov = write_grid_overlay(args.channel, still, config_dir=cfg)
        print(f"grid overlay -> {ov}  (green 1-ft grid should land on the real poles)")


if __name__ == "__main__":
    _cli()

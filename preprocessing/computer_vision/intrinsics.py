"""
Lens-intrinsics (distortion) calibration from a checkerboard clip.

CH03/CH04 are wide fisheyes; a single homography/poly fit to a few ground points
overfits (LOO error ~55-97 cm). The fix: estimate the lens distortion explicitly
from a checkerboard, then `calibration.py --refit` undistorts the clicked points
and fits a clean homography that generalises across the whole frame.

Capture (per camera): record a ~60-90 s clip waving a flat checkerboard at varied
tilts (+-30..45 deg), covering ALL frame zones incl. the corners/edges, near+far,
stop-and-go (no motion blur). The board's field location is irrelevant - intrinsics
are a property of the lens.

    python intrinsics.py --channel CH03 --clip scratch/CH03_board.mp4
    python intrinsics.py --channel CH03 --clip <video> --square 9.84 --pattern 3x3

Writes configs/CH03_intrinsics.json {model,K,D,image_size,rms_px,n_views} and a
before/after undistortion preview PNG (eyeball: the straight wall should straighten).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

CONFIG_DIR = Path(__file__).resolve().parent / "configs"

# candidate INNER-corner patterns (cols, rows) tried when --pattern auto
CANDIDATE_PATTERNS = [(3, 3), (3, 4), (4, 3), (2, 3), (3, 2), (4, 4), (5, 4), (4, 5)]


def _resize_long_edge(img, long_edge: int):
    h, w = img.shape[:2]
    s = long_edge / max(h, w)
    if s >= 1.0:
        return img, 1.0
    return cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA), s


_FIND_FLAGS = (cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK)


def _detect(gray, pattern):
    """Fast detect (no sub-pixel). Returns Nx2 corners in `gray` coords, or None."""
    if pattern[0] < 3 or pattern[1] < 3:        # findChessboardCorners needs both sides >=3
        return None
    try:
        ok, c = cv2.findChessboardCorners(gray, pattern, flags=_FIND_FLAGS)
    except cv2.error:
        return None
    return c.reshape(-1, 2) if ok else None


def _refine(full_gray, corners):
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
    c = cv2.cornerSubPix(full_gray, corners.reshape(-1, 1, 2).astype(np.float32),
                         (11, 11), (-1, -1), crit)
    return c.reshape(-1, 2)


def _auto_pattern(det_frames, candidates, need=4):
    """Lock onto the pattern that detects most in the first frames (on downscaled images)."""
    votes = {}
    for g in det_frames:
        for pat in candidates:
            if _detect(g, pat) is not None:
                votes[pat] = votes.get(pat, 0) + 1
                break
        if votes and max(votes.values()) >= need:
            break
    return max(votes, key=votes.get) if votes else None


def _zone(pt, w, h):
    return (min(2, int(pt[0] / w * 3)), min(2, int(pt[1] / h * 3)))


def collect(video: Path, pattern, every: int, long_edge: int, max_per_zone: int,
            det_edge: int = 1280):
    """Sample frames, detect the board (on a downscaled copy for speed, refine at full
    res), keep well-spread views. Returns (imgpts, pattern, (w,h), preview)."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open {video}")
    fulls, dets = [], []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % every == 0:
            full, _ = _resize_long_edge(frame, long_edge)
            fg = cv2.cvtColor(full, cv2.COLOR_BGR2GRAY)
            dg, s = _resize_long_edge(fg, det_edge)          # detect on the small copy
            fulls.append((full, fg)); dets.append((dg, fg.shape[1] / dg.shape[1]))
        i += 1
    cap.release()
    if not fulls:
        raise RuntimeError("no frames decoded")
    h, w = fulls[0][1].shape[:2]

    if pattern is None:
        pattern = _auto_pattern([d for d, _ in dets[: min(60, len(dets))]], CANDIDATE_PATTERNS)
        if pattern is None:
            raise RuntimeError("could not auto-detect the checkerboard in any sampled frame; "
                               "pass --pattern CxR, check lighting/scale, or grab a sharper clip")
        print(f"auto-detected pattern (inner corners) = {pattern[0]}x{pattern[1]}")

    zone_count, imgpts, preview = {}, [], None
    for (full, fg), (dg, up) in zip(fulls, dets):
        c = _detect(dg, pattern)
        if c is None:
            continue
        c = _refine(fg, c * up)                              # scale to full res, sub-pixel refine
        z = _zone(c.mean(axis=0), w, h)
        if zone_count.get(z, 0) >= max_per_zone:
            continue
        zone_count[z] = zone_count.get(z, 0) + 1
        imgpts.append(c.astype(np.float32))
        if preview is None:
            preview = full.copy()
    print(f"sampled {len(fulls)} frames, kept {len(imgpts)} board views over "
          f"{len(zone_count)}/9 frame zones (max {max_per_zone}/zone)")
    return imgpts, pattern, (w, h), preview


def make_objp(pattern, square):
    cols, rows = pattern
    objp = np.zeros((cols * rows, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    return objp * float(square)


def _reproj_rms(objp_list, imgpts, K, D, rvecs, tvecs, fisheye):
    tot, n = 0.0, 0
    for o, ip, rv, tv in zip(objp_list, imgpts, rvecs, tvecs):
        if fisheye:
            proj, _ = cv2.fisheye.projectPoints(o.reshape(-1, 1, 3), rv, tv, K, D)
        else:
            proj, _ = cv2.projectPoints(o, rv, tv, K, D)
        proj = proj.reshape(-1, 2)
        tot += float(np.sum((proj - ip.reshape(-1, 2)) ** 2)); n += len(proj)
    return float(np.sqrt(tot / n)) if n else float("inf")


def _calib_pinhole(objp_list, imgpts, size):
    # few corners/view -> keep the model small (k1,k2,k3 only) so it stays stable
    flags = cv2.CALIB_ZERO_TANGENT_DIST
    rms, K, D, rvecs, tvecs = cv2.calibrateCamera(objp_list, imgpts, size, None, None, flags=flags)
    return {"model": "pinhole", "K": K, "D": D.ravel(), "rvecs": rvecs, "tvecs": tvecs, "rms": rms}


# fisheye flag constants (names vary across OpenCV builds; fall back to known int values)
_FISH_RECOMPUTE = getattr(cv2.fisheye, "CALIB_RECOMPUTE_EXTRINSIC", 1)
_FISH_FIX_SKEW = getattr(cv2.fisheye, "CALIB_FIX_SKEW", 4)


def _calib_fisheye(objp_list, imgpts, size):
    obj = [o.reshape(-1, 1, 3).astype(np.float64) for o in objp_list]
    img = [p.reshape(-1, 1, 2).astype(np.float64) for p in imgpts]
    K = np.zeros((3, 3)); D = np.zeros((4, 1))
    n = len(obj)
    rvecs = [np.zeros((1, 1, 3)) for _ in range(n)]
    tvecs = [np.zeros((1, 1, 3)) for _ in range(n)]
    flags = (_FISH_RECOMPUTE | _FISH_FIX_SKEW)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-6)
    rms, K, D, rvecs, tvecs = cv2.fisheye.calibrate(obj, img, size, K, D, rvecs, tvecs, flags, crit)
    return {"model": "fisheye", "K": K, "D": D.ravel(), "rvecs": rvecs, "tvecs": tvecs, "rms": rms}


def _heldout_rms(objp_list, imgpts, size, calib_fn):
    """Train on 80% of views, score reprojection on the held-out 20% (honest generalisation)."""
    n = len(imgpts)
    if n < 6:
        return float("inf")
    rng = np.random.default_rng(0)
    idx = rng.permutation(n)
    k = max(1, n // 5)
    test, train = idx[:k], idx[k:]
    tr = calib_fn([objp_list[i] for i in train], [imgpts[i] for i in train], size)
    # solve each test view's pose with the trained K,D, then measure reprojection
    tot, m = 0.0, 0
    for i in test:
        o, ip = objp_list[i], imgpts[i]
        if tr["model"] == "fisheye":
            ipu = cv2.fisheye.undistortPoints(ip.reshape(-1, 1, 2).astype(np.float64),
                                              tr["K"], tr["D"], P=tr["K"])
            ok, rv, tv = cv2.solvePnP(o, ipu.reshape(-1, 1, 2).astype(np.float32), tr["K"],
                                      np.zeros(5), flags=cv2.SOLVEPNP_ITERATIVE)
            proj, _ = cv2.fisheye.projectPoints(o.reshape(-1, 1, 3), rv, tv, tr["K"], tr["D"])
        else:
            ok, rv, tv = cv2.solvePnP(o, ip, tr["K"], tr["D"], flags=cv2.SOLVEPNP_ITERATIVE)
            proj, _ = cv2.projectPoints(o, rv, tv, tr["K"], tr["D"])
        if not ok:
            return float("inf")
        proj = proj.reshape(-1, 2)
        tot += float(np.sum((proj - ip.reshape(-1, 2)) ** 2)); m += len(proj)
    return float(np.sqrt(tot / m)) if m else float("inf")


# ============================ plumb-line (wall) method ============================
# For fixed pole cameras a checkerboard rarely reaches the frame corners (where distortion
# lives). The paddock wall is a big, high-contrast, KNOWN-straight structure spanning the
# whole width -> click points along its straight rails and solve the radial distortion that
# makes them straight. Focal length is seeded from the FOV spec (the later homography absorbs
# any residual linear scale), so we only optimise the radial terms k1,k2,k3.

def pick_lines(still_png):
    """Click points along each known-straight line; 'n' = next line, 'u' = undo, 'q' = done."""
    img = cv2.imread(str(still_png))
    if img is None:
        raise FileNotFoundError(still_png)
    lines, cur = [], []
    win = "plumb-line: click along each STRAIGHT wall edge"

    def redraw():
        d = img.copy()
        for L in lines + [cur]:
            for (x, y) in L:
                cv2.circle(d, (int(x), int(y)), 4, (0, 255, 0), -1)
            if len(L) >= 2:
                cv2.polylines(d, [np.asarray(L, np.int32).reshape(-1, 1, 2)], False, (0, 200, 255), 1)
        msg = f"line {len(lines)+1}, {len(cur)} pts | click along ONE straight edge; n=next u=undo q=done"
        cv2.rectangle(d, (0, 0), (d.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(d, msg, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1)
        cv2.imshow(win, d)

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            cur.append([float(x), float(y)]); redraw()

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    redraw()
    while True:
        k = cv2.waitKey(20) & 0xFF
        if k == ord("n") and len(cur) >= 3:
            lines.append(list(cur)); cur.clear(); redraw()
        elif k == ord("u") and cur:
            cur.pop(); redraw()
        elif k == ord("q"):
            if len(cur) >= 3:
                lines.append(list(cur))
            break
    cv2.destroyAllWindows()
    return [np.asarray(L, float) for L in lines]


def _line_residual_sq(pts):
    """Sum of squared perpendicular distances of Nx2 pts from their total-least-squares line."""
    c = pts.mean(axis=0)
    u, s, vt = np.linalg.svd(pts - c)
    n = vt[1]                                   # unit normal to the best-fit line
    return float(np.sum(((pts - c) @ n) ** 2))


def _plumb_cost(radial, lines, K):
    D = np.array([radial[0], radial[1], 0.0, 0.0, radial[2]], float)
    tot = 0.0
    for L in lines:
        u = cv2.undistortPoints(L.reshape(-1, 1, 2), K, D, P=K).reshape(-1, 2)
        tot += _line_residual_sq(u)
    return tot


def _pattern_search(f, x0, step=0.1, shrink=0.5, tol=1e-7, iters=200):
    """Numpy-only coordinate pattern search (no scipy). Good for 2-3 smooth params."""
    x = np.asarray(x0, float); fx = f(x)
    for _ in range(iters):
        improved = False
        for i in range(len(x)):
            for s in (step, -step):
                y = x.copy(); y[i] += s; fy = f(y)
                if fy < fx:
                    x, fx = y, fy; improved = True
        if not improved:
            step *= shrink
            if step < tol:
                break
    return x, fx


def solve_plumb(lines, K):
    radial, cost = _pattern_search(lambda r: _plumb_cost(r, lines, K), [0.0, 0.0, 0.0])
    D = np.array([radial[0], radial[1], 0.0, 0.0, radial[2]], float)
    npts = sum(len(L) for L in lines)
    rms0 = np.sqrt(sum(_line_residual_sq(L) for L in lines) / npts)
    rms1 = np.sqrt(_plumb_cost(radial, lines, K) / npts)
    return D, radial, rms0, rms1


def run_plumb(args):
    import calibration as cal
    cfg = Path(args.config_dir)
    still = Path(args.still) if args.still else cfg / f"{args.channel}_reference.png"
    if not still.exists():
        raise SystemExit(f"no still {still}; grab one with extract_clip.py --channel {args.channel} --frame")
    layout = fc.load_layout()
    ih, iw = cv2.imread(str(still)).shape[:2]
    K = cal.intrinsics_from_specs(args.channel, iw, ih, layout)
    lines = pick_lines(still)
    if len(lines) < 2:
        raise SystemExit("need >=2 straight lines (e.g. wall top rail + bottom rail), >=3 pts each.")
    D, radial, rms0, rms1 = solve_plumb(lines, K)
    print(f"plumb-line: {len(lines)} lines, straightness RMS {rms0:.2f}px -> {rms1:.2f}px  "
          f"(k1={radial[0]:.4f} k2={radial[1]:.4f} k3={radial[2]:.4f})")
    out = {"channel": args.channel, "model": "pinhole",
           "K": np.asarray(K, float).tolist(), "D": D.tolist(),
           "image_size": [int(iw), int(ih)], "method": "plumb_line",
           "lines": int(len(lines)), "straightness_rms_px": float(rms1)}
    p = cfg / f"{args.channel}_intrinsics.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"saved {p}")
    und = cv2.undistort(cv2.imread(str(still)), np.asarray(K), D)
    pv = cfg / f"{args.channel}_undistort_preview.png"
    cv2.imwrite(str(pv), np.vstack([cv2.imread(str(still)), und]))
    print(f"wrote {pv}  (top=raw, bottom=undistorted; the wall should be STRAIGHTER below)")
    print(f"next: python calibration.py --channel {args.channel} --refit")


def main() -> None:
    ap = argparse.ArgumentParser(description="Lens-intrinsics calibration (checkerboard or plumb-line).")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--clip", help="checkerboard video (board waved through the frame)")
    ap.add_argument("--plumb", action="store_true",
                    help="plumb-line method: click points along straight wall edges on the still")
    ap.add_argument("--still", help="reference still for --plumb (default configs/<CH>_reference.png)")
    ap.add_argument("--square", type=float, default=9.8425, help="square size cm (3-7/8 in)")
    ap.add_argument("--pattern", default="auto", help="inner corners CxR (e.g. 3x3) or 'auto'")
    ap.add_argument("--every", type=int, default=10, help="sample every Nth frame")
    ap.add_argument("--scale", type=int, default=2560, help="long-edge px (match the reference still)")
    ap.add_argument("--max-per-zone", type=int, default=6)
    ap.add_argument("--config-dir", default=str(CONFIG_DIR))
    args = ap.parse_args()

    if args.plumb:
        run_plumb(args)
        return
    if not args.clip:
        raise SystemExit("provide --clip <board video> (checkerboard) or --plumb (wall method).")

    pattern = None
    if args.pattern != "auto":
        cols, rows = (int(v) for v in args.pattern.lower().split("x"))
        pattern = (cols, rows)

    imgpts, pattern, size, preview = collect(
        Path(args.clip), pattern, args.every, args.scale, args.max_per_zone)
    if len(imgpts) < 6:
        raise SystemExit(f"only {len(imgpts)} board views; need >=6 (>=20 recommended). "
                         "Capture a longer/sharper clip covering more of the frame.")
    objp = make_objp(pattern, args.square)
    objp_list = [objp.copy() for _ in imgpts]

    # pick the better distortion model by HELD-OUT reprojection (not in-sample)
    results = {}
    for name, fn in (("pinhole", _calib_pinhole), ("fisheye", _calib_fisheye)):
        try:
            ho = _heldout_rms(objp_list, imgpts, size, fn)
            full = fn(objp_list, imgpts, size)
            results[name] = (ho, full)
            print(f"  {name}: held-out RMS={ho:.3f}px  in-sample RMS={full['rms']:.3f}px")
        except cv2.error as e:
            print(f"  {name}: failed ({str(e).splitlines()[-1][:80]})")
    if not results:
        raise SystemExit("both calibration models failed; check the board/clip.")
    best = min(results, key=lambda k: results[k][0])
    ho, full = results[best]
    K, D = full["K"], full["D"]
    print(f"chosen model: {best}  (held-out RMS={ho:.3f}px)")

    out = {
        "channel": args.channel, "model": best,
        "K": np.asarray(K, float).tolist(), "D": np.asarray(D, float).ravel().tolist(),
        "image_size": [int(size[0]), int(size[1])],
        "pattern": [pattern[0], pattern[1]], "square_cm": args.square,
        "rms_px": float(full["rms"]), "heldout_rms_px": float(ho), "n_views": len(imgpts),
    }
    cfg = Path(args.config_dir)
    p = cfg / f"{args.channel}_intrinsics.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"saved {p}")

    # before/after preview for the wall-straightness eyeball check
    if preview is not None:
        if best == "fisheye":
            und = cv2.fisheye.undistortImage(preview, np.asarray(K), np.asarray(D), Knew=np.asarray(K))
        else:
            und = cv2.undistort(preview, np.asarray(K), np.asarray(D))
        combo = np.vstack([preview, und])
        pv = cfg / f"{args.channel}_undistort_preview.png"
        cv2.imwrite(str(pv), combo)
        print(f"wrote {pv}  (top=raw, bottom=undistorted; the wall should look STRAIGHTER below)")
    print(f"next: python calibration.py --channel {args.channel} --refit")


if __name__ == "__main__":
    main()

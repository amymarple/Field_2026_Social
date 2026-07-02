"""
View-quality + glass-noise-resistant motion for the IR-window shelter cams (CH05/CH06).

CH05/CH06 look at the rats THROUGH an IR-transmitting window, so rain, condensation/fog,
water drips, and sun glare land on the glass between the lens and the animals. A raw ROI
frame-difference (what shelter_sleep used) counts those artifacts as "motion" -> false
`occupied-high-motion`, and fog flattens contrast -> false `low-motion`/`empty`.

This module provides the two primitives shelter_sleep needs to stop that (Phase A):

  1. view_quality(gray, mask, cfg) -> one of  'clear' / 'degraded' / 'unusable'
     from cheap per-ROI metrics (mean luma, saturated/dark pixel ratios ported from
     reolink_record/overexposure_check.ps1, Laplacian-variance sharpness -> fog, contrast).

  2. robust_inside_motion(frames_gray, mask, cfg) -> a motion score that survives fog but
     rejects glass artifacts:
        illumination-normalize each burst frame (subtract ROI mean -> cancels glare / AE hunting)
        -> temporal MEDIAN background (removes static drips)
        -> keep only pixels DARKER than the background (the rat body / black dorsal stripe;
           rain drops are BRIGHT/specular and are dropped)
        -> morphological open (removes speckle) -> sum area of rat-sized blobs.
     A stationary rat sits in the median (no residual) -> low score = rest proxy. A moving dark
     rat darkens pixels vs the median -> high score. Rain/glare/AE never score as activity.

Plus a tiny loader for data_manifests/field_conditions.yaml so a logged fog/rain window can
force a bin to >= 'degraded' (belt-and-suspenders on top of the auto-detection).

Pure numpy + OpenCV, GPU-free, ROI-cropped -> light on the live-capture PC. Offline self-test:
    python view_quality.py --selftest
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE / "configs"
DEFAULT_CFG_PATH = CONFIG_DIR / "view_quality.yaml"

TIERS = ("clear", "degraded", "unusable")

# ---- thresholds (overridable via configs/view_quality.yaml) ----
# Exposure/black thresholds mirror reolink_record/overexposure_check.ps1. Sharpness/contrast
# are content-dependent -> tune per camera on real frames; these defaults suit ~1280x960 IR.
DEFAULTS = {
    # near-black (dead feed / covered / very dark) -> unusable
    "dark_luma": 16,            # a pixel <= this counts as black
    "dark_ratio_unusable": 0.80,
    "mean_low_unusable": 12,
    # glare / blown-out (sun on glass) -> degraded, or unusable if severe
    "sat_luma": 250,            # a pixel >= this counts as saturated
    "sat_ratio_degraded": 0.12,
    "sat_ratio_unusable": 0.35,
    "mean_high_unusable": 235,
    # fog / condensation -> low sharpness + low contrast
    "sharpness_degraded": 40.0,   # variance-of-Laplacian below this = soft/foggy
    "sharpness_unusable": 8.0,
    "contrast_degraded": 18.0,    # ROI std below this = washed out
    "contrast_unusable": 7.0,
    # fog / condensation on the glass BRIGHTENS the ROI (IR backscatter). Detected per-channel as
    # mean >= fog_mean AND sharpness <= fog_sharpness. Disabled by default (fires only when a
    # channel sets thresholds in view_quality.yaml); baselines differ per camera so no global value.
    "fog_mean": 100000.0,
    "fog_sharpness": 0.0,
    # robust motion
    "motion_pix_thresh": 18.0,    # per-pixel darkening (vs median bg) to count as changed
    "open_ksize": 3,              # morphological-open kernel (kills speckle)
    "min_blob_area_frac": 0.0015, # a "rat-sized" blob is at least this fraction of the ROI
    "motion_thresh": 0.30,        # inside_motion_score (% of ROI) above this = high-motion
    "present_motion_floor": 0.08, # score above this = something present even if detector missed
}


def load_config(path: str | Path | None = None, channel: str | None = None) -> dict:
    """DEFAULTS merged with configs/view_quality.yaml `view_quality:` block, then (if `channel`
    given) that camera's overrides under `channels: <CH>:`. Per-channel is needed because fog
    thresholds (brightness/sharpness baselines) differ between cameras."""
    cfg = dict(DEFAULTS)
    p = Path(path) if path else DEFAULT_CFG_PATH
    if p.exists():
        try:
            import yaml
            doc = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
        except ImportError:
            print("[view_quality] PyYAML not installed; using built-in defaults.")
            return cfg
        except Exception as e:  # noqa: BLE001 - a bad config must not crash analysis
            print(f"[view_quality] could not read {p}: {e}; using defaults.")
            return cfg
        blocks = [doc.get("view_quality", doc) or {}]
        if channel:
            blocks.append((doc.get("channels", {}) or {}).get(channel, {}) or {})
        for block in blocks:
            for k, v in block.items():
                if k in cfg and isinstance(v, (int, float)):
                    cfg[k] = v
    return cfg


# ============================ frame I/O ============================
def read_gray(p):
    """Read a frame as a guaranteed 2-D single-channel uint8 array. IMREAD_GRAYSCALE can still
    return a 3-D array here (a singleton (H,W,1) axis, or genuine 3-channel), which breaks the ROI
    `shape` unpack and cv2 color ops -- normalize all cases. Returns None on failure."""
    g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if g is None:
        return None
    if g.ndim == 3:
        g = g[:, :, 0] if g.shape[2] == 1 else cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
    return np.ascontiguousarray(g)


# ============================ zones ============================
def poly_mask(poly, w: int, h: int) -> np.ndarray:
    """Boolean HxW mask from an Nx2 polygon (image px)."""
    m = np.zeros((h, w), np.uint8)
    pts = np.asarray(poly, np.int32).reshape(-1, 2)
    if len(pts) >= 3:
        cv2.fillPoly(m, [pts], 255)
    return m > 0


def load_zones(channel: str, config_dir: str | Path = CONFIG_DIR) -> dict:
    """Zones for a channel: {inside:[poly], doors:[[p,p],...], image_size:[w,h]}.

    `inside` (the shelter footprint polygon) is the only zone Phase A uses; `doors` are 2-point
    gate lines for the deferred Phase-B entry/exit counting. Falls back to the calibration shelter
    quad as `inside` if no zones file exists yet, so the pipeline still runs before place_zones.py
    is used. (Legacy doorway/outside polygon keys are still read if present, for back-compat.)
    """
    p = Path(config_dir) / f"{channel}_zones.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    calib = Path(config_dir) / f"{channel}_calib.json"
    if calib.exists():
        d = json.loads(calib.read_text(encoding="utf-8-sig"))
        w, h = d.get("image_size") or [1280, 960]
        return {"inside": d.get("image_px", []), "doorway": [], "outside": [],
                "image_size": [int(w), int(h)], "_fallback": True}
    raise FileNotFoundError(f"No zones or calib for {channel} ({p}).")


# ============================ view quality ============================
def zone_metrics(gray: np.ndarray, mask: np.ndarray) -> dict:
    """Cheap exposure/sharpness/contrast metrics over the masked ROI."""
    roi = gray[mask]
    if roi.size == 0:
        return {"n": 0, "mean": 0.0, "sat_ratio": 0.0, "dark_ratio": 1.0,
                "contrast": 0.0, "sharpness": 0.0}
    lap = cv2.Laplacian(gray, cv2.CV_64F, ksize=3)
    return {
        "n": int(roi.size),
        "mean": float(roi.mean()),
        "sat_ratio": float((roi >= DEFAULTS["sat_luma"]).mean()),
        "dark_ratio": float((roi <= DEFAULTS["dark_luma"]).mean()),
        "contrast": float(roi.std()),
        "sharpness": float(lap[mask].var()),
    }


def classify(metrics: dict, cfg: dict) -> str:
    """Map metrics -> 'clear' / 'degraded' / 'unusable' (conservative: prefer degraded)."""
    if metrics["n"] == 0:
        return "unusable"
    m = metrics
    # near-black (dead / covered / too dark to read)
    if m["dark_ratio"] >= cfg["dark_ratio_unusable"] or m["mean"] <= cfg["mean_low_unusable"]:
        return "unusable"
    # glare / blown-out
    if m["sat_ratio"] >= cfg["sat_ratio_unusable"] or m["mean"] >= cfg["mean_high_unusable"]:
        return "unusable"
    # fog / condensation (soft + washed out)
    if m["sharpness"] <= cfg["sharpness_unusable"] or m["contrast"] <= cfg["contrast_unusable"]:
        return "unusable"
    # fog-on-glass backscatter: bright AND soft (per-channel thresholds; disabled unless set)
    fog = m["mean"] >= cfg["fog_mean"] and m["sharpness"] <= cfg["fog_sharpness"]
    if (fog or m["sat_ratio"] >= cfg["sat_ratio_degraded"] or
            m["sharpness"] <= cfg["sharpness_degraded"] or
            m["contrast"] <= cfg["contrast_degraded"]):
        return "degraded"
    return "clear"


def view_quality(gray: np.ndarray, mask: np.ndarray, cfg: dict) -> tuple[str, dict]:
    """(tier, metrics) for one zone. gray = uint8 grayscale, mask = boolean ROI."""
    metr = zone_metrics(gray, mask)
    return classify(metr, cfg), metr


# ============================ robust motion ============================
def robust_inside_motion(frames_gray, mask: np.ndarray, cfg: dict) -> tuple[float, dict]:
    """Glass-noise-resistant motion score over a short burst of grayscale frames.

    Returns (score, debug). `score` = % of the ROI covered by coherent DARK moving blobs.
    Rejects rain (bright speckle), glare/AE (global shift), and static drips (in the median).
    """
    fs = [np.asarray(f, np.float32) for f in frames_gray if f is not None]
    if len(fs) < 2 or not mask.any():
        return 0.0, {"n_frames": len(fs), "blob_area": 0, "roi_area": int(mask.sum())}
    # 1. illumination-normalize: subtract each frame's ROI mean (cancels glare / AE hunting)
    norm = np.stack([f - float(f[mask].mean()) for f in fs], axis=0)      # (T,H,W)
    # 2. temporal median background (static drips + static rat live here -> no residual)
    bg = np.median(norm, axis=0)
    # 3. keep only DARKENING vs background (rat body / dark stripe; bright drips excluded)
    dark = np.clip(bg[None] - norm, 0.0, None).max(axis=0)               # strongest darkening
    change = (dark >= cfg["motion_pix_thresh"]).astype(np.uint8)
    change[~mask] = 0
    # 4. morphological open (kill speckle) + keep rat-sized connected blobs
    k = np.ones((int(cfg["open_ksize"]), int(cfg["open_ksize"])), np.uint8)
    opened = cv2.morphologyEx(change, cv2.MORPH_OPEN, k)
    roi_area = int(mask.sum()) or 1
    min_area = max(1, int(cfg["min_blob_area_frac"] * roi_area))
    n, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    blob_area = sum(int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)
                    if stats[i, cv2.CC_STAT_AREA] >= min_area)
    score = 100.0 * blob_area / roi_area
    return float(score), {"n_frames": len(fs), "blob_area": blob_area,
                          "roi_area": roi_area, "min_area": min_area}


# ============================ field_conditions.yaml cross-check ============================
def load_conditions(path: str | Path) -> list[dict]:
    """Parse data_manifests/field_conditions.yaml -> list of window dicts. [] if unavailable."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        import yaml
    except ImportError:
        print("[view_quality] PyYAML not installed; weather-log cross-check disabled.")
        return []
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    except Exception as e:  # noqa: BLE001
        print(f"[view_quality] could not read {p}: {e}")
        return []
    return list(doc.get("conditions", []))


def _channel_hit(cond_channels, channel: str) -> bool:
    if cond_channels in (None, "all"):
        return True
    if isinstance(cond_channels, str):
        return cond_channels == channel
    return channel in cond_channels


def in_degraded_window(conditions, channel: str, ts) -> tuple[bool, str]:
    """Is timestamp `ts` (pandas Timestamp) inside a logged fog/rain window for `channel`?

    Times in the log are OBSERVED wall-clock; the OSD runs ~1 h behind the FILENAMES on this
    rig, so a small mismatch is expected. Matching is done against the bin's filename-derived
    timestamp; adjust the log or shift the bin time upstream if you need tighter alignment.
    """
    import pandas as pd
    for c in conditions:
        if not _channel_hit(c.get("channels"), channel):
            continue
        try:
            day = pd.Timestamp(str(c["date"])).date()
        except Exception:  # noqa: BLE001
            continue
        if ts.date() != day:
            continue
        start = pd.Timestamp(f"{day} {c.get('start', '00:00')}")
        end = pd.Timestamp(f"{day} {c['end']}") if c.get("end") else pd.Timestamp(f"{day} 23:59:59")
        if start <= ts <= end:
            return True, f"{c.get('type', 'degraded')}: {c.get('note', '')}".strip()
    return False, ""


# ============================ self-test (offline, synthetic) ============================
def _textured(w, h, amp=45, seed=0):
    """A textured mid-gray scene (good contrast + sharpness) = a 'clear' background."""
    rng = np.random.default_rng(seed)
    base = np.full((h, w), 128.0, np.float32)
    base += rng.normal(0, amp / 3.0, (h, w)).astype(np.float32)   # fine texture -> sharpness
    yy, xx = np.mgrid[0:h, 0:w]
    base += amp * np.sin(xx / 7.0) * np.cos(yy / 9.0)             # structure -> contrast
    return np.clip(base, 0, 255).astype(np.uint8)


def _with_dark_blob(scene, cx, cy, r=9, val=35):
    f = scene.copy()
    cv2.circle(f, (cx, cy), r, int(val), -1)
    return f


def _selftest() -> int:
    cfg = load_config()
    w = h = 120
    mask = np.zeros((h, w), bool)
    mask[20:100, 20:100] = True
    scene = _textured(w, h)
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want if isinstance(want, str) else want(got)
        ok = ok and good
        print(f"  {name:34s} -> {got!s:>10}   {'OK' if good else 'FAIL (want ' + str(want) + ')'}")

    print("=== view_quality self-test (synthetic, no disk/ffmpeg/GPU) ===")
    print("view-quality classification:")
    check("clear scene", view_quality(scene, mask, cfg)[0], "clear")
    fog = cv2.GaussianBlur((scene.astype(np.float32) * 0.25 + 150).astype(np.uint8), (0, 0), 6)
    check("fog (soft, washed out)", view_quality(fog, mask, cfg)[0], lambda t: t in ("degraded", "unusable"))
    glare = scene.copy(); glare[30:90, 30:90] = 255
    check("glare (blown-out patch)", view_quality(glare, mask, cfg)[0], lambda t: t in ("degraded", "unusable"))
    black = np.full((h, w), 4, np.uint8)
    check("near-black (dead feed)", view_quality(black, mask, cfg)[0], "unusable")

    print("robust motion (score vs thresholds):")
    thr, floor = cfg["motion_thresh"], cfg["present_motion_floor"]
    # moving dark rat: dark blob at different positions across the burst
    moving = [_with_dark_blob(scene, 40, 55), _with_dark_blob(scene, 55, 58), _with_dark_blob(scene, 70, 60)]
    s_move = robust_inside_motion(moving, mask, cfg)[0]
    check(f"moving dark rat (>= {thr})", round(s_move, 3), lambda s: s >= thr)
    # stationary rat: same dark blob every frame -> lives in the median -> low score (rest)
    stat = [_with_dark_blob(scene, 55, 58)] * 3
    s_stat = robust_inside_motion(stat, mask, cfg)[0]
    check(f"stationary rat (< {floor})", round(s_stat, 3), lambda s: s < floor)
    # rain: bright scattered speckle, different each frame -> excluded (not darkening)
    rain = []
    rng = np.random.default_rng(1)
    for _ in range(3):
        f = scene.copy()
        ys = rng.integers(20, 100, 60); xs = rng.integers(20, 100, 60)
        f[ys, xs] = 255
        rain.append(f)
    s_rain = robust_inside_motion(rain, mask, cfg)[0]
    check(f"rain bright speckle (< {floor})", round(s_rain, 3), lambda s: s < floor)
    # global brightness shift (AE hunting): whole frame brightens -> normalized away
    shift = [np.clip(scene.astype(np.int16) + d, 0, 255).astype(np.uint8) for d in (-30, 0, 30)]
    s_shift = robust_inside_motion(shift, mask, cfg)[0]
    check(f"global AE shift (< {floor})", round(s_shift, 3), lambda s: s < floor)

    print(f"\nSELF TEST: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


def _inside_mask_for(zones, g):
    """Inside-ROI boolean mask scaled to frame g's size (falls back to full frame)."""
    h, w = g.shape[:2]
    isz = zones.get("image_size") or [w, h]
    poly = np.asarray(zones.get("inside", []), float).reshape(-1, 2)
    if len(poly) < 3:
        return np.ones((h, w), bool)
    return poly_mask(poly * np.array([w / isz[0], h / isz[1]]), w, h)


def _probe(channel, date, hours, every_sec, threads, config) -> int:
    """Sample an hour's CLOSED frames and print the real inside-ROI metric distribution + tier mix,
    so view_quality.yaml thresholds can be tuned to actual fog-vs-clear frames (no detector)."""
    import shutil
    from collections import Counter
    import extract_clip as ec
    import scan_for_rats as scan
    cfg = load_config(config, channel)
    zones = load_zones(channel)
    W = int((zones.get("image_size") or [1280, 960])[0])
    files = sorted((ec.REC_ROOT / channel).glob(f"{channel}_{date}_*.mp4"))
    if hours:
        files = [f for f in files if scan.clip_start(f.name)[1] in set(hours)]
    files = [f for f in files if scan.is_closed(f.name)]
    if not files:
        raise SystemExit(f"no closed files for {channel} {date} hours={hours}")
    tmp = HERE / "scratch" / f"probe_{channel}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    mask, rows = None, []
    for f in files:
        frames, _ = scan.sample_frames(f, every_sec, W, tmp, threads)
        for p in frames:
            g = read_gray(p)
            if g is None:
                continue
            if mask is None:
                mask = _inside_mask_for(zones, g)
            m = zone_metrics(g, mask)
            m["tier"] = classify(m, cfg)
            rows.append(m)
        for p in tmp.glob("*.png"):
            p.unlink()
    shutil.rmtree(tmp, ignore_errors=True)
    if not rows:
        raise SystemExit("no frames sampled.")

    def stat(key):
        a = np.array([r[key] for r in rows], float)
        return (f"{a.min():9.2f} {np.percentile(a,10):9.2f} {np.percentile(a,50):9.2f} "
                f"{np.percentile(a,90):9.2f} {a.max():9.2f}")
    print(f"\n[{channel} {date} hours={hours}]  {len(rows)} bins  inside-ROI metrics:")
    print(f"  {'metric':10s}: {'min':>9} {'p10':>9} {'p50':>9} {'p90':>9} {'max':>9}")
    for key in ("mean", "contrast", "sharpness", "sat_ratio", "dark_ratio"):
        print(f"  {key:10s}: {stat(key)}")
    print("  tier mix (current thresholds):", dict(Counter(r["tier"] for r in rows)))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="View-quality + robust-motion primitives (CH05/CH06).",
                                 allow_abbrev=False)
    ap.add_argument("--selftest", action="store_true", help="offline synthetic logic check, then exit")
    ap.add_argument("--probe", action="store_true",
                    help="sample real CLOSED frames and print inside-ROI metric distribution (threshold tuning)")
    ap.add_argument("--channel")
    ap.add_argument("--date")
    ap.add_argument("--hours", type=int, nargs="+")
    ap.add_argument("--every-sec", type=float, default=60.0)
    ap.add_argument("--ffmpeg-threads", type=int, default=2)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    if args.probe:
        if not (args.channel and args.date):
            ap.error("--probe needs --channel and --date")
        raise SystemExit(_probe(args.channel, args.date, args.hours, args.every_sec,
                                args.ffmpeg_threads, args.config))
    ap.error("nothing to do; try --selftest or --probe (this module is imported by shelter_sleep.py).")


if __name__ == "__main__":
    main()

"""
Shelter occupancy + REST PROXY for the IR-WINDOW shelter cams (CH05/CH06).

NOT individual tracking, NOT EEG sleep. Per time-bin we report a shelter-level state:
    empty                 - no rat present
    occupied_low_motion   - rat(s) present, little movement (rest proxy)
    occupied_high_motion  - rat(s) present, active movement
    indeterminate         - the inside-glass view is too degraded to read occupancy/rest

CH05/CH06 image the rats THROUGH an IR-transmitting window, so rain, condensation/fog, water
drips, and sun glare land on the glass between lens and animals. The whole point of this stage
is that those artifacts must NEVER be counted as rat activity. So each bin carries a per-zone
`view_quality` (clear / degraded / unusable), and:
  - a degraded inside-glass bin can never become automatic `occupied_high_motion`;
  - an unusable inside-glass bin is `indeterminate` for occupancy/rest;
  - motion inside is measured with a glass-noise-resistant signal (view_quality.robust_inside_motion:
    illumination-normalize -> temporal median -> keep only dark moving blobs -> reject rain/glare/AE);
  - raw YOLO counts per zone are kept as EVIDENCE columns, never overwritten by the fused estimate;
  - doorway/outside detections are supporting evidence (sparse sampling misses crossings), not a count.

CAPTURE SAFETY: only CLOSED recordings ('_to_' files) are read, never the file being recorded
(enforced in scan_for_rats.is_closed; see CLAUDE.md).

    python shelter_sleep.py --date 2026-06-30                 # CH05+CH06, full day
    python shelter_sleep.py --date 2026-06-30 --hours 3 4 5   # a few hours (dry run)

Outputs (outputs/, gitignored): <CH>_sleep_<date>.csv (per-bin), sleep_timeline_<date>.png,
<CH>_rest_heatmap_<date>.png (approximate, clear bins only), and a printed split summary.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

import extract_clip as ec
import field_coords as fc
import scan_for_rats as scan
import view_quality as vq
import glass_regime as gr        # annotation-only optical-regime covariate; changes no metric/decision
import measurement_context as mc  # annotation + provenance only (camera covariates + run manifest)

HERE = Path(__file__).resolve().parent
# Shelter default detector (also inherited by validate_shelter.py via ss.DEF_WEIGHTS). Points at the
# 2026-07-04 fine-tune on the expanded CH05/CH06 set (val mAP50 0.876, up from ~0.52). NB: a numbered
# run dir is volatile - a later train_detector.py run makes rat_feasibility-7; re-point or promote to a
# stable name if you retrain. Prior default was runs/detect/rat_daynight (kept on disk, not deleted).
DEF_WEIGHTS = HERE / "runs" / "detect" / "rat_feasibility-6" / "weights" / "best.pt"
CONDITIONS_PATH = HERE.parents[1] / "data_manifests" / "field_conditions.yaml"

STATE_COLORS = {"empty": "#e8e8e8", "occupied_low_motion": "#3b6fb5",
                "occupied_high_motion": "#e08a1e", "indeterminate": "#9a9a9a"}
STATES = tuple(STATE_COLORS)


def resolve_day_files(channel: str, date: str, hours):
    """Closed hour-files for a channel+date (never the open/recording file)."""
    files = sorted((ec.REC_ROOT / channel).glob(f"{channel}_{date}_*.mp4"))
    if hours:
        files = [f for f in files if scan.clip_start(f.name)[1] in set(hours)]
    kept = [f for f in files if scan.is_closed(f.name)]
    dropped = [f.name for f in files if not scan.is_closed(f.name)]
    if dropped:
        print(f"[{channel}] skipping {len(dropped)} OPEN file(s): {dropped}")
    return kept


# ---- backward-compatible ROI helpers (still used by validate_shelter.py) ----
def load_roi(channel: str, config_dir=fc.CONFIG_DIR):
    """Shelter quad (the 4 clicked corners) + calib image size, from the calibration JSON."""
    d = json.loads((Path(config_dir) / f"{channel}_calib.json").read_text(encoding="utf-8-sig"))
    ipx = np.asarray(d["image_px"], np.float32)
    w, h = d.get("image_size") or [1280, 960]
    return ipx, int(w), int(h)


def roi_mask(ipx, w, h):
    m = np.zeros((h, w), np.uint8)
    hull = cv2.convexHull(ipx.astype(np.int32))
    cv2.fillConvexPoly(m, hull, 255)
    return m > 0


def zone_masks(zones: dict, w: int, h: int) -> dict:
    """Boolean masks for inside/doorway/outside, scaling polygons from the zones' image_size
    to the actual (w, h) of the sampled frames."""
    isz = zones.get("image_size") or [w, h]
    sx, sy = w / isz[0], h / isz[1]
    out = {}
    for z in ("inside", "doorway", "outside"):
        poly = np.asarray(zones.get(z, []), float).reshape(-1, 2)
        out[z] = vq.poly_mask(poly * np.array([sx, sy]), w, h) if len(poly) >= 3 else np.zeros((h, w), bool)
    return out


def sample_burst(src, t, W, tmp, i, threads, gap, n_burst):
    """One ffmpeg call at time t -> a short burst of `n_burst` frames `gap` s apart (for the
    glass-noise-resistant motion signal). Returns a list of frame paths (may be shorter)."""
    pat = tmp / f"{src.stem}_{i:05d}_%02d.png"
    ec._run_ffmpeg(["-threads", str(threads), "-ss", f"{t:.2f}", "-i", str(src),
                    "-vf", f"fps={1.0/gap:.4f},scale={W}:-2", "-frames:v", str(n_burst), str(pat)])
    return sorted(tmp.glob(f"{src.stem}_{i:05d}_*.png"))


def sample_pair(src, t, W, tmp, i, threads, gap):
    """Back-compat 2-frame burst (validate_shelter.py). Returns (frame0, frame1_or_None)."""
    outs = sample_burst(src, t, W, tmp, i, threads, gap, 2)
    return (outs[0] if outs else None), (outs[1] if len(outs) > 1 else None)


read_gray = vq.read_gray   # single source of truth (used here + by validate_shelter as ss.read_gray)


def _zone_of(cx, cy, masks) -> str | None:
    """Which zone a detection centre falls in (inside > doorway > outside priority)."""
    h, w = masks["inside"].shape
    xi, yi = int(round(cx)), int(round(cy))
    if not (0 <= xi < w and 0 <= yi < h):
        return None
    for z in ("inside", "doorway", "outside"):
        if masks[z][yi, xi]:
            return z
    return None


def _fuse(view_inside, n_inside, present_inside, motion, cfg):
    """Conservative occupancy estimate + state. Degraded never -> high; unusable -> indeterminate."""
    if view_inside == "unusable":
        return np.nan, "none", "indeterminate"
    if not present_inside:
        return (n_inside if view_inside == "clear" else 0), \
               ("high" if view_inside == "clear" else "low"), "empty"
    if view_inside == "degraded":
        est = n_inside if n_inside > 0 else 1                 # at least one; count not trusted
        return est, "low", "occupied_low_motion"             # never auto-high under degraded glass
    # clear
    state = "occupied_high_motion" if motion >= cfg["motion_thresh"] else "occupied_low_motion"
    return n_inside, "high", state


def analyze_channel(channel, files, model, args, cfg, conditions):
    zones = vq.load_zones(channel)
    if zones.get("_fallback"):
        print(f"[{channel}] no {channel}_zones.json yet -> using calib quad as inside only "
              f"(run place_zones.py to add doorway/outside).")
    calib = fc.load_calib(channel)
    W = int((zones.get("image_size") or [1280, 960])[0])
    tmp = HERE / "scratch" / f"sleep_{channel}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    masks, mask_hw = None, None
    rows, heat = [], []
    for f in files:
        start, _ = scan.clip_start(f.name)
        dur = scan.video_duration(f)
        if not dur:
            continue
        bursts = []                                           # (t, [frame_paths])
        for i, t in enumerate(np.arange(0, dur, args.every_sec)):
            fr = sample_burst(f, float(t), W, tmp, i, args.ffmpeg_threads, args.motion_gap, args.n_burst)
            if fr:
                bursts.append((float(t), fr))
        # detect on frame0 of each burst, in small batches (whole list = one giant batch -> OOM)
        f0s = [fr[0] for _, fr in bursts]
        dets = {}
        for ci in range(0, len(f0s), args.batch):
            for j, r in enumerate(model.predict([str(p) for p in f0s[ci:ci + args.batch]],
                                                conf=args.conf, classes=[0], imgsz=W,
                                                device=args.device, verbose=False)):
                b = r.boxes
                if b is None or not len(b):
                    dets[ci + j] = np.empty((0, 2))
                else:
                    xy = b.xyxy.cpu().numpy()
                    dets[ci + j] = np.c_[(xy[:, 0] + xy[:, 2]) / 2, (xy[:, 1] + xy[:, 3]) / 2]
        for idx, (t, fr) in enumerate(bursts):
            grays = [read_gray(p) for p in fr]
            grays = [g for g in grays if g is not None]
            if not grays:
                continue
            if masks is None:                                 # build zone masks once, at frame size
                mh, mw = grays[0].shape[:2]
                masks = zone_masks(zones, mw, mh); mask_hw = (mw, mh)
            # per-zone view quality (inside + doorway)
            vqi, _ = vq.view_quality(grays[0], masks["inside"], cfg)
            vqd, _ = (vq.view_quality(grays[0], masks["doorway"], cfg)
                      if masks["doorway"].any() else ("n/a", {}))
            # zone-assigned raw detector counts (EVIDENCE, kept separate from the estimate)
            centers = dets.get(idx, np.empty((0, 2)))
            n = {"inside": 0, "doorway": 0, "outside": 0}
            inside_centers = []
            for cx, cy in centers:
                z = _zone_of(cx, cy, masks)
                if z:
                    n[z] += 1
                    if z == "inside":
                        inside_centers.append((cx, cy))
            # glass-noise-resistant inside motion
            motion, _ = vq.robust_inside_motion(grays, masks["inside"], cfg)
            # weather-log cross-check: a logged fog/rain window forces >= degraded
            t_abs = (start + pd.Timedelta(seconds=t)) if start is not None else pd.Timestamp(t, unit="s")
            weather_logged, _note = vq.in_degraded_window(conditions, channel, t_abs)
            if weather_logged and vqi == "clear":
                vqi = "degraded"
            # fuse -> occupancy estimate + state (conservative)
            present_inside = (vqi in ("clear", "degraded") and
                              (n["inside"] > 0 or motion > cfg["present_motion_floor"]))
            n_est, conf, state = _fuse(vqi, n["inside"], present_inside, motion, cfg)
            usable_head = (vqi == "clear")
            usable_coarse = (vqi != "unusable") or (vqd == "clear")
            rows.append({"channel": channel, "t": t_abs, "file": f.name,
                         "view_quality_inside": vqi, "view_quality_doorway": vqd,
                         "n_detected_inside": n["inside"], "n_detected_doorway": n["doorway"],
                         "n_detected_outside_near_shelter": n["outside"],
                         "inside_motion_score": round(motion, 3),
                         "n_inside_estimated": n_est, "n_inside_confidence": conf,
                         "state": state, "weather_logged": weather_logged,
                         "usable_for_headline_summary": usable_head,
                         "usable_for_coarse_activity": usable_coarse})
            # approximate resting heatmap: ONLY clear + low-motion + real inside detections
            if state == "occupied_low_motion" and vqi == "clear" and inside_centers:
                heat.append(fc.to_field(channel, np.array(inside_centers), calib=calib, src_size=mask_hw))
        for p in tmp.glob(f"{f.stem}_*.png"):                 # bound scratch disk per file
            p.unlink()
    shutil.rmtree(tmp, ignore_errors=True)
    df = pd.DataFrame(rows).sort_values("t").reset_index(drop=True) if rows else pd.DataFrame()
    if not df.empty:                                      # append optical-regime covariate columns only
        df = gr.annotate(df, ts="t", channel=channel)     # (metadata; no fusion/view/count/safety logic reads it)
    heat = np.vstack(heat) if heat else np.empty((0, 2))
    return df, heat


def _runs(states):
    """Contiguous (state, start_idx, end_idx) runs for broken_barh shading."""
    out, i = [], 0
    while i < len(states):
        j = i
        while j + 1 < len(states) and states[j + 1] == states[i]:
            j += 1
        out.append((states[i], i, j)); i = j + 1
    return out


def timeline_plot(dfs, date, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    chans = list(dfs)
    fig, axes = plt.subplots(len(chans), 1, figsize=(13, 2.8 * len(chans)), sharex=True, squeeze=False)
    for ax, ch in zip(axes[:, 0], chans):
        d = dfs[ch]
        if d.empty:
            continue
        t = d["t"].to_numpy()
        for state, i0, i1 in _runs(d["state"].tolist()):                 # state shading
            ax.axvspan(t[i0], t[i1], color=STATE_COLORS.get(state, "#cccccc"), alpha=0.85, lw=0)
        # hatched band over degraded/unusable inside-view windows (never read as activity)
        deg = (d["view_quality_inside"].isin(["degraded", "unusable"])).tolist()
        for bad, i0, i1 in _runs(deg):
            if bad:
                ax.axvspan(t[i0], t[i1], facecolor="none", edgecolor="black", hatch="////", alpha=0.35, lw=0)
        ax2 = ax.twinx()
        ax2.plot(t, pd.to_numeric(d["n_inside_estimated"], errors="coerce").to_numpy(),
                 color="black", lw=0.8)
        ax2.set_ylabel("n inside (est)", fontsize=8); ax2.set_ylim(bottom=0)
        ax.set_ylabel(ch); ax.set_yticks([])
    axes[-1, 0].set_xlabel(f"time of day ({date})")
    handles = [Patch(color=c, label=s) for s, c in STATE_COLORS.items()]
    handles.append(Patch(facecolor="white", edgecolor="black", hatch="////", label="inside view degraded"))
    fig.legend(handles=handles, loc="upper center", ncol=5, fontsize=8, frameon=False)
    fig.suptitle(f"Shelter occupancy + rest proxy  {date}   (rest proxy = presence + low inside motion; "
                 "degraded glass never counts as high-motion)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out, dpi=120); plt.close(fig)


def heatmap_plot(channel, heat, date, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = json.loads((fc.CONFIG_DIR / f"{channel}_calib.json").read_text(encoding="utf-8-sig"))
    w = np.asarray(d["world_points"], float)
    x0, x1 = w[:, 0].min() - 20, w[:, 0].max() + 20
    y0, y1 = w[:, 1].min() - 20, w[:, 1].max() + 20
    fig, ax = plt.subplots(figsize=(5, 5))
    if len(heat):
        ax.hist2d(heat[:, 0], heat[:, 1], bins=24, range=[[x0, x1], [y0, y1]], cmap="magma")
    ax.add_patch(plt.Rectangle((w[:, 0].min(), w[:, 1].min()), np.ptp(w[:, 0]), np.ptp(w[:, 1]),
                               fill=False, ec="cyan", lw=1.5))
    ax.set_aspect("equal"); ax.set_xlabel("x cm"); ax.set_ylabel("y cm")
    ax.set_title(f"{channel} approx resting locations ({date})\n(clear-view occupied_low_motion; calib approximate)")
    fig.tight_layout(); fig.savefig(out, dpi=120); plt.close(fig)


def summarize(dfs, args):
    print("\n=== shelter rest-proxy summary (view-quality aware) ===")
    bin_min = args.every_sec / 60.0
    for ch, d in dfs.items():
        if d.empty:
            print(f"[{ch}] no bins"); continue
        nb = len(d)
        vqc = d["view_quality_inside"].value_counts()
        mix = "  ".join(f"{k} {vqc.get(k,0)/nb*100:.0f}%" for k in ("clear", "degraded", "unusable"))
        print(f"[{ch}] {nb} bins x {args.every_sec:.0f}s   inside-view: {mix}")
        clear = d[d["usable_for_headline_summary"]]
        print(f"    HEADLINE (clear bins only, n={len(clear)}):")
        vc = clear["state"].value_counts()
        for s in ("empty", "occupied_low_motion", "occupied_high_motion"):
            print(f"      {s:22s} {vc.get(s,0):5d} bins  ({vc.get(s,0)*bin_min/60:5.1f} h)")
        occ = clear[clear["state"].str.startswith("occupied")]["n_detected_inside"]
        if len(occ):
            print(f"      animals/shelter when occupied (raw count, undercounts huddles): "
                  f"median {int(occ.median())}, max {int(occ.max())}")
        ind = int((d["state"] == "indeterminate").sum())
        coarse = d[d["usable_for_coarse_activity"] & (d["view_quality_inside"] != "clear")]
        if len(coarse):
            c_occ = coarse["state"].str.startswith("occupied").mean() * 100
            print(f"    COARSE (degraded-but-usable bins, n={len(coarse)}): occupied {c_occ:.0f}% "
                  f"(low-confidence; never high-motion)")
        print(f"    indeterminate (inside view unusable): {ind} bins ({ind*bin_min/60:.1f} h) - excluded")
    if len(dfs) == 2:
        a, b = list(dfs)
        occ_a = (dfs[a][dfs[a]["usable_for_headline_summary"]]["state"].str.startswith("occupied")).mean() * 100
        occ_b = (dfs[b][dfs[b]["usable_for_headline_summary"]]["state"].str.startswith("occupied")).mean() * 100
        if not np.isnan(occ_a) and not np.isnan(occ_b):
            print(f"usage (clear bins): {a} occupied {occ_a:.0f}% vs {b} {occ_b:.0f}%  "
                  f"({'CH5-preferred' if occ_a > occ_b else 'CH6-preferred'})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Shelter occupancy + rest proxy, view-quality aware (CH05/CH06).",
                                 allow_abbrev=False)
    ap.add_argument("--channels", nargs="+", default=["CH05", "CH06"])
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--hours", type=int, nargs="+", help="restrict to these start hours (dry run)")
    ap.add_argument("--weights", default=str(DEF_WEIGHTS))
    ap.add_argument("--every-sec", type=float, default=45.0, help="sparse sample interval (s)")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--n-burst", type=int, default=3, help="frames per bin for the motion burst")
    ap.add_argument("--motion-gap", type=float, default=0.5, help="seconds between burst frames")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--ffmpeg-threads", type=int, default=2)
    ap.add_argument("--device", default="0")
    ap.add_argument("--config", default=None, help="view_quality.yaml (default configs/view_quality.yaml)")
    ap.add_argument("--conditions", default=str(CONDITIONS_PATH),
                    help="field_conditions.yaml for the weather cross-check (set '' to disable)")
    args = ap.parse_args()

    if not Path(args.weights).exists():
        raise SystemExit(f"weights not found: {args.weights}")
    conditions = vq.load_conditions(args.conditions) if args.conditions else []
    if conditions:
        print(f"weather cross-check: {len(conditions)} logged window(s) from {Path(args.conditions).name}")
    from ultralytics import YOLO
    model = YOLO(args.weights)

    outputs = HERE / "outputs"; outputs.mkdir(exist_ok=True)
    context = mc.build_context("shelter_sleep.py", args, args.channels,
                               view_quality_config=(args.config or HERE / "configs" / "view_quality.yaml"),
                               field_conditions=(args.conditions or None),
                               glass_treatments=HERE.parents[1] / "data_manifests" / "glass_treatments.yaml")
    rid = context["mc_run_id"]; inputs = {}
    dfs = {}
    for ch in args.channels:
        files = resolve_day_files(ch, args.date, args.hours)
        if not files:
            print(f"[{ch}] no closed files for {args.date}"); continue
        print(f"[{ch}] {len(files)} closed file(s)")
        inputs[ch] = [f.name for f in files]
        cfg = vq.load_config(args.config, ch)          # per-channel (fog thresholds differ per cam)
        df, heat = analyze_channel(ch, files, model, args, cfg, conditions)
        if df.empty:
            print(f"[{ch}] no bins produced"); continue
        df = mc.annotate_camera(df, ch); df["mc_run_id"] = rid   # measurement_context covariates (additive)
        df.to_csv(outputs / f"{ch}_sleep_{args.date}.csv", index=False)
        heatmap_plot(ch, heat, args.date, outputs / f"{ch}_rest_heatmap_{args.date}.png")
        dfs[ch] = df
    if dfs:
        timeline_plot(dfs, args.date, outputs / f"sleep_timeline_{args.date}.png")
        summarize(dfs, args)
        context["inputs"] = inputs
        mc.write_manifest(outputs / f"shelter_sleep_{args.date}.measurement_context.json", context)
        print(f"\noutputs -> {outputs}  (per-bin CSVs, timeline, heatmaps, measurement_context.json)")


if __name__ == "__main__":
    main()

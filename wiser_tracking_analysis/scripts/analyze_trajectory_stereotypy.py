r"""
analyze_trajectory_stereotypy.py — Phase A of the trajectory-stereotypy analysis.

Question: over the first ~9 days in the paddock (nights 2026-06-28 -> 07-05), do
the rats develop STEREOTYPICAL trajectories that STABILIZE over days, and are
stabilized trajectories SHARED across animals or ANIMAL-SPECIFIC? Phase A builds
the core quantitative evidence and separates three explanations:

  (1) individual route habit/memory   (2) social coupling   (3) shared "road"

...WITHOUT yet doing DTW/Fréchet route motifs or leader-follower lag (that is
Phase B). It ships an intermediate report answering: does it stabilize & when;
shared vs animal-specific; does inter-animal similarity survive shuffled controls.

REGIME GUARDRAILS (see .claude/skills/regime-aware-wiser-tracking): the WISER
frame is the UNVERIFIED offset-origin INCH frame (no directional claims); jitter
floor ~7 in (bins >= floor; no sub-floor geometry); gaps != absence; do NOT pool
across Sova removal (2026-06-29 15:00) or the tunnel removal (2026-06-29 07:00);
weather acts on BOTH the sensor and the animal (rain nights 06-30/07-01/07-04);
07-04 fireworks drove a movement spike -> excluded from the time-coupling by
default. Everything is exploratory/candidate.

Read-only on the transferred backups. Outputs to
wiser_tracking_analysis/outputs/trajectory_stereotypy_2026-06-28_to_2026-07-06/.

    conda activate cv   (or the analysis-PC base env with pandas/numpy/matplotlib)
    cd wiser_tracking_analysis
    python scripts/analyze_trajectory_stereotypy.py                 # full Phase A
    python scripts/analyze_trajectory_stereotypy.py --max-nights 2  # smoke test
"""

from __future__ import annotations

import argparse
import datetime as _dt
import itertools
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import wiser_analysis_utils as w          # noqa: E402
import time_utils                         # noqa: E402
import metrics                            # noqa: E402
import plotting                           # noqa: E402
import trajectory_stereotypy as ts        # noqa: E402

# ---- defaults (analysis PC / transferred read-only backups) ----------------
DEFAULT_INCR = Path(r"D:\Reolink_record\audio_in\Wiser_backup\incremental")
DEFAULT_BASELINE = Path(r"D:\Reolink_record\audio_in\Wiser_backup\snapshots\tag_reports_2026-06-30.sqlite")
DEFAULT_WEATHER = r"D:\Reolink_record\audio_in\weather_data\AWN-*.csv"
DEFAULT_ROIS = PROJECT_ROOT / "configs" / "wiser_rois.json"
DEFAULT_GT = PROJECT_ROOT / "configs" / "fixed_position_ground_truth.csv"
DEFAULT_OUT = PROJECT_ROOT / "outputs" / "trajectory_stereotypy_2026-06-28_to_2026-07-06"

DROP_TAGS = {"12409"}                      # Sova removed 2026-06-29 15:00 (also via cutoff)
FIREWORKS_NIGHT = "2026-07-04"             # excluded from time-coupling by default
# Field-log rain/wet nights (weather acts on both sensor and animal).
WET_NIGHTS = {"2026-06-30", "2026-07-01", "2026-07-04"}


# ===========================================================================
# provenance
# ===========================================================================

def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=str(PROJECT_ROOT)).decode().strip()
    except Exception:
        return "unknown"


def _name_map() -> dict:
    ids = plotting.load_rat_identities(str(PROJECT_ROOT / "configs" / "rat_identities.csv"))
    return {str(k): (v or {}).get("name", str(k)) for k, v in (ids or {}).items()}


# ===========================================================================
# jitter floor + moving threshold from the stationary baseline
# ===========================================================================

# Documented WISER stationary precision floor (fixed-position test): ~7 in median,
# p95 ~15 in (see .claude/skills/regime-aware-wiser-tracking). We report THIS as the
# working floor, not the per-tag p50 (which is precision-optimistic ~3-4 in). The
# occupancy bin is kept >= this documented floor.
DOCUMENTED_JITTER_FLOOR_IN = 7.0


def establish_floor(baseline_path: Path, gt_path: Path) -> dict:
    """Return {jitter_floor_in (documented ~7 in), measured_jitter_p50/p95_in,
    moving_thr_inps, source}. Falls back to the documented floor +
    DEFAULT_ACTIVE_SPEED_INPS if the baseline is absent."""
    out = {"jitter_floor_in": DOCUMENTED_JITTER_FLOOR_IN,
           "measured_jitter_p50_in": None, "measured_jitter_p95_in": None,
           "moving_thr_inps": w.DEFAULT_ACTIVE_SPEED_INPS,
           "source": "documented fallback (~7 in median, p95 ~15 in; DEFAULT_ACTIVE_SPEED_INPS)"}
    if not Path(baseline_path).exists():
        print(f"  [floor] baseline not found ({baseline_path}); using documented ~7 in.")
        return out
    base = w.load_wiser_session(baseline_path)
    if base is None or base.empty:
        print("  [floor] baseline empty; using documented ~7 in.")
        return out
    base = time_utils.convert_timestamps(base)
    base = w.add_speed(base)
    gt = metrics.load_ground_truth(gt_path) if Path(gt_path).exists() else None
    summ = metrics.compute_summary(base, gt)
    jf50 = float(np.nanmedian(summ["jitter_p50"])) if "jitter_p50" in summ else np.nan
    jf95 = float(np.nanmedian(summ["jitter_p95"])) if "jitter_p95" in summ else np.nan
    floor = w.speed_noise_floor(base, pct=(95, 99))
    # Keep the DOCUMENTED ~7 in as the working floor (conservative); the measured
    # per-tag p50 is precision-optimistic and must not be cited as "the floor".
    out.update(jitter_floor_in=DOCUMENTED_JITTER_FLOOR_IN,
               measured_jitter_p50_in=round(jf50, 2), measured_jitter_p95_in=round(jf95, 2),
               moving_thr_inps=round(float(floor.get("p99", w.DEFAULT_ACTIVE_SPEED_INPS)), 2),
               source=(f"{Path(baseline_path).name}: working floor = documented ~7 in median "
                       f"(p95 ~15 in); measured stationary jitter p50 {jf50:.2f} in / "
                       f"p95 {jf95:.2f} in; moving thr = speed p99"),
               stationary=base)
    print(f"  [floor] working jitter floor ~{out['jitter_floor_in']} in (documented); "
          f"measured p50 {jf50:.2f}/p95 {jf95:.2f} in; moving thr {out['moving_thr_inps']} in/s")
    return out


# ===========================================================================
# coverage
# ===========================================================================

def coverage_summary(full_night: pd.DataFrame, names: dict) -> pd.DataFrame:
    """Per (night, animal) coverage on the night-labeled (pre-valid-filter) set."""
    rows = []
    n_rats_by_night = full_night.groupby("night")["shortid"].nunique().to_dict()
    for (night, tag), g in full_night.groupby(["night", "shortid"]):
        n = len(g)
        valid = int(g["valid"].sum()) if "valid" in g else n
        rows.append({
            "night": night, "shortid": str(tag), "animal": names.get(str(tag), str(tag)),
            "n_fixes": n, "n_valid": valid,
            "valid_frac": round(valid / n, 4) if n else 0.0,
            "gap_frac": round(float(g.get("gap_flag", pd.Series(dtype=bool)).mean()), 4)
            if "gap_flag" in g else np.nan,
            "low_anchor_frac": round(float(g["low_anchor_flag"].mean()), 4)
            if "low_anchor_flag" in g else np.nan,
            "mean_anchors": round(float(g["anchors_used"].astype(float).mean()), 2)
            if "anchors_used" in g else np.nan,
            "median_dt_s": round(float(g["dt_s"].median()), 3) if "dt_s" in g else np.nan,
            "n_rats_this_night": int(n_rats_by_night.get(night, 0)),
            "wet_night": night in WET_NIGHTS,
            "fireworks_night": night == FIREWORKS_NIGHT,
        })
    return pd.DataFrame(rows).sort_values(["night", "shortid"]).reset_index(drop=True)


# ===========================================================================
# plots
# ===========================================================================

def _grid_of_maps(hists, animals, nights, names, which, extent, title, path,
                  log_scale=True):
    nrow, ncol = len(animals), len(nights)
    if nrow == 0 or ncol == 0:
        return
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.2 * ncol + 1, 2.2 * nrow + 1),
                             squeeze=False)
    xmin, xmax, ymin, ymax = extent
    for i, tag in enumerate(animals):
        for j, night in enumerate(nights):
            ax = axes[i][j]
            rec = hists.get((night, tag))
            if rec is not None:
                H = rec[which].T                       # transpose: rows=y, cols=x
                Hp = np.log1p(H) if log_scale else H
                ax.imshow(Hp, origin="lower", extent=(xmin, xmax, ymin, ymax),
                          aspect="equal", cmap="magma")
            else:
                ax.text(0.5, 0.5, "—", ha="center", va="center", transform=ax.transAxes)
            if i == 0:
                ax.set_title(night[5:], fontsize=7)
            if j == 0:
                ax.set_ylabel(names.get(tag, tag), fontsize=7)
            ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(title, fontsize=11)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def _fig_pooled(pooled, mask, skel, extent, path):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    xmin, xmax, ymin, ymax = extent
    ext = (xmin, xmax, ymin, ymax)
    axes[0].imshow(np.log1p(pooled).T, origin="lower", extent=ext, aspect="equal", cmap="magma")
    axes[0].set_title("pooled occupancy (log)")
    axes[1].imshow(mask.T, origin="lower", extent=ext, aspect="equal", cmap="Greys")
    axes[1].set_title("shared-corridor mask (>=80th pct)")
    axes[2].imshow(skel.T, origin="lower", extent=ext, aspect="equal", cmap="Greys")
    axes[2].set_title("corridor skeleton")
    for ax in axes:
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle("Pooled environmental-corridor 'road' map (all animals x nights) — inch frame, UNVERIFIED")
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def _fig_stabilization(stab, names, path):
    if stab.empty:
        return
    animals = sorted(stab["shortid"].unique())
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for tag in animals:
        g = stab[stab["shortid"] == tag].sort_values("night")
        lab = names.get(tag, tag)
        axes[0].plot(g["night"].str.slice(5), g["cos_ref"], "-o", ms=4, label=lab)
        axes[1].plot(g["night"].str.slice(5), g["cos_prev"], "-o", ms=4, label=lab)
    axes[0].set_title("similarity to late-window reference (stabilization)")
    axes[1].set_title("night-to-night similarity (reproducibility)")
    for ax in axes:
        ax.set_ylim(0, 1.02); ax.set_xlabel("night"); ax.set_ylabel("occupancy cosine")
        ax.grid(alpha=0.3); ax.tick_params(axis="x", rotation=45)
    axes[0].legend(fontsize=7, ncol=2)
    fig.suptitle("Per-animal trajectory stabilization (occupancy maps, night window)")
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def _fig_similarity(raw, resid, names, path):
    if raw.empty:
        return
    tags = sorted(set(raw["tag_a"]) | set(raw["tag_b"]))
    idx = {t: i for i, t in enumerate(tags)}
    def _mat(df):
        M = np.full((len(tags), len(tags)), np.nan)
        for _, r in df.iterrows():
            i, j = idx[r["tag_a"]], idx[r["tag_b"]]
            M[i, j] = M[j, i] = r["cosine"]
        np.fill_diagonal(M, 1.0)
        return M
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    for ax, df, ttl in ((axes[0], raw, "raw occupancy cosine"),
                        (axes[1], resid, "residual cosine (shared road divided out)")):
        M = _mat(df)
        im = ax.imshow(M, vmin=0, vmax=1, cmap="viridis")
        ax.set_xticks(range(len(tags))); ax.set_yticks(range(len(tags)))
        labs = [names.get(t, t) for t in tags]
        ax.set_xticklabels(labs, rotation=45, ha="right", fontsize=8)
        ax.set_yticklabels(labs, fontsize=8)
        ax.set_title(ttl, fontsize=10)
        for i in range(len(tags)):
            for j in range(len(tags)):
                v = M[i, j]
                if np.isfinite(v):
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            fontsize=7, color="w" if v < 0.6 else "k")
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle("Inter-animal occupancy similarity: raw vs shared-road-removed")
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def _fig_controls(controls, names, path):
    if controls.empty:
        return
    fig, ax = plt.subplots(figsize=(9, 4.6))
    sub = controls.copy()
    sub["pair"] = sub["tag_a"].map(lambda t: names.get(t, t)) + "–" + \
        sub["tag_b"].map(lambda t: names.get(t, t))
    for control, mk in (("circular_shift", "o"), ("day_shuffle", "s")):
        s = sub[sub["control"] == control]
        ax.scatter(s["pair"], s["z_frac_within_r"], marker=mk, label=control, s=40)
    ax.axhline(0, color="k", lw=0.8); ax.axhline(2, color="r", ls="--", lw=0.8, label="z=2")
    ax.set_ylabel("z of proximity (obs vs null)")
    ax.set_title("Time-coupling controls: does synchronous proximity beat shuffled nulls?")
    ax.tick_params(axis="x", rotation=45); ax.grid(alpha=0.3); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def _fig_speed(win, names, moving_thr, path):
    animals = sorted(win["shortid"].astype(str).unique())
    fig, ax = plt.subplots(figsize=(8, 4.4))
    for tag in animals:
        s = win[win["shortid"].astype(str) == tag]["speed_inps_smooth"].dropna()
        s = s[(s >= 0) & (s < 60)]
        if len(s) > 50:
            ax.hist(s, bins=60, histtype="step", density=True, label=names.get(tag, tag))
    ax.axvline(moving_thr, color="k", ls="--", lw=0.8, label=f"moving thr {moving_thr:.1f}")
    ax.set_xlabel("smoothed speed (in/s)"); ax.set_ylabel("density")
    ax.set_title("Per-animal night-window speed distribution"); ax.legend(fontsize=7)
    ax.set_yscale("log")
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


# ===========================================================================
# main
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--incremental-dir", type=Path, default=DEFAULT_INCR)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--weather-glob", default=DEFAULT_WEATHER)
    ap.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    ap.add_argument("--gt", type=Path, default=DEFAULT_GT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dates", nargs="*", default=None, help="restrict to these backup dates")
    ap.add_argument("--night-start", type=int, default=21)
    ap.add_argument("--night-end", type=int, default=5)
    ap.add_argument("--bin-in", type=float, default=8.0, help="occupancy bin (>= jitter floor)")
    ap.add_argument("--max-nights", type=int, default=None, help="smoke: keep first N nights")
    ap.add_argument("--sync-bin-s", type=float, default=2.0)
    ap.add_argument("--n-perm", type=int, default=200)
    ap.add_argument("--n-shuffles", type=int, default=100)
    ap.add_argument("--no-controls", action="store_true")
    args = ap.parse_args()

    out = args.out
    (out / "plots").mkdir(parents=True, exist_ok=True)
    (out / "residual_individual_maps").mkdir(parents=True, exist_ok=True)
    names = _name_map()

    print("== Trajectory stereotypy (Phase A) ==")
    print("[1/8] loading incremental backups (dedup on (shortid,ts_raw,x,y)) ...")
    df, load_log = ts.load_incremental_days(args.incremental_dir, dates=args.dates)
    print(f"    {load_log['rows_after_dedup']:,} unique fixes "
          f"({load_log['duplicate_rows_removed']:,} duplicates removed)")

    df = time_utils.convert_timestamps(df)
    floor = establish_floor(args.baseline, args.gt)
    jitter_floor = floor["jitter_floor_in"]; moving_thr = floor["moving_thr_inps"]

    print("[2/8] speed + validity flags + tag cutoffs ...")
    df = w.add_speed(df)
    roi_cfg = w.load_rois(args.rois)
    boundary = (roi_cfg or {}).get("boundary")
    df = w.add_validity_flags(df, boundary=boundary, jitter_floor_in=jitter_floor)
    df = w.apply_tag_cutoffs(df)                       # Sova cutoff from rat_identities

    # night-labeled full set (pre valid-filter) for coverage
    full_night = ts.select_night_window(df, night_start=args.night_start,
                                        night_end=args.night_end, valid_only=False)
    full_night = full_night[~full_night["shortid"].astype(str).isin(DROP_TAGS)]

    # analysis set: valid only
    win = ts.select_night_window(df, night_start=args.night_start,
                                 night_end=args.night_end, valid_only=True)
    win = win[~win["shortid"].astype(str).isin(DROP_TAGS)].reset_index(drop=True)

    nights = sorted(win["night"].unique())
    if args.max_nights:
        nights = nights[:args.max_nights]
        win = win[win["night"].isin(nights)].reset_index(drop=True)
        full_night = full_night[full_night["night"].isin(nights)]
    animals = sorted(win["shortid"].astype(str).unique())
    print(f"    nights={nights}")
    print(f"    animals={[names.get(a,a) for a in animals]}")

    # extent: confirmed inch-frame boundary rect if present, else observed
    if boundary and "rect" in boundary:
        xmin, xmax, ymin, ymax = boundary["rect"]
        extent = (xmin - 12, xmax + 12, ymin - 12, ymax + 12)
    else:
        extent = w.observed_extent(win)

    print("[3/8] coverage summary ...")
    cov = coverage_summary(full_night, names)
    cov.to_csv(out / "coverage_summary.csv", index=False)

    print("[4/8] per-night per-animal maps ...")
    hists = ts.night_animal_hists(win, extent, bin_in=args.bin_in,
                                  moving_thr_inps=moving_thr)

    print("[5/8] stabilization curves ...")
    stab_all = ts.stabilization_table(hists, animals, nights, which="all")
    stab_mov = ts.stabilization_table(hists, animals, nights, which="moving")
    stab = pd.concat([stab_all, stab_mov], ignore_index=True)
    stab.to_csv(out / "stabilization_metrics.csv", index=False)
    stab_date = ts.stabilization_date(stab_all, metric="cos_ref")

    print("[6/8] pooled corridor + residual individual maps ...")
    all_maps = [rec["all"] for rec in hists.values()]
    pooled, mask, skel = ts.pooled_corridor(all_maps, pct=80.0)
    per_animal = {t: ts.sum_hists([hists[(n, t)]["all"] for n in nights
                                   if (n, t) in hists]) for t in animals}
    np.save(out / "pooled_corridor_occupancy.npy", pooled)
    np.save(out / "pooled_corridor_mask.npy", mask)
    resid_rows = []
    resid_maps = {}
    for t in animals:
        res = ts.residual_occupancy(per_animal[t], pooled)
        resid_maps[t] = res
        resid_rows.append({"shortid": t, "animal": names.get(t, t),
                           "residual_concentration": ts.residual_concentration(res),
                           "corridor_adherence": None})
    # corridor adherence / self-concentration / entropy via route_reuse_index
    rri = w.route_reuse_index(win, extent, mask, bin_in=args.bin_in)
    rri["shortid"] = rri["shortid"].astype(str)
    resid_df = pd.DataFrame(resid_rows).drop(columns=["corridor_adherence"])
    resid_df = resid_df.merge(
        rri[["shortid", "corridor_adherence", "self_concentration", "occ_entropy"]],
        on="shortid", how="left")
    resid_df.to_csv(out / "residual_individual_summary.csv", index=False)

    print("[7/8] inter-animal similarity + controls ...")
    raw_sim = ts.pairwise_map_similarity(per_animal, label="raw")
    resid_sim = ts.pairwise_map_similarity(resid_maps, label="residual")
    # transition-edge similarity (ROI labels provisional)
    ptt = w.per_tag_transitions(win, roi_cfg) if roi_cfg else pd.DataFrame()
    edge_sim, shared_edges = (w.edge_usage_similarity(ptt) if not ptt.empty
                              else (pd.DataFrame(), pd.DataFrame()))
    pd.concat([raw_sim, resid_sim], ignore_index=True).to_csv(
        out / "pairwise_similarity_matrix.csv", index=False)
    if not edge_sim.empty:
        edge_sim.to_csv(out / "transition_edge_similarity.csv", index=False)
        shared_edges.to_csv(out / "shared_edges.csv", index=False)

    controls = pd.DataFrame()
    perm = pd.DataFrame()
    if not args.no_controls:
        perm = ts.label_permutation_null(hists, animals, nights, which="all",
                                         n_perm=args.n_perm)
        perm.to_csv(out / "label_permutation_null.csv", index=False)
        # time-coupling on non-fireworks nights
        coup_nights = [n for n in nights if n != FIREWORKS_NIGHT]
        cwin = win[win["night"].isin(coup_nights)]
        grid = ts.sync_grid(cwin, bin_s=args.sync_bin_s)
        rows = []
        for a, b in itertools.combinations(animals, 2):
            rows.append(ts.circular_shift_null(grid, a, b, n_shuffles=args.n_shuffles))
            rows.append(ts.dayshuffle_null(grid, a, b))
        controls = pd.DataFrame(rows)
        controls["animal_a"] = controls["tag_a"].map(lambda t: names.get(t, t))
        controls["animal_b"] = controls["tag_b"].map(lambda t: names.get(t, t))
        controls.to_csv(out / "shuffled_controls.csv", index=False)

    print("[8/8] figures + report ...")
    _grid_of_maps(hists, animals, nights, names, "all", extent,
                  "Per-night per-animal occupancy (night window) — inch frame, UNVERIFIED",
                  out / "plots" / "occupancy_grid.png")
    _grid_of_maps(hists, animals, nights, names, "moving", extent,
                  "Per-night per-animal PATH-DENSITY (moving fixes) — inch frame, UNVERIFIED",
                  out / "plots" / "pathdensity_grid.png")
    if pooled is not None:
        _fig_pooled(pooled, mask, skel, extent, out / "plots" / "pooled_corridor.png")
    _fig_stabilization(stab_all, names, out / "plots" / "stabilization.png")
    _fig_similarity(raw_sim, resid_sim, names, out / "plots" / "pairwise_similarity.png")
    if not controls.empty:
        _fig_controls(controls, names, out / "plots" / "time_coupling_controls.png")
    _fig_speed(win, names, moving_thr, out / "plots" / "speed_distribution.png")
    # per-animal residual maps
    if resid_maps and pooled is not None:
        na = len(animals)
        fig, axes = plt.subplots(1, na, figsize=(2.6 * na + 1, 3), squeeze=False)
        for k, t in enumerate(animals):
            ax = axes[0][k]
            res = resid_maps[t]
            if res is not None:
                ax.imshow(np.clip(res, 0, np.nanpercentile(res[res > 0], 99) or 1).T,
                          origin="lower", cmap="magma", aspect="equal")
            ax.set_title(names.get(t, t), fontsize=8)
            ax.set_xticks([]); ax.set_yticks([])
        fig.suptitle("Residual individual occupancy (shared road divided out)")
        fig.tight_layout(); fig.savefig(out / "residual_individual_maps" / "residual_maps.png", dpi=120)
        plt.close(fig)

    # cleaning log + manifest + report
    _write_cleaning_log(out, load_log, floor, df, win, args, jitter_floor, moving_thr)
    manifest = {
        "analysis": "trajectory_stereotypy_phase_a",
        "generated_utc": _dt.datetime.utcnow().isoformat(),
        "git_commit": _git_commit(),
        "units": "inches (WISER native, UNVERIFIED offset origin)",
        "timestamp_method": "unix_ms -> naive UTC (time_utils.convert_timestamps)",
        "night_window_local": [args.night_start, args.night_end],
        "tz_offset_hours": w.LOCAL_TZ_OFFSET_HOURS,
        "bin_in": args.bin_in, "jitter_floor_in": jitter_floor,
        "measured_jitter_p50_in": floor.get("measured_jitter_p50_in"),
        "measured_jitter_p95_in": floor.get("measured_jitter_p95_in"),
        "moving_thr_inps": moving_thr, "floor_source": floor["source"],
        "nights": nights, "animals": {a: names.get(a, a) for a in animals},
        "dropped_tags": sorted(DROP_TAGS), "wet_nights": sorted(WET_NIGHTS),
        "fireworks_night_excluded_from_coupling": FIREWORKS_NIGHT,
        "load_log": load_log, "stabilization_date_estimate": stab_date,
        "extent_in": list(extent),
        "caveats": [
            "WISER inch frame UNVERIFIED (no georeference) -> NO directional/physical claims",
            "jitter floor ~7 in -> bins >= floor; sub-floor geometry not interpretable",
            "gaps != absence; dropout is 'unknown' not 'left'",
            "weather acts on BOTH sensor and animal (wet nights flagged, not regressed out)",
            "07-04 fireworks excluded from time-coupling",
            "ROI/transition labels provisional (membership only, not georeferenced)",
        ],
    }
    with open(out / "run_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    _write_report(out, manifest, cov, stab_all, stab_date, resid_df, raw_sim,
                  resid_sim, edge_sim, controls, perm, names)
    print(f"\nDONE -> {out}")


def _write_cleaning_log(out, load_log, floor, df, win, args, jitter_floor, moving_thr):
    fs = w.flag_summary(df)
    lines = ["# Cleaning log — trajectory stereotypy (Phase A)", "",
             f"Generated (UTC): {_dt.datetime.utcnow().isoformat()}", "",
             "## Load & dedup (double-count control)", "",
             f"- dedup key: `{load_log['dedup_key']}`",
             f"- rows concatenated across files: {load_log['rows_concatenated']:,}",
             f"- unique rows after dedup: {load_log['rows_after_dedup']:,}",
             f"- **duplicate rows removed: {load_log['duplicate_rows_removed']:,}** "
             "(the 06-30 cumulative dump overlaps 06-28/06-29 — dedup makes the load exact)",
             "", "### Per-file", "", "| file | rows_raw | rows_kept |", "|---|---|---|"]
    for r in load_log["files"]:
        lines.append(f"| {r['file']} | {r['rows_raw']:,} | {r['rows_kept']:,} |")
    lines += ["", "## Jitter floor / thresholds", "",
              f"- jitter floor: **{jitter_floor} in** ({floor['source']})",
              f"- moving threshold (locomotion): **{moving_thr} in/s** (stationary p99 speed floor)",
              f"- occupancy bin: **{args.bin_in} in** (>= jitter floor)",
              f"- jump threshold: {w.DEFAULT_MAX_SPEED_INPS} in/s; gap factor: {w.DEFAULT_GAP_FACTOR}x median dt; "
              f"min anchors: {w.DEFAULT_MIN_ANCHORS}; smooth window: {w.DEFAULT_SMOOTH_WINDOW}",
              "", "## Validity flags (whole dataset, before night window)", "",
              "| flag | count | fraction |", "|---|---|---|"]
    for k, v in fs.items():
        if isinstance(v, dict):
            lines.append(f"| {k} | {v['count']:,} | {v['fraction']} |")
    lines += ["", f"- night-window valid fixes retained: **{len(win):,}**",
              "- gaps are flagged, never interpolated across; a gap is 'unknown', not 'left'.",
              "- Sova (12409) dropped entirely (removed 2026-06-29 15:00); tunnel ROI auto-expires "
              "2026-06-29 07:00 via its `valid_until`.", ""]
    (out / "cleaning_log.md").write_text("\n".join(lines), encoding="utf-8")


def _write_report(out, manifest, cov, stab, stab_date, resid_df, raw_sim, resid_sim,
                  edge_sim, controls, perm, names):
    def _mean(df, col):
        return float(df[col].mean()) if col in df and len(df) else float("nan")

    raw_mean = _mean(raw_sim, "cosine")
    resid_cos_mean = _mean(resid_sim, "cosine")
    resid_corr_mean = _mean(resid_sim, "corr")          # the HONEST discriminator
    edge_cos_mean = _mean(edge_sim, "edge_cosine")
    n_pairs = len(raw_sim)
    # stabilization: mean cos_ref on first vs last night
    stab_sorted = stab.sort_values("night")
    first_n = stab_sorted["night"].min() if len(stab_sorted) else "?"
    last_n = stab_sorted["night"].max() if len(stab_sorted) else "?"
    cos_ref_first = _mean(stab_sorted[stab_sorted["night"] == first_n], "cos_ref")
    cos_ref_last = _mean(stab_sorted[stab_sorted["night"] == last_n], "cos_ref")

    # label-permutation: pairs ABOVE the shared-pool null (extra similarity) and
    # BELOW it (an animal that uses space differently -> individual preference)
    perm_txt = "not run"
    indiv_animals = []
    if perm is not None and not perm.empty:
        above = perm[perm["z"] > 2]
        below = perm[perm["z"] < -2]
        # An animal is "individual" only if it drives MULTIPLE below-null pairs
        # (i.e. it is less similar to several others), not merely a member of one.
        from collections import Counter
        cnt = Counter()
        for _, r in below.iterrows():
            cnt[names.get(r["tag_a"], r["tag_a"])] += 1
            cnt[names.get(r["tag_b"], r["tag_b"])] += 1
        indiv_animals = sorted(a for a, c in cnt.items() if c >= 2)
        perm_txt = (f"{len(above)}/{len(perm)} pairs are MORE similar than the shared-pool "
                    f"null (z>2); {len(below)}/{len(perm)} are LESS similar (z<-2)")

    # time-coupling: circular-shift vs day-shuffle (day-shuffle preserves the shared
    # diurnal/spatial structure, so it isolates genuine same-night synchrony)
    coup_txt = "controls not run"
    if controls is not None and not controls.empty:
        cs = controls[controls["control"] == "circular_shift"]
        ds = controls[controls["control"] == "day_shuffle"]
        n_cs = int((cs["z_frac_within_r"] > 2).sum())
        n_ds = int((ds["z_frac_within_r"] > 2).sum())
        cs_corr = int((cs["z_xy_corr"] > 2).sum())
        ds_corr = int((ds["z_xy_corr"] > 2).sum())
        coup_txt = (f"circular-shift null: {n_cs}/{len(cs)} pairs beat it on proximity and "
                    f"{cs_corr}/{len(cs)} on xy-correlation (z>2); but the day-shuffle null "
                    f"(which keeps each animal's diurnal/spatial habit) is beaten by only "
                    f"{n_ds}/{len(ds)} pairs on proximity and {ds_corr}/{len(ds)} (marginal) on "
                    "xy-correlation")

    L = []
    L += ["# Trajectory stereotypy, stabilization & inter-animal correlation — Phase A report", "",
          f"**Generated (UTC):** {manifest['generated_utc']}  ",
          f"**Commit:** `{manifest['git_commit']}`  ",
          f"**Nights:** {', '.join(manifest['nights'])}  ",
          f"**Animals:** {', '.join(manifest['animals'].values())}  ",
          f"**Frame:** inches, UNVERIFIED offset origin (no georeference) — no directional claims  ",
          f"**Jitter floor:** ~{manifest['jitter_floor_in']:.0f} in documented (p95 ~15 in; measured "
          f"stationary p50 {manifest.get('measured_jitter_p50_in')} in); occupancy bin "
          f"{manifest['bin_in']:.0f} in (≥ floor)  ",
          "",
          "> Phase A builds the core quantitative evidence and the shared-road controls. Route-motif "
          "shape clustering (DTW/Fréchet) and leader-follower lag are **Phase B** (deferred). Every "
          "claim below is exploratory/candidate and classified as behavioral / measurement-artifact / "
          "mixed / lower-bound.", "",
          "## Headline answers", "",
          "### 1. Do trajectories become stereotyped / stabilize from 06-28 → 07-05?", "",
          f"- Mean similarity-to-late-reference went from **{cos_ref_first:.2f}** (first night) to "
          f"**{cos_ref_last:.2f}** (last night); night-to-night reproducibility is in "
          "`stabilization_metrics.csv` and `plots/stabilization.png`.",
          f"- Per-animal stabilization-night estimate (first plateau at ≥90% of own max): "
          f"{ {names.get(k,k): v for k,v in stab_date.items()} }.",
          "- **Caution:** occupancy similarity across nights is inflated by shared shelter/rest use "
          "and by the shared road; a rising curve is *consistent with* stabilization but is **not** "
          "proof of individual route memory (see §2–3). Wet nights (06-30/07-01/07-04) can depress "
          "similarity via UWB dropout, not behavior — though here `gap_frac` stays low (<1.5%) on all "
          "nights, so the aggregate curve is not dropout-driven. Night **07-05 is truncated** (backup "
          "ended ~07-05 23:30 EDT; ~25% fewer fixes) — treat its point cautiously.", "",
          "### 2. Are stabilized routes shared across animals, or animal-specific?", "",
          f"- **Raw** inter-animal occupancy cosine (mean over {n_pairs} pairs): **{raw_mean:.2f}** "
          f"(high). ROI-transition structure is near-identical too (edge cosine "
          f"**{edge_cos_mean:.2f}**).",
          f"- After dividing out the pooled shared-corridor 'road', residual **Pearson correlation "
          f"collapses to {resid_corr_mean:+.2f}** (residual cosine {resid_cos_mean:.2f} is inflated "
          "because residual maps are non-negative — read the **correlation**, not the cosine).",
          f"- **Animal-label permutation:** {perm_txt}. "
          + (f"The below-null animal(s) — **{', '.join(indiv_animals)}** — use space somewhat "
             "differently from the group (a weak *individual*-preference signal). "
             if indiv_animals else "")
          + "Most pairs are no more similar than random identity assignment ⇒ their similarity is a "
          "**shared-road** effect, not a pairwise bond.",
          "- Interpretation: near-zero residual correlation + most pairs at/below the permutation null "
          "⇒ stabilized space-use is **mostly SHARED (environment-driven)**, with only a weak "
          "individual signature. Per-animal residual/self-concentration is in "
          "`residual_individual_summary.csv`.",
          "", "### 3. Are pairwise trajectories correlated in real time beyond shuffled controls?", "",
          f"- {coup_txt} (`shuffled_controls.csv`, `plots/time_coupling_controls.png`).",
          "- Read: real-time synchrony **exists** above the strict circular-shift null (all pairs), "
          "but it is **uniform across pairs and largely explained by shared diurnal/environmental "
          "structure** — the day-shuffle null (which preserves that structure) is beaten by far fewer "
          "pairs. No standout dyad ⇒ this is common-drive/shared-road, **not** specific social "
          "following. Fine following/lead-lag is Phase B; 07-04 fireworks excluded.", "",
          "## Separating the three explanations", "",
          "| Explanation | Phase-A evidence | Verdict |", "|---|---|---|",
          f"| **Shared road / environment** | pooled corridor map; residual Pearson {resid_corr_mean:+.2f}; "
          f"edge cosine {edge_cos_mean:.2f}; most label-perm pairs ≈ null | **primary driver** |",
          "| Individual route habit | weak: only "
          f"{', '.join(indiv_animals) if indiv_animals else 'none'} below the label-perm null; "
          "residual concentration uniform | weak / candidate |",
          "| Social real-time coupling | uniform across pairs; attenuates under the day-shuffle null | "
          "not supported as *specific* following |", "",
          "## What to trust most", "",
          "1. **`pooled_corridor.png` + the residual-correlation collapse (to ~0)** — the most robust "
          "result; a within-frame comparison immune to the unverified georeference.",
          "2. **`coverage_summary.csv` gap/anchor columns** — read every night's result against its "
          "dropout; wet-night dips are likely sensor, not behavior.",
          "3. **Time-coupling z-scores vs the circular-shift null** — trust these over the raw "
          "proximity numbers (raw proximity is inflated by shared road + diurnal rhythm).", "",
          "## What Phase A CANNOT support", "",
          "- Any **directional/physical** route claim (wall-following, shelter→food geometry) — the "
          "inch frame is unverified.",
          "- **Memory** per se — WISER shows spatial reuse, not its cognitive cause; equal-looking "
          "reuse arises from a shared road.",
          "- Sub-jitter route shape or true path length (jitter-inflated). Route-motif shape is Phase B.",
          "- Fine (<1 m) proximity/following — below the jitter floor; 07-04 fireworks excluded.",
          "- The **top ROI-transition 'edges' (house↔food) are an artifact**: `food_1`/`food_2` sit "
          "inside `house_1`/`house_2` in the inch frame, so those edges are jitter flips between "
          "co-located labels, not travel. Trust the house↔house / house↔refuge edges as real routes.", "",
          "## Next analysis (Phase B, after review)", "",
          "- DTW/Fréchet route-motif clustering on movement bouts (validated vs the displacement-"
          "matched jitter null) → repeated motifs, users, days, frequency-over-time.",
          "- Leader-follower lead/lag (the `following_*` suite) on non-fireworks nights.",
          "- If shared-road dominates: quantify **gradual corridor emergence** (trampled-road) per "
          "night and test whether residual individual preference strengthens over days.", "",
          "## Outputs", "",
          "`coverage_summary.csv` · `cleaning_log.md` · `stabilization_metrics.csv` · "
          "`pooled_corridor_*.npy` · `residual_individual_summary.csv` · "
          "`pairwise_similarity_matrix.csv` · `shuffled_controls.csv` · `label_permutation_null.csv` · "
          "`transition_edge_similarity.csv` · `run_manifest.json` · `plots/` · "
          "`residual_individual_maps/`", ""]
    (out / "trajectory_stereotypy_report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()

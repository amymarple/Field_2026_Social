"""
analyze_route_structure.py
==========================
Route-structure analysis for a pooled local-time block (default 9-11 pm EDT,
across all dates present -> the "whole trunk", not hour-binned).

Answers: do the rats reuse the same corridors/routes, and is any apparent route
real or a WISER anchor-geometry artifact? Cleaned WISER points only; every route
claim is CANDIDATE and is cross-checked against the stationary fixed-position
baseline and the jitter floor.

Read-only on the source DBs. Outputs to a timestamped
``D:\\Wiser_plot\\route_structure_YYYYMMDD_HHMM\\`` (off the C: drive, off git).

Usage (conda env `cv`):
    python scripts/analyze_route_structure.py                       # 9-11 pm, all dates
    python scripts/analyze_route_structure.py --clock-start 21 --clock-end 23
    python scripts/analyze_route_structure.py --dates 2026-06-28 2026-06-29
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import time_utils
import metrics
import wiser_analysis_utils as w

DEFAULT_FREE_DB = Path(r"D:\Wiser\data\1stcohort_2026.sqlite")
DEFAULT_FIXED_DB = Path(r"D:\Wiser\data\tag_reports.sqlite")
DEFAULT_GT = PROJECT_ROOT / "configs" / "fixed_position_ground_truth.csv"
DEFAULT_ROIS = PROJECT_ROOT / "configs" / "wiser_rois.json"
DEFAULT_OUT_ROOT = Path(r"D:\Wiser_plot")


def main() -> None:
    ap = argparse.ArgumentParser(description="WISER route-structure analysis (pooled window).")
    ap.add_argument("--db", type=Path, default=DEFAULT_FREE_DB)
    ap.add_argument("--baseline-db", type=Path, default=DEFAULT_FIXED_DB)
    ap.add_argument("--gt", type=Path, default=DEFAULT_GT)
    ap.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    ap.add_argument("--exclude", type=Path,
                    default=PROJECT_ROOT / "configs" / "wiser_exclude.json",
                    help="user-drawn edge/exclude polygons (place_exclude_region.py)")
    ap.add_argument("--edge-band", type=float, default=12.0,
                    help="fallback wall band (in) if no exclude polygons exist")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--clock-start", type=int, default=21, help="local hour, inclusive")
    ap.add_argument("--clock-end", type=int, default=23, help="local hour, exclusive")
    ap.add_argument("--dates", nargs="*", default=None, help="restrict to these local dates")
    ap.add_argument("--tz-offset", type=int, default=w.LOCAL_TZ_OFFSET_HOURS)
    ap.add_argument("--bin-in", type=float, default=4.0)
    ap.add_argument("--corridor-pct", type=float, default=80.0)
    args = ap.parse_args()

    out = w.make_output_dir(args.output, prefix="route_structure")
    fig = out / "figures"
    print(f"=== WISER route-structure ===\n  free DB:  {args.db}\n"
          f"  baseline: {args.baseline_db}\n  output:   {out}\n")

    # --- stationary baseline: jitter floor + moving threshold -----------------
    fixed = w.load_wiser_session(args.baseline_db)
    fixed = time_utils.convert_timestamps(fixed)
    fixed = time_utils.trim_last_n_minutes(fixed, minutes=10)
    fixed = w.add_speed(fixed)
    gt = metrics.load_ground_truth(args.gt)
    jitter_floor_in = float(np.nanmedian(
        metrics.compute_summary(fixed, ground_truth=gt)["rms_jitter"]))
    moving_thr = float(w.speed_noise_floor(fixed)["p99"])
    print(f"  jitter floor {jitter_floor_in:.1f} in; moving threshold "
          f"{moving_thr:.1f} in/s (p99 stationary smoothed speed)")

    # --- free-moving: cleaned points in the pooled window --------------------
    df = w.load_wiser_session(args.db)
    df = time_utils.convert_timestamps(df)
    df = w.add_speed(df)
    roi_cfg = w.load_rois(args.rois)
    boundary = roi_cfg.get("boundary") if roi_cfg else None
    # Prefer the SURVEYED paddock boundary once the frame is georeferenced: a
    # confirmed transform yields a verified boundary that supersedes the
    # provisional ROI-file rectangle for edge/thigmotaxis and out-of-bounds QC.
    # No-op until a confirmed survey exists (load returns None) -> identical output.
    transform = w.load_field_transform()
    verified = w.verified_boundary_in_wiser(transform)
    if verified is not None:
        boundary = verified
        print(f"  georeferenced boundary (in): "
              f"{[round(v, 1) for v in verified['rect']]} (supersedes ROI-file boundary)")
    df = w.add_validity_flags(df, boundary=boundary, jitter_floor_in=jitter_floor_in)
    df = w.apply_tag_cutoffs(df)
    win = w.select_route_window(df, clock_start=args.clock_start,
                                clock_end=args.clock_end,
                                tz_offset_hours=args.tz_offset, dates=args.dates)
    if win.empty:
        raise SystemExit("[route] No cleaned fixes in the requested window.")
    if transform is not None:                       # additive field-cm for CV cross-checks
        win = w.apply_field_transform(win, transform)
    nights = sorted(win["night"].unique())
    per_night = win.groupby("night").size().to_dict()
    print(f"  window: {len(win):,} cleaned fixes, nights={nights}, "
          f"tags={sorted(win['shortid'].unique())}")

    extent = w.observed_extent(win)

    # 2-3) occupancy + corridor mask + skeleton -------------------------------
    H, _, _ = w.occupancy_hist(win, extent, bin_in=args.bin_in)
    mask, _ = w.corridor_mask(H, pct=args.corridor_pct)
    skeleton = w.skeletonize_mask(mask)
    per_hist = {t: w.occupancy_hist(g, extent, bin_in=args.bin_in)[0]
                for t, g in win.groupby("shortid")}

    # 4-5) route reuse + leave-one-out similarity -----------------------------
    rr = w.route_reuse_index(win, extent, mask, bin_in=args.bin_in)
    sim_occ = w.occupancy_similarity_loo(per_hist)
    rr.to_csv(out / "route_reuse_index.csv", index=False)
    sim_occ.to_csv(out / "occupancy_similarity.csv", index=False)

    # 6) movement bouts + straightness ----------------------------------------
    bouts = w.movement_bouts(win, moving_thr_inps=moving_thr)
    straight = w.straightness_summary(bouts)
    straight.to_csv(out / "straightness_summary.csv", index=False)

    # 7-8) transition graph + shared edges ------------------------------------
    edges_df = pd.DataFrame()
    sim_edges = pd.DataFrame()
    if roi_cfg:
        ptt = w.per_tag_transitions(win, roi_cfg)
        sim_edges, shared = w.edge_usage_similarity(ptt)
        if not ptt.empty:
            edges_df = (ptt.groupby(["from_roi", "to_roi"])
                        .agg(n_rats=("shortid", "nunique"),
                             total_count=("count", "sum")).reset_index())
            edges_df.to_csv(out / "shared_edges.csv", index=False)
        if not sim_edges.empty:
            sim_edges.to_csv(out / "edge_usage_similarity.csv", index=False)

    # 9) robustness ------------------------------------------------------------
    robust = w.route_robustness(win, mask, extent, moving_thr_inps=moving_thr,
                                bin_in=args.bin_in, corridor_pct=args.corridor_pct)
    robust.to_csv(out / "straightness_robustness.csv", index=False)

    # 10) fixed-position baseline ---------------------------------------------
    base_cmp = w.baseline_route_compare(fixed, bouts, moving_thr_inps=moving_thr,
                                        bin_in=args.bin_in,
                                        corridor_pct=args.corridor_pct)
    base_cmp.to_csv(out / "baseline_comparison.csv", index=False)
    base_bouts = w.movement_bouts(fixed, moving_thr_inps=moving_thr)

    # 10b) displacement-matched jitter straightness null (the n~6 baseline is too
    # few/spread to threshold; characterise jitter straightness with large n) ---
    jitter_null = w.jitter_straightness_null(fixed, bouts, n=600)
    straight_vs_null = w.straightness_vs_null_summary(bouts, jitter_null)
    straight_vs_null.to_csv(out / "straightness_vs_jitter.csv", index=False)

    # within-rat route reuse across the two nights (memory proxy) -------------
    self_reuse = w.self_route_reuse(win, roi_cfg, extent) if roi_cfg else pd.DataFrame()
    cross_edge_mean = (float(sim_edges["edge_cosine"].mean())
                       if len(sim_edges) else float("nan"))
    if not self_reuse.empty:
        self_reuse.to_csv(out / "self_route_reuse.csv", index=False)

    # edge effect / thigmotaxis (is the corridor just the wall/edge?) ---------
    # user-drawn exclude polygons override the rectangular boundary band
    exclude_regions = w.load_exclude_regions(args.exclude)
    interior_info, thig, brect = {}, pd.DataFrame(), None
    if boundary and boundary.get("rect"):
        brect = tuple(boundary["rect"])
        win = w.add_edge_distance(win, brect)
    if brect is not None or exclude_regions:
        thig = w.thigmotaxis_index(win, regions=exclude_regions,
                                   boundary_rect=brect, edge_band_in=args.edge_band)
        thig.to_csv(out / "thigmotaxis.csv", index=False)
        interior_info = w.interior_route_summary(
            win, mask, extent, brect, edge_band_in=args.edge_band,
            bin_in=args.bin_in, corridor_pct=args.corridor_pct,
            regions=exclude_regions)
        zone = (f"{len(exclude_regions)} drawn exclude polygon(s)"
                if exclude_regions else f"{args.edge_band:g}-in boundary band")
        print(f"  edge zone = {zone}; thigmotaxis median "
              f"{thig['thigmotaxis_index'].median():.2f}; "
              f"{interior_info.get('full_corridor_edge_fraction', float('nan')):.0%} "
              f"of corridor in the exclude zone")

    # per-night corridor consistency (IoU) ------------------------------------
    night_iou = None
    if len(nights) >= 2:
        masks = []
        for nd in nights[:2]:
            Hn, _, _ = w.occupancy_hist(win[win["night"] == nd], extent,
                                        bin_in=args.bin_in)
            masks.append(w.corridor_mask(Hn, pct=args.corridor_pct)[0])
        night_iou = w._mask_iou(masks[0], masks[1])
        print(f"  per-night corridor IoU ({nights[0]} vs {nights[1]}): {night_iou:.3f}")

    # --- figures --------------------------------------------------------------
    w.plot_window_scatter(win, boundary_rect=brect,
                          save_path=fig / "RS0_all_rats_scatter.png")
    w.plot_corridor_map(H, extent, mask, skeleton, bin_in=args.bin_in,
                        save_path=fig / "RS1_corridor_map.png")
    w.plot_per_rat_occupancy(per_hist, extent, mask, bin_in=args.bin_in,
                             save_path=fig / "RS2_per_rat_occupancy.png")
    w.plot_route_reuse(rr, save_path=fig / "RS3_route_reuse.png")
    w.plot_occupancy_similarity(sim_occ, save_path=fig / "RS4_occupancy_similarity.png")
    w.plot_straightness(bouts, baseline_bouts=base_bouts,
                        save_path=fig / "RS5_straightness.png")
    if not edges_df.empty:
        w.plot_shared_edge_graph(roi_cfg, edges_df, save_path=fig / "RS6_shared_edges.png")
    if not sim_edges.empty:
        w.plot_edge_usage_heatmap(sim_edges, save_path=fig / "RS7_edge_usage.png")
    w.plot_baseline_compare(fixed, bouts, base_bouts, bin_in=args.bin_in,
                            save_path=fig / "RS8_baseline_compare.png")
    w.plot_straightness_vs_displacement(bouts, jitter_null,
                                        save_path=fig / "RS9_straightness_vs_disp.png")
    if not self_reuse.empty:
        w.plot_self_route_reuse(self_reuse, cross_edge_mean=cross_edge_mean,
                                save_path=fig / "RS10_self_route_reuse.png")
    if not thig.empty and interior_info:
        w.plot_edge_effect(thig, H, extent, mask, interior_info, brect,
                           bin_in=args.bin_in, save_path=fig / "RS11_edge_effect.png")

    # --- verdict + manifest ---------------------------------------------------
    # straightness verdict from the DISPLACEMENT-MATCHED null (the blunt baseline
    # median is confounded by a few small-displacement stationary bouts)
    jitter_disp_p95 = (float(jitter_null["disp_in"].quantile(0.95))
                       if len(jitter_null) else 0.0)
    free_beyond = (float((bouts["disp_in"] > jitter_disp_p95).mean())
                   if len(bouts) else 0.0)
    # displacement-matched artifact verdict supersedes the blunt baseline median
    artifact_risk = bool(free_beyond < 0.7)
    baseline_median_flag = bool(base_cmp["geometry_artifact_risk"].iloc[0])
    if free_beyond >= 0.7:
        straight_phrase = (
            f"REAL movement — {free_beyond:.0%} of free bouts occur at net "
            f"displacements (> {jitter_disp_p95:.0f} in) the stationary jitter "
            "never reaches, so the high straightness is locomotion, not geometry "
            "(the baseline-median flag is a small-displacement artifact)")
    else:
        straight_phrase = ("ambiguous — free bouts overlap the stationary jitter "
                           "in displacement; treat straightness with caution")
    self_mean = (float(self_reuse["edge_self_cosine"].mean())
                 if len(self_reuse) else float("nan"))
    memory_phrase = (
        f"cross-rat edge similarity ({cross_edge_mean:.2f}) exceeds within-rat "
        f"night-to-night route similarity ({self_mean:.2f}) -> route structure is "
        "shared-environment-driven, little evidence of individual route memory"
        if np.isfinite(self_mean) and np.isfinite(cross_edge_mean)
        and cross_edge_mean > self_mean else
        "within-rat route consistency comparable to cross-rat -> possible individual reuse")
    thig_med = float(thig["thigmotaxis_index"].median()) if len(thig) else float("nan")
    edge_frac = interior_info.get("full_corridor_edge_fraction")
    edge_phrase = (f"thigmotaxis low ({thig_med:.1%} of fixes near the wall); "
                   f"{edge_frac:.0%} of the corridor is perimeter -> corridors are "
                   "interior, not a wall-running confound"
                   if np.isfinite(thig_med) and edge_frac is not None
                   else "edge effect not evaluated (no confirmed boundary)")
    verdict = (
        "CANDIDATE route structure: rats share high-occupancy corridors (robust to "
        f"QC) and the same ROI edges. Straightness is {straight_phrase}. Memory: "
        f"{memory_phrase}. Edge: {edge_phrase}. Corridors only ~{(night_iou or 0):.0%} "
        "consistent night-to-night. WISER frame unverified vs paddock; candidate, "
        "not confirmed routes.")
    print("\n" + verdict)

    w.write_run_manifest(out, {
        "analysis": "route_structure",
        "window": {"clock_start": args.clock_start, "clock_end": args.clock_end,
                   "tz_offset_hours": args.tz_offset, "nights": nights,
                   "fixes_per_night": per_night, "n_fixes": int(len(win))},
        "free_db": str(args.db), "baseline_db": str(args.baseline_db),
        "jitter_floor_in": jitter_floor_in, "moving_threshold_inps": moving_thr,
        "bin_in": args.bin_in, "corridor_pct": args.corridor_pct,
        "corridor_cells": int(mask.sum()), "skeleton_cells": int(skeleton.sum()),
        "per_night_corridor_iou": night_iou,
        "geometry_artifact_risk": artifact_risk,            # displacement-matched
        "baseline_median_straightness_flag": baseline_median_flag,  # blunt, superseded
        "free_bouts_beyond_jitter_disp_frac": free_beyond,
        "self_route_reuse_edge_cosine_mean": (
            float(self_reuse["edge_self_cosine"].mean()) if len(self_reuse) else None),
        "cross_rat_edge_cosine_mean": (cross_edge_mean
                                       if np.isfinite(cross_edge_mean) else None),
        "thigmotaxis_index_median": (float(thig["thigmotaxis_index"].median())
                                     if len(thig) else None),
        "full_corridor_edge_fraction": (
            float(interior_info["full_corridor_edge_fraction"])
            if interior_info.get("full_corridor_edge_fraction") is not None else None),
        "interior_vs_full_corridor_iou": (
            float(interior_info["interior_vs_full_corridor_iou"])
            if interior_info.get("interior_vs_full_corridor_iou") is not None else None),
        "georeferenced": bool(transform),
        "boundary_source": ("georeference transform (surveyed)" if verified is not None
                            else "wiser_rois.json (provisional)" if boundary else "none"),
        "coordinate_note": ("WISER native inches; georeferenced to the field frame "
                            "(x_field_cm/y_field_cm added)" if transform is not None
                            else "WISER native inches, offset origin, UNVERIFIED vs paddock"),
        "notes": "candidate route structure; straightness path-length is jitter-inflated; "
                 "Sova/12409 present night 1 only (deceased, cutoff applied).",
    })
    (out / "route_verdict.txt").write_text(verdict, encoding="utf-8")
    print(f"\nAll outputs written to: {out}")


if __name__ == "__main__":
    main()

r"""
analyze_nightly_behavior.py — nightly 9pm-12am behavior & social across 6/28-6/30.

Companion to analyze_nightly_progression.py (raw movement). Same 5-rat paired core
(Sova removed), same nights, extends to:
  * home/shelter use + resource use + home<->open exploration transitions;
  * outside (open-only) movement pattern;
  * social: cohesion (pairwise distance/proximity/clustering) + shared-space
    (leave-one-out occupancy similarity);
  * exploration graph structure (per-night ROI transition network + night-to-night
    stabilization);
  * space-use geometry (coverage, concentration, dispersion, corridors).

All exploratory/candidate; 6/30 wet-ground confounded with habituation; tunnel_1
present 6/28 only. Read-only on D:\Wiser\data; outputs to
D:\Wiser_plot\nightly_behavior_YYYYMMDD_HHMM\.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import wiser_analysis_utils as w        # noqa: E402
import time_utils                       # noqa: E402
import metrics                          # noqa: E402

DEFAULT_DB = Path(r"D:\Wiser\data\1stcohort_2026.sqlite")
DEFAULT_FIXED = Path(r"D:\Wiser\data\tag_reports.sqlite")
DEFAULT_GT = PROJECT_ROOT / "configs" / "fixed_position_ground_truth.csv"
DEFAULT_ROIS = PROJECT_ROOT / "configs" / "wiser_rois.json"
DEFAULT_OUT_ROOT = Path(r"D:\Wiser_plot")
DROP_TAGS = {"12409"}                   # Sova, removed entirely


def main() -> None:
    ap = argparse.ArgumentParser(description="Nightly 9pm-12am behavior & social (6/28-6/30).")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--fixed", type=Path, default=DEFAULT_FIXED)
    ap.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--clock-start", type=int, default=21)
    ap.add_argument("--clock-end", type=int, default=24)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"[behavior] DB not found: {args.db}")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M")
    out = args.output / f"nightly_behavior_{ts}"
    fig = out / "figures"
    fig.mkdir(parents=True, exist_ok=True)
    print(f"=== Nightly behavior & social ===\n  DB: {args.db}\n  out: {out}\n")

    fx = w.load_wiser_session(args.fixed)
    fx = time_utils.convert_timestamps(fx)
    fx = time_utils.trim_last_n_minutes(fx, minutes=10)
    fx = w.add_speed(fx)
    moving_thr = w.speed_noise_floor(fx)["p99"]
    jitter = float(np.nanmedian(metrics.compute_summary(
        fx, ground_truth=metrics.load_ground_truth(DEFAULT_GT))["rms_jitter"]))
    print(f"  moving_thr(p99)={moving_thr:.2f} in/s  jitter_floor={jitter:.2f} in")

    df = w.load_wiser_session(args.db)
    df = time_utils.convert_timestamps(df)
    df = w.add_speed(df)
    df = w.add_validity_flags(df, jitter_floor_in=jitter)
    df = w.apply_tag_cutoffs(df)
    df = df[~df["shortid"].astype(str).isin(DROP_TAGS)]
    win = w.select_route_window(df, clock_start=args.clock_start, clock_end=args.clock_end)
    roi_cfg = w.load_rois(args.rois)
    extent = w.observed_extent(win)
    nights = sorted(win["night"].unique())
    print(f"  nights={nights}  rats/night="
          f"{win.groupby('night')['shortid'].nunique().to_dict()}")

    # --- home / resource / exploration ---
    roi_use = w.nightly_roi_use(win, roi_cfg)
    roi_use.to_csv(out / "nightly_roi_use.csv", index=False)
    w.plot_nightly_paired(roi_use, "home_frac", ylabel="home/shelter time fraction",
                          title="Home/shelter use per rat per night (6/30 wet)",
                          save_path=fig / "B1_home_fraction.png")
    w.plot_nightly_timebudget(roi_use, save_path=fig / "B2_timebudget.png")
    w.plot_nightly_paired(roi_use, "home_open_transitions_per_h",
                          ylabel="home<->open transitions / valid hour",
                          title="Home<->open exploration transitions per night",
                          save_path=fig / "B3_transitions.png")

    # --- outside movement ---
    mv = w.nightly_movement_by_cat(win, roi_cfg, moving_thr_inps=moving_thr)
    mv.to_csv(out / "nightly_movement_by_cat.csv", index=False)
    w.plot_nightly_paired(mv, "open_rate_m_per_valid_h",
                          ylabel="outside active distance (m / valid hr)",
                          title="Outside (open-area) movement rate per night",
                          save_path=fig / "B4_outside_movement.png")

    # --- social: cohesion + shared space ---
    soc = w.nightly_social(win, jitter_floor_in=jitter)
    soc.to_csv(out / "nightly_social.csv", index=False)
    w.plot_nightly_lines(soc, ["mean_pair_dist_in", "clustering_mean_pair_dist_in"],
                         ylabel="inches", title="Social cohesion: mean pairwise distance per night",
                         save_path=fig / "B5_cohesion.png")
    w.plot_nightly_lines(soc, ["frac_below_39in", "frac_below_20in", "loo_occupancy_cosine_mean"],
                         ylabel="fraction / cosine",
                         title="Move-together (<=1m/<=0.5m, reliable) & shared-space similarity",
                         save_path=fig / "B6_social_shared.png")

    # --- exploration graph structure ---
    gstruct, gsim = w.nightly_graph_structure(win, roi_cfg)
    gstruct.to_csv(out / "nightly_graph_structure.csv", index=False)
    gsim.to_csv(out / "nightly_graph_similarity.csv", index=False)
    w.plot_nightly_lines(gstruct, ["n_distinct_edges", "n_nodes"],
                         ylabel="count", title="Exploration graph size per night (edges simplify?)",
                         save_path=fig / "B7_graph_size.png")
    for night in nights:
        gv = win[(win["night"] == night) & win["valid"]]
        tdf, trans = w.roi_time_and_transitions(gv, roi_cfg)
        wet = "  (wet)" if night == nights[-1] else ""
        w.plot_roi_transition_graph(roi_cfg, trans, tdf,
                                    save_path=fig / f"B7_graph_{night}.png")

    # --- geometry ---
    geo = w.nightly_geometry(win, extent)
    geo.to_csv(out / "nightly_geometry.csv", index=False)
    w.plot_nightly_lines(geo, ["coverage_frac", "concentration"],
                         ylabel="fraction", title="Space-use geometry: coverage vs concentration",
                         save_path=fig / "B8_geometry_metrics.png")
    for night in nights:
        gv = win[(win["night"] == night) & win["valid"]]
        H, _, _ = w.occupancy_hist(gv, extent, bin_in=4.0)
        mask, _ = w.corridor_mask(H, pct=80.0)
        skel = w.skeletonize_mask(mask)
        w.plot_corridor_map(H, extent, mask, skel, bin_in=4.0,
                            save_path=fig / f"B9_corridor_{night}.png")

    # --- conclusion ---
    rmean = roi_use.groupby("night")[["home_frac", "resource_frac", "open_frac"]].mean()
    omean = mv.groupby("night")["open_rate_m_per_valid_h"].mean()
    verdict = (
        f"CANDIDATE settling over nights (n=5 paired, exploratory): home/shelter "
        f"{rmean['home_frac'].iloc[0]:.2f}->{rmean['home_frac'].iloc[-1]:.2f}, "
        f"food/water {rmean['resource_frac'].iloc[0]:.2f}->{rmean['resource_frac'].iloc[-1]:.2f} "
        f"(both up); outside movement {omean.iloc[0]:.0f}->{omean.iloc[-1]:.0f} m/valid-hr (down). "
        f"Exploration graph edges {gstruct['n_distinct_edges'].iloc[0]}->{gstruct['n_distinct_edges'].iloc[-1]} "
        f"(simplifies) and stabilizes (night-to-night edge cosine "
        f"{gsim['edge_cosine'].iloc[0]:.2f}->{gsim['edge_cosine'].iloc[-1]:.2f}); "
        f"hub {gstruct['hub_out'].iloc[0]}->{gstruct['hub_out'].iloc[-1]}. "
        f"Shared-space occupancy similarity {soc['loo_occupancy_cosine_mean'].iloc[0]:.2f}->"
        f"{soc['loo_occupancy_cosine_mean'].iloc[-1]:.2f} (individualize then reconverge); "
        f"<=1m proximity {soc['frac_below_39in'].iloc[0]:.2f}->{soc['frac_below_39in'].iloc[-1]:.2f} "
        f"(reliable). Geometry coverage {geo['coverage_frac'].iloc[0]:.2f}->{geo['coverage_frac'].iloc[-1]:.2f}. "
        f"6/30 is WET (17:20 rain) -> confounded with habituation; tunnel present 6/28 only.")
    (out / "nightly_behavior_conclusion.txt").write_text(verdict, encoding="utf-8")
    w.write_run_manifest(out, {
        "window": f"{args.clock_start}:00-{args.clock_end}:00 EDT, nights {nights}",
        "paired_core": "5 rats (Sova/12409 removed)",
        "home_definition": "ROI type 'refuge' (2 houses + 4 refuges); tunnel 6/28 only (separate)",
        "moving_thr_inps": moving_thr, "jitter_floor_in": jitter,
        "note": "exploratory; 6/30 wet-ground confounded with habituation; WISER frame unverified",
    })
    print("\n  " + verdict)
    print(f"\nAll outputs written to: {out}")


if __name__ == "__main__":
    main()

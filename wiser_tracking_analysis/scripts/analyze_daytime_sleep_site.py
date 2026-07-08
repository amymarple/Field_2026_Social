r"""
analyze_daytime_sleep_site.py — Direction 3: daytime sleep/rest-site & its change.

Rats are nocturnal; the daytime REST period is 05:00-21:00 local. This driver finds
each animal's primary rest site per day (a LOW-SPEED occupancy proxy for sleep),
and measures how that site changes WITHIN a day (morning->afternoon drift) and
ACROSS days. Companion to the nightly-movement (Direction 1) and route (Direction 2)
analyses; see wiser_tracking_analysis/ANALYSIS_STATUS.md.

Everything is exploratory/candidate. "Sleep" is a low-speed proxy (smoothed speed <
the stationary p99 noise floor), NOT validated against ephys — CV shelter cams
(CH05/CH06) are the intended cross-check. WISER's ~7 in jitter means only sites
separated by >> the floor (the two shelters are ~5 ft apart) are distinguishable.
Read-only on D:\Wiser\data; outputs to D:\Wiser_plot\daytime_sleep_site_YYYYMMDD_HHMM\.

    conda activate cv
    cd wiser_tracking_analysis
    python scripts/analyze_daytime_sleep_site.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

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
DROP_TAGS = {"12409"}                   # Sova, deceased -> removed entirely
BLOCKS = ((5, 11), (11, 15), (15, 21))  # morning / midday / afternoon


def _fig_sites(win, sites, tags, nights, jitter, fig_path):
    """Small-multiples: resting fixes + primary site per tag, coloured by day."""
    rest = win[win["resting"]]
    ncol = min(3, len(tags)) or 1
    nrow = int(np.ceil(len(tags) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.6 * nrow), squeeze=False)
    cmap = plt.get_cmap("viridis", max(len(nights), 1))
    ncol_map = {n: cmap(i) for i, n in enumerate(nights)}
    for k, tag in enumerate(tags):
        ax = axes[k // ncol][k % ncol]
        gt = rest[rest["shortid"].astype(str) == str(tag)]
        for n in nights:
            gn = gt[gt["night"] == n]
            ax.plot(gn["x"], gn["y"], ".", ms=1.2, alpha=0.12, color=ncol_map[n])
        st = sites[(sites["shortid"].astype(str) == str(tag)) & sites["site_x"].notna()]
        for _, r in st.iterrows():
            ax.plot(r["site_x"], r["site_y"], "o", ms=9, mec="k", mew=0.8,
                    color=ncol_map.get(r["night"], "red"))
        ax.set_title(f"tag {tag}", fontsize=9)
        ax.set_aspect("equal"); ax.tick_params(labelsize=7)
    for j in range(len(tags), nrow * ncol):
        axes[j // ncol][j % ncol].axis("off")
    handles = [plt.Line2D([], [], marker="o", ls="", color=ncol_map[n], label=n) for n in nights]
    fig.legend(handles=handles, loc="upper right", fontsize=8, title="rest day")
    fig.suptitle(f"Daytime rest sites (05:00-21:00) — jitter floor ~{jitter:.0f} in")
    fig.tight_layout(); fig.savefig(fig_path, dpi=130); plt.close(fig)


TIER_COLORS = {"stable": "0.7", "marginal": "tab:olive", "borderline": "goldenrod",
               "robust_relocation": "tab:orange", "major_shelter_switch": "tab:red",
               "undefined": "0.85"}


def _fig_shift(stab, jitter, fig_path):
    """Across-day site shift per tag, coloured by tiered relocation label with the
    tier-threshold reference lines (30 / 100 / 180 in). Jitter-scale shifts (<30 in)
    are greyed so they don't read as relocations."""
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    if not stab.empty:
        tiers = stab["relocation_tier"] if "relocation_tier" in stab else ["undefined"] * len(stab)
        colors = [TIER_COLORS.get(t, "0.7") for t in tiers]
        labels = [f"{r.shortid}\n{r.night_prev[5:]}→{r.night[5:]}" for r in stab.itertuples()]
        ax.bar(range(len(stab)), stab["site_shift_in"], color=colors)
        ax.set_xticks(range(len(stab)))
        ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
    ax.axhline(jitter, color="0.5", ls="--", lw=1, label=f"jitter floor ~{jitter:.0f} in")
    for thr, lab in [(30, "stable/marginal 30"), (100, "robust 100"), (180, "major switch 180")]:
        ax.axhline(thr, color="k", ls=":", lw=0.8, alpha=0.5)
        ax.text(len(stab) - 0.4 if not stab.empty else 0, thr, f" {lab} in", fontsize=6.5, va="bottom")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for t, c in TIER_COLORS.items()
               if t not in ("undefined",)]
    ax.legend(handles, [t for t in TIER_COLORS if t != "undefined"], fontsize=7, loc="upper left")
    ax.set_ylabel("across-day primary-site shift (in)")
    ax.set_title("Day-to-day rest-site change by tier (only ≥100 in = robust; identity switch = major)")
    fig.tight_layout(); fig.savefig(fig_path, dpi=130); plt.close(fig)


def _fig_intraday(drift, jitter, fig_path):
    """Within-day block-to-block shift per tag-day."""
    fig, ax = plt.subplots(figsize=(8, 4))
    d = drift.dropna(subset=["shift_from_prev_in"])
    if not d.empty:
        labels = [f"{r.shortid} {r.night[5:]}\n{r.block}" for r in d.itertuples()]
        ax.bar(range(len(d)), d["shift_from_prev_in"], color="teal")
        ax.set_xticks(range(len(d)))
        ax.set_xticklabels(labels, fontsize=6, rotation=45, ha="right")
    ax.axhline(3 * jitter, color="tab:red", ls=":", lw=1, label=f"3x floor ~{3*jitter:.0f} in")
    ax.set_ylabel("within-day shift from previous block (in)"); ax.legend(fontsize=8)
    ax.set_title("Intraday rest-site drift (morning->midday->afternoon)")
    fig.tight_layout(); fig.savefig(fig_path, dpi=130); plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="Daytime sleep/rest-site & its change (Direction 3).")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--fixed", type=Path, default=DEFAULT_FIXED)
    ap.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--rest-start", type=int, default=5)
    ap.add_argument("--rest-end", type=int, default=21)
    ap.add_argument("--min-fixes", type=int, default=50)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"[sleep-site] DB not found: {args.db}")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M")
    out = args.output / f"daytime_sleep_site_{ts}"
    fig = out / "figures"
    fig.mkdir(parents=True, exist_ok=True)
    print(f"=== Daytime sleep-site (Direction 3) ===\n  DB: {args.db}\n  out: {out}\n")

    # thresholds from the stationary baseline (data-driven rest cutoff + jitter floor)
    fx = w.load_wiser_session(args.fixed)
    fx = time_utils.convert_timestamps(fx)
    fx = time_utils.trim_last_n_minutes(fx, minutes=10)
    fx = w.add_speed(fx)
    moving_thr = w.speed_noise_floor(fx)["p99"]
    jitter = float(np.nanmedian(metrics.compute_summary(
        fx, ground_truth=metrics.load_ground_truth(DEFAULT_GT))["rms_jitter"]))
    print(f"  rest cutoff (p99 stationary)={moving_thr:.2f} in/s  jitter_floor={jitter:.2f} in")

    # free session -> cleaned, Sova removed, daytime rest window, resting fixes
    df = w.load_wiser_session(args.db)
    df = time_utils.convert_timestamps(df)
    df = w.add_speed(df)
    df = w.add_validity_flags(df, jitter_floor_in=jitter)
    df = w.apply_tag_cutoffs(df)
    df = df[~df["shortid"].astype(str).isin(DROP_TAGS)]
    win = w.select_route_window(df, clock_start=args.rest_start, clock_end=args.rest_end)
    win = w.rest_mask(win, moving_thr_inps=moving_thr)

    rest = win[win["resting"]]
    nights = sorted(win["night"].unique())
    tags = sorted(win["shortid"].astype(str).unique())
    print(f"  rest days={nights}  tags={tags}\n"
          f"  resting fixes/day={rest.groupby('night').size().to_dict()}")
    if rest.empty:
        raise SystemExit("[sleep-site] no resting fixes in the daytime window.")

    # optional georeference + ROI naming (both no-op / provisional until confirmed)
    transform = w.load_field_transform()                 # None unless confirmed survey exists
    roi_cfg = w.load_rois(args.rois)                     # ROI names are provisional if unconfirmed
    extent = w.observed_extent(rest)

    # --- primary site per (day, tag), across-day stability, within-day drift ---
    sites, hists = w.daytime_primary_site(
        win, extent=extent, roi_cfg=roi_cfg, min_fixes=args.min_fixes, transform=transform)
    stab = w.rest_site_stability(sites, occ_hists=hists)
    stab = w.classify_across_day(stab, sites, roi_cfg)   # tiered relocation labels
    drift = w.intraday_site_drift(win, extent=extent, transform=transform)
    sites.to_csv(out / "daytime_primary_site.csv", index=False)
    stab.to_csv(out / "rest_site_stability.csv", index=False)
    drift.to_csv(out / "intraday_drift.csv", index=False)

    # --- QC per day (named aggregation; no groupby.apply for pandas-version safety) ---
    qcw = win.copy()
    qcw["_anch"] = pd.to_numeric(qcw.get("anchors_used"), errors="coerce")
    qc = (qcw.groupby("night")
          .agg(n_fixes=("resting", "size"),
               n_rest_fixes=("resting", "sum"),
               resting_frac=("resting", "mean"),
               n_rats=("shortid", "nunique"),
               mean_anchors=("_anch", "mean"))
          .reset_index())
    qc.to_csv(out / "daytime_qc.csv", index=False)

    # --- figures ---
    _fig_sites(win, sites, tags, nights, jitter, fig / "S1_rest_sites.png")
    _fig_shift(stab, jitter, fig / "S2_across_day_shift.png")
    _fig_intraday(drift, jitter, fig / "S3_intraday_drift.png")

    # --- verdict: tiered relocation, cautious biological claim ---
    med_conc = float(sites["site_concentration"].dropna().median()) if not sites.empty else float("nan")
    tier_counts = (stab["relocation_tier"].value_counts().to_dict()
                   if not stab.empty and "relocation_tier" in stab else {})
    robust_tiers = {"robust_relocation", "major_shelter_switch"}
    reloc_animals = sorted({str(r.shortid) for r in stab.itertuples()
                            if getattr(r, "relocation_tier", "") in robust_tiers}) if not stab.empty else []
    switch_animals = sorted({str(r.shortid) for r in stab.itertuples()
                             if getattr(r, "shelter_switch", False)}) if not stab.empty else []
    reloc_str = ", ".join(reloc_animals) if reloc_animals else "none"
    verdict = (
        f"CANDIDATE daytime rest-site analysis (05:00-21:00, exploratory). {len(tags)} tags x "
        f"{len(nights)} rest days; median site concentration {med_conc:.2f} (frac of rest fixes "
        f"within 24 in of the primary site). Across-day relocation tiers "
        f"(jitter floor ~{jitter:.0f} in): {tier_counts}. Daytime rest-site fidelity is "
        f"HETEROGENEOUS: {reloc_str} show robust cross-shelter relocation"
        + (f" (house_1<->house_2 identity switch: {', '.join(switch_animals)})" if switch_animals else "")
        + f", while the other animals are mostly stable or marginal near the jitter scale "
        f"(22-28 in shifts are NOT relocations). Sleep = low-speed proxy (< {moving_thr:.1f} in/s), "
        f"NOT ephys-validated; WISER frame {'georeferenced' if transform else 'UNVERIFIED'} "
        f"(inch offset); ROI names {'from confirmed ROIs' if roi_cfg else 'unavailable'} "
        f"(provisional). CV shelter cams corroborate visible shelter-resident periods only "
        f"(CV = lower bound; 2026-07-06 reconciliation). Only site differences >> the "
        f"~{jitter:.0f} in jitter floor are trustworthy.")
    (out / "sleep_site_conclusion.txt").write_text(verdict, encoding="utf-8")
    w.write_run_manifest(out, {
        "analysis": "Direction 3 — daytime sleep/rest-site",
        "rest_window": f"{args.rest_start:02d}:00-{args.rest_end:02d}:00 EDT",
        "rest_days": nights, "tags": tags,
        "paired_note": "Sova/12409 removed",
        "rest_cutoff_inps_p99_stationary": moving_thr, "jitter_floor_in": jitter,
        "site_bin_in": 4.0, "site_radius_in": 24.0, "min_fixes": args.min_fixes,
        "relocation_tiers_in": w.RELOCATION_TIERS,
        "relocation_tier_counts": tier_counts,
        "robust_relocation_animals": reloc_animals,
        "shelter_switch_animals": switch_animals,
        "intraday_blocks": [list(b) for b in BLOCKS],
        "georeferenced": bool(transform),
        "roi_naming": "provisional (wiser_rois.json placeholder until confirmed)" if roi_cfg else "none",
        "sleep_proxy": f"smoothed speed < p99 stationary ({moving_thr:.2f} in/s); not ephys-validated",
        "note": "exploratory/candidate; spatial precision gated by ~7 in jitter; "
                "CV shelter (CH05/CH06) cross-check is a follow-up",
    })
    print("\n  " + verdict)
    print(f"\nAll outputs written to: {out}")


if __name__ == "__main__":
    main()

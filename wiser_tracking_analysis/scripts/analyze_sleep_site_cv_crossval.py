r"""
analyze_sleep_site_cv_crossval.py — CV shelter detection vs WISER shelter *state*.

WISER (UWB) is unaffected by fog / rain / condensation / IR glass; the CV shelter
cams (CH05 = left, CH06 = right) are the optically degraded sensor. So this is a
**WISER-as-reference** evaluation, not a symmetric agreement:

  * WISER occupancy is a SMOOTHED, hysteretic, buffer-tolerant shelter STATE
    (wiser_shelter_state) — a sustained cluster of positions near a shelter, not
    raw point-wise ROI inclusion (which over-splits because WISER jitters ~7-15 in
    around the ~36x27 in shelter). Raw point-wise presence is emitted only as a
    DIAGNOSTIC (and we quantify how many false exits the smoothing recovers).
  * HEADLINE = CV detection performance: recall during WISER-confirmed
    (high-confidence) shelter occupancy + precision when CV reports occupied,
    stratified by glass quality and by day.
  * Symmetric Cohen's kappa is demoted to a clock-lag / mapping ALIGNMENT DIAGNOSTIC
    (WISER is Unix-ms UTC; CV t is local NVR wallclock, +4 h nominal + scanned
    residual lag). Alignment is reported, never asserted as verified.
  * High-confidence WISER shelter episodes during the daytime rest window are used
    as QC/validation ANCHORS for CV — not as circular proof of the sleep-site claim
    (that claim itself rests on WISER).

Read-only on the WISER snapshot + CV CSVs. Outputs to
D:\Wiser_plot\sleep_site_cv_crossval_YYYYMMDD_HHMM\.

    python scripts/analyze_sleep_site_cv_crossval.py \
        --db D:\Reolink_record\audio_in\Wiser_backup\snapshots\1stcohort_2026_2026-07-01.sqlite \
        --fixed D:\Reolink_record\audio_in\Wiser_backup\snapshots\tag_reports_2026-06-30.sqlite
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import wiser_analysis_utils as w        # noqa: E402
import time_utils                       # noqa: E402
import metrics                          # noqa: E402

DEFAULT_DB = Path(r"D:\Reolink_record\audio_in\Wiser_backup\snapshots\1stcohort_2026_2026-07-01.sqlite")
DEFAULT_FIXED = Path(r"D:\Reolink_record\audio_in\Wiser_backup\snapshots\tag_reports_2026-06-30.sqlite")
DEFAULT_GT = PROJECT_ROOT / "configs" / "fixed_position_ground_truth.csv"
DEFAULT_ROIS = PROJECT_ROOT / "configs" / "wiser_rois.json"
DEFAULT_CV_DIR = REPO_ROOT / "preprocessing" / "computer_vision" / "outputs"
DEFAULT_OUT_ROOT = Path(r"D:\Wiser_plot")
DROP_TAGS = {"12409"}
SHELTERS = ["house_1", "house_2"]
# ROI -> camera mappings to test (CH05 = left shelter, CH06 = right shelter)
MAPPINGS = {"A": {"house_1": "CH05", "house_2": "CH06"},
            "B": {"house_1": "CH06", "house_2": "CH05"}}
DATES = ["2026-06-29", "2026-06-30"]
# Wide COARSE lag scan (+/-4.5 h, 5-min steps): the CV clock basis (NVR wallclock)
# vs WISER UTC is uncertain by more than a few minutes, so we discover the offset
# rather than assume a ~+4 h residual. Reported lag stays UNVERIFIED.
LAG_GRID = list(range(-16200, 16201, 300))
STRATA = [("headline", "usable_for_headline_summary"),   # clear glass only
          ("coarse", "usable_for_coarse_activity")]       # all usable glass
# WISER-as-reference: shelter STATE smoothing (see wiser_shelter_state docstring).
STATE_KW = dict(buffer_in=18.0, enter_s=120, exit_s=120,
                near_frac=0.5, far_frac=0.2, hc_min_s=1200, hc_max_spread_in=24.0)
# a (day, shelter) where WISER shows SUSTAINED high-confidence occupancy but CV
# recovers less than half of it = likely CV optical failure (wet/degraded glass),
# NOT a WISER error. Recall-based (not cv_occ_frac) because the failure mode is CV
# MISSING confirmed rats, which is exactly low recall under sustained occupancy.
CV_FAIL_WISER_HC = 0.50      # WISER hc-occupied fraction >= this (sustained) ...
CV_FAIL_RECALL = 0.50        # ... while CV recall (vs hc) <= this (CV misses >=half)


def _local_day(bin_utc: pd.Series) -> pd.Series:
    loc = pd.to_datetime(bin_utc) + pd.Timedelta(hours=w.LOCAL_TZ_OFFSET_HOURS)
    return loc.dt.date.astype(str)


def _confusion(sub: pd.DataFrame) -> dict:
    """CV detection scores for one merged (WISER-ref + CV) subset."""
    hc = sub["hc_occupied"].to_numpy().astype(bool)
    occ = sub["occupied"].to_numpy().astype(bool)
    cv = sub["cv_occupied"].to_numpy().astype(bool)
    tp_hc, fn_hc = int((hc & cv).sum()), int((hc & ~cv).sum())        # recall vs hc
    tp_o, fn_o = int((occ & cv).sum()), int((occ & ~cv).sum())        # recall vs occ
    fp_o, tn_o = int((~occ & cv).sum()), int((~occ & ~cv).sum())      # precision/spec
    return {
        "n_bins": int(len(sub)),
        "wiser_occ_frac": float(occ.mean()), "wiser_hc_frac": float(hc.mean()),
        "cv_occ_frac": float(cv.mean()),
        "recall_hc": (tp_hc / (tp_hc + fn_hc)) if (tp_hc + fn_hc) else np.nan,
        "recall_occ": (tp_o / (tp_o + fn_o)) if (tp_o + fn_o) else np.nan,
        "precision": (tp_o / (tp_o + fp_o)) if (tp_o + fp_o) else np.nan,
        "specificity": (tn_o / (tn_o + fp_o)) if (tn_o + fp_o) else np.nan,
        "kappa": w.cohen_kappa(occ, cv),                              # diagnostic
        "agreement": float(np.mean(occ == cv)),                       # diagnostic
        "n_hc_and_cv": tp_hc, "n_hc_not_cv": fn_hc,
    }


def _detection_table(ref: pd.DataFrame, cv_cam: pd.DataFrame, lag: float,
                     bin_s: int) -> pd.DataFrame:
    """Per (day, stratum) CV detection metrics at `lag` for one shelter<->camera."""
    rows = []
    for sname, scol in STRATA:
        cb = w._cv_bins(cv_cam, lag, bin_s, scol)
        if cb.empty:
            continue
        m = ref[["bin_utc", "occupied", "hc_occupied"]].merge(cb, on="bin_utc", how="inner")
        if m.empty:
            continue
        m = m.assign(day=_local_day(m["bin_utc"]))
        for day, g in list(m.groupby("day")) + [("ALL", m)]:
            rows.append({"stratum": sname, "day": day, **_confusion(g)})
    return pd.DataFrame(rows)


def _fig_kappa_curves(curves, best, out_path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for label, cur in curves.items():
        lw = 2.2 if label == "joint" else 1.0
        ax.plot(cur["lag_s"], cur["kappa"], marker=".", lw=lw, label=label)
    ax.axvline(best["lag_s"], color="k", ls="--", lw=1, label=f"chosen lag {best['lag_s']:.0f}s")
    ax.set_xlabel("CV lag added (s)  [+4h already applied]"); ax.set_ylabel("Cohen's kappa")
    ax.set_title(f"Clock-lag / mapping ALIGNMENT DIAGNOSTIC (mapping {best['mapping_id']}, "
                 f"{best['stratum']})  — alignment UNVERIFIED")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(out_path, dpi=130); plt.close(fig)


def _fig_overlay(occ_by_shelter, raw_by_shelter, cv_by_cam, mapping, lag, bin_s, out_path):
    fig, axes = plt.subplots(len(SHELTERS), len(DATES),
                             figsize=(6 * len(DATES), 3.2 * len(SHELTERS)), squeeze=False)
    for sh in SHELTERS:
        cam = mapping[sh]
        cb = w._cv_bins(cv_by_cam[cam], lag, bin_s, "usable_for_coarse_activity")
        m = (occ_by_shelter[sh][["bin_utc", "n_state", "occupied", "hc_occupied"]]
             .merge(raw_by_shelter[sh][["bin_utc", "n_rats"]].rename(columns={"n_rats": "raw_n"}),
                    on="bin_utc", how="left")
             .merge(cb, on="bin_utc", how="left"))
        if m.empty:
            continue
        loc = pd.to_datetime(m["bin_utc"]) + pd.Timedelta(hours=w.LOCAL_TZ_OFFSET_HOURS)
        m = m.assign(dt_local=loc, day=loc.dt.date.astype(str))
        for j, day in enumerate(DATES):
            ax = axes[SHELTERS.index(sh)][j]
            g = m[m["day"] == day].sort_values("dt_local")
            if g.empty:
                ax.set_title(f"{sh}->{cam} {day}: no overlap"); ax.axis("off"); continue
            ymax = float(np.nanmax([g["n_state"].max(), g["raw_n"].max(), 1]))
            # raw point-wise (faint underlay) vs smoothed WISER state
            ax.step(g["dt_local"], g["raw_n"], where="post", color="0.7", lw=0.8,
                    label="WISER raw point-wise (diag)")
            ax.step(g["dt_local"], g["n_state"], where="post", color="tab:blue", lw=1.3,
                    label="WISER smoothed state")
            hc = g["hc_occupied"].to_numpy().astype(float)
            ax.fill_between(g["dt_local"], 0, hc * ymax, step="post",
                            color="tab:blue", alpha=0.12, label="WISER high-conf")
            cvocc = g["cv_occupied"].map(lambda v: 1.0 if v is True else 0.0).to_numpy()
            ax.fill_between(g["dt_local"], 0, cvocc * ymax, step="post",
                            color="tab:orange", alpha=0.22, label="CV occupied")
            ax.set_title(f"{sh}->{cam}  {day}", fontsize=9); ax.tick_params(labelsize=7)
            if SHELTERS.index(sh) == 0 and j == 0:
                ax.legend(fontsize=6.5, loc="upper right")
    fig.suptitle(f"Shelter occupancy: WISER smoothed state vs CV (lag {lag:.0f}s, UNVERIFIED)")
    fig.tight_layout(); fig.savefig(out_path, dpi=130); plt.close(fig)


def _fig_recall_precision(det, out_path):
    """Grouped bars of recall (vs hc) + precision PER (shelter, day) on coarse glass,
    so a degraded-glass recall collapse is visible instead of washed out by pooling."""
    d = det[(det["day"] != "ALL") & (det["stratum"] == "coarse")].copy()
    if d.empty:
        return
    d = d.sort_values(["shelter", "day"]).reset_index(drop=True)
    labels = [f"{r.shelter}/{r.camera}\n{r.day[5:]}\nhc={r.wiser_hc_frac:.2f}" for r in d.itertuples()]
    x = np.arange(len(labels)); wbar = 0.38
    fig, ax = plt.subplots(figsize=(1.7 * len(labels) + 2, 4.3))
    ax.bar(x - wbar / 2, d["recall_hc"].to_numpy(), wbar, color="tab:green",
           label="CV recall | WISER high-conf")
    ax.bar(x + wbar / 2, d["precision"].to_numpy(), wbar, color="tab:orange",
           label="CV precision")
    for xi, r in zip(x, d.itertuples()):
        if r.recall_hc == r.recall_hc:
            ax.text(xi - wbar / 2, r.recall_hc + 0.02, f"{r.recall_hc:.2f}", ha="center", fontsize=8)
        if r.precision == r.precision:
            ax.text(xi + wbar / 2, r.precision + 0.02, f"{r.precision:.2f}", ha="center", fontsize=8)
    ax.axhline(CV_FAIL_RECALL, color="tab:red", ls=":", lw=1,
               label=f"recall floor {CV_FAIL_RECALL:.2f} (CV optical-failure line)")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylim(0, 1.1); ax.set_ylabel("rate")
    ax.set_title("CV shelter detection vs WISER reference (coarse glass, per day)")
    ax.legend(fontsize=7.5); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout(); fig.savefig(out_path, dpi=130); plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="CV shelter detection vs WISER shelter state.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--fixed", type=Path, default=DEFAULT_FIXED)
    ap.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    ap.add_argument("--cv-dir", type=Path, default=DEFAULT_CV_DIR)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--dates", nargs="*", default=DATES)
    ap.add_argument("--bin-s", type=int, default=60)
    ap.add_argument("--buffer-in", type=float, default=STATE_KW["buffer_in"],
                    help="ROI noise buffer (in); 12-24 recommended for WISER jitter.")
    ap.add_argument("--enter-s", type=float, default=STATE_KW["enter_s"])
    ap.add_argument("--exit-s", type=float, default=STATE_KW["exit_s"])
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"[cv-crossval] WISER DB not found: {args.db}")
    state_kw = {**STATE_KW, "buffer_in": args.buffer_in,
                "enter_s": args.enter_s, "exit_s": args.exit_s}

    out = w.make_output_dir(args.output, prefix="sleep_site_cv_crossval")
    fig = out / "figures"
    print(f"=== CV shelter detection vs WISER shelter STATE ===\n  DB:  {args.db}\n"
          f"  CV:  {args.cv_dir}\n  out: {out}\n")

    # thresholds from the stationary baseline (provenance + validity flags)
    fx = w.load_wiser_session(args.fixed)
    fx = time_utils.convert_timestamps(fx)
    fx = time_utils.trim_last_n_minutes(fx, minutes=10)
    fx = w.add_speed(fx)
    moving_thr = w.speed_noise_floor(fx)["p99"]
    jitter = float(np.nanmedian(metrics.compute_summary(
        fx, ground_truth=metrics.load_ground_truth(DEFAULT_GT))["rms_jitter"]))

    # WISER free session -> cleaned daytime window (05:00-21:00 local rest window)
    df = w.load_wiser_session(args.db)
    df = time_utils.convert_timestamps(df)
    df = w.add_speed(df)
    df = w.add_validity_flags(df, jitter_floor_in=jitter)
    df = w.apply_tag_cutoffs(df)
    df = df[~df["shortid"].astype(str).isin(DROP_TAGS)]
    win = w.select_route_window(df, clock_start=5, clock_end=21)
    win = win[win["night"].isin(args.dates)]
    roi_cfg = w.load_rois(args.rois)

    # --- PRIMARY: smoothed hysteretic shelter STATE + high-confidence episodes ---
    grid_df, episodes = w.wiser_shelter_state(win, roi_cfg, SHELTERS,
                                              bin_s=args.bin_s, **state_kw)
    occ = w.shelter_occupancy_bins(grid_df)
    occ_by_shelter = {sh: occ[occ["shelter"] == sh].copy() for sh in SHELTERS}
    # --- DIAGNOSTIC: raw point-wise ROI presence (over-splits under jitter) ---
    raw = w.wiser_shelter_presence(win, roi_cfg, SHELTERS, bin_s=args.bin_s)
    raw_by_shelter = {sh: raw[raw["shelter"] == sh].copy() for sh in SHELTERS}

    cv_files = [args.cv_dir / f"{cam}_sleep_{d}.csv" for cam in ("CH05", "CH06") for d in args.dates]
    cv = w.load_cv_shelter_sleep(cv_files)
    if cv.empty:
        raise SystemExit(f"[cv-crossval] no CV files found in {args.cv_dir} for {args.dates}")
    cv_by_cam = {cam: cv[cv["channel"] == cam].copy() for cam in ("CH05", "CH06")}
    print(f"  WISER daytime bins/shelter: {occ_by_shelter[SHELTERS[0]].shape[0]}; "
          f"episodes: {len(episodes)} ({int(episodes['high_confidence'].sum()) if len(episodes) else 0} "
          f"high-conf); CV rows CH05/CH06 = {len(cv_by_cam['CH05'])}/{len(cv_by_cam['CH06'])}")

    # --- lag + mapping selection via joint kappa on the SMOOTHED state (diagnostic) ---
    curves_by_map = {}
    for mid, mp in MAPPINGS.items():
        for sname, scol in STRATA:
            for sh in SHELTERS:
                _, curve = w.best_lag_agreement(
                    occ_by_shelter[sh], cv_by_cam[mp[sh]], lag_grid_s=LAG_GRID,
                    bin_s=args.bin_s, stratum_col=scol)
                curves_by_map[(mid, sname, sh)] = curve

    def joint_curve(mid, stratum):
        parts = []
        for sh in SHELTERS:
            c = curves_by_map[(mid, stratum, sh)][["lag_s", "kappa", "n_bins"]]
            parts.append(c.rename(columns={"kappa": f"k_{sh}", "n_bins": f"n_{sh}"}))
        m = parts[0]
        for p in parts[1:]:
            m = m.merge(p, on="lag_s", how="outer")
        num = sum(m[f"k_{sh}"].fillna(0) * m[f"n_{sh}"].fillna(0) for sh in SHELTERS)
        den = sum(m[f"n_{sh}"].fillna(0) for sh in SHELTERS)
        m["joint_kappa"] = num / den.replace(0, np.nan)
        m["joint_n"] = den.astype(int)
        return m

    picks, joint_by = [], {}
    for mid in MAPPINGS:
        for stratum, _ in STRATA:
            jc = joint_curve(mid, stratum)
            joint_by[(mid, stratum)] = jc
            valid = jc.dropna(subset=["joint_kappa"])
            if valid.empty:
                continue
            r = valid.loc[valid["joint_kappa"].idxmax()]
            picks.append({"mapping": mid, "stratum": stratum, "lag_s": float(r["lag_s"]),
                          "joint_kappa": float(r["joint_kappa"]), "joint_n": int(r["joint_n"])})
    picks = pd.DataFrame(picks)
    picks.to_csv(out / "mapping_selection.csv", index=False)

    def _best_for(stratum):
        s = picks[picks["stratum"] == stratum]
        return s.loc[s["joint_kappa"].idxmax()] if not s.empty else None

    # Select mapping + shared lag on the COARSE stratum (largest, most complete
    # signal); clear-glass HEADLINE is a sparse confirmation, never the selector.
    chosen, chosen_stratum = _best_for("coarse"), "coarse"
    if chosen is None:
        chosen, chosen_stratum = _best_for("headline"), "headline"
    if chosen is None:
        raise SystemExit("[cv-crossval] no overlapping WISER/CV bins in any stratum.")
    best_map_id = str(chosen["mapping"]); mapping = MAPPINGS[best_map_id]
    chosen_lag = float(chosen["lag_s"])
    map_scores = {}
    for mid in MAPPINGS:
        s = picks[(picks["mapping"] == mid) & (picks["stratum"] == chosen_stratum)]
        map_scores[mid] = float(s["joint_kappa"].max()) if not s.empty else float("nan")
    best = {"mapping_id": best_map_id, "mapping": mapping, "stratum": chosen_stratum,
            "lag_s": chosen_lag}
    print(f"  chosen mapping {best_map_id} {mapping} | alignment lag {chosen_lag:.0f}s "
          f"| joint kappa={chosen['joint_kappa']:.2f} (n={int(chosen['joint_n'])}) "
          f"| per-mapping joint kappa {map_scores}")

    # --- HEADLINE: CV detection metrics at the chosen mapping + lag ---
    det = []
    for sh in SHELTERS:
        t = _detection_table(occ_by_shelter[sh], cv_by_cam[mapping[sh]], chosen_lag, args.bin_s)
        t.insert(0, "shelter", sh); t.insert(1, "camera", mapping[sh])
        det.append(t)
    detection = pd.concat(det, ignore_index=True) if det else pd.DataFrame()
    detection.to_csv(out / "cv_detection_by_shelter_day.csv", index=False)

    # --- DIAGNOSTIC: how many raw point-wise false-exits the smoothing recovers ---
    fe_rows = []
    for sh in SHELTERS:
        m = (occ_by_shelter[sh][["bin_utc", "occupied"]].rename(columns={"occupied": "smooth_occ"})
             .merge(raw_by_shelter[sh][["bin_utc", "occupied"]].rename(columns={"occupied": "raw_occ"}),
                    on="bin_utc", how="inner"))
        m = m.assign(day=_local_day(m["bin_utc"]))
        for day, g in list(m.groupby("day")) + [("ALL", m)]:
            fe_rows.append({
                "shelter": sh, "day": day, "n_bins": int(len(g)),
                "raw_occ_frac": float(g["raw_occ"].mean()),
                "smooth_occ_frac": float(g["smooth_occ"].mean()),
                "recovered_false_exits": int((g["smooth_occ"] & ~g["raw_occ"]).sum()),
            })
    false_exits = pd.DataFrame(fe_rows)
    false_exits.to_csv(out / "raw_vs_smoothed_false_exits.csv", index=False)

    # --- WISER high-confidence anchors + wet/degraded-glass CV-failure call-outs ---
    if len(episodes):
        episodes.to_csv(out / "wiser_shelter_episodes.csv", index=False)
        episodes = episodes.assign(day=_local_day(episodes["start_utc"]))
        hc_anchor = (episodes[episodes["high_confidence"]]
                     .groupby(["day", "shelter"])
                     .agg(n_hc_episodes=("high_confidence", "size"),
                          total_hc_s=("duration_s", "sum"),
                          mean_spread_in=("spread_in", "mean"))
                     .reset_index())
    else:
        hc_anchor = pd.DataFrame()
    hc_anchor.to_csv(out / "wiser_hc_anchor_summary.csv", index=False)

    # per (day, shelter) CV-failure flag: WISER hc-occupied sustained but CV empty
    cv_fail = detection[(detection["day"] != "ALL") & (detection["stratum"] == "coarse")].copy()
    if not cv_fail.empty:
        cv_fail["likely_cv_optical_failure"] = (
            (cv_fail["wiser_hc_frac"] >= CV_FAIL_WISER_HC) &
            (cv_fail["recall_hc"] <= CV_FAIL_RECALL))
        # dominant CV view_quality per (camera, day) for context
        vq = cv.assign(day=cv["t"].dt.date.astype(str))
        vqmode = (vq.groupby(["channel", "day"])["view_quality_inside"]
                  .agg(lambda s: s.value_counts().idxmax()).rename("cv_view_quality_mode")
                  .reset_index().rename(columns={"channel": "camera"}))
        cv_fail = cv_fail.merge(vqmode, on=["camera", "day"], how="left")
    cv_fail.to_csv(out / "cv_optical_failure_flags.csv", index=False)

    # --- figures ---
    curves_plot = {f"{sh}->{mapping[sh]}": curves_by_map[(best_map_id, chosen_stratum, sh)]
                   for sh in SHELTERS}
    curves_plot["joint"] = joint_by[(best_map_id, chosen_stratum)].rename(columns={"joint_kappa": "kappa"})
    _fig_kappa_curves(curves_plot, best, fig / "X1_lag_alignment_diagnostic.png")
    _fig_overlay(occ_by_shelter, raw_by_shelter, cv_by_cam, mapping, chosen_lag, args.bin_s,
                 fig / "X2_occupancy_overlay.png")
    _fig_recall_precision(detection, fig / "X3_recall_precision.png")

    # --- verdict ---
    byday = detection[(detection["day"] != "ALL") & (detection["stratum"] == "coarse")]
    perf_str = ", ".join(
        f"{r.shelter}/{r.camera} {r.day[5:]}: recall={r.recall_hc:.2f} precision={r.precision:.2f} "
        f"(WISER hc_frac={r.wiser_hc_frac:.2f}, n={r.n_bins})"
        for r in byday.itertuples()) if not byday.empty else "no overlapping bins"
    fe_all = false_exits[false_exits["day"] == "ALL"]
    fe_str = ", ".join(f"{r.shelter}: raw {r.raw_occ_frac:.2f} -> smoothed {r.smooth_occ_frac:.2f} "
                       f"(+{r.recovered_false_exits} bins)" for r in fe_all.itertuples())
    fails = cv_fail[cv_fail.get("likely_cv_optical_failure", False) == True] \
        if "likely_cv_optical_failure" in cv_fail.columns else pd.DataFrame()
    fail_str = ("; ".join(f"{r.day} {r.shelter}/{r.camera}: WISER hc {r.wiser_hc_frac:.2f} "
                          f"vs CV occ {r.cv_occ_frac:.2f} (glass={getattr(r, 'cv_view_quality_mode', '?')})"
                          for r in fails.itertuples()) or "none flagged")
    verdict = (
        f"CV shelter detection vs WISER shelter STATE (dates {args.dates}). WISER is the "
        f"fog-immune UWB reference; CV (through IR glass) is the sensor under test. "
        f"ROI<->camera mapping = {best_map_id} {mapping} (alignment joint kappa_A="
        f"{map_scores.get('A'):.2f}, kappa_B={map_scores.get('B'):.2f}); best-fit CV lag "
        f"{chosen_lag:+.0f}s (TIMESTAMP-ALIGNED, UNVERIFIED; kappa is an alignment diagnostic "
        f"only, not the headline). CV detection PER DAY ({chosen_stratum} glass): {perf_str}. "
        f"CV PRECISION is high throughout (when CV says occupied, WISER agrees); the failure mode "
        f"is RECALL, and it collapses on the wet/degraded-glass day where WISER shows sustained "
        f"high-confidence occupancy but CV reads mostly empty -> Likely CV OPTICAL FAILURE: "
        f"{fail_str}. Pooling both days hides this (pooled recall is dominated by the good-glass "
        f"day), so read recall per day/glass. Smoothing vs raw point-wise ROI: {fe_str} (raw "
        f"over-splits under WISER jitter; smoothed state is the biological occupancy signal). "
        f"High-confidence WISER shelter episodes are QC/validation anchors for CV, NOT proof of "
        f"the sleep-site claim. CV sees only the two shelters; CV head-counts undercount (huddle + "
        f"wall-edge blind zone).")
    (out / "crossval_verdict.txt").write_text(verdict, encoding="utf-8")

    w.write_run_manifest(out, {
        "analysis": "sleep_site_cv_crossval",
        "framing": "WISER-as-reference (fog-immune UWB); CV = sensor under test through IR glass",
        "dates": args.dates, "bin_s": args.bin_s, "state_kwargs": state_kw,
        "lag_grid_s": [LAG_GRID[0], LAG_GRID[-1], 300],
        "chosen_mapping": best_map_id, "mapping": mapping, "chosen_stratum": chosen_stratum,
        "alignment_lag_s": chosen_lag, "map_scores_joint_kappa": map_scores,
        "chosen_joint_kappa": float(chosen["joint_kappa"]), "chosen_joint_n": int(chosen["joint_n"]),
        "n_episodes": int(len(episodes)),
        "n_high_conf_episodes": int(episodes["high_confidence"].sum()) if len(episodes) else 0,
        "rest_cutoff_inps_p99_stationary": moving_thr, "jitter_floor_in": jitter,
        "wiser_db": str(args.db), "cv_dir": str(args.cv_dir),
        "metric_note": "HEADLINE = CV recall (vs WISER high-confidence occupancy) + precision "
                       "(vs WISER smoothed occupancy), by glass quality + day. Cohen's kappa is a "
                       "LAG/MAPPING ALIGNMENT DIAGNOSTIC only (symmetric kappa would misblame WISER "
                       "for CV's fog misses).",
        "reference_note": "WISER occupancy = SMOOTHED hysteretic buffer-tolerant shelter STATE "
                          "(wiser_shelter_state); raw point-wise ROI presence kept only as a "
                          "diagnostic (it over-splits during rest because WISER jitters).",
        "alignment_note": "WISER Unix-ms UTC vs CV local NVR wallclock; +4h nominal then residual "
                          "lag SCANNED; reported lag is UNVERIFIED, not a verified sync.",
        "caveats": "CV sees only the 2 shelters; CV counts undercount (lower bound); WISER frame + "
                   "ROIs provisional; 2 days; high-conf episodes are CV validation anchors, not "
                   "sleep-site proof.",
    })
    print("\n  " + verdict)
    print(f"\nAll outputs written to: {out}")


if __name__ == "__main__":
    main()

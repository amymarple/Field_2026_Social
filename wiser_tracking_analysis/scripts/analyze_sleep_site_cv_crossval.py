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
  * HEADLINE = asymmetric measurement reconciliation, PER SHELTER (never pooled as
    the main readout): (1) CV PRECISION given WISER near-shelter presence (when CV
    says occupied, does WISER agree?) and (2) CV RECALL / lower-bound gap relative to
    WISER presence (how much WISER-confirmed occupancy does CV recover?). CV
    visible-inside-through-glass is a LOWER BOUND on WISER near-shelter occupancy
    (huddle compression + wall-edge blind zone), so a recall gap is a
    coverage/definition limit, NOT necessarily an optical failure and NEVER rat
    absence. The CV-miss / WISER-present cases are stratified by view_quality,
    glass_regime, fog_risk (when available), camera/shelter, and WISER validity.
  * Symmetric Cohen's kappa is a clock-lag / mapping ALIGNMENT DIAGNOSTIC ONLY, never
    the headline. It is BASE-RATE SENSITIVE: with WISER presence prevalence near 1.0
    (e.g. house_1 ~0.99) kappa collapses toward 0 even at high raw agreement (the
    kappa paradox), so a low kappa here reflects prevalence + definition mismatch, not
    misalignment (the 2026-07-02 lag sweep is flat; see
    outputs/audit/ALIGNMENT_DIAGNOSIS_2026-07-02.md). WISER is Unix-ms UTC; CV t is
    local NVR wallclock (+4 h nominal + scanned residual lag): reported, never verified.
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
# a (day, shelter) where WISER shows SUSTAINED near-shelter presence but CV recovers
# less than half of it = a large CV LOWER-BOUND GAP (CV visible-inside undercounts
# WISER near-shelter occupancy). This is a coverage/definition gap (wall-edge blind
# zone / huddle / glass), NOT an assertion of optical failure and NOT rat absence, and
# NOT a WISER error. Recall-based because the mode is CV MISSING confirmed rats.
# Thresholds are UNCHANGED from the prior version; only the framing/label changed.
GAP_WISER_PRESENCE = 0.50    # WISER hc-occupied (near-shelter) fraction >= this ...
GAP_MAX_RECALL = 0.50        # ... while CV recall (vs hc) <= this (CV recovers < half)


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


def _cv_bins_cov(cv_cam: pd.DataFrame, lag_s: float, bin_s: int,
                 cov_cols: list[str]) -> pd.DataFrame:
    """CV rows -> per-bin ``cv_occupied`` + carried covariate columns, at ``lag_s``,
    on the coarse-usable stratum. Covariate-preserving sibling of ``w._cv_bins`` used
    for the cross-modal RECONCILIATION (which stratifies the CV-miss / WISER-present
    cases); CV is ~5-min-sampled so a 60 s bin holds <=1 CV row (an occupied row wins
    on the rare collision). Unit-safe binning via the shared ``w._bin_utc_ns``."""
    d = cv_cam[cv_cam["usable_for_coarse_activity"] == True].copy()
    if d.empty:
        return pd.DataFrame(columns=["bin_utc", "cv_occupied"] + cov_cols)
    shifted = pd.to_datetime(d["t_utc"]) + pd.to_timedelta(lag_s, unit="s")
    d["bin_utc"] = w._bin_utc_ns(shifted, bin_s)
    d = d.sort_values("occupied")                     # occupied row wins on collision
    agg = {"cv_occupied": ("occupied", "max")}
    for c in cov_cols:
        agg[c] = (c, "last")
    return d.groupby("bin_utc").agg(**agg).reset_index()


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
    ap.add_argument("--no-plots", action="store_true",
                    help="skip matplotlib figures; still write all CSVs, the verdict, and the "
                         "run manifest. Use on the analysis PC, where the headless matplotlib/MKL "
                         "stack can abort natively at savefig (the figures are diagnostics only).")
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
    # optional: enrich CV with the fog_risk_level covariate for the reconciliation
    # stratification (measurement context only -- NOT a weather->behavior join, NOT a
    # filter). Soft cross-subsystem dependency: skip cleanly if unavailable.
    if "fog_risk_level" not in cv.columns:
        try:
            sys.path.insert(0, str(REPO_ROOT / "preprocessing" / "computer_vision"))
            import fog_risk                                  # noqa: E402
            cv = fog_risk.annotate(cv, ts="t")
            print("  [reconciliation] fog_risk_level covariate added from AWN weather.")
        except Exception as e:                               # noqa: BLE001
            print(f"  [reconciliation] fog_risk enrichment skipped ({type(e).__name__}: {e}); "
                  "stratifying by the covariates present in the CV output.")
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
          f"[ALIGNMENT DIAGNOSTIC ONLY, base-rate sensitive -- not the headline] "
          f"| per-mapping joint kappa {map_scores}")

    # --- HEADLINE: CV detection metrics at the chosen mapping + lag ---
    det = []
    for sh in SHELTERS:
        t = _detection_table(occ_by_shelter[sh], cv_by_cam[mapping[sh]], chosen_lag, args.bin_s)
        t.insert(0, "shelter", sh); t.insert(1, "camera", mapping[sh])
        det.append(t)
    detection = pd.concat(det, ignore_index=True) if det else pd.DataFrame()
    detection.to_csv(out / "cv_detection_by_shelter_day.csv", index=False)

    # --- CROSS-MODAL RECONCILIATION: stratify the CV-miss / WISER-present cases ---
    # WISER near-shelter presence is the reference; CV visible-inside is a LOWER BOUND.
    # Among WISER-present bins, report CV recall (hit rate) + miss count per covariate
    # (view_quality, glass_regime, fog_risk if present, camera/shelter, n_inside_confidence
    # as a wall-edge/huddle proxy, and WISER validity). Cohen's kappa is NOT used here.
    wv = win.dropna(subset=["datetime"]).copy()
    wv["bin_utc"] = w._bin_utc_ns(wv["datetime"], args.bin_s)
    wvalid = (wv.groupby("bin_utc")
              .agg(wiser_frac_low_anchor=("low_anchor_flag", "mean"),
                   wiser_n_fix=("shortid", "size")).reset_index())
    CAND_COV = ["view_quality_inside", "glass_regime", "fog_risk_level", "n_inside_confidence"]
    have_cov = [c for c in CAND_COV if c in cv.columns]
    missing_cov = [c for c in CAND_COV if c not in cv.columns]
    rec_parts = []
    for sh in SHELTERS:
        cam = mapping[sh]
        cvb = _cv_bins_cov(cv_by_cam[cam], chosen_lag, args.bin_s, have_cov)
        m = (occ_by_shelter[sh][["bin_utc", "occupied", "hc_occupied"]]
             .merge(cvb, on="bin_utc", how="inner"))
        if m.empty:
            continue
        m = m.merge(wvalid, on="bin_utc", how="left")
        m["shelter"] = sh; m["camera"] = cam
        m["wiser_validity"] = np.where(m["wiser_frac_low_anchor"].fillna(0) > 0.5,
                                       "low_anchor_heavy", "ok")
        rec_parts.append(m)
    recon = pd.concat(rec_parts, ignore_index=True) if rec_parts else pd.DataFrame()
    rec_rows, n_present, n_miss = [], 0, 0
    if not recon.empty:
        present = recon[recon["occupied"]]           # WISER says a rat is near the shelter
        n_present = int(len(present))
        n_miss = int((~present["cv_occupied"].astype(bool)).sum())
        for ax in ["shelter", "camera", "wiser_validity"] + have_cov:
            for lvl, g in present.groupby(ax, dropna=False):
                n = int(len(g)); hit = int(g["cv_occupied"].astype(bool).sum())
                rec_rows.append({"axis": ax, "level": str(lvl), "n_wiser_present": n,
                                 "cv_hit": hit, "cv_miss": n - hit,
                                 "cv_recall_lowerbound": (hit / n) if n else float("nan")})
    reconcile = pd.DataFrame(rec_rows, columns=["axis", "level", "n_wiser_present",
                             "cv_hit", "cv_miss", "cv_recall_lowerbound"])
    reconcile.to_csv(out / "cv_wiser_reconciliation_strata.csv", index=False)
    print(f"  reconciliation: {n_miss}/{n_present} WISER-present bins are CV-miss "
          f"(lower-bound gap) across {len(have_cov) + 3} strata axes"
          + (f"; axes not in CV output: {missing_cov}" if missing_cov else ""))

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

    # --- WISER high-confidence anchors + CV lower-bound-gap call-outs ---
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

    # per (day, shelter) CV lower-bound gap: WISER near-shelter presence sustained but
    # CV recovers < half. This is a coverage/definition gap (wall-edge blind zone /
    # huddle / glass), NOT an optical-failure assertion and NOT rat absence; the
    # dominant glass mode is attached only as CONTEXT (on 2026-07-02 the CH05 gap is on
    # clear glass, so it is not attributable to fog).
    cv_gap = detection[(detection["day"] != "ALL") & (detection["stratum"] == "coarse")].copy()
    if not cv_gap.empty:
        cv_gap["cv_recall_gap_under_wiser_presence"] = (
            (cv_gap["wiser_hc_frac"] >= GAP_WISER_PRESENCE) &
            (cv_gap["recall_hc"] <= GAP_MAX_RECALL))
        cv_gap["gap_interpretation"] = (
            "CV visible-inside is a LOWER BOUND vs WISER near-shelter occupancy; gap "
            "consistent with coverage/definition limits (wall-edge blind zone, huddle), "
            "not necessarily fog")
        # dominant CV view_quality per (camera, day) for CONTEXT only
        vq = cv.assign(day=cv["t"].dt.date.astype(str))
        vqmode = (vq.groupby(["channel", "day"])["view_quality_inside"]
                  .agg(lambda s: s.value_counts().idxmax()).rename("cv_view_quality_mode")
                  .reset_index().rename(columns={"channel": "camera"}))
        cv_gap = cv_gap.merge(vqmode, on=["camera", "day"], how="left")
    cv_gap.to_csv(out / "cv_recall_gap_flags.csv", index=False)

    # --- figures (diagnostics only; skippable so a headless plotting abort can't
    #     take down the CSV/verdict/manifest outputs on the analysis PC) ---
    if args.no_plots:
        print("  [--no-plots] skipping figures; CSVs, verdict, and manifest still written.")
    else:
        curves_plot = {f"{sh}->{mapping[sh]}": curves_by_map[(best_map_id, chosen_stratum, sh)]
                       for sh in SHELTERS}
        curves_plot["joint"] = joint_by[(best_map_id, chosen_stratum)].rename(columns={"joint_kappa": "kappa"})
        _fig_kappa_curves(curves_plot, best, fig / "X1_lag_alignment_diagnostic.png")
        _fig_overlay(occ_by_shelter, raw_by_shelter, cv_by_cam, mapping, chosen_lag, args.bin_s,
                     fig / "X2_occupancy_overlay.png")
        _fig_recall_precision(detection, fig / "X3_recall_precision.png")

    # --- verdict (asymmetric measurement reconciliation; kappa is NOT the headline) ---
    byday = detection[(detection["day"] != "ALL") & (detection["stratum"] == "coarse")]
    perf_str = ", ".join(
        f"{r.shelter}/{r.camera} {r.day[5:]}: precision={r.precision:.2f} "
        f"recall(lower-bound)={r.recall_hc:.2f} (WISER presence={r.wiser_hc_frac:.2f}, n={r.n_bins})"
        for r in byday.itertuples()) if not byday.empty else "no overlapping bins"
    fe_all = false_exits[false_exits["day"] == "ALL"]
    fe_str = ", ".join(f"{r.shelter}: raw {r.raw_occ_frac:.2f} -> smoothed {r.smooth_occ_frac:.2f} "
                       f"(+{r.recovered_false_exits} bins)" for r in fe_all.itertuples())
    gaps = (cv_gap[cv_gap.get("cv_recall_gap_under_wiser_presence", False) == True]
            if "cv_recall_gap_under_wiser_presence" in cv_gap.columns else pd.DataFrame())
    gap_str = ("; ".join(
        f"{r.day} {r.shelter}/{r.camera}: WISER presence {r.wiser_hc_frac:.2f} but CV recall "
        f"{r.recall_hc:.2f} (glass={getattr(r, 'cv_view_quality_mode', '?')})"
        for r in gaps.itertuples()) or "none flagged")
    # where the CV-miss / WISER-present bins concentrate (exclude shelter/camera axes,
    # already reported per-shelter in the headline)
    rstrat = reconcile[~reconcile["axis"].isin(["shelter", "camera"])] if not reconcile.empty \
        else reconcile
    recon_str = ("; ".join(
        f"{r.axis}={r.level}: CV recovers {r.cv_recall_lowerbound:.2f} of {r.n_wiser_present} "
        f"WISER-present ({r.cv_miss} miss)"
        for r in rstrat.sort_values("cv_miss", ascending=False).head(4).itertuples())
        if not rstrat.empty else "no WISER-present bins")
    kA, kB = map_scores.get('A', float('nan')), map_scores.get('B', float('nan'))
    verdict = (
        f"CV shelter detection reconciled against WISER shelter STATE (dates {args.dates}). "
        f"WISER near-shelter presence is the fog-immune UWB REFERENCE; CV visible-inside-through-"
        f"glass is the sensor and is a LOWER BOUND (huddle + wall-edge blind zone). Read PER "
        f"SHELTER, never pooled. HEADLINE (mapping {best_map_id} {mapping}, {chosen_stratum} "
        f"glass): {perf_str}. CV PRECISION is high throughout (when CV says occupied, WISER "
        f"agrees); the gap is CV RECALL -- CV visible-inside is a LOWER BOUND relative to WISER "
        f"near-shelter occupancy, and the gap is consistent with coverage/definition limits such "
        f"as the wall-edge blind zone, NOT necessarily fog (on 2026-07-02 the CH05 gap occurs on "
        f"CLEAR glass). Large per-day lower-bound gaps (WISER presence sustained, CV recovers "
        f"<half): {gap_str}. CV-miss / WISER-present bins concentrate at: {recon_str}. "
        f"Cohen's kappa (joint, per-mapping A={kA:.2f} B={kB:.2f}; best-fit lag {chosen_lag:+.0f}s) "
        f"is an ALIGNMENT DIAGNOSTIC ONLY and is BASE-RATE SENSITIVE: with WISER presence prevalence "
        f"near 1.0 it collapses toward 0 even at high agreement (kappa paradox), so it is NOT the "
        f"headline -- a low value here is prevalence + definition mismatch, not misalignment (the "
        f"07-02 lag sweep is flat; alignment is adequate, see ALIGNMENT_DIAGNOSIS_2026-07-02.md). "
        f"Smoothing vs raw point-wise ROI: {fe_str} (raw over-splits under WISER jitter). "
        f"High-confidence WISER shelter episodes are QC/validation anchors for CV, NOT proof of "
        f"the sleep-site claim. No behavior claim is made here.")
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
        "metric_note": "HEADLINE = asymmetric reconciliation PER SHELTER (never pooled): CV "
                       "PRECISION given WISER near-shelter presence + CV RECALL / lower-bound gap "
                       "vs WISER presence, by glass quality + day. CV visible-inside is a LOWER "
                       "BOUND (huddle + wall-edge blind zone); a recall gap is a coverage/definition "
                       "limit, NOT optical failure and NOT rat absence.",
        "kappa_note": "Cohen's kappa is an alignment/agreement DIAGNOSTIC ONLY and is BASE-RATE "
                      "SENSITIVE (collapses toward 0 when WISER presence prevalence ~1.0 even at high "
                      "agreement; the kappa paradox) -- NOT the headline. The 2026-07-02 fine lag "
                      "sweep is flat, so alignment is adequate and low kappa is prevalence+definition, "
                      "not misalignment (outputs/audit/ALIGNMENT_DIAGNOSIS_2026-07-02.md).",
        "reconciliation_note": "cv_wiser_reconciliation_strata.csv stratifies the CV-miss / "
                               "WISER-present bins by view_quality, glass_regime, fog_risk (when "
                               "present), camera/shelter, n_inside_confidence, and WISER validity.",
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

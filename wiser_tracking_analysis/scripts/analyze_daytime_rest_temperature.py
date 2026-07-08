r"""
analyze_daytime_rest_temperature.py — Direction 3 (Stage B): within-day rest-site
relocation and its relationship to temperature / time-of-day / weather.

Question: beyond "do rats sleep in the same place across days?", does daytime rest-site
choice follow a REGULAR WITHIN-DAY pattern (e.g. morning near refuge/wall -> midday
inside a cooler shelter -> late-afternoon relocation), and is it temperature-linked?

Method (candidate, measurement-limited):
  * REST BOUTS = sustained low-speed periods (wiser_shelter... rest proxy; NOT ephys sleep),
    gap-aware. A WISER dropout is 'unknown', never 'awake'/'left'.
  * Each bout -> centroid, dominant ROI/zone, distance to house_1/house_2, day window,
    and (if weather present) outside-air temperature at bout start/midpoint.
  * Relocation EVENTS = between-bout centroid shift >=100 in OR shelter-identity /
    zone change (jitter-scale wiggles excluded).
  * Temperature is a COVARIATE ON BOTH PATHS: rain/wet also attenuates UWB, so we report
    per-animal-day DROPOUT and never read a wet-day 'disappearance' as a rest-site move.

Guardrails: WISER inch frame is UNVERIFIED -> ROI-identity + outside-temp/time PROXIES
only, no physical "shade/cooler-spot" claim. Field-log notes (6/29 "house may be too hot",
6/30 rain ~17:30) are HYPOTHESES, not labels. CV corroborates visible shelter-resident
periods only (lower bound; 2026-07-06 reconciliation). Language stays "temperature-linked"
/ "consistent with microclimate-driven relocation", never "temperature causes".

Read-only on the DB + AWN weather CSVs. Data outputs to
D:\Wiser_plot\direction3_temperature_relocation_<ts>\; the report is also written to
wiser_tracking_analysis/outputs/direction3_temperature_relocation/.
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

DEFAULT_DB = Path(r"D:\Reolink_record\audio_in\Wiser_backup\snapshots\1stcohort_2026_2026-07-01.sqlite")
DEFAULT_FIXED = Path(r"D:\Reolink_record\audio_in\Wiser_backup\snapshots\tag_reports_2026-06-30.sqlite")
DEFAULT_GT = PROJECT_ROOT / "configs" / "fixed_position_ground_truth.csv"
DEFAULT_ROIS = PROJECT_ROOT / "configs" / "wiser_rois.json"
DEFAULT_WEATHER = [
    Path(r"D:\Reolink_record\audio_in\weather_data\AWN-F8B3B78DEAC9-20260628-20260705.csv"),
    Path(r"D:\Reolink_record\audio_in\weather_data\AWN-F8B3B78DEAC9-20260628-20260629.csv"),
    Path(r"D:\Reolink_record\audio_in\weather_data\AWN-F8B3B78DEAC9-20260630-20260701.csv"),
]
DEFAULT_OUT_ROOT = Path(r"D:\Wiser_plot")
REPORT_DIR = PROJECT_ROOT / "outputs" / "direction3_temperature_relocation"
DROP_TAGS = {"12409"}
REST_START, REST_END = 5, 21
BIN_S = 60
ZONE_ORD = {"open": 5, "wall": 4, "resource": 3, "tunnel": 3, "refuge": 2, "shelter": 1}
# Field-log CONTEXT (hypotheses, NOT labels — FIELD_OBSERVATIONS.md circularity warning)
DAY_CONTEXT = {
    "2026-06-28": "warm ~22-23C (evening release ~19:25); partial day",
    "2026-06-29": "sunny/HOT ~30C; obs 11:48 'pile to sleep, prefer above metal/in shade, house may be too hot'; Sova removed 15:00",
    "2026-06-30": "sunny/humid HIGH ~34C; thunderstorm/rain ~17:30 (rats bolt to shelter); AM IR-condensation fogged glass",
}


def _entropy(counts) -> float:
    p = np.asarray([c for c in counts if c > 0], float)
    if p.sum() == 0:
        return float("nan")
    p = p / p.sum()
    return float(abs((p * np.log2(p)).sum()))   # abs -> avoid -0.0 for a single site


def _align_temp(bouts: pd.DataFrame, wx: pd.DataFrame) -> pd.DataFrame:
    """merge_asof outside-air weather onto bout start + midpoint (nearest, <=10 min).
    merge_asof drops the left index, so map back through a stable id column."""
    add_cols = ["temp_start_c", "temp_mid_c", "humidity_mid", "rain_mid_mmhr", "solar_mid_wm2"]
    b = bouts.copy().reset_index(drop=True)
    b["_id"] = np.arange(len(b))
    if b.empty or wx.empty or "temp_c" not in wx.columns:
        for c in add_cols:
            b[c] = np.nan
        return b.drop(columns=["_id"])
    wcols = [c for c in ["temp_c", "humidity", "rain_rate_mmhr", "solar_wm2"] if c in wx.columns]
    wsort = wx[["datetime_utc"] + wcols].dropna(subset=["datetime_utc"]).sort_values("datetime_utc")
    b["start_dt"] = pd.to_datetime(b["start_utc"])
    b["mid_dt"] = pd.to_datetime((b["start_utc"].astype("int64") + b["end_utc"].astype("int64")) // 2)
    s = pd.merge_asof(b[["_id", "start_dt"]].sort_values("start_dt"), wsort,
                      left_on="start_dt", right_on="datetime_utc",
                      direction="nearest", tolerance=pd.Timedelta("10min")).set_index("_id")
    m = pd.merge_asof(b[["_id", "mid_dt"]].sort_values("mid_dt"), wsort,
                      left_on="mid_dt", right_on="datetime_utc",
                      direction="nearest", tolerance=pd.Timedelta("10min")).set_index("_id")
    b = b.set_index("_id")
    b["temp_start_c"] = s["temp_c"] if "temp_c" in wcols else np.nan
    b["temp_mid_c"] = m["temp_c"] if "temp_c" in wcols else np.nan
    b["humidity_mid"] = m["humidity"] if "humidity" in wcols else np.nan
    b["rain_mid_mmhr"] = m["rain_rate_mmhr"] if "rain_rate_mmhr" in wcols else np.nan
    b["solar_mid_wm2"] = m["solar_wm2"] if "solar_wm2" in wcols else np.nan
    return b.reset_index(drop=True).drop(columns=["start_dt", "mid_dt"], errors="ignore")


def _window_temp(wx: pd.DataFrame) -> pd.DataFrame:
    """Mean outside-air temp / rain per (local date, window)."""
    if wx.empty or "temp_c" not in wx.columns:
        return pd.DataFrame(columns=["night", "window", "mean_temp_c", "mean_rain_mmhr"])
    d = wx.copy()
    d["night"] = d["datetime_local"].dt.date.astype(str)
    d["window"] = d["datetime_local"].dt.hour.map(w.day_window)
    d = d[d["window"] != "off_window"]
    aggkw = {"mean_temp_c": ("temp_c", "mean")}
    if "rain_rate_mmhr" in d.columns:
        aggkw["mean_rain_mmhr"] = ("rain_rate_mmhr", "mean")
    agg = d.groupby(["night", "window"]).agg(**aggkw).reset_index()
    if "mean_rain_mmhr" not in agg.columns:
        agg["mean_rain_mmhr"] = np.nan
    return agg


def _fig_timeline(seq, wx, events, nights, tags, out_path):
    """Per-day: each animal's per-WINDOW rest-site centroid_x over time (house_1/
    house_2 ref lines) with outside temp on a twin axis; relocation events marked."""
    ndays = len(nights)
    fig, axes = plt.subplots(ndays, 1, figsize=(11, 3.2 * ndays), squeeze=False)
    cmap = plt.get_cmap("tab10")
    tcol = {t: cmap(i % 10) for i, t in enumerate(tags)}
    h1x, h2x = 411.5, 613.6   # house_1 / house_2 x in the WISER inch frame (ref only)
    for r, night in enumerate(nights):
        ax = axes[r][0]
        ytrans = ax.get_yaxis_transform()   # x in axes-fraction, y in data (avoids datetime x=0)
        ax.axhline(h1x, color="0.6", ls="--", lw=0.8)
        ax.text(0.005, h1x, "house_1", fontsize=7, color="0.4", transform=ytrans, va="bottom")
        ax.axhline(h2x, color="0.6", ls="--", lw=0.8)
        ax.text(0.005, h2x, "house_2", fontsize=7, color="0.4", transform=ytrans, va="bottom")
        bn = seq[seq["night"] == night]
        for t in tags:
            gt = bn[bn["shortid"].astype(str) == str(t)].sort_values("window_order")
            if gt.empty:
                continue
            tloc = pd.to_datetime(gt["start_utc"].astype("int64")) + pd.Timedelta(hours=w.LOCAL_TZ_OFFSET_HOURS)
            ax.plot(tloc, gt["centroid_x"], "-o", ms=4, lw=1.0, color=tcol[t], label=str(t), alpha=0.85)
        ev = events[events["night"] == night] if not events.empty else events
        for _, e in ev.iterrows():
            et = pd.Timestamp(int(e["start_utc"])) + pd.Timedelta(hours=w.LOCAL_TZ_OFFSET_HOURS)
            ax.axvline(et, color="tab:red", ls=":", lw=0.6, alpha=0.5)
        # temp overlay
        if not wx.empty and "temp_c" in wx.columns:
            wd = wx[(wx["datetime_local"].dt.date.astype(str) == night)]
            wd = wd[(wd["datetime_local"].dt.hour >= REST_START) & (wd["datetime_local"].dt.hour < REST_END)]
            if not wd.empty:
                axt = ax.twinx()
                axt.plot(wd["datetime_local"], wd["temp_c"], color="tab:red", lw=1.4, alpha=0.6)
                axt.set_ylabel("outside T (C)", color="tab:red", fontsize=8)
                axt.tick_params(labelsize=7, colors="tab:red")
        ax.set_ylabel("rest centroid x (in)\n<-house_1  house_2->", fontsize=8)
        ax.set_title(f"{night} — {DAY_CONTEXT.get(night, '')}", fontsize=8.5)
        ax.tick_params(labelsize=7)
        if r == 0:
            ax.legend(fontsize=7, ncol=len(tags), loc="upper right", title="tag")
    fig.suptitle("Direction 3 (Stage B): within-day rest-site (centroid x) vs time + outside temp\n"
                 "red dotted = relocation event · WISER inch frame UNVERIFIED (relative only)", fontsize=10)
    fig.savefig(out_path, dpi=130, bbox_inches="tight"); plt.close(fig)


def _fig_convergence(conv, out_path):
    """Rest-zone entropy + max-shelter-share per (day, window) with mean temp."""
    if conv.empty:
        return
    nights = sorted(conv["night"].unique())
    order = [w0 for w0, _, _ in w.DAY_WINDOWS]
    fig, axes = plt.subplots(1, len(nights), figsize=(4.6 * len(nights), 4), squeeze=False, sharey=True)
    for j, night in enumerate(nights):
        ax = axes[0][j]
        g = conv[conv["night"] == night].set_index("window").reindex(order)
        x = np.arange(len(order))
        ax.plot(x, g["zone_entropy_bits"], "-o", color="tab:blue", label="rest-zone entropy (bits)")
        ax.plot(x, g["max_shelter_share"], "-s", color="tab:green", label="max animals in one shelter")
        ax.set_xticks(x); ax.set_xticklabels([o.replace("_", "\n") for o in order], fontsize=6.5)
        ax.set_title(f"{night}", fontsize=9); ax.grid(alpha=0.3)
        if not g["mean_temp_c"].isna().all():
            axt = ax.twinx()
            axt.plot(x, g["mean_temp_c"], color="tab:red", lw=1.3, alpha=0.6)
            axt.set_ylabel("mean T (C)", color="tab:red", fontsize=8); axt.tick_params(labelsize=7, colors="tab:red")
        if j == 0:
            ax.legend(fontsize=7, loc="upper left"); ax.set_ylabel("entropy / #animals")
    fig.suptitle("Rest-site convergence by window (low entropy / high share = converged)", fontsize=10)
    fig.tight_layout(); fig.savefig(out_path, dpi=130); plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="Direction 3 Stage B: within-day rest-site vs temperature.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--fixed", type=Path, default=DEFAULT_FIXED)
    ap.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    ap.add_argument("--weather", type=Path, nargs="*", default=DEFAULT_WEATHER)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"[rest-temp] WISER DB not found: {args.db}")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M")
    out = args.output / f"direction3_temperature_relocation_{ts}"
    fig = out / "figures"
    fig.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Direction 3 Stage B: within-day rest-site vs temperature ===\n  DB: {args.db}\n  out: {out}\n")

    # thresholds
    fx = w.load_wiser_session(args.fixed)
    fx = time_utils.convert_timestamps(fx)
    fx = time_utils.trim_last_n_minutes(fx, minutes=10)
    fx = w.add_speed(fx)
    moving_thr = w.speed_noise_floor(fx)["p99"]
    jitter = float(np.nanmedian(metrics.compute_summary(
        fx, ground_truth=metrics.load_ground_truth(DEFAULT_GT))["rms_jitter"]))
    print(f"  rest cutoff={moving_thr:.2f} in/s  jitter={jitter:.2f} in")

    # WISER -> cleaned daytime rest window
    df = w.load_wiser_session(args.db)
    df = time_utils.convert_timestamps(df)
    df = w.add_speed(df)
    df = w.add_validity_flags(df, jitter_floor_in=jitter)
    df = w.apply_tag_cutoffs(df)
    df = df[~df["shortid"].astype(str).isin(DROP_TAGS)]
    win = w.select_route_window(df, clock_start=REST_START, clock_end=REST_END)
    win = w.rest_mask(win, moving_thr_inps=moving_thr)
    roi_cfg = w.load_rois(args.rois)
    win = w.assign_roi(win, roi_cfg)
    nights = sorted(win["night"].unique())
    tags = sorted(win["shortid"].astype(str).unique())

    # weather
    wx = w.load_weather_multi(args.weather)
    print(f"  nights={nights} tags={tags}  weather rows={len(wx)}")

    # --- rest bouts + within-day sequence + relocation events ---
    bouts = w.rest_bouts(win, roi_cfg=roi_cfg, bin_s=BIN_S)
    bouts = _align_temp(bouts, wx)
    seq = w.within_day_sequence(win, roi_cfg)
    # within-day relocation from the per-window SITE sequence (rats rest ~90% of the
    # day, so speed-bouts collapse to ~1/day; relocation is a location change inside
    # one long rest state, resolved at window granularity here).
    events = w.relocation_events(seq, order_col="window_order", min_shift_in=100.0)
    bouts.to_csv(out / "rest_bouts_by_animal_day.csv", index=False)
    seq.to_csv(out / "within_day_rest_site_sequence.csv", index=False)
    events.to_csv(out / "relocation_events.csv", index=False)
    bouts.to_csv(out / "temperature_aligned_rest_bouts.csv", index=False)

    # --- per-animal-day DROPOUT (a wet day can attenuate UWB -> guards the 6/30 read) ---
    exp_bins = (REST_END - REST_START) * 3600 // BIN_S
    winb = win.dropna(subset=["x", "y", "datetime"]).copy()
    winb["bin_utc"] = w._bin_utc_ns(winb["datetime"], BIN_S)
    drop_rows = []
    for (night, sid), g in winb.groupby(["night", "shortid"]):
        present = g["bin_utc"].nunique()
        drop_rows.append({"night": night, "shortid": sid, "present_bins": int(present),
                          "expected_bins": int(exp_bins),
                          "dropout_frac": float(1 - present / exp_bins)})
    dropout = pd.DataFrame(drop_rows)
    dropout.to_csv(out / "dropout_by_animal_day.csv", index=False)

    # --- heat/midday convergence: per (night, window) across animals ---
    wtemp = _window_temp(wx)
    conv_rows = []
    for (night, wname), g in seq.groupby(["night", "window"]):
        # within_day_sequence already yields one dominant-zone row per animal/window
        per_animal = g.drop_duplicates(subset="shortid")
        zc = per_animal["dominant_zone_class"].value_counts()
        rc = per_animal[per_animal["dominant_zone_class"] == "shelter"]["dominant_roi"].value_counts()
        conv_rows.append({
            "night": night, "window": wname, "n_animals": int(per_animal.shape[0]),
            "n_in_shelter": int(zc.get("shelter", 0)),
            "house_1_count": int(rc.get("house_1", 0)), "house_2_count": int(rc.get("house_2", 0)),
            "max_shelter_share": int(rc.max()) if len(rc) else 0,
            "zone_entropy_bits": _entropy(zc.values),
        })
    conv = pd.DataFrame(conv_rows)
    if not conv.empty and not wtemp.empty:
        conv = conv.merge(wtemp, on=["night", "window"], how="left")
    else:
        conv["mean_temp_c"] = np.nan
        conv["mean_rain_mmhr"] = np.nan
    order = {w0: i for i, (w0, _, _) in enumerate(w.DAY_WINDOWS)}
    conv["window_order"] = conv["window"].map(order)
    conv = conv.sort_values(["night", "window_order"]).reset_index(drop=True)
    conv.to_csv(out / "heat_midday_convergence_summary.csv", index=False)

    # --- relocation summary by kind / day + temp at event ---
    if not events.empty:
        ev = events.copy()
        ev["event_dt_local"] = pd.to_datetime(ev["start_utc"]) + pd.Timedelta(hours=w.LOCAL_TZ_OFFSET_HOURS)
        ev["night"] = ev["event_dt_local"].dt.date.astype(str)
        reloc_summary = (ev.groupby(["night", "kind"]).size().rename("n_events").reset_index())
    else:
        reloc_summary = pd.DataFrame(columns=["night", "kind", "n_events"])
    reloc_summary.to_csv(out / "relocation_summary_by_day_kind.csv", index=False)

    # --- figures ---
    _fig_timeline(seq, wx, events, nights, tags, fig / "T1_rest_site_timeline.png")
    _fig_convergence(conv, fig / "T2_convergence_by_window.png")

    # --- report ---
    report = _build_report(bouts, seq, events, conv, dropout, reloc_summary,
                           nights, tags, jitter, moving_thr, wx, out)
    (out / "direction3_temperature_relocation_report.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "direction3_temperature_relocation_report.md").write_text(report, encoding="utf-8")

    w.write_run_manifest(out, {
        "analysis": "Direction 3 Stage B — within-day rest-site vs temperature",
        "rest_window": f"{REST_START:02d}:00-{REST_END:02d}:00 EDT", "bin_s": BIN_S,
        "nights": nights, "tags": tags, "rest_cutoff_inps": moving_thr, "jitter_floor_in": jitter,
        "n_bouts": int(len(bouts)), "n_relocation_events": int(len(events)),
        "weather_files": [str(p) for p in args.weather if Path(p).exists()],
        "weather_alignment": "wall-clock UTC, UNVERIFIED (~5 min); AWN local -04:00",
        "frame": "WISER inch offset, UNVERIFIED — ROI-identity + outside-temp/time proxies only",
        "caveats": "sleep=low-speed proxy (not ephys); temperature is a covariate on BOTH the "
                   "animal path and the UWB dropout path (see dropout_by_animal_day.csv); observer "
                   "field notes are hypotheses not labels; CV corroborates visible shelter-resident "
                   "periods only (lower bound).",
    })
    print(f"\n  bouts={len(bouts)}  relocation_events={len(events)}  report -> {REPORT_DIR}")
    print(f"All outputs written to: {out}")


def _dom_by_window(seq, night, tag):
    g = seq[(seq["night"] == night) & (seq["shortid"].astype(str) == str(tag))]
    return {r.window: r.dominant_roi for r in g.itertuples()}


def _build_report(bouts, seq, events, conv, dropout, reloc_summary, nights, tags,
                  jitter, moving_thr, wx, out) -> str:
    L = []
    L.append("# Direction 3 (Stage B) — within-day rest-site relocation & temperature\n")
    L.append(f"*Candidate / measurement-limited. Rest = low-speed proxy (< {moving_thr:.1f} in/s), "
             f"NOT ephys-validated. WISER inch frame UNVERIFIED (ROI-identity + outside-temp/time "
             f"proxies only). Jitter floor ~{jitter:.0f} in. Weather alignment wall-clock UTC, "
             f"unverified ~5 min. Field-log notes are hypotheses, not labels.*\n")
    L.append(f"Days: {', '.join(nights)} · tags: {', '.join(tags)} · "
             f"{len(bouts)} rest bouts · {len(events)} within-day relocation events.\n")

    # dropout guard
    L.append("## Dropout guard (does a wet day fake a 'move'?)\n")
    dd = dropout.pivot_table(index="shortid", columns="night", values="dropout_frac")
    L.append("Per-animal daytime **dropout fraction** (share of the 05:00–21:00 minute grid with no "
             "WISER fix). Rain/wet attenuates UWB, so a high-dropout day can mimic a rest-site change:\n")
    L.append("```\n" + dd.round(2).to_string() + "\n```\n")
    hi = dropout[dropout["dropout_frac"] > 0.25]
    L.append(f"- Animal-days with >25% dropout: {len(hi)}"
             + (f" ({', '.join(hi['night']+'/'+hi['shortid'].astype(str))})" if len(hi) else "")
             + ". Interpret their rest-site reads as **lower-confidence**.\n")

    # Q1 within-day sequence
    L.append("## Q1/Q3 — Do rats move rest sites within a day (morning → midday/afternoon)?\n")
    for night in nights:
        L.append(f"**{night}** — {DAY_CONTEXT.get(night, '')}")
        for t in tags:
            dw = _dom_by_window(seq, night, t)
            if not dw:
                continue
            seqstr = " → ".join(f"{k.split('_')[0]}:{dw[k]}" for k in
                                [w0 for w0, _, _ in w.DAY_WINDOWS] if k in dw)
            L.append(f"  - {t}: {seqstr}")
        L.append("")
    L.append("Read: a within-day sequence that changes ROI across windows is a candidate within-day "
             "relocation; a constant ROI is within-day site fidelity.\n")

    # Q2/Q4 temperature-linked
    L.append("## Q2/Q4 — Relocation vs temperature / time-of-day; midday convergence\n")
    L.append("Per (day, window): rest-zone entropy across animals (low = converged to few sites), "
             "max animals sharing one shelter, and mean outside temp:\n")
    show = conv[["night", "window", "n_in_shelter", "house_1_count", "house_2_count",
                 "max_shelter_share", "zone_entropy_bits", "mean_temp_c"]].copy()
    L.append("```\n" + show.round(2).to_string(index=False) + "\n```\n")
    if not reloc_summary.empty:
        L.append("Relocation events by day/kind:\n```\n" + reloc_summary.to_string(index=False) + "\n```\n")
    # data-driven observed pattern (convergence vs dispersal at the heat peak)
    L.append("**Observed within-day pattern (computed, still descriptive over 3 days):**")
    for night in nights:
        c = conv[conv["night"] == night].set_index("window")
        ins = c[c["n_in_shelter"] > 0]
        peakw = str(ins["zone_entropy_bits"].idxmin()) if not ins.empty else "none"
        lm = c["zone_entropy_bits"].get("late_morning", np.nan)
        mh = c["zone_entropy_bits"].get("midday_heat", np.nan)
        if lm == lm and mh == mh:
            trend = ("DISPERSED (entropy rose)" if mh > lm + 0.01
                     else "CONVERGED (entropy fell)" if mh < lm - 0.01 else "unchanged")
        else:
            trend = "n/a (few in-shelter windows)"
        mh_ins = c["n_in_shelter"].get("midday_heat", np.nan)
        n_an = c["n_animals"].get("midday_heat", np.nan)
        if not (mh_ins == mh_ins):        # no midday window (partial day)
            shelter_note = ""
        elif mh_ins == 0:
            shelter_note = " — but all animals are OUT of shelters (open field) at the heat peak"
        elif mh_ins == n_an:
            shelter_note = " — all animals IN a shelter at the heat peak"
        else:
            shelter_note = f" — {int(mh_ins)} of {int(n_an)} in a shelter at the heat peak"
        L.append(f"  - {night}: peak shelter convergence in **{peakw}**; at the 12:00–15:00 heat "
                 f"peak rest sites **{trend}**{shelter_note} (vs late-morning).")
    hs = events[events.get("kind", "") == "shelter_switch"] if not events.empty else events
    if not hs.empty and "to" in hs.columns:
        hs_heat = hs[hs["to"].isin(["midday_heat", "afternoon"])]
        who = sorted({f"{r.night}/{r.shortid}" for r in hs_heat.itertuples()})
        L.append(f"  - House_1→house_2 shelter switches landing at/after the heat peak: "
                 f"{', '.join(who) if who else 'none'} — a **candidate temperature-linked** "
                 f"midday relocation (NOT proof; house_2 is not verified cooler).")
    L.append("\nNote: on the hot dry day (6/29) the animals **converge to house_1 in late morning "
             "then DISPERSE at the heat peak** (a subset relocates to house_2) — i.e. the heat peak "
             "is associated with dispersal, not convergence. Descriptive only; do NOT claim "
             "causation.\n")

    # Q5/Q6 6/30 convergence
    L.append("## Q5/Q6 — 6/30 convergence to house_1: thermal/wet, social, habit, or measurement?\n")
    d630 = dropout[dropout["night"] == "2026-06-30"]
    L.append(f"- 6/30 mean dropout {d630['dropout_frac'].mean():.2f} vs 6/29 "
             f"{dropout[dropout['night']=='2026-06-29']['dropout_frac'].mean():.2f} — "
             "if comparable, the convergence is not merely missing data.\n")
    L.append("- Candidate interpretations (not mutually exclusive): **wet-day convergence** (rain "
             "~17:30 + AM condensation), **thermal** (hottest day), **social aggregation** "
             "(co-location beyond site availability), vs **individual habit** (house_1 is the "
             "baseline site for 3/5 animals anyway). WISER cannot separate sleep state or true "
             "shelter microclimate — that needs shelter-temperature / ephys. CV corroborates only "
             "the visible shelter-resident periods (lower bound; 2026-07-06 reconciliation).\n")

    # answers
    L.append("## Direct answers\n")
    L.append("1. **Across-day robust relocation:** 12386 and 12407 (house_1↔house_2 identity switch, "
             "~185–212 in); see Stage A `rest_site_stability.csv` tiers.")
    L.append("2. **Jitter/marginal only:** 12378, 12380, 12395 (all across-day shifts < 30 in = stable).")
    L.append("3. **Within-day moves:** see the per-day sequences above (ROI change across windows).")
    L.append("4. **Temperature regularity:** reported as entropy/share vs window+temp — descriptive, "
             "3 days; labelled temperature-**linked** at most.")
    L.append("5. **6/30 convergence:** quantified above with the dropout guard.")
    L.append("6. **Best interpretation:** candidate / measurement-limited — WISER supports site-level "
             "within-day movement and cross-shelter switching by 2 animals; thermal/social/habit "
             "cannot be separated without shelter temperature or more days.\n")
    L.append(f"\n*Figures + CSVs: `{out}`.*\n")
    return "\n".join(L)


if __name__ == "__main__":
    main()

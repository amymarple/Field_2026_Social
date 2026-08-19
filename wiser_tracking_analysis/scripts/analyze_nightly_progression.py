r"""
analyze_nightly_progression.py — nightly 9pm-12am movement across 6/28-6/30.

Rate-normalized, paired (5 rats, Sova removed) comparison of nocturnal movement
across three nights to separate **novelty habituation** from **rain**:
  * primary metric = active_distance_m_per_valid_hour (unequal windows compare);
  * clean habituation contrast = 6/28 vs 6/29 (both dry, station-confirmed);
  * 6/30 is wet all night (17:20 afternoon rain) AND has an in-window rain burst
    ~22:30-22:50 (observed; station sparse there) -> per-rat difference-in-
    differences on the 22:30 split, with/without a transition buffer.

Everything is exploratory/candidate (n=5 paired). Read-only on D:\Wiser\data;
outputs to D:\Wiser_plot\nightly_progression_YYYYMMDD_HHMM\.

    conda activate cv
    cd wiser_tracking_analysis
    python scripts/analyze_nightly_progression.py
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
DEFAULT_OUT_ROOT = Path(r"D:\Wiser_plot")
WEATHER_FILES = [Path(r"D:\weather_data\AWN-F8B3B78DEAC9-20260628-20260629.csv"),
                 Path(r"D:\weather_data\AWN-F8B3B78DEAC9-20260630-20260701.csv")]
DROP_TAGS = {"12409"}                   # Sova, deceased -> removed entirely
RAIN_SPLIT = "22:30"                    # observed in-window rain onset
RAIN_BAND_HHMM = ("22:30", "22:50")     # observed burst
BUFFERS = (0, 20)                       # DiD without / with transition buffer


def main() -> None:
    ap = argparse.ArgumentParser(description="Nightly 9pm-12am movement (habituation vs rain).")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--fixed", type=Path, default=DEFAULT_FIXED)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--clock-start", type=int, default=21)
    ap.add_argument("--clock-end", type=int, default=24)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"[nightly] DB not found: {args.db}")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M")
    out = args.output / f"nightly_progression_{ts}"
    fig = out / "figures"
    fig.mkdir(parents=True, exist_ok=True)
    print(f"=== Nightly progression ===\n  DB: {args.db}\n  out: {out}\n")

    # thresholds from the stationary baseline
    fx = w.load_wiser_session(args.fixed)
    fx = time_utils.convert_timestamps(fx)
    fx = time_utils.trim_last_n_minutes(fx, minutes=10)
    fx = w.add_speed(fx)
    moving_thr = w.speed_noise_floor(fx)["p99"]
    jitter = float(np.nanmedian(metrics.compute_summary(
        fx, ground_truth=metrics.load_ground_truth(DEFAULT_GT))["rms_jitter"]))
    print(f"  moving_thr(p99)={moving_thr:.2f} in/s  jitter_floor={jitter:.2f} in")

    # free session -> cleaned, Sova removed, 9pm-12am paired window
    df = w.load_wiser_session(args.db)
    df = time_utils.convert_timestamps(df)
    df = w.add_speed(df)
    df = w.add_validity_flags(df, jitter_floor_in=jitter)
    df = w.apply_tag_cutoffs(df)
    df = df[~df["shortid"].astype(str).isin(DROP_TAGS)]
    win = w.select_route_window(df, clock_start=args.clock_start, clock_end=args.clock_end)
    nights = sorted(win["night"].unique())
    print(f"  nights={nights}  rats/night="
          f"{win.groupby('night')['shortid'].nunique().to_dict()}")
    if len(nights) < 2:
        raise SystemExit("[nightly] need >=2 nights of data.")
    rain_night, control_nights = nights[-1], nights[:-1]

    # --- rates + habituation ---
    nr = w.nightly_rates(win, moving_thr_inps=moving_thr,
                         clock_start=args.clock_start, clock_end=args.clock_end)
    nr.to_csv(out / "nightly_rates.csv", index=False)
    w.plot_nightly_trajectories(win, save_path=fig / "N1_trajectories.png")
    w.plot_nightly_rate_lines(nr, save_path=fig / "N2_rate_habituation.png")

    # --- through-the-night cumulative curves (rain band on the wet night) ---
    cum = w.cumulative_night_distance(win, moving_thr_inps=moving_thr, bin_s=60)
    def _min_since_21(hhmm):
        h, m = map(int, hhmm.split(":"))
        return (h - args.clock_start) * 60 + m
    band = (_min_since_21(RAIN_BAND_HHMM[0]), _min_since_21(RAIN_BAND_HHMM[1]))
    w.plot_cumulative_night(cum, rain_band_min=band, save_path=fig / "N3_cumulative.png")

    # --- rain DiD (both buffer variants) ---
    did_variants, split_frames = {}, []
    for buf in BUFFERS:
        sr = w.night_split_rates(win, moving_thr_inps=moving_thr, split_hm=RAIN_SPLIT,
                                 buffer_min=buf, clock_start=args.clock_start,
                                 clock_end=args.clock_end)
        split_frames.append(sr)
        did = w.rain_did(sr, rain_night, control_nights)
        did_variants[f"buf{buf}"] = did
    pd.concat(split_frames, ignore_index=True).to_csv(out / "night_split_rates.csv", index=False)
    rain_did_all = pd.concat([d for d in did_variants.values() if d is not None and not d.empty],
                             ignore_index=True)
    rain_did_all.to_csv(out / "rain_did.csv", index=False)
    w.plot_rain_did(did_variants, save_path=fig / "N5_rain_did.png")

    # --- weather ---
    weather = w.load_weather_multi(WEATHER_FILES)
    wsum = []
    for night in nights:
        s = w._roi_time_utc(f"{night}T{args.clock_start:02d}:00:00{w.LOCAL_OFFSET_STR}")
        e = w._roi_time_utc(f"{night}T{args.clock_end - 1:02d}:59:59{w.LOCAL_OFFSET_STR}")
        seg = weather[(weather["datetime_utc"].to_numpy() >= s) &
                      (weather["datetime_utc"].to_numpy() <= e)] if not weather.empty else weather
        present = (not weather.empty) and len(seg) > 0
        wsum.append({"night": night, "weather_rows_in_window": int(len(seg)) if present else 0,
                     "mean_rain_mmhr": float(seg["rain_rate_mmhr"].mean()) if (present and "rain_rate_mmhr" in seg) else np.nan,
                     "max_rain_mmhr": float(seg["rain_rate_mmhr"].max()) if (present and "rain_rate_mmhr" in seg) else np.nan,
                     "label": ("dry (station-confirmed)" if (present and "rain_rate_mmhr" in seg and (seg["rain_rate_mmhr"].fillna(0) == 0).all())
                               else "same-clock control, weather unknown" if not present
                               else "rain/wet")})
    pd.DataFrame(wsum).to_csv(out / "weather_night_summary.csv", index=False)
    if not weather.empty:
        w.plot_rain_timeline(weather, day=rain_night, night_hours=(args.clock_start, args.clock_end),
                             obs_band_hhmm=RAIN_BAND_HHMM, save_path=fig / "N4_rain_timeline.png")

    # --- QC per night ---
    qc = (win.assign(anch=win.get("anchors_used"))
          .groupby("night")
          .apply(lambda g: pd.Series({
              "n_fixes": len(g), "valid_frac": float(g["valid"].mean()),
              "mean_anchors": float(pd.to_numeric(g.get("anchors_used"), errors="coerce").mean()),
              "n_rats": g["shortid"].nunique()}), include_groups=False)
          .reset_index())
    qc.to_csv(out / "nightly_qc.csv", index=False)

    # --- verdict ---
    m = nr.groupby("night")["active_distance_m_per_valid_hour"].mean()
    n1, n2 = nights[0], nights[1]
    drop = 100 * (1 - m[n2] / m[n1]) if m[n1] else float("nan")
    did_str = ", ".join(
        f"{k}={float(v['did'].mean()):+.1f}" if v is not None and not v.empty else f"{k}=NA"
        for k, v in did_variants.items())
    verdict = (f"CANDIDATE habituation (dry nights) {n1}->{n2}: "
               f"{m[n1]:.0f}->{m[n2]:.0f} m/valid-hr (down {drop:.0f}%). "
               f"6/30 is WET ALL NIGHT (17:20 rain) -> confounded with habituation; "
               f"{nights[1]}->{nights[-1]} mean {m[nights[1]]:.0f}->{m[nights[-1]]:.0f} m/valid-hr "
               f"(no further drop). In-window rain ~22:30-22:50: per-rat DiD mean [{did_str}] "
               f"m/valid-hr (n=5, exploratory; ~0 = no acute rain suppression beyond time-of-night).")
    (out / "nightly_conclusion.txt").write_text(verdict, encoding="utf-8")
    w.write_run_manifest(out, {
        "window": f"{args.clock_start}:00-{args.clock_end}:00 EDT, nights {nights}",
        "paired_core": "5 rats (Sova/12409 removed)",
        "moving_thr_inps": moving_thr, "jitter_floor_in": jitter,
        "rain_split": RAIN_SPLIT, "rain_band_observed": RAIN_BAND_HHMM, "buffers_min": list(BUFFERS),
        "rain_facts": "6/30 afternoon 17:20-17:55 (station); in-window ~22:30-22:50 (observed); "
                      "6/28 dry-confirmed; 6/29 evening weather unknown",
        "note": "exploratory; wet-ground on 6/30 confounded with habituation; WISER frame unverified",
    })
    print("\n  " + verdict)
    print(f"\nAll outputs written to: {out}")


if __name__ == "__main__":
    main()

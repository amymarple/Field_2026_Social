r"""Diurnal soundscape panel: loudness + bird-vs-ambient bands + AWN weather + WISER rat activity.

Stacks time-aligned panels over one or more local days:
  1) Leq (relative dBFS) — overall loudness.
  2) bird-like 2-8 kHz band vs ambient 0-1 kHz band, with rain/broadband-aware "biophony likely"
     shading (birds lift only 2-8 kHz; wind/rain lift both bands + logged rain is suppressed).
  3) AWN rain rate + wind speed.
  4) WISER rat activity per hour (fraction of time moving + active metres), 5 rats (Sova dropped).
Rain periods (rain_rate > 0) are shaded across all panels.

THREE device clocks are involved — audio (camera/NVR, local wallclock), weather (AWN station), and
WISER (WISER computer, UTC shifted -4 h to local). Their alignment is timestamp-only and UNVERIFIED.
Audio levels are relative camera-mic dBFS (NOT SPL), ceiling 8 kHz. WISER "activity" is above-noise-
floor movement in WISER inches (NO georeference / spatial claim).

    python scripts/plot_soundscape_day.py --channel CH01 --date 2026-06-30
    python scripts/plot_soundscape_day.py --channel CH01 --dates 2026-06-29,2026-06-30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.weather import load_awn, find_awn_files  # noqa: E402
from src.plotting import biophony_active  # noqa: E402

DEFAULT_WEATHER_DIR = r"D:\Reolink_record\audio_in\weather_data"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default="CH01")
    ap.add_argument("--date", help="single day YYYY-MM-DD")
    ap.add_argument("--dates", help="comma-separated YYYY-MM-DD list (multi-day)")
    ap.add_argument("--output-dir", default=str(PROJECT_ROOT / "outputs"))
    ap.add_argument("--weather-dir", default=DEFAULT_WEATHER_DIR)
    ap.add_argument("--resample", default="5min")
    ap.add_argument("--no-wiser", action="store_true", help="skip the WISER activity panel")
    args = ap.parse_args()

    if not args.date and not args.dates:
        raise SystemExit("give --date or --dates")
    dates = sorted({d.strip() for d in (args.dates.split(",") if args.dates else [args.date])})
    out_dir = Path(args.output_dir)
    rs = args.resample

    # --- audio (concat each day's CSV) ---
    frames = []
    for d in dates:
        p = out_dir / f"audio_features_{args.channel}_{d}.csv"
        if not p.exists():
            print(f"  (no audio CSV for {d}: {p.name})")
            continue
        frames.append(pd.read_csv(p))
    if not frames:
        raise SystemExit("no audio CSVs found for the requested dates")
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["window_start_timestamp"])
    ok = df[df["qc_flag"] == "ok"].set_index("ts").sort_index()
    leq = ok["leq_dbfs_relative"].resample(rs).median()
    amb = ok["band_0_1k_db"].resample(rs).median()
    bird = ok["band_2_8k_db"].resample(rs).median()
    floor = float(bird.quantile(0.10))

    day0 = pd.Timestamp(dates[0])
    day1 = pd.Timestamp(dates[-1]) + pd.Timedelta(days=1)

    # --- weather (optional) ---
    w = pd.DataFrame()
    try:
        files = find_awn_files(args.weather_dir)
        if files:
            w = load_awn(files)
            w = w[(w["ts"] >= day0 - pd.Timedelta(hours=1)) & (w["ts"] < day1 + pd.Timedelta(hours=1))]
    except Exception as e:
        print(f"  (weather unavailable: {e})")

    # rain resampled onto the band grid -> used to suppress biophony shading under logged rain
    rain_grid = None
    if not w.empty and "rain_mm_hr" in w.columns:
        rain_grid = (w.set_index("ts")["rain_mm_hr"].resample(rs).max().reindex(bird.index))
    active = biophony_active(bird, amb, rain=rain_grid)

    # --- WISER rat activity (optional; slow load) ---
    act = pd.DataFrame()
    if not args.no_wiser:
        try:
            from analysis.wiser_activity import hourly_rat_activity
            act = hourly_rat_activity(dates)
            if act.empty:
                print("  (WISER activity empty for these dates)")
        except Exception as e:
            print(f"  (WISER activity unavailable: {e})")

    n_panels = 4 if not act.empty else 3
    fig, axes = plt.subplots(n_panels, 1, figsize=(13, 3.0 * n_panels), sharex=True)
    ax1, ax2, ax3 = axes[0], axes[1], axes[2]
    ax4 = axes[3] if n_panels == 4 else None

    span = f"{dates[0]}" if len(dates) == 1 else f"{dates[0]} → {dates[-1]}"

    # Panel 1: loudness
    ax1.plot(leq.index, leq.to_numpy(), color="tab:blue", lw=1.0)
    ax1.set_ylabel("Leq\nrel. dBFS")
    ax1.set_title(f"Diurnal soundscape — {args.channel}  {span}   "
                  f"(relative dBFS, NOT SPL; ≤8 kHz; cross-device alignment UNVERIFIED)", fontsize=10)

    # Panel 2: bird vs ambient bands, rain/broadband-aware shading
    ax2.plot(amb.index, amb.to_numpy(), color="tab:orange", lw=1.0,
             label="ambient 0–1 kHz (wind·rain·rumble)")
    ax2.plot(bird.index, bird.to_numpy(), color="tab:green", lw=1.3,
             label="bird-like 2–8 kHz (biophony)")
    ax2.axhline(floor, color="tab:green", lw=0.7, ls=":", alpha=0.7, label="2–8 kHz night floor")
    y0, y1 = ax2.get_ylim()
    ax2.fill_between(bird.index, y0, y1, where=active, color="tab:green", alpha=0.10, step="mid",
                     label="biophony likely (excl. broadband wind/rain)")
    ax2.set_ylim(y0, y1)
    ax2.set_ylabel("band energy\nrel. dB")
    ax2.legend(loc="upper right", fontsize=7)

    # Panel 3: weather
    if not w.empty and "rain_mm_hr" in w.columns:
        ax3.fill_between(w["ts"], 0, w["rain_mm_hr"].to_numpy(), color="tab:blue", alpha=0.5,
                         step="mid", label="rain rate (mm/hr)")
        ax3.set_ylabel("rain\nmm/hr")
        if "wind_mph" in w.columns:
            axw = ax3.twinx()
            axw.plot(w["ts"], w["wind_mph"].to_numpy(), color="0.4", lw=0.9, label="wind (mph)")
            axw.set_ylabel("wind mph", color="0.4")
            axw.tick_params(axis="y", labelcolor="0.4")
        ax3.legend(loc="upper right", fontsize=7)
    else:
        ax3.text(0.5, 0.5, "no AWN weather for these dates", ha="center", va="center",
                 transform=ax3.transAxes, color="0.5")

    # Panel 4: WISER rat activity (nocturnal — contrast with the daytime soundscape)
    if ax4 is not None:
        ax4.bar(act["ts_local"], act["active_frac"], width=1 / 24 * 0.9, align="edge",
                color="tab:purple", alpha=0.55, label="rat active fraction (5 rats, mean)")
        ax4.set_ylabel("rat\nactive frac")
        axm = ax4.twinx()
        axm.plot(act["ts_local"] + pd.Timedelta(minutes=30), act["active_distance_m"],
                 color="0.4", lw=0.9, marker=".", ms=3, label="active metres/h")
        axm.set_ylabel("active m/h", color="0.4")
        axm.tick_params(axis="y", labelcolor="0.4")
        ax4.legend(loc="upper right", fontsize=7)

    axes[-1].set_xlabel("time (local wallclock)")

    # Shade rain periods across all panels
    if not w.empty and "rain_mm_hr" in w.columns and (w["rain_mm_hr"] > 0).any():
        half = (w["ts"].diff().median() or pd.Timedelta(minutes=5)) / 2
        for ts in w.loc[w["rain_mm_hr"] > 0, "ts"]:
            for ax in axes:
                ax.axvspan(ts - half, ts + half, color="tab:blue", alpha=0.06, lw=0)

    for ax in axes:
        ax.set_xlim(day0, day1)
    fig.text(0.5, 0.005,
             "Birds active by day (panel 2 shading), rats active by night (panel 4) — inverse cycles. "
             "Rain shading = logged rain. THREE device clocks; alignment UNVERIFIED.",
             ha="center", va="bottom", fontsize=7.5, style="italic", color="0.35")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    tag = dates[0] if len(dates) == 1 else f"{dates[0]}_to_{dates[-1]}"
    out_png = out_dir / "plots" / f"{args.channel}_{tag}_soundscape_panel.png"
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    print(f"  wrote {out_png}")

    if not w.empty and "rain_mm_hr" in w.columns:
        wet = w.assign(day=w["ts"].dt.strftime("%Y-%m-%d"), h=w["ts"].dt.hour)
        wet = wet[wet["rain_mm_hr"] > 0]
        print(f"  rain logged (day, hour): {sorted(set(zip(wet['day'], wet['h']))) or 'none'}")
    if not act.empty:
        peak = act.loc[act["active_frac"].idxmax()]
        print(f"  peak rat activity: {peak['ts_local']:%Y-%m-%d %H:%M} "
              f"(active_frac={peak['active_frac']:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

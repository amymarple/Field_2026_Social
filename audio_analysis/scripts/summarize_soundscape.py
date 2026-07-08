"""Minimal daily summary from already-extracted feature CSVs (Phase 1).

Reads outputs/audio_features_<CH>_<date>.csv, prints a compact QC + level/index summary,
and optionally writes simple plots. Heavy diurnal/cross-modal analysis belongs on the
main analysis computer (Phase 2). Not required for extraction.

    python scripts/summarize_soundscape.py --channel CH01 --date 2026-06-29 [--plots]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--output-dir", default=str(PROJECT_ROOT / "outputs"))
    ap.add_argument("--plots", action="store_true", help="write level + index PNGs")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    csv_path = out_dir / f"audio_features_{args.channel}_{args.date}.csv"
    if not csv_path.exists():
        raise SystemExit(f"no CSV at {csv_path}")

    df = pd.read_csv(csv_path)
    df["window_start_timestamp"] = pd.to_datetime(df["window_start_timestamp"])
    n = len(df)
    print(f"{csv_path.name}: {n} windows")
    print("  QC flag counts:")
    for flag, c in df["qc_flag"].value_counts().items():
        print(f"    {flag:14s} {c:5d}  ({100*c/n:.1f}%)")

    ok = df[df["qc_flag"] == "ok"]
    if len(ok):
        print(f"  usable (ok) windows: {len(ok)}")
        lq = ok["leq_dbfs_relative"]
        print(f"  Leq dBFS relative: median {lq.median():.1f}, "
              f"p10 {lq.quantile(.1):.1f}, p90 {lq.quantile(.9):.1f}  (NOT SPL)")
        for idx in ("aci", "bi_2_8k_camera", "ndsi_1_2k_vs_2_8k_camera", "adi"):
            if idx in ok and ok[idx].notna().any():
                print(f"  {idx:26s} median {ok[idx].median():.3f}")
        print("  hourly median Leq (relative dBFS):")
        hourly = ok.set_index("window_start_timestamp")["leq_dbfs_relative"].resample("1h").median()
        for ts, v in hourly.items():
            if pd.notna(v):
                print(f"    {ts:%H:%M}  {v:6.1f}")
    else:
        print("  no usable windows (all silent / pre-mic / flagged).")

    if args.plots and len(ok):
        from src.plotting import (plot_level_over_time, plot_index_timeseries,
                                  plot_bird_vs_ambient)
        pdir = out_dir / "plots"
        pdir.mkdir(parents=True, exist_ok=True)
        p1 = plot_level_over_time(csv_path, pdir / f"{args.channel}_{args.date}_level.png")
        p2 = plot_index_timeseries(csv_path, pdir / f"{args.channel}_{args.date}_indices.png")
        p3 = plot_bird_vs_ambient(csv_path, pdir / f"{args.channel}_{args.date}_bird_vs_ambient.png")
        print(f"  plots: {p1.name}, {p2.name}, {p3.name}")


if __name__ == "__main__":
    main()

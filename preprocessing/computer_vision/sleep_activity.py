"""
Sleep / activity from a field-cm trajectory.

Given a trajectory in the common field frame (columns: time_s or t, x_field_cm,
y_field_cm, optionally animal_id), compute speed (cm/s) and classify each sample as
**rest** or **active** by a speed threshold, then merge consecutive samples into
bouts with a minimum duration (so brief jitter doesn't fragment a long rest).

"rest" is the coarse proxy for sleeping here; finer sleep scoring (posture,
duration, time-of-day) comes later, but this gives activity budgets now.

    python sleep_activity.py --input traj.csv --out-prefix out\session1
    python sleep_activity.py --synthetic         # self-test, no inputs
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _time_col(df: pd.DataFrame) -> str:
    for c in ("t", "time_s"):
        if c in df.columns:
            return c
    raise ValueError("trajectory needs a 't' or 'time_s' column")


def _animal_groups(df: pd.DataFrame):
    """Yield (animal_id_or_None, sub-df). Explicit iteration (no groupby.apply)."""
    if "animal_id" in df.columns:
        for k, g in df.groupby("animal_id", sort=False):
            yield k, g
    else:
        yield None, df


def compute_speed(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'speed_cm_s' column (per animal_id if present)."""
    tcol = _time_col(df)
    parts = []
    for _, g in _animal_groups(df):
        g = g.sort_values(tcol).copy()
        dt = g[tcol].diff()
        dist = np.sqrt(g["x_field_cm"].diff() ** 2 + g["y_field_cm"].diff() ** 2)
        g["speed_cm_s"] = (dist / dt).replace([np.inf, -np.inf], np.nan)
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def classify(df: pd.DataFrame, speed_thresh: float = 5.0,
             min_bout_s: float = 30.0) -> pd.DataFrame:
    """Label each sample rest/active, then absorb sub-min-duration bouts."""
    tcol = _time_col(df)
    df = df.copy()
    df["state"] = np.where(df["speed_cm_s"].fillna(0) >= speed_thresh, "active", "rest")

    parts = []
    for _, g in _animal_groups(df):
        g = g.sort_values(tcol).reset_index(drop=True)
        # iteratively flip the shortest sub-threshold bout into its neighbour
        while True:
            change = (g["state"] != g["state"].shift()).cumsum()
            segs = list(g.groupby(change).groups.values())
            if len(segs) <= 1:
                break
            durs = [(g.loc[ix[-1], tcol] - g.loc[ix[0], tcol], list(ix)) for ix in segs]
            short = [d for d in durs if d[0] < min_bout_s]
            if not short:
                break
            _, ix = min(short, key=lambda d: d[0])
            prev = ix[0] - 1
            nxt = ix[-1] + 1
            src = prev if prev >= 0 else nxt          # neighbour to inherit from
            g.loc[ix, "state"] = g.loc[src, "state"]
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def bouts(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the per-sample states into bout rows."""
    tcol = _time_col(df)
    rows = []
    for k, g in _animal_groups(df):
        g = g.sort_values(tcol).reset_index(drop=True)
        change = (g["state"] != g["state"].shift()).cumsum()
        for _, idx in g.groupby(change).groups.items():
            seg = g.loc[list(idx)]
            rows.append({
                "animal_id": k,
                "state": seg["state"].iloc[0],
                "start_s": round(float(seg[tcol].iloc[0]), 2),
                "end_s": round(float(seg[tcol].iloc[-1]), 2),
                "duration_s": round(float(seg[tcol].iloc[-1] - seg[tcol].iloc[0]), 2),
                "mean_speed_cm_s": round(float(seg["speed_cm_s"].mean(skipna=True) or 0), 2),
            })
    return pd.DataFrame(rows)


def synthetic_trajectory(fps: int = 5) -> pd.DataFrame:
    """move ~40 s -> rest ~60 s -> move ~40 s, in field cm (for self-test)."""
    segs = []
    t0 = 0.0
    n = 40 * fps                                   # move across the field
    segs.append(pd.DataFrame({"time_s": t0 + np.arange(n) / fps,
                              "x_field_cm": np.linspace(50, 400, n),
                              "y_field_cm": np.linspace(50, 600, n)}))
    t0 += n / fps
    n = 60 * fps                                   # rest (tiny jitter)
    segs.append(pd.DataFrame({"time_s": t0 + np.arange(n) / fps,
                              "x_field_cm": 400 + np.random.default_rng(0).normal(0, 0.4, n),
                              "y_field_cm": 600 + np.random.default_rng(1).normal(0, 0.4, n)}))
    t0 += n / fps
    n = 40 * fps                                   # move again
    segs.append(pd.DataFrame({"time_s": t0 + np.arange(n) / fps,
                              "x_field_cm": np.linspace(400, 120, n),
                              "y_field_cm": np.linspace(600, 1100, n)}))
    df = pd.concat(segs, ignore_index=True)
    df["animal_id"] = "synthetic:1"
    return df


def run(df: pd.DataFrame, speed_thresh: float, min_bout_s: float):
    sp = compute_speed(df)
    cls = classify(sp, speed_thresh, min_bout_s)
    return cls, bouts(cls)


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Speed + rest/active bouts from a field-cm trajectory.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="trajectory CSV (t/time_s, x_field_cm, y_field_cm)")
    src.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out-prefix", default="sleep_activity")
    ap.add_argument("--speed-thresh", type=float, default=5.0, help="cm/s active threshold")
    ap.add_argument("--min-bout-s", type=float, default=30.0)
    args = ap.parse_args()

    df = synthetic_trajectory() if args.synthetic else pd.read_csv(args.input)
    per_sample, bout_df = run(df, args.speed_thresh, args.min_bout_s)

    Path(args.out_prefix).parent.mkdir(parents=True, exist_ok=True)
    per_sample.to_csv(f"{args.out_prefix}_speed.csv", index=False)
    bout_df.to_csv(f"{args.out_prefix}_bouts.csv", index=False)
    print(f"wrote {args.out_prefix}_speed.csv ({len(per_sample)} samples) and "
          f"{args.out_prefix}_bouts.csv ({len(bout_df)} bouts)")
    print(bout_df.to_string(index=False))


if __name__ == "__main__":
    _cli()

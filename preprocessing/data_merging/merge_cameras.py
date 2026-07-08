"""
Merge per-camera tracks into one common field-frame table.

Each input CSV is the canonical per-camera schema written by
preprocessing/computer_vision/animal_tracking.py:

    camera, frame, time_s, track_id, conf, x_img, y_img, x_field_cm, y_field_cm

Because every camera's x_field_cm / y_field_cm are already in the SAME common
field frame (cm), merging is a concatenation + temporal sort. This module:
  - loads and concatenates the per-camera CSVs (field coords preserved),
  - aligns time across cameras (optionally by absolute clip start time parsed
    from the recording filename, reusing the recorder's start_to_end naming),
  - emits a merged table keyed by (time, animal_id) in field cm,
  - validates that field coords survived and flags points outside the field.

Stable cross-camera identity (the same rat seen by two cameras) is a later stage;
for now animal_id = "<camera>:<track_id>" so nothing is silently collapsed.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

# field bounds (cm): x = 40 ft length, y = 20 ft width
FIELD_X_CM, FIELD_Y_CM = 1219.2, 609.6

# CH01_2026-06-26_17-00-01_to_18-00-00.mp4  ->  start datetime
_TS_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")


def clip_start_seconds(name: str) -> Optional[float]:
    """Epoch seconds of a recording's start time, parsed from its filename."""
    m = _TS_RE.search(name)
    if not m:
        return None
    return pd.Timestamp(f"{m.group(1)} {m.group(2).replace('-', ':')}").timestamp()


def merge_tracks(csv_paths: Sequence[Path], out: Path,
                 absolute_time: bool = False) -> pd.DataFrame:
    """Concatenate per-camera track CSVs into the merged common-frame table."""
    frames = []
    for p in csv_paths:
        p = Path(p)
        df = pd.read_csv(p)
        missing = {"camera", "time_s", "x_field_cm", "y_field_cm"} - set(df.columns)
        if missing:
            raise ValueError(f"{p} missing columns: {missing}")
        if absolute_time:
            base = clip_start_seconds(p.stem) or 0.0
            df["t"] = base + df["time_s"]
        else:
            df["t"] = df["time_s"]
        df["animal_id"] = df["camera"].astype(str) + ":" + df["track_id"].astype(str)
        frames.append(df)

    merged = pd.concat(frames, ignore_index=True).sort_values(["t", "animal_id"])
    cols = ["t", "animal_id", "camera", "track_id", "conf",
            "x_field_cm", "y_field_cm", "x_img", "y_img", "frame"]
    merged = merged[[c for c in cols if c in merged.columns]]

    # validation
    n_nan = int(merged[["x_field_cm", "y_field_cm"]].isna().any(axis=1).sum())
    outside = (
        (merged["x_field_cm"] < 0) | (merged["x_field_cm"] > FIELD_X_CM) |
        (merged["y_field_cm"] < 0) | (merged["y_field_cm"] > FIELD_Y_CM)
    )
    if n_nan:
        print(f"WARNING: {n_nan} rows have no field coords (camera not calibrated?)")
    if int(outside.sum()):
        print(f"WARNING: {int(outside.sum())} rows fall outside the "
              f"{FIELD_X_CM:.0f}x{FIELD_Y_CM:.0f} cm field")

    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out, index=False)
    print(f"merged {len(csv_paths)} file(s) -> {out}  "
          f"({len(merged)} rows, {merged['animal_id'].nunique()} animal_id(s))")
    return merged


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Merge per-camera tracks into the common field frame.")
    ap.add_argument("--inputs", nargs="+", required=True, help="per-camera track CSVs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--absolute-time", action="store_true",
                    help="offset each camera by its clip start time (from filename)")
    args = ap.parse_args()
    merge_tracks([Path(p) for p in args.inputs], Path(args.out), args.absolute_time)


if __name__ == "__main__":
    _cli()

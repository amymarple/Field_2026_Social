"""Load Ambient Weather Network (AWN) CSV exports for cross-modal soundscape analysis.

The AWN ``Date`` column is ISO 8601 **with an explicit local UTC offset** (e.g. ``-04:00`` EDT).
Audio (and WISER) timestamps are **local wallclock** (camera/NVR filename time), so we align on
local wallclock by stripping the offset (`tz_localize(None)` keeps the local wall time). This is
timestamp alignment only and **UNVERIFIED across devices** — the weather-station clock is not tied
to the camera/NVR clock (see ``data_manifests/2026-06-29-camera-audio.yaml``). Treat weather as a
covariate over time, not a synchronized signal.

Units are passed through from AWN unchanged: temp °C, wind mph, rain mm/hr, etc.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

# AWN column header -> tidy name. Keys must match the export headers exactly.
_RENAME = {
    "Outdoor Temperature (°C)": "temp_c",
    "Wind Speed (mph)": "wind_mph",
    "Wind Gust (mph)": "gust_mph",
    "Rain Rate (mm/hr)": "rain_mm_hr",
    "Event Rain (mm)": "event_rain_mm",
    "Daily Rain (mm)": "daily_rain_mm",
    "Humidity (%)": "humidity_pct",
    "Solar Radiation (W/m^2)": "solar_wm2",
    "Relative Pressure (mmHg)": "pressure_mmhg",
}


def load_awn(paths: str | Path | Iterable[str | Path]) -> pd.DataFrame:
    """Load one or more AWN CSV exports into a tidy, time-sorted frame.

    Returns columns ``ts`` (tz-naive **local wallclock**, matching the audio feature timestamps)
    plus the renamed weather variables. Duplicate timestamps (overlapping exports) are dropped.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    frames = []
    for p in paths:
        df = pd.read_csv(p, encoding="utf-8-sig")
        if "Date" not in df.columns:
            continue
        ts = pd.to_datetime(df["Date"], errors="coerce")   # tz-aware (has -04:00 offset)
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_localize(None)                    # drop tz, keep LOCAL wall time
        df = df.rename(columns=_RENAME)
        keep = ["ts"] + [c for c in _RENAME.values() if c in df.columns]
        out = df.assign(ts=ts)[keep]
        frames.append(out)
    if not frames:
        raise FileNotFoundError(f"No readable AWN CSVs with a 'Date' column in {paths!r}")
    combined = (pd.concat(frames, ignore_index=True)
                .dropna(subset=["ts"])
                .drop_duplicates(subset=["ts"])
                .sort_values("ts")
                .reset_index(drop=True))
    return combined


def find_awn_files(weather_dir: str | Path) -> list[Path]:
    """All ``AWN-*.csv`` exports in a directory (sorted)."""
    return sorted(Path(weather_dir).glob("AWN-*.csv"))

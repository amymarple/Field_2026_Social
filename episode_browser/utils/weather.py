"""
weather.py — load Ambient Weather Network (AWN) CSV exports. Data-layer ONLY.

Adapted from `audio_analysis/analysis/weather.py` (each subsystem stands alone in this
repo, so the tiny parser is duplicated rather than cross-imported).

The AWN `Date` column is ISO 8601 with an explicit local UTC offset (e.g. `-04:00` EDT).
We strip the offset and keep the **local wall time** (tz-naive EDT), which is how the
field observations and the rest of the field data are recorded. Episode times are UTC —
the browser aligns weather to episodes on **local wall-clock**, which is UNVERIFIED
across devices (the weather-station clock is not tied to the WISER/NVR clock). Treat
weather as a covariate over time, never a synchronized signal.

Units pass through from AWN unchanged: temp °C, wind mph, rain mm/hr, humidity %.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd

# Default location on the analysis PC; override with EPISODE_BROWSER_WEATHER_DIR.
DEFAULT_DIR = r"D:\Reolink_record\audio_in\weather_data"

_RENAME = {
    "Outdoor Temperature (°C)": "temp_c",
    "Wind Speed (mph)": "wind_mph",
    "Wind Gust (mph)": "gust_mph",
    "Rain Rate (mm/hr)": "rain_mm_hr",
    "Humidity (%)": "humidity_pct",
    "Solar Radiation (W/m^2)": "solar_wm2",
}
_COLS = ["ts"] + list(dict.fromkeys(_RENAME.values()))


def weather_dir() -> Path:
    return Path(os.environ.get("EPISODE_BROWSER_WEATHER_DIR", DEFAULT_DIR))


def load_weather(directory: Optional[str | Path] = None) -> pd.DataFrame:
    """Load all AWN-*.csv in `directory` into a tidy, time-sorted frame.

    Returns columns `ts` (tz-naive LOCAL wall time / EDT) + renamed weather variables.
    Empty (typed) frame if the directory is absent or holds no readable AWN exports —
    so the browser runs unchanged when weather is not available.
    """
    d = Path(directory) if directory else weather_dir()
    if not d.exists():
        return pd.DataFrame(columns=_COLS)
    frames = []
    for p in sorted(d.glob("AWN-*.csv")):
        try:
            df = pd.read_csv(p, encoding="utf-8-sig")
        except Exception:  # noqa: BLE001
            continue
        if "Date" not in df.columns:
            continue
        ts = pd.to_datetime(df["Date"], errors="coerce")
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_localize(None)          # drop offset, keep LOCAL wall time
        df = df.rename(columns=_RENAME)
        keep = ["ts"] + [c for c in _RENAME.values() if c in df.columns]
        frames.append(df.assign(ts=ts)[keep])
    if not frames:
        return pd.DataFrame(columns=_COLS)
    return (pd.concat(frames, ignore_index=True)
            .dropna(subset=["ts"])
            .drop_duplicates(subset=["ts"])
            .sort_values("ts")
            .reset_index(drop=True))


def slice_window(w: pd.DataFrame, start_edt, end_edt) -> pd.DataFrame:
    """Rows whose local-wall `ts` falls in [start_edt, end_edt] (both tz-naive EDT)."""
    if w.empty:
        return w
    return w[(w["ts"] >= start_edt) & (w["ts"] <= end_edt)]


def nearest(w: pd.DataFrame, ts_edt) -> Optional[dict]:
    """The single weather sample closest in time to `ts_edt` (tz-naive EDT), or None."""
    if w.empty:
        return None
    i = (w["ts"] - ts_edt).abs().idxmin()
    return w.loc[i].to_dict()


def availability() -> dict:
    d = weather_dir()
    return {"weather_dir": str(d), "exists": d.exists(),
            "files": len(list(d.glob("AWN-*.csv"))) if d.exists() else 0}

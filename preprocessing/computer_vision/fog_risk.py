"""fog_risk.py — weather-lite condensation/fog-RISK covariate for shelter camera MEASUREMENT quality.

MEASUREMENT-CONTEXT ONLY. This estimates how likely the CH05/CH06 IR glass was to fog/condense from
weather; it is NOT a behavioral feature and NOT proof the view was foggy. The *direct* measurement of view
degradation is the video-derived `view_quality_inside` column. Fog-risk exists to **explain / stratify**
view-quality degradation and detector misses — it must NEVER change thresholds, view-quality/safety logic,
counts, or exclude bins. Pure + additive, like glass_regime.py / measurement_context.py.

Inputs (Ambient Weather AWN export): air temperature, dew point, relative humidity, rain rate; local hour.
Derived:
  dewpoint_gap  = air_temp_c - dew_point_c   (small gap => air near saturation => condensation risk)
  fog_risk_level = low | medium | high       (heuristic risk cue, NOT a calibrated fog model)
  fog_risk_reason = which factors fired (small dewpoint gap / high RH / rain-wet / pre-dawn)

Clock: AWN `Date` carries a -04:00 (EDT) offset; we strip it to LOCAL wallclock to match the Reolink
filename-derived bin timestamps. This is an UNVERIFIED cross-device alignment (a covariate over time), and
`weather_lag_min` records how far the nearest weather sample was from the bin.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_WEATHER_DIR = Path(os.environ.get("FIELD_WEATHER_DIR", r"D:\Reolink_record\audio_in\weather_data"))

# Heuristic RISK thresholds (condensation cues, not a validated fog model). Documented, tunable, and
# used ONLY to label risk — never to gate a measurement.
GAP_HIGH_C = 1.5     # dewpoint gap <= this  -> air within 1.5 C of saturation -> strong condensation risk
GAP_MED_C = 3.0
RH_HIGH = 97.0       # %
RH_MED = 92.0
PREDAWN = (3, 8)     # local-hour window (radiative cooling + the observed pre-dawn fog window); weak cue only
WX_TOL_MIN = 30.0    # nearest-weather-sample tolerance; beyond this a bin gets no fog-risk (NaN)

FOG_COLS = ["fog_risk_level", "fog_risk_reason", "dewpoint_gap",
            "humidity_pct", "rain_mm_hr", "weather_lag_min"]

_AWN = {"Outdoor Temperature (°C)": "air_temp_c", "Dew Point (°C)": "dew_point_c",
        "Humidity (%)": "humidity_pct", "Rain Rate (mm/hr)": "rain_mm_hr"}


def classify(air_temp_c, dew_point_c, humidity_pct, rain_mm_hr, hour):
    """(fog_risk_level, fog_risk_reason, dewpoint_gap) from weather. Risk ESTIMATE only."""
    gap = None if (air_temp_c is None or dew_point_c is None or pd.isna(air_temp_c) or pd.isna(dew_point_c)) \
        else float(air_temp_c) - float(dew_point_c)
    rh = None if humidity_pct is None or pd.isna(humidity_pct) else float(humidity_pct)
    wet = rain_mm_hr is not None and not pd.isna(rain_mm_hr) and float(rain_mm_hr) > 0
    predawn = hour is not None and PREDAWN[0] <= int(hour) < PREDAWN[1]
    high = ((gap is not None and gap <= GAP_HIGH_C) or (rh is not None and rh >= RH_HIGH)
            or (wet and gap is not None and gap <= GAP_MED_C))
    med = ((gap is not None and gap <= GAP_MED_C) or (rh is not None and rh >= RH_MED) or wet
           or (predawn and rh is not None and rh >= 85))
    level = "high" if high else "medium" if med else "low"
    reasons = []
    if gap is not None and gap <= GAP_MED_C:
        reasons.append(f"dewpoint gap {gap:.1f}C")
    if rh is not None and rh >= RH_MED:
        reasons.append(f"RH {rh:.0f}%")
    if wet:
        reasons.append(f"rain {float(rain_mm_hr):.1f}mm/hr")
    if predawn:
        reasons.append("pre-dawn")
    return level, ("; ".join(reasons) or "clear/dry"), gap


def load_weather(weather_dir=None):
    """Load AWN CSV export(s) -> tidy frame [ts(local naive), air_temp_c, dew_point_c, humidity_pct,
    rain_mm_hr, dewpoint_gap, fog_risk_level, fog_risk_reason], time-sorted + deduped. None if unavailable."""
    d = Path(weather_dir) if weather_dir else DEFAULT_WEATHER_DIR
    files = sorted(d.glob("AWN-*.csv")) if d.exists() else []
    if not files:
        return None
    frames = []
    for p in files:
        try:
            df = pd.read_csv(p, encoding="utf-8-sig")
        except Exception:  # noqa: BLE001
            continue
        if "Date" not in df.columns:
            continue
        ts = pd.to_datetime(df["Date"], errors="coerce")
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_localize(None)                 # strip -04:00 -> LOCAL wallclock
        df = df.rename(columns=_AWN)
        keep = [c for c in _AWN.values() if c in df.columns]
        frames.append(df.assign(ts=ts)[["ts"] + keep])
    if not frames:
        return None
    w = (pd.concat(frames, ignore_index=True).dropna(subset=["ts"])
         .drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True))
    w["dewpoint_gap"] = w["air_temp_c"] - w["dew_point_c"]
    cls = [classify(r.air_temp_c, r.dew_point_c, r.humidity_pct, r.rain_mm_hr, r.ts.hour)
           for r in w.itertuples()]
    w["fog_risk_level"] = [c[0] for c in cls]
    w["fog_risk_reason"] = [c[1] for c in cls]
    return w


def fog_risk_at(ts, weather=None, weather_dir=None):
    """Nearest-in-time fog-risk record for a single timestamp (covariate), or None if no weather within
    WX_TOL_MIN. Never decides validity."""
    w = weather if weather is not None else load_weather(weather_dir)
    if w is None or w.empty:
        return None
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    i = (w["ts"] - t).abs().idxmin()
    lag = abs((w.loc[i, "ts"] - t).total_seconds()) / 60.0
    if lag > WX_TOL_MIN:
        return None
    r = w.loc[i]
    return {"fog_risk_level": r["fog_risk_level"], "fog_risk_reason": r["fog_risk_reason"],
            "dewpoint_gap": round(float(r["dewpoint_gap"]), 2), "humidity_pct": r["humidity_pct"],
            "rain_mm_hr": r["rain_mm_hr"], "weather_lag_min": round(lag, 1)}


def annotate(df, ts, weather=None, weather_dir=None):
    """Return a COPY of df with the fog-risk covariate columns appended (nearest weather sample within
    WX_TOL_MIN). Existing columns untouched. `ts` = a column name in df or a sequence of timestamps."""
    out = df.copy()
    if len(out) == 0:
        for c in FOG_COLS:
            if c not in out.columns:
                out[c] = pd.Series(dtype="object")
        return out
    w = weather if weather is not None else load_weather(weather_dir)
    tvals = pd.to_datetime(out[ts] if isinstance(ts, str) and ts in out.columns
                           else pd.Series(list(ts), index=out.index), errors="coerce")
    if w is None or w.empty:
        for c in FOG_COLS:
            out[c] = pd.NA
        return out
    tmp = pd.DataFrame({"_ts": tvals.values, "_ord": range(len(out))}).dropna(subset=["_ts"]).sort_values("_ts")
    tmp["_ts"] = tmp["_ts"].astype("datetime64[ns]")            # normalize resolution (merge_asof needs matching dtype)
    wj = w.copy(); wj["ts"] = wj["ts"].astype("datetime64[ns]")
    m = pd.merge_asof(tmp, wj, left_on="_ts", right_on="ts", direction="nearest",
                      tolerance=pd.Timedelta(minutes=WX_TOL_MIN))
    m["weather_lag_min"] = (m["_ts"] - m["ts"]).abs().dt.total_seconds() / 60.0
    m = m.set_index("_ord")
    for c in FOG_COLS:
        col = pd.Series([pd.NA] * len(out), index=out.index, dtype="object")
        if c in m.columns:
            col.loc[m.index] = m[c].values
        out[c] = col
    return out

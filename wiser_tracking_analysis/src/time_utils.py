"""
time_utils.py — Detect and convert WISER timestamp formats.

Supports:
    Unix milliseconds  (13-digit integer ~1e12)
    Unix seconds       (10-digit integer ~1e9)
    ISO 8601 strings   (e.g. "2024-03-15T10:22:05")
    Float seconds with sub-second precision
"""

import pandas as pd
import numpy as np
import warnings


# Reasonable epoch bounds for plausibility checks.
_EPOCH_MS_MIN = 1_000_000_000_000   # 2001-09-09 in ms
_EPOCH_MS_MAX = 9_999_999_999_999   # 2286 in ms
_EPOCH_S_MIN  = 1_000_000_000       # 2001-09-09 in s
_EPOCH_S_MAX  = 9_999_999_999       # 2286 in s


def _detect_format(series: pd.Series) -> str:
    """
    Inspect a raw timestamp column and return one of:
        'unix_ms'   — Unix milliseconds (integer or float)
        'unix_s'    — Unix seconds (integer or float)
        'datetime'  — parseable string / already datetime-like
        'unknown'   — unrecognised; conversion will be attempted anyway
    """
    # Drop nulls for inspection.
    sample = series.dropna()
    if sample.empty:
        return "unknown"

    # Try numeric interpretation first.
    try:
        numeric = pd.to_numeric(sample, errors="raise")
        median = float(numeric.median())

        if _EPOCH_MS_MIN <= median <= _EPOCH_MS_MAX:
            return "unix_ms"
        if _EPOCH_S_MIN <= median <= _EPOCH_S_MAX:
            return "unix_s"
        # Float seconds with sub-second component (e.g. 1_700_000_000.123)
        if _EPOCH_S_MIN / 1000 <= median <= _EPOCH_S_MAX * 10:
            return "unix_s"

    except (ValueError, TypeError):
        pass  # Not purely numeric — try string parsing below.

    # Try treating as a string datetime.
    try:
        pd.to_datetime(sample.iloc[0])
        return "datetime"
    except Exception:
        pass

    return "unknown"


def convert_timestamps(df: pd.DataFrame, raw_col: str = "ts_raw") -> pd.DataFrame:
    """
    Add a 'datetime' column (timezone-naive UTC) to *df* by converting *raw_col*.

    Also adds a 'elapsed_s' column (seconds since the first observation).

    Prints a one-line note about which format was detected.
    """
    if raw_col not in df.columns:
        raise KeyError(f"Column '{raw_col}' not found in DataFrame.")

    fmt = _detect_format(df[raw_col])
    print(f"  Timestamp format detected: {fmt}")

    if fmt == "unix_ms":
        df["datetime"] = pd.to_datetime(
            pd.to_numeric(df[raw_col], errors="coerce"), unit="ms", utc=True
        ).dt.tz_localize(None)

    elif fmt == "unix_s":
        df["datetime"] = pd.to_datetime(
            pd.to_numeric(df[raw_col], errors="coerce"), unit="s", utc=True
        ).dt.tz_localize(None)

    elif fmt == "datetime":
        df["datetime"] = pd.to_datetime(df[raw_col], errors="coerce", utc=False)
        # Strip timezone info to keep everything comparable.
        if df["datetime"].dt.tz is not None:
            df["datetime"] = df["datetime"].dt.tz_localize(None)

    else:
        warnings.warn(
            f"[time_utils] Could not detect timestamp format for column '{raw_col}'. "
            "Attempting generic pd.to_datetime conversion."
        )
        df["datetime"] = pd.to_datetime(df[raw_col], errors="coerce")

    n_bad = df["datetime"].isna().sum()
    if n_bad > 0:
        warnings.warn(f"[time_utils] {n_bad} timestamps could not be converted and are NaT.")

    # Elapsed seconds from first valid timestamp.
    t0 = df["datetime"].min()
    df["elapsed_s"] = (df["datetime"] - t0).dt.total_seconds()

    return df


def trim_last_n_minutes(df: pd.DataFrame, minutes: float = 10) -> pd.DataFrame:
    """
    Remove rows from the last *minutes* of the recording.

    Uses the 'datetime' column; call convert_timestamps() first.
    """
    if "datetime" not in df.columns:
        raise KeyError("'datetime' column missing. Run convert_timestamps() first.")

    cutoff = df["datetime"].max() - pd.Timedelta(minutes=minutes)
    before = len(df)
    df = df[df["datetime"] <= cutoff].copy()
    removed = before - len(df)
    print(f"  Trimmed {removed:,} rows from the last {minutes} minutes "
          f"(cutoff: {cutoff}).")
    return df

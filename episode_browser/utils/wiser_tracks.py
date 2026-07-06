"""
wiser_tracks.py — load REAL WISER UWB positions from the daily backup. Data-layer ONLY.

Reads the read-only gzipped daily CSV exports under the WISER backup
(`incremental/1stcohort_2026_<date>.csv.gz`), the canonical WISER schema
(`shortid, location_x, location_y, timestamp[, calculation_error]`). Positions are
**inches in the WISER native OFFSET frame** — UNVERIFIED vs the physical paddock cm
frame. This module never converts inches→cm: until the georeference transform is
confirmed, real WISER tracks must be shown in their own inch frame, not on the cm
paddock (see CLAUDE.md / field_transform).

Timestamps are Unix-ms UTC (same clock the episode store uses), so filtering by the
browser's UTC window aligns to the same wall-clock moment.

Light by construction: only loads the day file(s) covering the requested window, reads
just the needed columns, and downsamples per tag for plotting.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
ROIS_PATH = REPO_ROOT / "wiser_tracking_analysis" / "configs" / "wiser_rois.json"
DEFAULT_DIR = r"D:\Reolink_record\audio_in\Wiser_backup"
EDT_TZ = "Etc/GMT+4"
_COLS = ["shortid", "rat", "x", "y", "ts", "calc_err"]


def backup_dir() -> Path:
    return Path(os.environ.get("EPISODE_BROWSER_WISER_DIR", DEFAULT_DIR))


def _incremental_file(date_str: str) -> Path:
    return backup_dir() / "incremental" / f"1stcohort_2026_{date_str}.csv.gz"


def read_day(date_str: str) -> pd.DataFrame:
    """Full day file -> [shortid, x, y, ts, calc_err] (inches, Unix-ms). Heavy; cache it.

    Empty typed frame if the file is absent/unreadable.
    """
    empty = pd.DataFrame(columns=["shortid", "x", "y", "ts", "calc_err"])
    p = _incremental_file(date_str)
    if not p.exists():
        return empty
    try:
        df = pd.read_csv(
            p, compression="gzip",
            usecols=["shortid", "location_x", "location_y", "timestamp", "calculation_error"],
        )
    except Exception:  # noqa: BLE001
        return empty
    return df.rename(columns={"location_x": "x", "location_y": "y",
                              "timestamp": "ts", "calculation_error": "calc_err"})


def candidate_dates(t0_ms: int, t1_ms: int) -> list[str]:
    """EDT dates whose day-file may cover [t0, t1] (a file spans ~2 days), most-likely first."""
    d0 = pd.to_datetime(int(t0_ms), unit="ms", utc=True).tz_convert(EDT_TZ)
    return list(dict.fromkeys(
        (d0 + pd.Timedelta(days=k)).strftime("%Y-%m-%d") for k in (0, 1, -1)))


def filter_window(day_df: pd.DataFrame, t0_ms: int, t1_ms: int,
                  name_map: Optional[dict] = None,
                  shortids: Optional[set[str]] = None,
                  max_per_rat: int = 600) -> pd.DataFrame:
    """Rows in [t0, t1], optionally limited to `shortids`, tagged with a `rat` name and
    downsampled to ~max_per_rat points per tag for light plotting."""
    if day_df.empty:
        return pd.DataFrame(columns=_COLS)
    w = day_df[(day_df["ts"] >= t0_ms) & (day_df["ts"] <= t1_ms)].copy()
    if shortids:
        w = w[w["shortid"].astype(str).isin(shortids)]
    if w.empty:
        return pd.DataFrame(columns=_COLS)
    # shortid is a TAG id — resolve to the animal name via rat_identities.csv
    # (the roster in CLAUDE.md / FIELD_OBSERVATIONS): 12378 Siesta, 12395 Sen,
    # 12407 Dormi, 12386 Nox, 12380 Hypnos, 12409 Sova.
    name_map = name_map or {}
    w["rat"] = w["shortid"].map(lambda s: name_map.get(str(s), str(s)))

    # Downsample per tag with a vectorised mask (NOT groupby.apply — pandas >= 2.2
    # drops the grouping column from apply, which broke `w[_COLS]`).
    w = w.sort_values(["shortid", "ts"])
    rank = w.groupby("shortid").cumcount()
    size = w.groupby("shortid")["ts"].transform("size")
    step = (size // max_per_rat).clip(lower=1)
    w = w[rank % step == 0]
    return w[_COLS]


def load_boundary() -> Optional[dict]:
    """WISER paddock boundary rect in inches: {x0, x1, y0, y1}. None if unavailable."""
    if not ROIS_PATH.exists():
        return None
    try:
        text = re.sub(r"//.*", "", ROIS_PATH.read_text(encoding="utf-8"))
        rect = json.loads(text)["boundary"]["rect"]      # [x_min, x_max, y_min, y_max]
        return {"x0": rect[0], "x1": rect[1], "y0": rect[2], "y1": rect[3]}
    except Exception:  # noqa: BLE001
        return None


def load_landmarks() -> dict:
    """WISER-frame landmarks (inches) from wiser_rois.json — the SAME frame as the tracks.

    (wiser_rois derives the house boxes from field_layout.json's shelters, then places them
    in the WISER offset inch frame.) Returns {"rects": DataFrame[name,type,x0,x1,y0,y1],
    "points": DataFrame[name,type,x,y]}. Empty frames if unavailable.
    """
    rects, points = [], []
    if ROIS_PATH.exists():
        try:
            data = json.loads(re.sub(r"//.*", "", ROIS_PATH.read_text(encoding="utf-8")))
            for roi in data.get("rois", []):
                name, typ = roi.get("name", "?"), roi.get("type", "")
                x, y = roi.get("x"), roi.get("y")
                if x is None or y is None:
                    continue
                if roi.get("shape") == "rect" and roi.get("width_in") and roi.get("height_in"):
                    w, h = roi["width_in"], roi["height_in"]
                    if float(roi.get("orientation_deg", 0)) % 180 == 90:
                        w, h = h, w
                    rects.append({"name": name, "type": typ,
                                  "x0": x - w / 2, "x1": x + w / 2, "y0": y - h / 2, "y1": y + h / 2})
                else:
                    points.append({"name": name, "type": typ, "x": x, "y": y})
        except Exception:  # noqa: BLE001
            pass
    return {"rects": pd.DataFrame(rects, columns=["name", "type", "x0", "x1", "y0", "y1"]),
            "points": pd.DataFrame(points, columns=["name", "type", "x", "y"])}


def availability() -> dict:
    d = backup_dir()
    inc = d / "incremental"
    return {"wiser_dir": str(d), "exists": d.exists(),
            "day_files": len(list(inc.glob("1stcohort_2026_*.csv.gz"))) if inc.exists() else 0}

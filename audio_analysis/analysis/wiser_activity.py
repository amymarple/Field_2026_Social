"""Per-hour rat ACTIVITY from WISER UWB, aligned to local wallclock for the soundscape panels.

Reuses the `wiser_tracking_analysis` pipeline unchanged and **read-only**: `load_wiser_session`
(mode=ro + PRAGMA query_only) → `convert_timestamps` (Unix ms → naive **UTC**) → `add_speed` →
`add_validity_flags` → `hourly_activity`. WISER time is UTC; audio/weather are local wallclock, so
activity is shifted by `tz_offset_hours` (default −4 = EDT). This is a **cross-device (WISER computer
vs camera/NVR) alignment and is UNVERIFIED**. "Activity" = above-noise-floor movement in WISER inches
(NO georeference / spatial claim). Tag 12409 (Sova, deceased 2026-06-29 ~15:00) is dropped.

Data source: the transferred, read-only backup snapshots under
`D:\Reolink_record\audio_in\Wiser_backup\snapshots\` (full DB copies — the latest has the most
history; a per-day filter selects the requested local dates).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# Reuse the WISER analysis layer (imported flat, like its own scripts do).
REPO_ROOT = Path(__file__).resolve().parents[2]
_WISER_SRC = REPO_ROOT / "wiser_tracking_analysis" / "src"
if str(_WISER_SRC) not in sys.path:
    sys.path.insert(0, str(_WISER_SRC))

import wiser_analysis_utils as w   # noqa: E402
import time_utils as wtime         # noqa: E402

DEFAULT_SNAP_DIR = Path(r"D:\Reolink_record\audio_in\Wiser_backup\snapshots")
DROP_TAGS_DEFAULT = frozenset({"12409"})   # Sova, deceased 2026-06-29 ~15:00
IN_TO_M = 0.0254

_EMPTY = ["ts_local", "active_distance_m", "active_frac", "n_fixes"]


def latest_snapshot(snap_dir=DEFAULT_SNAP_DIR):
    """Newest full-DB snapshot (most history) under `snap_dir`, or None."""
    snaps = sorted(Path(snap_dir).glob("1stcohort_2026_*.sqlite"))
    return snaps[-1] if snaps else None


def hourly_rat_activity(dates, snapshot=None, *, tz_offset_hours: int = -4,
                        drop_tags=DROP_TAGS_DEFAULT, active_speed_inps: float = 12.0) -> pd.DataFrame:
    """Per-**local-hour** rat activity for the given local `dates` (iterable of 'YYYY-MM-DD').

    Returns columns: ``ts_local`` (local hour start), ``active_distance_m`` (summed over the retained
    rats — above-noise-floor path length), ``active_frac`` (mean fraction of time moving, 0–1),
    ``n_fixes``. Empty frame if nothing loads. Read-only; never writes to the WISER backup.
    """
    snapshot = Path(snapshot) if snapshot else latest_snapshot()
    if snapshot is None or not Path(snapshot).exists():
        return pd.DataFrame(columns=_EMPTY)

    df = w.load_wiser_session(snapshot)                 # read-only; keeps anchors_used/calc_error
    if df is None or df.empty:
        return pd.DataFrame(columns=_EMPTY)
    df = wtime.convert_timestamps(df)                   # -> naive UTC 'datetime'
    df = w.add_speed(df)
    df = w.add_validity_flags(df)
    if drop_tags:
        df = df[~df["shortid"].astype(str).isin({str(t) for t in drop_tags})]

    act = w.hourly_activity(df, active_speed_inps=active_speed_inps,
                            tz_offset_hours=tz_offset_hours, valid_only=True)
    gh = act["group_hour"].copy()
    gh["ts_local"] = pd.to_datetime(gh["hour_bin_utc"]) + pd.Timedelta(hours=tz_offset_hours)
    gh["active_distance_m"] = gh["active_distance_in"] * IN_TO_M

    want = {str(d) for d in dates}
    gh = gh[gh["ts_local"].dt.strftime("%Y-%m-%d").isin(want)]
    return (gh[["ts_local", "active_distance_m", "active_frac", "n"]]
            .rename(columns={"n": "n_fixes"})
            .sort_values("ts_local").reset_index(drop=True))

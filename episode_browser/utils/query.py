"""
query.py — filtering & lens ranking over an episode DataFrame. Data-layer ONLY.

Kept out of the UI so the same filter/rank logic serves scripts, notebooks, and a
future non-Streamlit frontend. Two rules encoded here that the spec insists on:

  * Absent lens scores are filterable AS ABSENT, never as zero. A score range
    filter EXCLUDES episodes lacking that score unless `include_absent=True`.
  * Ranking by a lens drops (does not zero-fill) episodes without that lens, so a
    ranking never invents a score of 0 for an unscored episode.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def _has_lens(row_scores, key: str) -> bool:
    return isinstance(row_scores, dict) and key in row_scores and row_scores[key] is not None


def lens_value(df: pd.DataFrame, key: str) -> pd.Series:
    """Series of a lens score, NaN where absent (absence preserved, not 0)."""
    return df.get("lens_scores", pd.Series([None] * len(df))).map(
        lambda s: s[key] if _has_lens(s, key) else float("nan")
    )


def filter_episodes(
    df: pd.DataFrame,
    t_start_ms: Optional[int] = None,
    t_end_ms: Optional[int] = None,
    levels: Optional[list[str]] = None,
    subjects: Optional[list[str]] = None,
    labels: Optional[list[str]] = None,
    zones: Optional[list[str]] = None,
    source_streams: Optional[list[str]] = None,
    state_model_ids: Optional[list[str]] = None,
    qc_flags: Optional[list[str]] = None,
    min_boundary_conf: Optional[float] = None,
    min_identity_conf: Optional[float] = None,
    min_tracking_quality: Optional[float] = None,
    rain: Optional[bool] = None,
    light_phase: Optional[list[str]] = None,
    lens_key: Optional[str] = None,
    lens_min: Optional[float] = None,
    lens_max: Optional[float] = None,
    include_absent_lens: bool = True,
) -> pd.DataFrame:
    """Apply the browser's filters. Missing criteria are skipped (None = no filter)."""
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)

    # Time overlap (episode intersects the window), not strict containment.
    if t_start_ms is not None:
        m &= df["t_end"] >= t_start_ms
    if t_end_ms is not None:
        m &= df["t_start"] <= t_end_ms

    if levels:
        m &= df["level"].isin(levels)
    if state_model_ids:
        m &= df["state_model_id"].isin(state_model_ids)

    def _list_any(col: str, wanted: list[str]) -> pd.Series:
        return df[col].map(lambda v: bool(set(v or []) & set(wanted)) if isinstance(v, list) else False)

    if subjects:
        m &= _list_any("subject_ids", subjects)
    if labels:
        m &= _list_any("labels", labels)
    if source_streams:
        m &= _list_any("source_streams", source_streams)
    if qc_flags:
        m &= _list_any("qc_flags", qc_flags)

    if zones:
        m &= df["zones"].map(
            lambda z: bool(set((z or {}).keys()) & set(zones)) if isinstance(z, dict) else False
        )

    for col, thr in (("boundary_confidence", min_boundary_conf),
                     ("identity_confidence", min_identity_conf),
                     ("tracking_quality", min_tracking_quality)):
        if thr is not None:
            m &= df[col].fillna(-1) >= thr

    if rain is not None:
        m &= df["environment_context"].map(
            lambda e: bool(e.get("rain")) == rain if isinstance(e, dict) else False
        )
    if light_phase:
        m &= df["environment_context"].map(
            lambda e: e.get("light_phase") in light_phase if isinstance(e, dict) else False
        )

    # Lens score range — absence handled explicitly.
    if lens_key and (lens_min is not None or lens_max is not None):
        vals = lens_value(df, lens_key)
        present = vals.notna()
        in_range = pd.Series(True, index=df.index)
        if lens_min is not None:
            in_range &= vals >= lens_min
        if lens_max is not None:
            in_range &= vals <= lens_max
        m &= (present & in_range) | (~present & include_absent_lens)

    return df[m]


def rank_by_lens(df: pd.DataFrame, key: str, descending: bool = True,
                 drop_absent: bool = True) -> pd.DataFrame:
    """Sort by a lens score. Episodes lacking the score are dropped (drop_absent)
    or sorted last — NEVER treated as 0."""
    vals = lens_value(df, key)
    out = df.assign(_lens=vals)
    if drop_absent:
        out = out[out["_lens"].notna()]
    out = out.sort_values("_lens", ascending=not descending, na_position="last")
    return out.drop(columns="_lens")


def _search_blob(row: dict, name_map: Optional[dict] = None) -> str:
    """Flatten an episode's text-ish fields into one lowercase search string."""
    parts: list[str] = []
    for col in ("episode_id", "level", "state_model_id", "notes"):
        v = row.get(col)
        if v:
            parts.append(str(v))
    for col in ("labels", "source_streams", "qc_flags"):
        v = row.get(col)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
    subs = row.get("subject_ids")
    if isinstance(subs, list):
        for s in subs:
            parts.append(str(s))                       # tag id
            if name_map:
                parts.append(str(name_map.get(str(s), "")))   # resolved animal name
    z = row.get("zones")
    if isinstance(z, dict):
        parts.extend(z.keys())
    return " ".join(parts).lower()


def text_search(df: pd.DataFrame, query_str: str, name_map: Optional[dict] = None) -> pd.DataFrame:
    """NCBI-style free-text search across an episode 'index'.

    Case-insensitive, AND over whitespace-separated tokens (each token must appear
    somewhere in the episode's flattened text: id, level, state_model, subjects
    (tag id + resolved name), labels, zones, source streams, QC flags, notes).
    Empty query returns df unchanged.
    """
    q = (query_str or "").strip().lower()
    if not q or df.empty:
        return df
    tokens = q.split()
    blobs = df.apply(lambda r: _search_blob(r.to_dict(), name_map), axis=1)
    mask = blobs.map(lambda b: all(t in b for t in tokens))
    return df[mask]


def available_values(df: pd.DataFrame) -> dict:
    """Distinct filterable values present in the data, for populating UI widgets."""
    def _flat(col):
        if col not in df.columns:
            return []
        s = set()
        for v in df[col]:
            if isinstance(v, list):
                s.update(v)
        return sorted(s)

    def _keys(col):
        if col not in df.columns:
            return []
        s = set()
        for v in df[col]:
            if isinstance(v, dict):
                s.update(v.keys())
        return sorted(s)

    return {
        "levels": sorted(df["level"].dropna().unique().tolist()) if "level" in df else [],
        "state_model_ids": sorted(df["state_model_id"].dropna().unique().tolist()) if "state_model_id" in df else [],
        "subjects": _flat("subject_ids"),
        "labels": _flat("labels"),
        "source_streams": _flat("source_streams"),
        "qc_flags": _flat("qc_flags"),
        "zones": _keys("zones"),
        "lens_keys": _keys("lens_scores"),
    }

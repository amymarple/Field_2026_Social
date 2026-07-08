"""
coverage.py — episode tiling & gap computation. Data-layer ONLY (no UI).

The COMPLETENESS INVARIANT, made computable: the substrate tiles time. Where no
episode exists, that is a GAP — the substrate failing to exist — and it must be
rendered, never blanked. This module turns an episode set + a record span into:

  * episoded intervals (the episodes themselves),
  * un-episoded gaps (the complement within the record span),
  * a "% of record tiled" completeness metric,
  * gap reasons, when a companion gaps table supplies them (tracking_lost,
    occlusion, no_data). Absent a reason, a gap is 'no_data' (unknown), not hidden.

Everything is computed per (subject_id, level) so the coverage timeline can render
one lane per subject per level.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

GAP_REASONS = ("tracking_lost", "occlusion", "no_data")


@dataclass
class Interval:
    t_start: int          # Unix ms
    t_end: int
    kind: str             # 'episode' | 'gap'
    reason: Optional[str] = None      # gap reason, or None for episodes
    episode_id: Optional[str] = None

    @property
    def duration_s(self) -> float:
        return (self.t_end - self.t_start) / 1000.0


def _merge_intervals(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/touching [start, end] spans."""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _explode_subjects(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (subject_id, episode). subject_ids is a list; 'unknown' is kept."""
    if "subject_ids" not in df.columns:
        return df.assign(subject_id="unknown")
    rows = df.copy()
    rows["subject_id"] = rows["subject_ids"].map(
        lambda v: v if isinstance(v, list) and v else ["unknown"]
    )
    return rows.explode("subject_id", ignore_index=True)


def record_span(df: pd.DataFrame) -> tuple[int, int]:
    """Overall [min t_start, max t_end] across all episodes (Unix ms)."""
    if df.empty:
        return (0, 0)
    return (int(df["t_start"].min()), int(df["t_end"].max()))


def compute_coverage(df: pd.DataFrame,
                     gaps_df: Optional[pd.DataFrame] = None,
                     span: Optional[tuple[int, int]] = None) -> dict:
    """Compute per-(subject, level) tiling.

    Returns {(subject_id, level): {"intervals": [Interval...],
                                   "tiled_s": float, "span_s": float,
                                   "pct_tiled": float}}.

    `gaps_df` (optional) columns: subject_id, level, t_start, t_end, reason —
    lets the caller attribute WHY a gap exists. Reasons only annotate; they never
    create or remove coverage. Where a computed gap overlaps a reason row, it takes
    that reason; otherwise 'no_data'.
    """
    if df.empty:
        return {}
    span = span or record_span(df)
    s0, s1 = span
    ex = _explode_subjects(df)

    result: dict = {}
    for (subj, level), grp in ex.groupby(["subject_id", "level"]):
        eps = _merge_intervals([(int(r.t_start), int(r.t_end)) for r in grp.itertuples()])

        intervals: list[Interval] = []
        # Episode intervals (keep episode_id where a single episode maps 1:1).
        for r in grp.itertuples():
            intervals.append(Interval(int(r.t_start), int(r.t_end), "episode",
                                      episode_id=getattr(r, "episode_id", None)))

        # Gaps = complement of merged episode spans within [s0, s1].
        cursor = s0
        gap_spans: list[tuple[int, int]] = []
        for gs, ge in eps:
            if gs > cursor:
                gap_spans.append((cursor, gs))
            cursor = max(cursor, ge)
        if cursor < s1:
            gap_spans.append((cursor, s1))

        for gs, ge in gap_spans:
            intervals.append(Interval(gs, ge, "gap",
                                      reason=_gap_reason(gaps_df, subj, level, gs, ge)))

        tiled_s = sum((e - s) for s, e in eps) / 1000.0
        span_s = max(0.0, (s1 - s0) / 1000.0)
        result[(subj, level)] = {
            "intervals": sorted(intervals, key=lambda iv: iv.t_start),
            "tiled_s": tiled_s,
            "span_s": span_s,
            "pct_tiled": (100.0 * tiled_s / span_s) if span_s > 0 else 0.0,
        }
    return result


def _gap_reason(gaps_df: Optional[pd.DataFrame], subj: str, level: str,
                gs: int, ge: int) -> str:
    """Attribute a reason to a gap by overlap with the companion gaps table."""
    if gaps_df is None or gaps_df.empty:
        return "no_data"
    m = gaps_df[
        (gaps_df["subject_id"] == subj)
        & (gaps_df["level"] == level)
        & (gaps_df["t_start"] < ge)
        & (gaps_df["t_end"] > gs)
    ]
    if m.empty:
        return "no_data"
    # Pick the reason covering the largest overlap.
    best, best_ov = "no_data", -1
    for r in m.itertuples():
        ov = min(ge, int(r.t_end)) - max(gs, int(r.t_start))
        if ov > best_ov:
            best, best_ov = str(r.reason), ov
    return best if best in GAP_REASONS else "no_data"


def overall_completeness(coverage: dict) -> float:
    """Record-wide '% of record tiled', pooled across all (subject, level) lanes."""
    tiled = sum(v["tiled_s"] for v in coverage.values())
    span = sum(v["span_s"] for v in coverage.values())
    return (100.0 * tiled / span) if span > 0 else 0.0

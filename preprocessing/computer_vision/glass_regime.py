"""Annotation-only lookup of the shelter IR-glass optical regime (data_manifests/glass_treatments.yaml).

PURE + SIDE-EFFECT-FREE COVARIATE ANNOTATOR. This module NEVER changes detector output, view-quality,
motion, counts, safety rules, thresholds, filtering, exclusion, or validity. It only reports *which optical
regime was in effect* at a timestamp, so shelter outputs can carry that as metadata for later analysis to
stratify / annotate before pooling across a glass change. Nothing here decides anything.

It reads `data_manifests/glass_treatments.yaml` (the step-function regime timeline: bare -> tape -> lift_1cm
-> antifog_film -> bare_seated_post_film). It does NOT touch `field_conditions.yaml` or the weather/fog path.

    from glass_regime import regime_at, annotate, GLASS_COLS
    seg = regime_at("2026-07-03 09:00", channel="CH05")   # -> the active regime dict (or None)
    df  = annotate(df, ts="t", channel="CH05")            # -> df + 6 glass_* covariate columns
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE.parents[1] / "data_manifests" / "glass_treatments.yaml"

# The covariate columns this module appends. All are annotation metadata — never inputs to any decision.
GLASS_COLS = ["glass_regime", "glass_layers", "glass_uncertain_layers",
              "glass_time_precision", "glass_confounded", "glass_regime_note"]


def load_regimes(path=None):
    """Parse glass_treatments.yaml -> (channels, regimes) with parsed datetimes; ([], []) if unavailable.

    Read-only. Segments are returned time-sorted with private `_start`/`_end` pandas Timestamps
    (`_end is None` = ongoing).
    """
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return None, []
    try:
        import yaml
    except ImportError:
        print("[glass_regime] PyYAML not installed; optical-regime annotation disabled.")
        return None, []
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        print(f"[glass_regime] could not read {p}: {e}")
        return None, []
    channels = doc.get("channels")
    regs = []
    for r in doc.get("regimes", []):
        try:
            st = pd.Timestamp(r["start"])
        except Exception:  # noqa: BLE001
            continue
        end = r.get("end")
        en = None if end in (None, "null", "") else pd.Timestamp(end)
        regs.append({**r, "_start": st, "_end": en})
    regs.sort(key=lambda r: r["_start"])
    return channels, regs


def regime_at(ts, channel=None, regimes=None, channels=None, path=None):
    """The active optical-regime segment dict at `ts` (a COVARIATE lookup), or None.

    Returns None when `ts` is outside all segments, unparseable, or `channel` is not one of the file's
    covered channels (glass regimes apply to the shelter cams only). This never decides validity/exclusion.
    """
    if regimes is None:
        channels, regimes = load_regimes(path)
    if channel is not None and channels and channel not in channels:
        return None
    t = pd.Timestamp(ts) if not isinstance(ts, pd.Timestamp) else ts
    if t is None or pd.isna(t):
        return None
    for r in regimes:
        en = r["_end"]
        if r["_start"] <= t and (en is None or t < en):
            return r
    return None


def _cell(seg):
    """Flatten one regime segment (or None) into the 6 covariate column values."""
    if seg is None:
        return {c: None for c in GLASS_COLS}
    conf = seg.get("confounded")
    return {
        "glass_regime": seg.get("regime"),
        "glass_layers": "|".join(seg.get("layers") or []),
        "glass_uncertain_layers": "|".join(seg.get("uncertain_layers") or []),
        "glass_time_precision": seg.get("time_precision"),
        "glass_confounded": bool(conf.get("value")) if isinstance(conf, dict) else bool(conf),
        "glass_regime_note": seg.get("note"),
    }


def annotate(df, ts, channel=None, path=None):
    """Return a COPY of `df` with the 6 glass_* covariate columns APPENDED. Existing columns are never
    touched (same rows, same order, same values) — this is annotation-only.

    ts      : a column name in df, OR a sequence (len == len(df)) of absolute timestamps.
    channel : a column name in df, OR a scalar channel (e.g. "CH05"), OR None.
    """
    out = df.copy()
    if len(out) == 0:                                   # keep the columns present even on an empty frame
        for c in GLASS_COLS:
            if c not in out.columns:
                out[c] = pd.Series(dtype="object")
        return out
    channels, regimes = load_regimes(path)
    ts_vals = out[ts] if isinstance(ts, str) and ts in out.columns else pd.Series(list(ts), index=out.index)
    if isinstance(channel, str) and channel in out.columns:
        ch_vals = out[channel]
    else:
        ch_vals = pd.Series([channel] * len(out), index=out.index)
    cells = [_cell(regime_at(t, ch, regimes, channels)) for t, ch in zip(ts_vals, ch_vals)]
    ann = pd.DataFrame(cells, index=out.index)
    for c in GLASS_COLS:                                # append only; never overwrite an existing column
        out[c] = ann[c]
    return out

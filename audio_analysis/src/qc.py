"""Quality-control flags for each analysis window.

A single primary ``qc_flag`` per window (precedence below), plus a ``clipped`` boolean
recorded independently and a ``valid_audio`` boolean (True only when qc_flag == 'ok').

Flags (high -> low precedence):
    decode_error   ffmpeg could not decode this file
    too_short      usable audio shorter than min_window_s
    partial_window trailing remainder shorter than a full window (and not processed)
    pre_mic_enable window starts before the mic was turned on (silent, not meaningful)
    silent         Leq at/below the silence floor (mic off / no signal)
    timeline_gap   window is fine, but a recording discontinuity precedes this file
    clipped        amplitude hit full scale (level unreliable)
    ok             usable
"""
from __future__ import annotations

OK = "ok"
SILENT = "silent"
PRE_MIC_ENABLE = "pre_mic_enable"
CLIPPED = "clipped"
DECODE_ERROR = "decode_error"
TIMELINE_GAP = "timeline_gap"
PARTIAL_WINDOW = "partial_window"
TOO_SHORT = "too_short"

ALL_FLAGS = (OK, SILENT, PRE_MIC_ENABLE, CLIPPED, DECODE_ERROR,
             TIMELINE_GAP, PARTIAL_WINDOW, TOO_SHORT)


def classify_window(*, decode_error: bool, audio_duration_s: float, window_s: float,
                    min_window_s: float, is_partial: bool, before_mic_on: bool,
                    leq_dbfs, silence_dbfs: float, clipped: bool,
                    gap_before: bool) -> str:
    """Return the single primary QC flag for a window (see module docstring)."""
    if decode_error:
        return DECODE_ERROR
    if audio_duration_s is None or audio_duration_s < min_window_s:
        return TOO_SHORT
    if is_partial:
        return PARTIAL_WINDOW
    if before_mic_on:
        return PRE_MIC_ENABLE
    if leq_dbfs is None or leq_dbfs <= silence_dbfs:
        return SILENT
    if gap_before:
        return TIMELINE_GAP
    if clipped:
        return CLIPPED
    return OK


def is_valid(qc_flag: str) -> bool:
    """Whether the window's audio is usable for level/soundscape analysis."""
    return qc_flag == OK

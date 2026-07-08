"""Per-window audio features.

ALL level values are RELATIVE camera-mic dBFS (full-scale reference = 1.0 on float PCM),
NOT calibrated SPL. The ecoacoustic indices are band-limited camera-specific variants
(8 kHz ceiling) named accordingly; compare only within this dataset.

Returns a flat dict per window so the extract script can map straight to CSV columns.
Any scikit-maad index that cannot be computed yields NaN and a note (never raises).
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np
from scipy.signal import welch

DBFS_FLOOR = -120.0          # clamp for level dBFS (digital silence -> this)
POWER_DB_FLOOR = -200.0      # clamp for spectral band power in dB
NAN = float("nan")


def _dbfs_from_meansquare(ms: float) -> float:
    if ms is None or ms <= 0 or not math.isfinite(ms):
        return DBFS_FLOOR
    return max(10.0 * math.log10(ms), DBFS_FLOOR)


def _level_features(x: np.ndarray, sr: int, subframe_s: float,
                    silence_dbfs: float) -> Dict[str, float]:
    """Leq, L10/L50/L90 percentile levels, peak, and silent-subframe count (dBFS)."""
    leq = _dbfs_from_meansquare(float(np.mean(x.astype(np.float64) ** 2)))
    peak_amp = float(np.max(np.abs(x))) if x.size else 0.0
    peak = max(20.0 * math.log10(peak_amp), DBFS_FLOOR) if peak_amp > 0 else DBFS_FLOOR

    sub_n = max(int(round(subframe_s * sr)), 1)
    levels = []
    for i in range(0, len(x), sub_n):
        sub = x[i:i + sub_n]
        if sub.size == 0:
            continue
        levels.append(_dbfs_from_meansquare(float(np.mean(sub.astype(np.float64) ** 2))))
    levels = np.asarray(levels, dtype=np.float64)
    if levels.size:
        # Ln = level exceeded n% of the time => percentile(100 - n).
        l10 = float(np.percentile(levels, 90))
        l50 = float(np.percentile(levels, 50))
        l90 = float(np.percentile(levels, 10))
        n_silent = int(np.sum(levels <= silence_dbfs))
    else:
        l10 = l50 = l90 = DBFS_FLOOR
        n_silent = 0
    return {
        "leq_dbfs_relative": round(leq, 2),
        "l10_dbfs_relative": round(l10, 2),
        "l50_dbfs_relative": round(l50, 2),
        "l90_dbfs_relative": round(l90, 2),
        "peak_dbfs_relative": round(peak, 2),
        "n_silent_subframes": n_silent,
    }


def _spectral_features(x: np.ndarray, sr: int, bands: dict, nperseg: int,
                       noverlap: int, rolloff_pct: float = 0.85) -> Dict[str, float]:
    """Band energies (dB), spectral centroid and rolloff via Welch PSD."""
    nps = min(nperseg, len(x)) if len(x) else nperseg
    out = {f"{name}_db": POWER_DB_FLOOR for name in bands}
    out.update({"centroid_hz": NAN, "rolloff_hz": NAN})
    if len(x) < 8:
        return out
    f, psd = welch(x.astype(np.float64), fs=sr, nperseg=nps,
                   noverlap=min(noverlap, nps - 1))
    total = float(np.sum(psd))
    for name, (lo, hi) in bands.items():
        mask = (f >= lo) & (f < hi)
        bp = float(np.sum(psd[mask]))
        out[f"{name}_db"] = round(max(10.0 * math.log10(bp), POWER_DB_FLOOR), 2) \
            if bp > 0 else POWER_DB_FLOOR
    if total > 0:
        out["centroid_hz"] = round(float(np.sum(f * psd) / total), 1)
        cum = np.cumsum(psd)
        idx = int(np.searchsorted(cum, rolloff_pct * total))
        idx = min(idx, len(f) - 1)
        out["rolloff_hz"] = round(float(f[idx]), 1)
    return out


def _ecoacoustic_indices(x: np.ndarray, sr: int, nperseg: int, noverlap: int,
                         antro: tuple, bio: tuple) -> Dict[str, object]:
    """scikit-maad indices, band-limited to the camera's 8 kHz ceiling.

    Each index is computed independently; any failure -> NaN + a note, never raises.
    """
    out = {"aci": NAN, "bi_2_8k_camera": NAN,
           "ndsi_1_2k_vs_2_8k_camera": NAN, "adi": NAN}
    notes = []
    try:
        from maad import sound, features as maad_features
    except Exception as e:  # maad not importable
        out["index_notes"] = f"maad import failed: {e}"
        return out

    try:
        Sxx_power, tn, fn, ext = sound.spectrogram(
            x.astype(np.float64), sr, nperseg=nperseg,
            noverlap=min(noverlap, nperseg - 1), mode="psd")
        Sxx_amp = np.sqrt(np.maximum(Sxx_power, 0.0))
    except Exception as e:
        out["index_notes"] = f"spectrogram failed: {e}"
        return out

    def _scalar(v, pick=0):
        """Pull a float from a scalar / tuple / array maad return."""
        if isinstance(v, (tuple, list)):
            v = v[pick]
        v = np.asarray(v, dtype=np.float64)
        return float(v) if v.ndim == 0 else float(np.nansum(v))

    # ACI (amplitude spectrogram; sum is the 3rd return value)
    try:
        out["aci"] = round(_scalar(maad_features.acoustic_complexity_index(Sxx_amp), pick=2), 4)
    except Exception as e:
        notes.append(f"aci:{e}")
    # Bioacoustic Index, restricted to 2-8 kHz (power spectrogram)
    try:
        out["bi_2_8k_camera"] = round(
            _scalar(maad_features.bioacoustics_index(Sxx_power, fn, flim=bio)), 4)
    except Exception as e:
        notes.append(f"bi:{e}")
    # NDSI-like: anthropophony 1-2 kHz vs biophony 2-8 kHz (power spectrogram; NDSI = 1st return)
    try:
        out["ndsi_1_2k_vs_2_8k_camera"] = round(
            _scalar(maad_features.soundscape_index(
                Sxx_power, fn, flim_bioPh=bio, flim_antroPh=antro), pick=0), 4)
    except Exception as e:
        notes.append(f"ndsi:{e}")
    # Acoustic Diversity Index, capped at 8 kHz (amplitude spectrogram)
    try:
        out["adi"] = round(_scalar(maad_features.acoustic_diversity_index(
            Sxx_amp, fn, fmax=int(bio[1]))), 4)
    except Exception as e:
        notes.append(f"adi:{e}")

    if notes:
        out["index_notes"] = "; ".join(notes)
    return out


# Keys produced by the spectral + ecoacoustic stage (NaN-filled when skipped).
_SPECTRAL_KEYS = ("band_0_1k_db", "band_1_2k_db", "band_2_8k_db", "centroid_hz",
                  "rolloff_hz", "aci", "bi_2_8k_camera", "ndsi_1_2k_vs_2_8k_camera", "adi")


def spectral_and_indices(x: np.ndarray, sr: int, cfg: dict) -> Dict[str, object]:
    """The expensive stage: band energies, centroid/rolloff, and scikit-maad indices."""
    bands = {name: (float(lo), float(hi)) for name, (lo, hi) in cfg["bands"].items()}
    out: Dict[str, object] = {}
    out.update(_spectral_features(x, sr, bands, cfg["fft_nperseg"], cfg["fft_noverlap"]))
    out.update(_ecoacoustic_indices(
        x, sr, cfg["fft_nperseg"], cfg["fft_noverlap"],
        antro=tuple(cfg["anthropophony_band"]), bio=tuple(cfg["biophony_band"])))
    return out


def compute_features(x: np.ndarray, sr: int, cfg: dict, spectral: bool = True) -> Dict[str, object]:
    """Per-window features + clipping flag. ``cfg`` is the loaded YAML config.

    Level + clipping are always computed (cheap). The expensive spectral + ecoacoustic
    stage runs only when ``spectral`` is True; otherwise those columns are NaN. Callers
    skip it for silent / pre-mic windows (don't analyse silence — keeps the field PC light).
    """
    feats: Dict[str, object] = {}
    feats.update(_level_features(x, sr, cfg["subframe_s"], cfg["silence_dbfs"]))
    feats["clipped"] = bool(np.any(np.abs(x) >= cfg["clip_amplitude"])) if x.size else False
    if spectral:
        feats.update(spectral_and_indices(x, sr, cfg))
    else:
        feats.update({k: NAN for k in _SPECTRAL_KEYS})
    return feats

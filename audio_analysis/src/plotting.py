"""Optional, lightweight plots. Nothing here runs by default during extraction.

Plots are driven by the already-written feature CSVs (cheap), except the single-window
spectrogram which decodes just one short window on demand. No all-day spectrograms.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Use a non-interactive backend so this works headless on the field PC.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _valid(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["window_start_timestamp"] = pd.to_datetime(df["window_start_timestamp"])
    return df


# One-line glossary of the statistical level metrics, shown as a caption on the plot so a
# reader unfamiliar with acoustic percentile levels can interpret the three traces. These are
# per-window statistics over the 1-s sub-frames (see features._level_features): Ln is the level
# exceeded n% of the time, so L90 is the quiet background and L10 the loud peaks.
_LEVEL_CAPTION = (
    "Leq = energy-averaged level (overall loudness)    ·    "
    "L10 = level exceeded 10% of the time (loud peaks)    ·    "
    "L90 = level exceeded 90% of the time (quiet background)"
)


def plot_level_over_time(csv_path, out_png) -> Path:
    """Leq + L10/L90 over time from a feature CSV (relative dBFS).

    Leq is the energy-averaged level; L10/L90 are percentile (statistical) levels over the 1-s
    sub-frames within each window — L10 = level exceeded 10% of the time (loud peaks),
    L90 = exceeded 90% of the time (quiet background). A caption on the figure states this too.
    """
    df = _valid(pd.read_csv(csv_path))
    ok = df[df["qc_flag"] == "ok"]
    fig, ax = plt.subplots(figsize=(11, 4.3))
    ax.plot(ok["window_start_timestamp"], ok["leq_dbfs_relative"], lw=1.0, label="Leq (energy-avg)")
    ax.plot(ok["window_start_timestamp"], ok["l90_dbfs_relative"], lw=0.7, alpha=0.6,
            label="L90 (quiet background)")
    ax.plot(ok["window_start_timestamp"], ok["l10_dbfs_relative"], lw=0.7, alpha=0.6,
            label="L10 (loud peaks)")
    ax.set_ylabel("relative dBFS (NOT SPL)")
    ax.set_xlabel("time")
    ax.set_title(f"Camera-mic level over time — {Path(csv_path).name}")
    ax.legend(loc="upper right", fontsize=8)
    fig.text(0.5, 0.01, _LEVEL_CAPTION, ha="center", va="bottom", fontsize=7.5,
             style="italic", color="0.35")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    out_png = Path(out_png)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return out_png


def plot_index_timeseries(csv_path, out_png,
                          cols=("aci", "bi_2_8k_camera", "ndsi_1_2k_vs_2_8k_camera", "adi")) -> Path:
    """Ecoacoustic index time series from a feature CSV."""
    df = _valid(pd.read_csv(csv_path))
    ok = df[df["qc_flag"] == "ok"]
    cols = [c for c in cols if c in ok.columns]
    fig, axes = plt.subplots(len(cols), 1, figsize=(11, 2.0 * len(cols)), sharex=True)
    if len(cols) == 1:
        axes = [axes]
    for ax, c in zip(axes, cols):
        ax.plot(ok["window_start_timestamp"], ok[c], lw=1.0)
        ax.set_ylabel(c, fontsize=8)
    axes[-1].set_xlabel("time")
    axes[0].set_title(f"Soundscape indices (camera-band-limited) — {Path(csv_path).name}")
    fig.autofmt_xdate()
    fig.tight_layout()
    out_png = Path(out_png)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return out_png


# Biophony ("birds likely") heuristic — dataset-relative (uncalibrated dBFS), NOT a validated bird
# detector. Two gates:
#   BIOPHONY_MARGIN_DB  — the 2–8 kHz band must rise this far above its own quiet-night floor, AND
#   BROADBAND_GUARD_DB  — it must exceed the *simultaneous* rise of the 0–1 kHz ambient band by this,
#                         so broadband weather events (wind/rain lift BOTH bands together) are rejected.
# Birds lift mainly 2–8 kHz while ambient stays flat; wind/rain lift both. On 6/30 the excess gap
# (bird − ambient, each vs its floor) is ≈ +16 dB at the dawn chorus but only ≈ +4 (rain) / +2 (wind).
BIOPHONY_MARGIN_DB = 8.0
BROADBAND_GUARD_DB = 6.0


def biophony_active(bird, ambient, rain=None):
    """Boolean mask (numpy) marking where biophony (birds) is likely, on the `bird` index.

    `bird` / `ambient` are aligned Series of relative band energy (dB). A bin is biophony-active when
    the 2–8 kHz band is well above its own night floor **and** rises more than the 0–1 kHz band does
    (rejecting broadband wind/rain). When `rain` (a Series of rain rate) is given, bins with logged
    rain are additionally suppressed. Heuristic only — see the module note above.
    """
    bird_excess = bird - bird.quantile(0.10)
    amb_excess = ambient - ambient.quantile(0.10)
    active = (bird_excess > BIOPHONY_MARGIN_DB) & ((bird_excess - amb_excess) > BROADBAND_GUARD_DB)
    if rain is not None:
        active = active & ~(rain.reindex(bird.index).fillna(0.0) > 0)
    return active.to_numpy()


def plot_bird_vs_ambient(csv_path, out_png, resample: str = "5min") -> Path:
    """Diurnal 'bird-like' (2–8 kHz) vs 'ambient' (0–1 kHz) band level over time.

    Both traces are per-window relative dB (energy in that band), median-resampled to `resample`
    for readability. The **2–8 kHz** band is the biophony / birdsong proxy; the **0–1 kHz** band is
    the wind / rain / rumble ambient floor. Shaded spans mark `biophony_active` — the 2–8 kHz band
    well above its night floor AND rising more than the ambient band (so broadband wind/rain events
    are excluded). Heuristic, NOT a validated bird detector (camera mic, ≤8 kHz, relative dBFS).
    Loudness is not enough to call birds: read this, not the Leq plot.
    """
    df = _valid(pd.read_csv(csv_path))
    ok = df[df["qc_flag"] == "ok"].set_index("window_start_timestamp").sort_index()
    fig, ax = plt.subplots(figsize=(11, 4.3))
    if not ok.empty:
        amb = ok["band_0_1k_db"].resample(resample).median()
        bird = ok["band_2_8k_db"].resample(resample).median()
        floor = float(bird.quantile(0.10))                      # day's quiet-night bird-band floor
        active = biophony_active(bird, amb)                     # broadband-guarded (no weather here)
        ax.plot(amb.index, amb.to_numpy(), color="tab:orange", lw=1.1,
                label="ambient / low band 0–1 kHz (wind · rain · rumble)")
        ax.plot(bird.index, bird.to_numpy(), color="tab:green", lw=1.4,
                label="bird-like band 2–8 kHz (biophony proxy)")
        ax.axhline(floor, color="tab:green", lw=0.7, ls=":", alpha=0.7,
                   label="night floor of 2–8 kHz band")
        y0, y1 = ax.get_ylim()
        ax.fill_between(bird.index, y0, y1, where=active, color="tab:green", alpha=0.10,
                        step="mid", label="biophony likely (excl. broadband wind/rain)")
        ax.set_ylim(y0, y1)
    ax.set_ylabel("relative band energy, dB (NOT SPL)")
    ax.set_xlabel("time")
    ax.set_title(f"Bird-like (2–8 kHz) vs ambient (0–1 kHz) — {Path(csv_path).name}")
    ax.legend(loc="upper right", fontsize=7.5, ncol=1)
    fig.text(0.5, 0.01,
             "Birds → the green 2–8 kHz band rises toward/above the orange ambient band (shaded). "
             "Ambient/rain/wind → orange dominates, green stays near its night floor.",
             ha="center", va="bottom", fontsize=7.5, style="italic", color="0.35")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    out_png = Path(out_png)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return out_png


def plot_window_spectrogram(samples: np.ndarray, sr: int, out_png, title="window") -> Path:
    """Spectrogram of ONE short window of samples (decode handled by the caller)."""
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.specgram(samples, NFFT=1024, Fs=sr, noverlap=512, cmap="magma")
    ax.set_ylabel("Hz")
    ax.set_xlabel("s")
    ax.set_ylim(0, sr / 2)
    ax.set_title(f"Spectrogram — {title}")
    fig.tight_layout()
    out_png = Path(out_png)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return out_png

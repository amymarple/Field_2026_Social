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


def plot_level_over_time(csv_path, out_png) -> Path:
    """Leq + L10/L90 over time from a feature CSV (relative dBFS)."""
    df = _valid(pd.read_csv(csv_path))
    ok = df[df["qc_flag"] == "ok"]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(ok["window_start_timestamp"], ok["leq_dbfs_relative"], lw=1.0, label="Leq")
    ax.plot(ok["window_start_timestamp"], ok["l90_dbfs_relative"], lw=0.7, alpha=0.6, label="L90 (background)")
    ax.plot(ok["window_start_timestamp"], ok["l10_dbfs_relative"], lw=0.7, alpha=0.6, label="L10 (peaks)")
    ax.set_ylabel("relative dBFS (NOT SPL)")
    ax.set_xlabel("time")
    ax.set_title(f"Camera-mic level over time — {Path(csv_path).name}")
    ax.legend(loc="upper right", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
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

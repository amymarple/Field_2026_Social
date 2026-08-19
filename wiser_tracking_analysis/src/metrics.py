"""
metrics.py — Precision (jitter) and accuracy (error) metrics for WISER UWB data.

Two modes:
    1. Jitter only  — no ground truth; measures repeatability around each tag's
                      own median position.
    2. Full error   — ground truth provided; measures both accuracy and precision.

All distances are in the same unit as the position columns (typically metres).
"""

import numpy as np
import pandas as pd
from pathlib import Path

PERCENTILES = [50, 75, 90, 95]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _radial_distance(dx: np.ndarray, dy: np.ndarray,
                     dz: np.ndarray | None = None) -> np.ndarray:
    """Euclidean distance from origin; 2D or 3D depending on dz."""
    sq = dx ** 2 + dy ** 2
    if dz is not None:
        sq = sq + dz ** 2
    return np.sqrt(sq)


def _percentile_dict(distances: np.ndarray, prefix: str) -> dict:
    """Build {prefix_p50: val, prefix_p75: val, ...} for the given distance array."""
    return {f"{prefix}_p{p}": np.nanpercentile(distances, p) for p in PERCENTILES}


# ---------------------------------------------------------------------------
# Per-tag summary builders
# ---------------------------------------------------------------------------

def _jitter_stats(group: pd.DataFrame) -> dict:
    """
    Precision metrics for a single tag group.

    The reference centre is the tag's own median position.
    """
    x = group["x"].values.astype(float)
    y = group["y"].values.astype(float)
    z = group["z"].values.astype(float) if "z" in group.columns else None

    med_x, med_y = np.nanmedian(x), np.nanmedian(y)
    med_z = np.nanmedian(z) if z is not None else None

    dx = x - med_x
    dy = y - med_y
    dz = (z - med_z) if z is not None else None

    radial = _radial_distance(dx, dy, dz)

    stats: dict = {
        "n_frames":   len(group),
        "mean_x":     np.nanmean(x),
        "mean_y":     np.nanmean(y),
        "median_x":   med_x,
        "median_y":   med_y,
        "std_x":      np.nanstd(x),
        "std_y":      np.nanstd(y),
        "rms_jitter": np.sqrt(np.nanmean(radial ** 2)),
    }
    if z is not None:
        stats.update({
            "mean_z":   np.nanmean(z),
            "median_z": med_z,
            "std_z":    np.nanstd(z),
        })
    stats.update(_percentile_dict(radial, "jitter"))
    return stats


def _error_stats(group: pd.DataFrame,
                 true_x: float, true_y: float,
                 true_z: float | None) -> dict:
    """
    Accuracy metrics when ground-truth position is known.
    """
    x = group["x"].values.astype(float)
    y = group["y"].values.astype(float)
    z = group["z"].values.astype(float) if ("z" in group.columns and true_z is not None) else None

    dx = x - true_x
    dy = y - true_y
    dz = (z - true_z) if z is not None else None

    error = _radial_distance(dx, dy, dz)

    mean_x, mean_y = np.nanmean(x), np.nanmean(y)
    bias_x = mean_x - true_x
    bias_y = mean_y - true_y

    stats: dict = {
        "true_x":    true_x,
        "true_y":    true_y,
        "bias_x":    bias_x,
        "bias_y":    bias_y,
        "bias_mag":  np.sqrt(bias_x ** 2 + bias_y ** 2),
        "mean_error":   np.nanmean(error),
        "median_error": np.nanmedian(error),
        "rmse":         np.sqrt(np.nanmean(error ** 2)),
    }
    if true_z is not None and z is not None:
        stats["true_z"]  = true_z
        stats["bias_z"]  = np.nanmean(z) - true_z
    stats.update(_percentile_dict(error, "error"))
    return stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_summary(df: pd.DataFrame,
                    ground_truth: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Compute per-tag summary statistics.

    Parameters
    ----------
    df : DataFrame with columns shortid, x, y, (z optional), datetime.
    ground_truth : optional DataFrame with columns shortid, true_x, true_y, (true_z).
                   If provided and a tag appears in it, full error metrics are added.

    Returns
    -------
    DataFrame indexed by shortid with all metrics as columns.
    """
    if ground_truth is not None:
        gt_index = ground_truth.set_index("shortid")
    else:
        gt_index = None

    rows: list[dict] = []
    for tag, group in df.groupby("shortid"):
        row: dict = {"shortid": tag}
        row.update(_jitter_stats(group))

        if gt_index is not None and tag in gt_index.index:
            gt = gt_index.loc[tag]
            true_z = float(gt["true_z"]) if "true_z" in gt.index and pd.notna(gt.get("true_z")) else None
            row.update(_error_stats(group, float(gt["true_x"]), float(gt["true_y"]), true_z))
        elif gt_index is not None:
            # Tag is in data but not in ground-truth CSV — report jitter only.
            print(f"  [metrics] No ground truth for tag '{tag}'; jitter-only metrics.")

        rows.append(row)

    summary = pd.DataFrame(rows).set_index("shortid")
    return summary


def add_per_frame_errors(df: pd.DataFrame,
                         ground_truth: pd.DataFrame | None) -> pd.DataFrame:
    """
    Add per-frame error columns to *df*.

    Adds:
        jitter_r  — radial distance from the tag's own median position
        error_r   — distance from ground-truth position (if GT provided)
    """
    result_frames: list[pd.DataFrame] = []

    # Pre-compute per-tag medians for jitter.
    medians = (
        df.groupby("shortid")[["x", "y"]]
        .median()
        .rename(columns={"x": "_med_x", "y": "_med_y"})
    )
    df = df.join(medians, on="shortid")
    df["jitter_r"] = _radial_distance(
        df["x"].values - df["_med_x"].values,
        df["y"].values - df["_med_y"].values,
    )
    df = df.drop(columns=["_med_x", "_med_y"])

    if ground_truth is not None:
        gt_index = ground_truth.set_index("shortid")
        true_x_map = gt_index["true_x"].to_dict()
        true_y_map = gt_index["true_y"].to_dict()

        df["error_r"] = df.apply(
            lambda r: (
                _radial_distance(
                    np.array([r["x"] - true_x_map[r["shortid"]]]),
                    np.array([r["y"] - true_y_map[r["shortid"]]]),
                )[0]
                if r["shortid"] in true_x_map else np.nan
            ),
            axis=1,
        )

    return df


def load_ground_truth(path: Path | str) -> pd.DataFrame | None:
    """
    Load the ground-truth CSV.  Returns None if the file does not exist
    or all true_x / true_y values are blank.
    """
    path = Path(path)
    if not path.exists():
        print(f"  [metrics] Ground-truth file not found at {path} — jitter-only mode.")
        return None

    gt = pd.read_csv(path, encoding="utf-8-sig")
    gt.columns = [c.strip().lower() for c in gt.columns]

    # Drop rows where the essential coordinates are missing.
    gt = gt.dropna(subset=["true_x", "true_y"])
    if gt.empty:
        print("  [metrics] Ground-truth CSV has no valid rows — jitter-only mode.")
        return None

    print(f"  [metrics] Ground-truth loaded: {len(gt)} tag(s).")
    return gt

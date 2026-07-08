"""
plotting.py — Diagnostic plots for WISER UWB tracking analysis.

All functions accept a matplotlib Axes or create their own figure.
Figures are saved to an output directory when a path is supplied.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")          # non-interactive backend; safe in scripts
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.colors import LogNorm, Normalize

# Consistent colour palette keyed by tag shortid.
try:
    import matplotlib
    _TAG_CMAP = matplotlib.colormaps["tab20"]
except (AttributeError, KeyError):
    _TAG_CMAP = cm.get_cmap("tab20")


def _tag_colors(tags: list) -> dict:
    """Assign a stable colour to each tag."""
    return {tag: _TAG_CMAP(i % 20) for i, tag in enumerate(sorted(tags))}


# Rat identity mapping (shortid -> name / physical tag / coband colour+pattern).
# shortid is a WISER *tag* id, not an animal; resolve via this explicit table.
import functools

_DEFAULT_IDENTITY_CSV = (Path(__file__).resolve().parent.parent
                         / "configs" / "rat_identities.csv")


@functools.lru_cache(maxsize=4)
def _load_identities_cached(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        return {}
    tbl = pd.read_csv(path, dtype=str).fillna("")
    cols = ("name", "physical_tag_id", "coband_color", "pattern", "ink_color",
            "valid_until")
    out = {}
    for _, r in tbl.iterrows():
        key = str(r.get("shortid", "")).strip()
        if key:
            out[key] = {c: str(r.get(c, "")).strip() for c in cols}
    return out


def load_rat_identities(path: Path | None = None) -> dict:
    """shortid (str) -> identity dict. Empty if the mapping file is absent."""
    return _load_identities_cached(str(path or _DEFAULT_IDENTITY_CSV))


def _tag_panel_title(tag, n: int, identities: dict) -> str:
    """Per-tag panel title. With an identity mapping:
    'Sen  (306b / 12395)\\nGreen · Open Circle   n=1,234'; otherwise 'Tag <id>'."""
    info = identities.get(str(tag)) if identities else None
    if not info:
        return f"Tag {tag}  (n={n:,})"
    name = info.get("name") or f"Tag {tag}"
    phys = info.get("physical_tag_id", "")
    ids = f"{phys} / {tag}" if phys else f"{tag}"
    bits = [b for b in (info.get("coband_color", ""), info.get("pattern", ""))
            if b and b.upper() != "N/A"]
    desc = " · ".join(bits)
    line2 = f"{desc}   n={n:,}" if desc else f"n={n:,}"
    return f"{name}  ({ids})\n{line2}"


def _save_or_show(fig: plt.Figure, path: Path | None) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=150, bbox_inches="tight")
        print(f"  Saved: {path}")
        plt.close(fig)
    else:
        plt.show()


# ---------------------------------------------------------------------------
# Plot 1 — Scatter of all estimated positions coloured by tag
# ---------------------------------------------------------------------------

def plot_position_scatter(df: pd.DataFrame,
                          ground_truth: pd.DataFrame | None = None,
                          save_path: Path | None = None) -> None:
    """Scatter plot of all estimated (x, y) positions, one colour per tag."""
    tags = df["shortid"].unique()
    colors = _tag_colors(list(tags))

    fig, ax = plt.subplots(figsize=(9, 7))
    for tag in sorted(tags):
        sub = df[df["shortid"] == tag]
        ax.scatter(sub["x"], sub["y"], s=4, alpha=0.4,
                   color=colors[tag], label=str(tag))

    if ground_truth is not None:
        ax.scatter(ground_truth["true_x"], ground_truth["true_y"],
                   marker="*", s=200, color="black", zorder=5,
                   label="Ground truth")
        for _, row in ground_truth.iterrows():
            ax.annotate(str(row["shortid"]),
                        (row["true_x"], row["true_y"]),
                        textcoords="offset points", xytext=(6, 6), fontsize=8)

    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_title("Estimated positions — all tags")
    ax.legend(markerscale=3, loc="best", fontsize=8)
    ax.set_aspect("equal", "datalim")
    ax.grid(True, linestyle="--", alpha=0.4)
    _save_or_show(fig, save_path)


# ---------------------------------------------------------------------------
# Plot 2 — Per-tag position cloud around median / ground truth
# ---------------------------------------------------------------------------

def plot_position_clouds(df: pd.DataFrame,
                         ground_truth: pd.DataFrame | None = None,
                         save_path: Path | None = None) -> None:
    """One subplot per tag showing the position cloud centred on median."""
    tags = sorted(df["shortid"].unique())
    n = len(tags)
    ncols = min(4, n)
    nrows = int(np.ceil(n / ncols))

    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(4 * ncols, 4 * nrows),
                             squeeze=False)
    colors = _tag_colors(tags)
    gt_index = ground_truth.set_index("shortid") if ground_truth is not None else None

    for idx, tag in enumerate(tags):
        ax = axes[idx // ncols][idx % ncols]
        sub = df[df["shortid"] == tag]
        med_x, med_y = sub["x"].median(), sub["y"].median()

        ax.scatter(sub["x"] - med_x, sub["y"] - med_y,
                   s=5, alpha=0.5, color=colors[tag])
        ax.axhline(0, color="grey", linewidth=0.8, linestyle="--")
        ax.axvline(0, color="grey", linewidth=0.8, linestyle="--")

        # Draw ground-truth offset when available.
        if gt_index is not None and tag in gt_index.index:
            gt = gt_index.loc[tag]
            ax.scatter(gt["true_x"] - med_x, gt["true_y"] - med_y,
                       marker="*", s=150, color="black", zorder=5, label="GT")
            ax.legend(fontsize=7)

        ax.set_title(f"Tag: {tag}", fontsize=9)
        ax.set_xlabel("ΔX (m)", fontsize=8)
        ax.set_ylabel("ΔY (m)", fontsize=8)
        ax.set_aspect("equal", "datalim")
        ax.grid(True, linestyle="--", alpha=0.3)

    # Hide unused subplots.
    for idx in range(n, nrows * ncols):
        axes[idx // ncols][idx % ncols].set_visible(False)

    fig.suptitle("Per-tag position clouds (centred on median)", fontsize=12)
    fig.tight_layout()
    _save_or_show(fig, save_path)


# ---------------------------------------------------------------------------
# Plot 3 — X and Y time series per tag
# ---------------------------------------------------------------------------

def plot_timeseries(df: pd.DataFrame,
                    save_path: Path | None = None) -> None:
    """Time series of X and Y position for every tag."""
    tags = sorted(df["shortid"].unique())
    colors = _tag_colors(tags)

    fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)

    for tag in tags:
        sub = df[df["shortid"] == tag].sort_values("elapsed_s")
        axes[0].plot(sub["elapsed_s"], sub["x"],
                     linewidth=0.6, alpha=0.7, color=colors[tag], label=str(tag))
        axes[1].plot(sub["elapsed_s"], sub["y"],
                     linewidth=0.6, alpha=0.7, color=colors[tag])

    axes[0].set_ylabel("X (m)")
    axes[0].set_title("X position over time")
    axes[0].legend(fontsize=7, loc="best")
    axes[0].grid(True, linestyle="--", alpha=0.4)

    axes[1].set_ylabel("Y (m)")
    axes[1].set_xlabel("Elapsed time (s)")
    axes[1].set_title("Y position over time")
    axes[1].grid(True, linestyle="--", alpha=0.4)

    fig.tight_layout()
    _save_or_show(fig, save_path)


# ---------------------------------------------------------------------------
# Plot 4 — Error over time (requires ground truth)
# ---------------------------------------------------------------------------

def plot_error_timeseries(df: pd.DataFrame,
                          save_path: Path | None = None) -> None:
    """Error distance over time per tag (requires 'error_r' column)."""
    if "error_r" not in df.columns:
        print("  [plotting] 'error_r' column not present — skipping error timeseries.")
        return

    tags = sorted(df["shortid"].unique())
    colors = _tag_colors(tags)

    fig, ax = plt.subplots(figsize=(12, 5))

    for tag in tags:
        sub = df[df["shortid"] == tag].sort_values("elapsed_s")
        valid = sub.dropna(subset=["error_r"])
        if valid.empty:
            continue
        ax.plot(valid["elapsed_s"], valid["error_r"],
                linewidth=0.7, alpha=0.75, color=colors[tag], label=str(tag))

    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("Error distance (m)")
    ax.set_title("Tracking error over time")
    ax.legend(fontsize=7, loc="best")
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    _save_or_show(fig, save_path)


# ---------------------------------------------------------------------------
# Plot 5 — Jitter histogram per tag
# ---------------------------------------------------------------------------

def plot_jitter_histograms(df: pd.DataFrame,
                           save_path: Path | None = None) -> None:
    """Histogram of radial jitter distance per tag."""
    if "jitter_r" not in df.columns:
        print("  [plotting] 'jitter_r' column not present — skipping jitter histograms.")
        return

    tags = sorted(df["shortid"].unique())
    n = len(tags)
    ncols = min(4, n)
    nrows = int(np.ceil(n / ncols))

    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(4 * ncols, 3.5 * nrows),
                             squeeze=False)
    colors = _tag_colors(tags)

    for idx, tag in enumerate(tags):
        ax = axes[idx // ncols][idx % ncols]
        sub = df[df["shortid"] == tag]["jitter_r"].dropna()
        ax.hist(sub, bins=40, color=colors[tag], alpha=0.8, edgecolor="none")
        ax.axvline(sub.median(), color="black", linestyle="--",
                   linewidth=1, label=f"Median {sub.median():.3f} m")
        ax.set_title(f"Tag: {tag}", fontsize=9)
        ax.set_xlabel("Jitter radius (m)", fontsize=8)
        ax.set_ylabel("Count", fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(True, linestyle="--", alpha=0.3)

    for idx in range(n, nrows * ncols):
        axes[idx // ncols][idx % ncols].set_visible(False)

    fig.suptitle("Radial jitter distribution per tag", fontsize=12)
    fig.tight_layout()
    _save_or_show(fig, save_path)


# ---------------------------------------------------------------------------
# Hourly scatter — per-tag position scatter (fast QC) + all-tags overlay
# ---------------------------------------------------------------------------

def plot_hourly_scatter(df: pd.DataFrame,
                        hour_label: str,
                        extent: tuple[float, float, float, float],
                        save_path: Path | None = None,
                        time_col: str | None = "ts_raw") -> None:
    """
    Fast-QC scatter for one time window: one panel per tag (points coloured by
    time within the hour, so the trajectory/movement reads at a glance) plus a
    combined all-tags overlay coloured by tag.

    Shares the fixed ``extent`` (xmin, xmax, ymin, ymax) in inches with the
    occupancy maps so panels are comparable hour-to-hour. A tag that has frozen
    collapses to a single dot; a dropout shows as a sparse/empty panel.
    """
    xmin, xmax, ymin, ymax = extent
    tags = sorted(df["shortid"].unique())
    colors = _tag_colors(tags)
    identities = load_rat_identities()

    def _setup(ax, title):
        ax.set_title(title, fontsize=9)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.tick_params(labelsize=7)
        ax.grid(True, linestyle="--", alpha=0.3)

    has_time = time_col is not None and time_col in df.columns

    n_panels = len(tags) + 1
    ncols = 4
    nrows = int(np.ceil(n_panels / ncols))
    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(4 * ncols, 4 * nrows), squeeze=False)
    flat = axes.ravel()

    time_im = None
    for idx, tag in enumerate(tags):
        ax = flat[idx]
        sub = df[df["shortid"] == tag]
        if has_time and len(sub) > 1:
            t = sub[time_col].to_numpy(dtype="float64")
            tnorm = (t - t.min()) / max(t.max() - t.min(), 1)
            time_im = ax.scatter(sub["x"], sub["y"], c=tnorm, cmap="viridis",
                                 s=3, alpha=0.5, linewidths=0)
        else:
            ax.scatter(sub["x"], sub["y"], s=3, alpha=0.5,
                       color=colors[tag], linewidths=0)
        _setup(ax, _tag_panel_title(tag, len(sub), identities))
        for spine in ax.spines.values():
            spine.set_edgecolor(colors[tag])
            spine.set_linewidth(2)

    # Overlay: all tags, coloured by tag.
    overlay_ax = flat[len(tags)]
    for tag in tags:
        sub = df[df["shortid"] == tag]
        overlay_ax.scatter(sub["x"], sub["y"], s=3, alpha=0.4,
                           color=colors[tag], label=str(tag), linewidths=0)
    _setup(overlay_ax, f"All tags  (n={len(df):,})")
    overlay_ax.legend(markerscale=3, fontsize=6, loc="best", ncol=2)

    for idx in range(n_panels, nrows * ncols):
        flat[idx].set_visible(False)

    if time_im is not None:
        cbar = fig.colorbar(time_im, ax=axes.ravel().tolist(),
                            shrink=0.6, location="right", pad=0.02)
        cbar.set_label("time within hour (early → late)", fontsize=8)

    fig.suptitle(f"WISER positions — {hour_label}  (X/Y inches)", fontsize=12)
    _save_or_show(fig, save_path)


# ---------------------------------------------------------------------------
# Occupancy maps — per-tag 2-D position-density heatmaps + overlay
# ---------------------------------------------------------------------------

def _occupancy_hist(sub: pd.DataFrame, xedges: np.ndarray, yedges: np.ndarray) -> np.ndarray:
    """2-D fix-count histogram. Returns array indexed [x_bin, y_bin]."""
    h, _, _ = np.histogram2d(sub["x"].to_numpy(), sub["y"].to_numpy(),
                             bins=[xedges, yedges])
    return h


def plot_occupancy_grid(df: pd.DataFrame,
                        hour_label: str,
                        extent: tuple[float, float, float, float],
                        bin_inches: float = 4.0,
                        save_path: Path | None = None,
                        log_scale: bool = True) -> None:
    """
    Occupancy map for one time window: one position-density heatmap per tag plus
    a combined all-animals overlay panel.

    Each panel is a 2-D histogram of the full point cloud over a *fixed* extent
    so panels are directly comparable to each other and across hours. ``extent``
    is ``(xmin, xmax, ymin, ymax)`` in inches; ``bin_inches`` defaults to 4 in to
    match WISER's ~3–4 in spatial resolution. Counts use a log colour scale by
    default (occupancy is heavy-tailed). Empty cells render white.

    The per-tag panels share one colour scale (driven by the busiest tag cell);
    the overlay has its own scale and colorbar since it sums all tags.
    """
    xmin, xmax, ymin, ymax = extent
    xedges = np.arange(xmin, xmax + bin_inches, bin_inches)
    yedges = np.arange(ymin, ymax + bin_inches, bin_inches)

    tags = sorted(df["shortid"].unique())
    colors = _tag_colors(tags)
    identities = load_rat_identities()

    # Per-tag histograms + combined overlay.
    tag_hists = {tag: _occupancy_hist(df[df["shortid"] == tag], xedges, yedges)
                 for tag in tags}
    overlay_hist = _occupancy_hist(df, xedges, yedges)

    tag_vmax = max((h.max() for h in tag_hists.values()), default=1) or 1
    overlay_vmax = overlay_hist.max() or 1

    def _norm(vmax: float):
        if log_scale:
            return LogNorm(vmin=1, vmax=max(vmax, 1))
        return Normalize(vmin=0, vmax=max(vmax, 1))

    cmap = plt.get_cmap("magma").copy()
    cmap.set_bad("white")          # empty (zero-count) cells -> white

    def _draw(ax, hist, norm, title):
        masked = np.ma.masked_where(hist <= 0, hist)
        im = ax.imshow(masked.T, origin="lower",
                       extent=(xmin, xmax, ymin, ymax),
                       aspect="equal", cmap=cmap, norm=norm,
                       interpolation="nearest")
        ax.set_title(title, fontsize=9)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.tick_params(labelsize=7)
        return im

    n_panels = len(tags) + 1                       # tags + overlay
    ncols = 4
    nrows = int(np.ceil(n_panels / ncols))
    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(4 * ncols, 4 * nrows),
                             squeeze=False)
    flat = axes.ravel()

    tag_norm = _norm(tag_vmax)
    tag_im = None
    for idx, tag in enumerate(tags):
        ax = flat[idx]
        n = int(tag_hists[tag].sum())
        tag_im = _draw(ax, tag_hists[tag], tag_norm,
                       _tag_panel_title(tag, n, identities))
        # Colour-coded frame so panels are easy to tell apart.
        for spine in ax.spines.values():
            spine.set_edgecolor(colors[tag])
            spine.set_linewidth(2)

    # Overlay panel (own scale + colorbar).
    overlay_ax = flat[len(tags)]
    overlay_im = _draw(overlay_ax, overlay_hist, _norm(overlay_vmax),
                       f"All tags  (n={int(overlay_hist.sum()):,})")
    fig.colorbar(overlay_im, ax=overlay_ax, fraction=0.046, pad=0.04)

    # Hide any leftover axes.
    for idx in range(n_panels, nrows * ncols):
        flat[idx].set_visible(False)

    # Shared colorbar for the per-tag panels.
    if tag_im is not None:
        cbar = fig.colorbar(tag_im, ax=axes.ravel().tolist(),
                            shrink=0.6, location="right", pad=0.02)
        scale = "log" if log_scale else "linear"
        cbar.set_label(f"fixes per {bin_inches:g}-in cell ({scale})", fontsize=8)

    fig.suptitle(f"WISER occupancy — {hour_label}  "
                 f"({bin_inches:g}-in bins, X/Y inches)", fontsize=12)
    _save_or_show(fig, save_path)


# ---------------------------------------------------------------------------
# Convenience: generate all plots at once
# ---------------------------------------------------------------------------

def generate_all_plots(df: pd.DataFrame,
                       ground_truth: pd.DataFrame | None,
                       output_dir: Path) -> None:
    """Save all diagnostic plots to *output_dir*."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    plot_position_scatter(df, ground_truth,
                          save_path=output_dir / "01_position_scatter.png")
    plot_position_clouds(df, ground_truth,
                         save_path=output_dir / "02_position_clouds.png")
    plot_timeseries(df, save_path=output_dir / "03_timeseries.png")
    plot_error_timeseries(df, save_path=output_dir / "04_error_timeseries.png")
    plot_jitter_histograms(df, save_path=output_dir / "05_jitter_histograms.png")

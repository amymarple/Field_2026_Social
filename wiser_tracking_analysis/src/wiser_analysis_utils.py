"""
wiser_analysis_utils.py — Reusable pilot-analysis helpers for WISER UWB data.

This module is the analysis layer on top of the existing WISER library
(:mod:`wiser_io`, :mod:`time_utils`, :mod:`metrics`, :mod:`plotting`). It adds
the pilot-study functionality: a *rich* read-only loader that preserves the
QC-bearing columns (``anchors_used``, ``calculation_error``, ``battery_voltage``)
that the canonical loader drops, speed (raw + smoothed), validity flags, social
proximity with a jitter-floor reliability check, occupancy + candidate-zone
inference, ROI time/transitions, hourly activity, acclimation windows, weather
loading/merging, plotting, and run-provenance writers.

Conventions (see implementation_plan/2026-06-29-wiser-pilot-analysis.md):
- **Units are INCHES** throughout (repo convention). Speeds are inches/second.
- WISER timestamps are Unix **ms, UTC**. Weather is local EDT (UTC-4) → UTC.
- The WISER coordinate frame is NOT verified against the physical paddock, so
  out-of-bounds is informational unless a *confirmed* boundary is supplied.
- Nothing here ever writes to the source data; SQLite is opened read-only.

QC gates interpretation: callers should establish the trustworthy spatial/
temporal scale (jitter floor, dropout/jump rates) before any behavioral claim.
"""

from __future__ import annotations

from pathlib import Path
import itertools
import json
import subprocess
import warnings

import numpy as np
import pandas as pd

# Reuse the existing library. Support both "import as package" (src.x) and
# "src on sys.path" execution styles.
try:                                                  # package-style import
    from . import wiser_io, time_utils, metrics, plotting, field_transform
except ImportError:                                   # flat import (src on path)
    import wiser_io, time_utils, metrics, plotting, field_transform  # type: ignore


# ---------------------------------------------------------------------------
# Constants (all distances in inches)
# ---------------------------------------------------------------------------

IN_TO_CM = 2.54
CM_TO_IN = 1.0 / IN_TO_CM

# Physical paddock in the CV field frame (origin at pole A0), in cm. Mirrors
# preprocessing/computer_vision/field_coords.py FIELD_X_CM / FIELD_Y_CM. Kept as
# plain constants so this core module has no dependency on the CV package.
FIELD_X_CM = 1219.2   # 40 ft length (x)
FIELD_Y_CM = 609.6    # 20 ft width (y)

# Default location of the georeference transform written by scripts/georeference_wiser.py.
DEFAULT_TRANSFORM_PATH = Path(__file__).resolve().parent.parent / "configs" / \
    "wiser_to_field_transform.json"

# Social-distance thresholds requested in metres, expressed in inches.
PROXIMITY_THRESHOLDS_IN = (0.5 / IN_TO_CM * 100,   # 0.5 m -> 19.69 in
                           1.0 / IN_TO_CM * 100,   # 1.0 m -> 39.37 in
                           2.0 / IN_TO_CM * 100)   # 2.0 m -> 78.74 in

DEFAULT_MIN_ANCHORS = 4          # anchors_used < this -> low-confidence fix
DEFAULT_GAP_FACTOR = 5.0         # dt > factor * per-tag median dt -> dropout
DEFAULT_MAX_SPEED_INPS = 200.0   # raw speed above this -> impossible jump
DEFAULT_ACTIVE_SPEED_INPS = 12.0 # fallback "active" floor (in/s); prefer the data-driven
                                 # stationary speed-noise floor from speed_noise_floor()
DEFAULT_SMOOTH_WINDOW = 7        # rolling-median window (samples) for position smoothing
DEFAULT_SPEED_WINDOW_S = 1.0     # fixed time window (s) for the smoothed locomotion speed
MAX_PLAUSIBLE_SPEED_INPS = 60.0  # smoothed speed above this = artifact (~1.5 m/s; above the
                                 # 99.9th pct of observed real movement, below a rat's absurd cap)
LOCAL_TZ_OFFSET_HOURS = -4       # EDT (UTC-4); the field PC / weather local time
LOCAL_OFFSET_STR = f"{LOCAL_TZ_OFFSET_HOURS:+03d}:00"   # "-04:00" for ISO local times

# Extra raw columns to preserve beyond the canonical shortid/ts_raw/x/y/z.
EXTRA_ALIASES: dict[str, list[str]] = {
    "calculation_error": ["calculation_error", "calc_error", "position_error"],
    "anchors_used":      ["anchors_used", "anchors", "num_anchors", "n_anchors"],
    "battery_voltage":   ["battery_voltage", "battery", "voltage"],
    "reportid":          ["reportid", "report_id"],
}


# ---------------------------------------------------------------------------
# Rich read-only loader (keeps QC columns)
# ---------------------------------------------------------------------------

def _standardise_rich(df: pd.DataFrame, source: str) -> pd.DataFrame | None:
    """Like ``wiser_io._standardise_df`` but also keeps the QC columns."""
    canon_map = wiser_io._match_columns(list(df.columns))     # {canonical: raw}
    required = ["shortid", "ts_raw", "x", "y"]
    missing = [c for c in required if c not in canon_map]
    if missing:
        warnings.warn(f"[wiser_analysis_utils] {source}: missing {missing}; "
                      f"columns are {list(df.columns)}")
        return None

    lower = {c.lower().strip(): c for c in df.columns}
    extra_map: dict[str, str] = {}
    for canon, aliases in EXTRA_ALIASES.items():
        for alias in aliases:
            if alias in lower:
                extra_map[canon] = lower[alias]
                break

    rename = {raw: canon for canon, raw in {**canon_map, **extra_map}.items()}
    out = df.rename(columns=rename)

    keep = [c for c in ["shortid", "ts_raw", "x", "y", "z"] if c in out.columns]
    keep += [c for c in EXTRA_ALIASES if c in out.columns]
    out = out[keep].copy()
    out["source_file"] = source
    return out


def load_wiser_session(path: Path | str,
                       table: str | None = None) -> pd.DataFrame | None:
    """
    Load one WISER SQLite session **read-only**, preserving QC columns.

    Returns a DataFrame with ``shortid, ts_raw, x, y[, z],`` plus whichever of
    ``calculation_error, anchors_used, battery_voltage, reportid`` exist. Safe
    against a live writer (``mode=ro`` + ``PRAGMA query_only=ON``). Targets a
    single ``.sqlite`` so it never double-counts an accompanying CSV extract.
    """
    path = Path(path)
    try:
        conn = wiser_io._connect_readonly(path)
    except Exception as exc:                          # pragma: no cover
        warnings.warn(f"[wiser_analysis_utils] cannot open {path.name}: {exc}")
        return None
    try:
        if table is None:
            table = wiser_io._pick_table(conn)
        if table is None:
            warnings.warn(f"[wiser_analysis_utils] {path.name}: no tables.")
            return None
        df = pd.read_sql(f'SELECT * FROM "{table}"', conn)
    finally:
        conn.close()

    if df.empty:
        warnings.warn(f"[wiser_analysis_utils] {path.name}/{table} empty.")
        return None
    return _standardise_rich(df, f"{path.name}/{table}")


def session_snapshot(path: Path | str,
                     table: str | None = None) -> dict:
    """
    Cheap read-only snapshot of a (possibly live) session: row count + timestamp
    bounds, captured at call time. Use this instead of hard-coding expected
    counts — the DB may still be growing.
    """
    path = Path(path)
    snap: dict = {"file": str(path), "snapshot_time_utc":
                  pd.Timestamp.utcnow().tz_localize(None).isoformat()}
    try:
        conn = wiser_io._connect_readonly(path)
        try:
            tbl = table or wiser_io._pick_table(conn)
            snap["table"] = tbl
            snap["n_rows"] = int(conn.execute(
                f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:                          # pragma: no cover
        snap["error"] = str(exc)
        return snap
    bounds = wiser_io.sqlite_time_bounds(path, table=table)
    if bounds:
        snap["min_ts_ms"], snap["max_ts_ms"] = bounds
        snap["min_utc"] = pd.to_datetime(bounds[0], unit="ms").isoformat()
        snap["max_utc"] = pd.to_datetime(bounds[1], unit="ms").isoformat()
        snap["duration_h"] = round((bounds[1] - bounds[0]) / 3_600_000, 3)
    return snap


# ---------------------------------------------------------------------------
# Speed (raw + smoothed) and per-step distance
# ---------------------------------------------------------------------------

def add_speed(df: pd.DataFrame,
              smooth_window: int = DEFAULT_SMOOTH_WINDOW,
              speed_window_s: float = DEFAULT_SPEED_WINDOW_S,
              max_plausible_inps: float = MAX_PLAUSIBLE_SPEED_INPS) -> pd.DataFrame:
    """
    Add per-tag ``dt_s``, raw and smoothed speed (inches/s) and step distance.

    ``speed_inps_raw`` is the frame-to-frame speed and **must not** be read as
    locomotion — WISER position jitter inflates it, and the sampling is bursty so
    two fixes milliseconds apart divide a small step by a tiny ``dt`` and explode
    to thousands of in/s.

    ``speed_inps_smooth`` is the robust locomotion speed: the displacement of the
    jitter-suppressed position (centred rolling-median, ``smooth_window`` samples)
    over a **fixed** ``speed_window_s``-second window, divided by that window. The
    fixed time base means a millisecond-apart pair can never blow the speed up, and
    the window averages out residual jitter. A smoothed speed above
    ``max_plausible_inps`` (a generous rat sprint, ~3.8 m/s) is a residual tracking
    artifact, not locomotion, and is set to NaN so it cannot pollute plots/means.

    ``step_in_smooth`` stays the per-sample smoothed step (summed for path
    distance, so it must remain non-overlapping — it is *not* the window
    displacement). Requires ``datetime`` from
    :func:`time_utils.convert_timestamps`.
    """
    if "datetime" not in df.columns:
        raise KeyError("Run time_utils.convert_timestamps() before add_speed().")

    parts: list[pd.DataFrame] = []
    for _, g in df.sort_values(["shortid", "datetime"]).groupby("shortid",
                                                                sort=False):
        g = g.copy()
        dt = g["datetime"].diff().dt.total_seconds()
        step_raw = np.hypot(g["x"].diff(), g["y"].diff())
        xs = g["x"].rolling(smooth_window, center=True, min_periods=1).median()
        ys = g["y"].rolling(smooth_window, center=True, min_periods=1).median()
        step_smooth = np.hypot(xs.diff(), ys.diff())

        # Fixed-time-window displacement speed (jitter-suppressed positions).
        t = (g["datetime"] - g["datetime"].iloc[0]).dt.total_seconds().to_numpy()
        xv = xs.to_numpy()
        yv = ys.to_numpy()
        half = speed_window_s / 2.0
        n = len(t)
        lo = np.clip(np.searchsorted(t, t - half, side="left"), 0, n - 1)
        hi = np.clip(np.searchsorted(t, t + half, side="right") - 1, 0, n - 1)
        dt_win = t[hi] - t[lo]
        disp = np.hypot(xv[hi] - xv[lo], yv[hi] - yv[lo])
        with np.errstate(divide="ignore", invalid="ignore"):
            v_smooth = np.where(dt_win > 0, disp / dt_win, np.nan)
        v_smooth = np.where(v_smooth > max_plausible_inps, np.nan, v_smooth)

        with np.errstate(divide="ignore", invalid="ignore"):
            g["dt_s"] = dt
            g["step_in_raw"] = step_raw
            g["step_in_smooth"] = step_smooth
            g["speed_inps_raw"] = (step_raw / dt).replace([np.inf, -np.inf], np.nan)
            g["speed_inps_smooth"] = v_smooth
        parts.append(g)

    return pd.concat(parts).reset_index(drop=True)


def speed_noise_floor(stationary_df: pd.DataFrame,
                      pct: tuple[int, ...] = (95, 99)) -> dict:
    """
    Empirical speed-noise floor from the **stationary** baseline.

    The stationary tags are not moving, so every non-zero ``speed_inps_smooth`` is
    pure tracking noise (position resolution ÷ sampling interval — e.g. ~3.5 in at
    ~4.4 Hz ≈ 8 in/s). These percentiles are therefore the floor **below which a
    free-moving tag's speed cannot be distinguished from jitter**, and the
    p99 is a principled, data-driven "active" threshold (replacing an arbitrary
    constant). Pass a stationary frame that has already been through
    :func:`add_speed`.
    """
    if "speed_inps_smooth" not in stationary_df.columns:
        raise KeyError("Run add_speed() on the stationary frame before "
                       "speed_noise_floor().")
    s = stationary_df["speed_inps_smooth"].dropna()
    out = {"median": float(s.median()), "n": int(s.size)}
    for p in pct:
        out[f"p{p}"] = float(s.quantile(p / 100.0))
    return out


# ---------------------------------------------------------------------------
# Validity flags (flags only — never deletes rows)
# ---------------------------------------------------------------------------

def add_validity_flags(df: pd.DataFrame,
                       *,
                       boundary: dict | None = None,
                       jitter_floor_in: float | None = None,
                       max_speed_inps: float = DEFAULT_MAX_SPEED_INPS,
                       gap_factor: float = DEFAULT_GAP_FACTOR,
                       min_anchors: int = DEFAULT_MIN_ANCHORS) -> pd.DataFrame:
    """
    Add per-row QC flags. **Adds columns only; never drops rows.**

    Flags: ``low_anchor_flag`` (anchors_used < min_anchors), ``gap_flag``
    (dropout: dt > gap_factor × per-tag median dt), ``jump_flag`` (raw speed >
    max_speed_inps), ``outside_provisional_bounds`` (outside the WISER-frame
    rectangle in ``boundary['rect']``). Composite ``valid`` excludes
    low-anchor/gap/jump rows; out-of-bounds enters ``valid`` **only** when
    ``boundary['confirmed']`` is true (a manually placed boundary), otherwise it
    is informational.

    The jump threshold is justified jointly by plausible rat locomotion (a rat
    sprint is ~3 m/s ≈ 118 in/s; ``max_speed_inps`` defaults to ~5 m/s) and the
    stationary jitter baseline: a jitter-induced step of ``jitter_floor_in`` over
    one ~0.28 s sample produces << max_speed_inps, so genuine jitter is not
    flagged as a jump. ``jitter_floor_in`` is recorded for provenance.
    """
    df = df.copy()

    if "anchors_used" in df.columns:
        df["low_anchor_flag"] = df["anchors_used"].astype(float) < min_anchors
    else:
        df["low_anchor_flag"] = False

    df["gap_flag"] = False
    for tag, idx in df.groupby("shortid").groups.items():
        med = df.loc[idx, "dt_s"].median()
        if med and np.isfinite(med) and med > 0:
            df.loc[idx, "gap_flag"] = df.loc[idx, "dt_s"] > gap_factor * med

    df["jump_flag"] = df["speed_inps_raw"] > max_speed_inps

    confirmed = bool(boundary.get("confirmed", False)) if boundary else False
    if boundary and "rect" in boundary:
        xmin, xmax, ymin, ymax = boundary["rect"]
        df["outside_provisional_bounds"] = ~(
            (df["x"] >= xmin) & (df["x"] <= xmax) &
            (df["y"] >= ymin) & (df["y"] <= ymax))
    else:
        df["outside_provisional_bounds"] = False

    df["valid"] = ~(df["low_anchor_flag"].fillna(False) |
                    df["gap_flag"].fillna(False) |
                    df["jump_flag"].fillna(False))
    if confirmed:
        df["valid"] &= ~df["outside_provisional_bounds"]

    df.attrs["jump_threshold_inps"] = max_speed_inps
    df.attrs["jitter_floor_in"] = jitter_floor_in
    df.attrs["bounds_confirmed"] = confirmed
    return df


def flag_summary(df: pd.DataFrame) -> dict:
    """Return counts/fractions of each flag, for the filtering log."""
    n = len(df)
    out = {"n_rows": n}
    for col in ["low_anchor_flag", "gap_flag", "jump_flag",
                "outside_provisional_bounds", "after_tag_cutoff", "valid"]:
        if col in df.columns:
            k = int(df[col].sum())
            out[col] = {"count": k, "fraction": round(k / n, 5) if n else 0.0}
    return out


def tag_cutoffs_utc(identities: dict | None = None) -> dict:
    """
    ``shortid`` (str) -> naive-UTC cutoff datetime64, from the ``valid_until``
    column of the rat-identity table (e.g. an animal's time of death/removal).
    Fixes at/after the cutoff are not valid behavioral data. Empty if no animal
    has a cutoff. Reuses :func:`plotting.load_rat_identities`.
    """
    ids = identities if identities is not None else plotting.load_rat_identities()
    out = {}
    for sid, info in (ids or {}).items():
        vu = (info or {}).get("valid_until", "")
        if vu:
            out[str(sid)] = _roi_time_utc(vu)
    return out


def apply_tag_cutoffs(df: pd.DataFrame, cutoffs: dict | None = None) -> pd.DataFrame:
    """
    Flag fixes at/after a per-tag cutoff (death/removal) as ``after_tag_cutoff``
    and drop them from the composite ``valid``. **Adds a column; never deletes
    rows** (consistent with :func:`add_validity_flags`). Requires ``datetime``.
    """
    df = df.copy()
    cutoffs = tag_cutoffs_utc() if cutoffs is None else cutoffs
    after = np.zeros(len(df), dtype=bool)
    if cutoffs and "datetime" in df.columns:
        sid = df["shortid"].astype(str).to_numpy()
        dt = df["datetime"].to_numpy()
        for tag, t in cutoffs.items():
            after |= (sid == str(tag)) & (dt >= np.datetime64(t))
    df["after_tag_cutoff"] = after
    if "valid" in df.columns:
        df["valid"] = df["valid"] & ~after
    return df


# ---------------------------------------------------------------------------
# Provisional extent / boundary helpers
# ---------------------------------------------------------------------------

def observed_extent(df: pd.DataFrame, pad_in: float = 12.0
                    ) -> tuple[float, float, float, float]:
    """Bounding box of valid points (+pad), in inches: (xmin, xmax, ymin, ymax)."""
    sub = df[df.get("valid", True)] if "valid" in df.columns else df
    sub = sub.dropna(subset=["x", "y"])
    return (float(sub["x"].min()) - pad_in, float(sub["x"].max()) + pad_in,
            float(sub["y"].min()) - pad_in, float(sub["y"].max()) + pad_in)


def load_rois(path: Path | str) -> dict | None:
    """Load a wiser_rois.json (boundary + ROI list). Returns None if absent."""
    path = Path(path)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Georeferencing: WISER-inch frame <-> physical field-cm frame
# ---------------------------------------------------------------------------
# These are the *consumers* of the transform fit by scripts/georeference_wiser.py.
# They are strictly guarded: with no confirmed transform on disk, every helper is
# a no-op (returns None / leaves data unchanged), so existing analyses behave
# exactly as before until a survey has been run and vetted (confirmed=true).

def load_field_transform(path: Path | str | None = None, *,
                         allow_unconfirmed: bool = False) -> dict | None:
    """
    Load the WISER-inch -> field-cm transform, or ``None``.

    Returns ``None`` when the config is absent, or when it exists but is not
    ``confirmed`` (unless ``allow_unconfirmed=True``). A ``None`` return is the
    signal to callers to fall back to WISER-native-inch behaviour. Reuses
    :func:`field_transform.load_transform`.
    """
    cfg = field_transform.load_transform(path or DEFAULT_TRANSFORM_PATH)
    if cfg is None:
        return None
    if not cfg.get("confirmed", False) and not allow_unconfirmed:
        return None
    return cfg


def apply_field_transform(df: pd.DataFrame, transform: dict | None) -> pd.DataFrame:
    """
    Add ``x_field_cm, y_field_cm`` (physical paddock cm, origin A0) from WISER
    inches, for cross-modal comparison with the CV pipeline. If ``transform`` is
    ``None`` the frame is returned unchanged (no columns added). Inch columns and
    all inch-based thresholds are untouched — the cm columns are purely additive.
    """
    if transform is None:
        return df
    df = df.copy()
    cm = field_transform.apply_transform(
        transform["matrix"], df[["x", "y"]].to_numpy())
    df["x_field_cm"] = cm[:, 0]
    df["y_field_cm"] = cm[:, 1]
    return df


def verified_boundary_in_wiser(transform: dict | None) -> dict | None:
    """
    The physical paddock rectangle (A0 .. C4) expressed **in WISER inches**, as a
    ``{"rect": [xmin,xmax,ymin,ymax], "confirmed": True, ...}`` boundary suitable
    for :func:`add_validity_flags`, :func:`distance_to_edge`, and
    :func:`thigmotaxis_index` — replacing the provisional ``observed_extent``
    bounding box with a *surveyed* boundary once the frame is georeferenced.

    Returns ``None`` if ``transform`` is ``None`` (callers then keep today's
    provisional boundary). The four field-cm corners are inverse-mapped to inches
    and reduced to an axis-aligned bounding box; because the WISER frame may be
    rotated, this box is a conservative (outer) bound of the true paddock.
    """
    if transform is None:
        return None
    inv = field_transform.invert_transform(transform["matrix"])
    corners_cm = [[0.0, 0.0], [FIELD_X_CM, 0.0],
                  [FIELD_X_CM, FIELD_Y_CM], [0.0, FIELD_Y_CM]]
    corners_in = field_transform.apply_transform(inv, corners_cm)
    xs, ys = corners_in[:, 0], corners_in[:, 1]
    return {
        "rect": [float(xs.min()), float(xs.max()),
                 float(ys.min()), float(ys.max())],
        "confirmed": True,
        "frame": "WISER inches, derived from georeference transform",
        "corners_in": corners_in.tolist(),
        "note": "axis-aligned bound of the (possibly rotated) surveyed paddock",
    }


# ---------------------------------------------------------------------------
# Social: common-time-grid resample, pairwise distances, proximity
# ---------------------------------------------------------------------------

def resample_common_grid(df: pd.DataFrame, bin_s: float = 1.0,
                         valid_only: bool = True) -> pd.DataFrame:
    """
    Per-tag median position on a shared ``bin_s``-second grid (for social
    analysis, which needs synchronous samples across asynchronous tags).
    """
    d = df.dropna(subset=["x", "y", "elapsed_s"]).copy()
    if valid_only and "valid" in d.columns:
        d = d[d["valid"]]
    d["tbin"] = np.floor(d["elapsed_s"] / bin_s).astype("int64")
    grid = (d.groupby(["shortid", "tbin"])
              .agg(x=("x", "median"), y=("y", "median"),
                   elapsed_s=("elapsed_s", "first"))
              .reset_index())
    return grid


def pairwise_distances(grid: pd.DataFrame) -> pd.DataFrame:
    """Long table of pairwise tag distances (inches) per time bin."""
    tags = sorted(grid["shortid"].unique())
    piv = grid.pivot_table(index="tbin", columns="shortid", values=["x", "y"])
    frames = []
    for a, b in itertools.combinations(tags, 2):
        try:
            dx = piv[("x", a)] - piv[("x", b)]
            dy = piv[("y", a)] - piv[("y", b)]
        except KeyError:
            continue
        dist = np.hypot(dx, dy)
        frames.append(pd.DataFrame({"tbin": dist.index, "tag_a": a, "tag_b": b,
                                    "dist_in": dist.values}))
    if not frames:
        return pd.DataFrame(columns=["tbin", "tag_a", "tag_b", "dist_in"])
    return pd.concat(frames).dropna(subset=["dist_in"]).reset_index(drop=True)


def proximity_summary(dist_long: pd.DataFrame,
                      thresholds_in=PROXIMITY_THRESHOLDS_IN,
                      jitter_floor_in: float | None = None) -> pd.DataFrame:
    """
    Per-pair fraction of time below each proximity threshold, **annotated with a
    reliability flag**: a result is marked unreliable when the per-tag jitter/
    error floor is comparable to the threshold (floor ≥ ½ × threshold), because
    two tags cannot be resolved as "close" below the localization noise.
    """
    rows = []
    floor = float(jitter_floor_in) if jitter_floor_in is not None else None
    for thr in thresholds_in:
        reliable = None if floor is None else (floor < 0.5 * thr)
        for (a, b), g in dist_long.groupby(["tag_a", "tag_b"]):
            rows.append({
                "tag_a": a, "tag_b": b,
                "threshold_in": round(thr, 2),
                "threshold_m": round(thr * IN_TO_CM / 100, 2),
                "frac_below": float((g["dist_in"] < thr).mean()),
                "n_bins": int(len(g)),
                "jitter_floor_in": floor,
                "reliable": reliable,
            })
    return pd.DataFrame(rows)


def clustering_index(dist_long: pd.DataFrame) -> pd.DataFrame:
    """Mean pairwise distance per time bin (smaller = more clustered)."""
    if dist_long.empty:
        return pd.DataFrame(columns=["tbin", "mean_pair_dist_in", "n_pairs"])
    return (dist_long.groupby("tbin")["dist_in"]
            .agg(mean_pair_dist_in="mean", n_pairs="size").reset_index())


# ---------------------------------------------------------------------------
# Occupancy + candidate-zone inference (NEVER labeled refuge/food/water)
# ---------------------------------------------------------------------------

def _box_blur(H: np.ndarray, passes: int = 2) -> np.ndarray:
    """Light 3x3 mean smoothing (numpy only; avoids a scipy dependency)."""
    Hs = H.astype(float)
    for _ in range(passes):
        acc = np.zeros_like(Hs)
        cnt = np.zeros_like(Hs)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                sl = np.roll(np.roll(Hs, dx, axis=0), dy, axis=1)
                acc += sl
                cnt += 1
        Hs = acc / cnt
    return Hs


def occupancy_hist(df: pd.DataFrame,
                   extent: tuple[float, float, float, float],
                   bin_in: float = 4.0):
    """2-D fix-count histogram over a fixed extent. Returns (H, xedges, yedges)."""
    xmin, xmax, ymin, ymax = extent
    xe = np.arange(xmin, xmax + bin_in, bin_in)
    ye = np.arange(ymin, ymax + bin_in, bin_in)
    sub = df.dropna(subset=["x", "y"])
    H, _, _ = np.histogram2d(sub["x"].to_numpy(), sub["y"].to_numpy(),
                             bins=[xe, ye])
    return H, xe, ye


def infer_candidate_zones(df: pd.DataFrame,
                          extent: tuple[float, float, float, float] | None = None,
                          k: int = 4, bin_in: float = 6.0,
                          min_sep_in: float = 24.0) -> pd.DataFrame:
    """
    Infer up to ``k`` high-occupancy clusters as **candidate** home-base zones.

    These are explicitly NOT confirmed refuge/food/water ROIs — they are only
    "candidate high-occupancy / home-base clusters" to be confirmed manually via
    the ROI GUI. Uses a lightly smoothed 2-D occupancy histogram and greedily
    picks the top-k cells separated by at least ``min_sep_in`` (no scipy needed).
    """
    if extent is None:
        extent = observed_extent(df)
    H, xe, ye = occupancy_hist(df, extent, bin_in)
    Hs = _box_blur(H)
    xc = 0.5 * (xe[:-1] + xe[1:])
    yc = 0.5 * (ye[:-1] + ye[1:])

    # Rank cells by smoothed occupancy, greedily pick peaks with min separation.
    order = np.argsort(Hs, axis=None)[::-1]
    picks: list[tuple[float, float, float]] = []
    for flat in order:
        ix, iy = np.unravel_index(flat, Hs.shape)
        if Hs[ix, iy] <= 0:
            break
        cx, cy = xc[ix], yc[iy]
        if all(np.hypot(cx - px, cy - py) >= min_sep_in for px, py, _ in picks):
            picks.append((cx, cy, float(H[ix, iy])))
        if len(picks) >= k:
            break

    return pd.DataFrame(
        [{"label": f"candidate_zone_{i+1}", "x": cx, "y": cy, "peak_fixes": n}
         for i, (cx, cy, n) in enumerate(picks)])


# ---------------------------------------------------------------------------
# ROI assignment, time-in-ROI, transitions
# ---------------------------------------------------------------------------

def _roi_time_utc(s) -> np.datetime64:
    """
    Parse an ROI validity time to naive-UTC datetime64.

    Accepts either a tz-aware local string (e.g. ``2026-06-29T07:00:00-04:00``,
    which reads as 7am local) or a naive string assumed to be UTC
    (e.g. ``2026-06-29T11:00:00``). Both denote the same instant; tz-aware input
    is converted to UTC so it compares correctly against the WISER Unix-ms (UTC)
    timestamps.
    """
    t = pd.Timestamp(s)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return np.datetime64(t)


def _point_in_rect(x: np.ndarray, y: np.ndarray, roi: dict) -> np.ndarray:
    """Boolean mask: points inside a (possibly rotated) rectangular ROI."""
    th = np.radians(roi.get("orientation_deg", 0.0))
    dx = x - roi["x"]
    dy = y - roi["y"]
    c, s = np.cos(-th), np.sin(-th)
    lx = c * dx - s * dy
    ly = s * dx + c * dy
    return (np.abs(lx) <= roi.get("width_in", 10.0) / 2) & \
           (np.abs(ly) <= roi.get("height_in", 10.0) / 2)


def _rect_membership(x: np.ndarray, y: np.ndarray, roi: dict,
                     buffer_in: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Two boolean masks for a (possibly rotated) rectangular ROI: ``in_core`` (inside
    the ROI, identical to :func:`_point_in_rect`) and ``in_buffer`` (inside the ROI
    grown by ``buffer_in`` on every side; ``in_buffer`` ⊇ ``in_core``).

    The buffer absorbs WISER position jitter (~7 in median, p95 ~15 in) around the
    small ~36 × 27 in shelter footprint so a jittered fix just outside the rectangle
    is not mistaken for the animal having left.
    """
    th = np.radians(roi.get("orientation_deg", 0.0))
    dx = np.asarray(x, float) - roi["x"]
    dy = np.asarray(y, float) - roi["y"]
    c, s = np.cos(-th), np.sin(-th)
    lx = np.abs(c * dx - s * dy)
    ly = np.abs(s * dx + c * dy)
    hw = roi.get("width_in", 10.0) / 2
    hh = roi.get("height_in", 10.0) / 2
    in_core = (lx <= hw) & (ly <= hh)
    in_buffer = (lx <= hw + buffer_in) & (ly <= hh + buffer_in)
    return in_core, in_buffer


def assign_roi(df: pd.DataFrame, roi_cfg: dict,
               edge_margin_in: float = 12.0) -> pd.DataFrame:
    """
    Add a ``roi`` label per row: the nearest ROI that contains the point (a
    circle within ``radius_in``, or a rect via :func:`_point_in_rect`); else
    ``edge`` (within ``edge_margin_in`` of a confirmed boundary) or ``open``.
    Nearest is by centre distance when shapes overlap.
    """
    df = df.copy()
    rois = roi_cfg.get("rois", []) if roi_cfg else []
    labels = np.full(len(df), "open", dtype=object)
    x = df["x"].to_numpy()
    y = df["y"].to_numpy()
    has_dt = "datetime" in df.columns
    dt = df["datetime"].to_numpy() if has_dt else None

    best = np.full(len(df), np.inf)
    for roi in rois:
        d = np.hypot(x - roi["x"], y - roi["y"])
        if roi.get("shape", "circle") == "rect":
            inside = _point_in_rect(x, y, roi)
        else:
            inside = d <= roi.get("radius_in", 12.0)
        # Time-varying ROI (e.g. a tunnel removed mid-session): only label points
        # whose timestamp falls within [valid_from, valid_until) (UTC). Outside the
        # window the structure is absent, so the point stays "open"/another ROI.
        vf, vu = roi.get("valid_from"), roi.get("valid_until")
        if has_dt and (vf or vu):
            tmask = np.ones(len(df), dtype=bool)
            if vf:
                tmask &= dt >= _roi_time_utc(vf)
            if vu:
                tmask &= dt < _roi_time_utc(vu)
            inside = inside & tmask
        take = inside & (d < best)
        labels[take] = roi["name"]
        best[take] = d[take]

    boundary = (roi_cfg or {}).get("boundary")
    if boundary and "rect" in boundary:
        xmin, xmax, ymin, ymax = boundary["rect"]
        near_edge = (
            (np.abs(x - xmin) <= edge_margin_in) |
            (np.abs(x - xmax) <= edge_margin_in) |
            (np.abs(y - ymin) <= edge_margin_in) |
            (np.abs(y - ymax) <= edge_margin_in))
        labels[(labels == "open") & near_edge] = "edge"

    df["roi"] = labels
    return df


def roi_time_and_transitions(df: pd.DataFrame, roi_cfg: dict,
                             valid_only: bool = True) -> tuple[pd.DataFrame,
                                                               pd.DataFrame]:
    """
    Return (time_in_roi, transitions). Time is estimated as
    ``n_samples × per-tag median dt`` (seconds). Transitions count consecutive
    ROI-label changes per tag.
    """
    d = assign_roi(df, roi_cfg)
    if valid_only and "valid" in d.columns:
        d = d[d["valid"]]

    time_rows = []
    trans_rows = []
    for tag, g in d.sort_values("datetime").groupby("shortid"):
        med_dt = g["dt_s"].median()
        med_dt = med_dt if (med_dt and np.isfinite(med_dt)) else np.nan
        counts = g["roi"].value_counts()
        for roi, c in counts.items():
            time_rows.append({"shortid": tag, "roi": roi, "n_samples": int(c),
                              "seconds": float(c * med_dt) if np.isfinite(med_dt)
                              else np.nan})
        seq = g["roi"].to_numpy()
        changes = seq[1:][seq[1:] != seq[:-1]]
        prev = seq[:-1][seq[1:] != seq[:-1]]
        for a, b in zip(prev, changes):
            trans_rows.append({"shortid": tag, "from_roi": a, "to_roi": b})

    time_df = pd.DataFrame(time_rows)
    trans = pd.DataFrame(trans_rows)
    if not trans.empty:
        trans = (trans.groupby(["from_roi", "to_roi"]).size()
                 .reset_index(name="count").sort_values("count", ascending=False))
    return time_df, trans


# ---------------------------------------------------------------------------
# Distance-to-edge / wall-vs-centre
# ---------------------------------------------------------------------------

def distance_to_edge(df: pd.DataFrame,
                     boundary_rect: tuple[float, float, float, float]
                     ) -> pd.Series:
    """Distance (inches) from each point to the nearest boundary edge."""
    xmin, xmax, ymin, ymax = boundary_rect
    x, y = df["x"].to_numpy(), df["y"].to_numpy()
    return pd.Series(np.minimum.reduce([x - xmin, xmax - x, y - ymin, ymax - y]),
                     index=df.index, name="dist_to_edge_in")


# ---------------------------------------------------------------------------
# Hourly activity (NOT circadian — < 24 h pilot)
# ---------------------------------------------------------------------------

def _add_local_time(df: pd.DataFrame,
                    tz_offset_hours: int = LOCAL_TZ_OFFSET_HOURS) -> pd.DataFrame:
    df = df.copy()
    df["datetime_local"] = df["datetime"] + pd.Timedelta(hours=tz_offset_hours)
    df["hour_bin_utc"] = df["datetime"].dt.floor("h")
    df["clock_hour"] = df["datetime_local"].dt.hour
    return df


def hourly_activity(df: pd.DataFrame,
                    active_speed_inps: float = DEFAULT_ACTIVE_SPEED_INPS,
                    tz_offset_hours: int = LOCAL_TZ_OFFSET_HOURS,
                    valid_only: bool = True) -> dict:
    """
    Per-tag, per-hour activity. Returns a dict of DataFrames:
    ``per_tag_hour`` (shortid × hour_bin_utc), ``group_hour`` (hour_bin_utc),
    ``by_clock_hour`` (pooled mean activity vs local clock-hour), and
    ``by_clock_per_tag`` (one row per shortid × clock-hour, so the plot can show
    the **between-rat mean ± SD**). Labeled hourly/diel **exploratory**, not
    circadian, since the pilot is < 24 h.

    ``active_distance_in`` is path length summed **only over above-threshold
    samples** — it rejects the jitter floor the same way ``active_frac`` does (raw
    path length is positively biased: a stationary tag accumulates ~270 m/h of
    pure jitter).
    """
    d = df.dropna(subset=["datetime"]).copy()
    if valid_only and "valid" in d.columns:
        d = d[d["valid"]]
    d = _add_local_time(d, tz_offset_hours)
    d["active"] = d["speed_inps_smooth"] > active_speed_inps
    # path length during real (above-noise-floor) movement only
    d["active_step_in"] = d["step_in_smooth"].where(d["active"], 0.0)

    per_tag_hour = (d.groupby(["shortid", "hour_bin_utc"])
                    .agg(distance_in=("step_in_smooth", "sum"),
                         active_distance_in=("active_step_in", "sum"),
                         active_frac=("active", "mean"),
                         n=("active", "size"),
                         clock_hour=("clock_hour", "first"))
                    .reset_index())
    group_hour = (per_tag_hour.groupby("hour_bin_utc")
                  .agg(distance_in=("distance_in", "sum"),
                       active_distance_in=("active_distance_in", "sum"),
                       active_frac=("active_frac", "mean"),
                       n=("n", "sum"),
                       clock_hour=("clock_hour", "first"))
                  .reset_index())
    by_clock = (d.groupby("clock_hour")
                .agg(active_frac=("active", "mean"),
                     mean_speed_inps=("speed_inps_smooth", "mean"),
                     n=("active", "size"))
                .reset_index())
    # one row per tag × clock-hour -> the figure aggregates to between-rat mean ± SD
    by_clock_per_tag = (d.groupby(["clock_hour", "shortid"])
                        .agg(active_frac=("active", "mean"),
                             active_distance_in=("active_step_in", "sum"),
                             n=("active", "size"))
                        .reset_index())
    return {"per_tag_hour": per_tag_hour, "group_hour": group_hour,
            "by_clock_hour": by_clock, "by_clock_per_tag": by_clock_per_tag}


def movement_summary(df: pd.DataFrame,
                     active_speed_inps: float = DEFAULT_ACTIVE_SPEED_INPS,
                     valid_only: bool = True) -> pd.DataFrame:
    """Per-tag total distance, mean speed, active fraction, n valid samples."""
    d = df.copy()
    if valid_only and "valid" in d.columns:
        d = d[d["valid"]]
    rows = []
    for tag, g in d.groupby("shortid"):
        dur_h = (g["datetime"].max() - g["datetime"].min()).total_seconds() / 3600
        rows.append({
            "shortid": tag,
            "n_valid": int(len(g)),
            "duration_h": round(dur_h, 3),
            "distance_in": float(np.nansum(g["step_in_smooth"])),
            "distance_m": float(np.nansum(g["step_in_smooth"]) * IN_TO_CM / 100),
            "mean_speed_inps": float(np.nanmean(g["speed_inps_smooth"])),
            "active_frac": float((g["speed_inps_smooth"] > active_speed_inps).mean()),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Acclimation windows (first 1 h / 3 h / rest)
# ---------------------------------------------------------------------------

def acclimation_windows(df: pd.DataFrame, roi_cfg: dict | None = None,
                        valid_only: bool = True) -> pd.DataFrame:
    """
    Compare first-1 h / first-3 h / rest per tag: distance, active fraction,
    spatial coverage (occupied bins), and top-zone dwell fraction. Descriptive
    only — with ~12 h (one night) this is NOT evidence of stable territory.
    """
    d = df.dropna(subset=["datetime"]).copy()
    if valid_only and "valid" in d.columns:
        d = d[d["valid"]]
    t0 = d["datetime"].min()
    d["h_since_start"] = (d["datetime"] - t0).dt.total_seconds() / 3600

    windows = {"first_1h": (0, 1), "first_3h": (0, 3), "rest_after_3h": (3, np.inf)}
    extent = observed_extent(d)
    rows = []
    for tag, g in d.groupby("shortid"):
        for name, (lo, hi) in windows.items():
            w = g[(g["h_since_start"] >= lo) & (g["h_since_start"] < hi)]
            if w.empty:
                continue
            H, _, _ = occupancy_hist(w, extent, bin_in=6.0)
            occupied = int((H > 0).sum())
            top = H.max()
            rows.append({
                "shortid": tag, "window": name, "n": int(len(w)),
                "distance_in": float(np.nansum(w["step_in_smooth"])),
                "active_frac": float((w["speed_inps_smooth"]
                                      > DEFAULT_ACTIVE_SPEED_INPS).mean()),
                "occupied_bins": occupied,
                "top_bin_dwell_frac": float(top / H.sum()) if H.sum() else np.nan,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def _find_col(cols, *needles):
    for c in cols:
        if all(n.lower() in c.lower() for n in needles):
            return c
    return None


def load_weather(path: Path | str) -> pd.DataFrame:
    """
    Load an Ambient Weather Network CSV. Parses the tz-aware ``Date`` column
    (local EDT, ``-04:00``) into ``datetime_utc`` (naive UTC) and
    ``datetime_local``; tidies the key numeric variables. Read-only.
    """
    path = Path(path)
    w = pd.read_csv(path, encoding="utf-8-sig")
    w.columns = [c.strip().strip('"') for c in w.columns]

    date_col = _find_col(w.columns, "date") or w.columns[0]
    dt = pd.to_datetime(w[date_col], utc=True)
    w["datetime_utc"] = dt.dt.tz_localize(None)
    # Etc/GMT+4 is a FIXED UTC-4 offset (sign inverted by POSIX convention).
    w["datetime_local"] = dt.dt.tz_convert("Etc/GMT+4").dt.tz_localize(None)

    renames = {
        "temp_c":      _find_col(w.columns, "Outdoor Temperature"),
        "feels_c":     _find_col(w.columns, "Feels Like"),
        "dewpoint_c":  _find_col(w.columns, "Dew Point"),
        "humidity":    _find_col(w.columns, "Humidity", "%"),
        "wind_mph":    _find_col(w.columns, "Wind Speed"),
        "solar_wm2":   _find_col(w.columns, "Solar Radiation"),
        "uv_index":    _find_col(w.columns, "Ultra-Violet"),
        "pressure_mmhg": _find_col(w.columns, "Relative Pressure"),
        "rain_rate_mmhr": _find_col(w.columns, "Rain Rate"),
        "event_rain_mm":  _find_col(w.columns, "Event Rain"),
        "daily_rain_mm":  _find_col(w.columns, "Daily Rain"),
    }
    for new, old in renames.items():
        if old is not None:
            w[new] = pd.to_numeric(w[old], errors="coerce")

    keep = ["datetime_utc", "datetime_local"] + [k for k in renames
                                                  if k in w.columns]
    return w[keep].sort_values("datetime_utc").reset_index(drop=True)


def load_weather_multi(paths) -> pd.DataFrame:
    """Load and concatenate several AWN weather CSVs (dedup on ``datetime_utc``);
    tolerant of missing files. Used to span nights that cross export files."""
    frames = []
    for p in paths:
        p = Path(p)
        if p.exists():
            try:
                frames.append(load_weather(p))
            except Exception:
                pass
    if not frames:
        return pd.DataFrame(columns=["datetime_utc", "datetime_local"])
    out = (pd.concat(frames, ignore_index=True)
           .drop_duplicates(subset="datetime_utc")
           .sort_values("datetime_utc").reset_index(drop=True))
    return out


def merge_activity_weather(group_hour: pd.DataFrame,
                           weather: pd.DataFrame) -> pd.DataFrame:
    """
    Merge hourly group activity with hourly-averaged weather on UTC hour.

    Alignment is by wall-clock UTC only and is **unverified** to better than the
    ~5-min weather logging interval (independent station clock).
    """
    wx = weather.copy()
    wx["hour_bin_utc"] = wx["datetime_utc"].dt.floor("h")
    num = wx.select_dtypes("number").columns
    wx_hourly = wx.groupby("hour_bin_utc")[list(num)].mean().reset_index()
    merged = group_hour.merge(wx_hourly, on="hour_bin_utc", how="left")
    merged.attrs["alignment"] = "wall-clock UTC, unverified (~5 min)"
    return merged


# ---------------------------------------------------------------------------
# Nightly progression (rate-normalized, paired across nights; habituation + rain)
#
# Primary metric is active_distance_m_per_valid_hour (active path length above the
# noise floor / the tag's valid tracked time), so unequal sub-windows compare.
# The rain test is a per-rat difference-in-differences on the within-night split.
# ---------------------------------------------------------------------------

def _night_bounds(night_date: str, clock_start: int, clock_end: int,
                  tz_offset_hours: int = LOCAL_TZ_OFFSET_HOURS):
    """Local ISO start/end strings for one night's window (tz-aware, -04:00)."""
    off = f"{tz_offset_hours:+03d}:00"
    start = f"{night_date}T{clock_start:02d}:00:00{off}"
    end_dt = pd.Timestamp(night_date) + pd.Timedelta(hours=clock_end)
    return start, end_dt.strftime("%Y-%m-%dT%H:%M:%S") + off


def _rate_from_df(d: pd.DataFrame, *, moving_thr_inps: float,
                  max_gap_s: float = 2.0) -> pd.DataFrame:
    """Per-tag active distance + rate from an already time/valid-filtered frame."""
    rows = []
    for tag, g in d.groupby("shortid"):
        g = g.sort_values("datetime")
        sp = g["speed_inps_smooth"].to_numpy()
        step = g["step_in_smooth"].to_numpy()
        dt_s = g["dt_s"].to_numpy()
        moving = np.isfinite(sp) & (sp > moving_thr_inps)
        dtc = np.where(np.isfinite(dt_s) & (dt_s <= max_gap_s), dt_s, 0.0)
        valid_time_h = float(np.nansum(dtc)) / 3600.0
        active_m = float(np.nansum(np.where(moving, step, 0.0))) * IN_TO_CM / 100.0
        rate = active_m / valid_time_h if valid_time_h > 0 else np.nan
        rows.append({"shortid": tag, "active_distance_m": active_m,
                     "valid_time_h": valid_time_h,
                     "active_distance_m_per_valid_hour": rate,
                     "active_frac": float(moving.mean()) if len(g) else np.nan,
                     "n_valid": int(len(g))})
    return pd.DataFrame(rows)


def window_rate(win: pd.DataFrame, start_local: str, end_local: str, *,
                moving_thr_inps: float, max_gap_s: float = 2.0) -> pd.DataFrame:
    """Per-tag active-distance rate in a local-time sub-window (valid fixes only)."""
    a = _roi_time_utc(start_local)
    b = _roi_time_utc(end_local)
    dtv = win["datetime"].to_numpy()
    d = win[(dtv >= a) & (dtv < b)]
    if "valid" in d.columns:
        d = d[d["valid"]]
    return _rate_from_df(d, moving_thr_inps=moving_thr_inps, max_gap_s=max_gap_s)


def nightly_rates(win: pd.DataFrame, *, moving_thr_inps: float,
                  clock_start: int = 21, clock_end: int = 24) -> pd.DataFrame:
    """Per-tag x night full-window rate table (primary metric + raw distance)."""
    parts = []
    for night, g in win.groupby("night"):
        s, e = _night_bounds(str(night), clock_start, clock_end)
        wr = window_rate(g, s, e, moving_thr_inps=moving_thr_inps)
        wr.insert(0, "night", str(night))
        parts.append(wr)
    return (pd.concat(parts, ignore_index=True) if parts
            else pd.DataFrame(columns=["night", "shortid"]))


def night_split_rates(win: pd.DataFrame, *, moving_thr_inps: float,
                      split_hm: str = "22:20", buffer_min: int = 0,
                      clock_start: int = 21, clock_end: int = 24) -> pd.DataFrame:
    """
    Per-rat pre/post rates and Δ (post−pre) for each night, on the same clock
    split. ``buffer_min`` drops a transition window ``[split, split+buffer)`` so
    the post window starts at split+buffer. Rates in active_distance_m_per_valid_hr.
    """
    rows = []
    for night in sorted(win["night"].unique()):
        g = win[win["night"] == night]
        pre_s, end_s = _night_bounds(str(night), clock_start, clock_end)
        split = f"{night}T{split_hm}:00{LOCAL_OFFSET_STR}"
        post_start = (pd.Timestamp(f"{night}T{split_hm}:00")
                      + pd.Timedelta(minutes=buffer_min)
                      ).strftime("%Y-%m-%dT%H:%M:%S") + LOCAL_OFFSET_STR
        pre = window_rate(g, pre_s, split, moving_thr_inps=moving_thr_inps
                          ).set_index("shortid")["active_distance_m_per_valid_hour"]
        post = window_rate(g, post_start, end_s, moving_thr_inps=moving_thr_inps
                           ).set_index("shortid")["active_distance_m_per_valid_hour"]
        for tag in sorted(set(pre.index) | set(post.index)):
            pr, po = float(pre.get(tag, np.nan)), float(post.get(tag, np.nan))
            rows.append({"night": str(night), "shortid": tag, "buffer_min": buffer_min,
                         "pre_rate": pr, "post_rate": po, "delta": po - pr})
    return pd.DataFrame(rows)


def rain_did(split_rates: pd.DataFrame, rain_night: str,
             control_nights) -> pd.DataFrame:
    """Per-rat difference-in-differences: Δ(rain_night) − Δ(control) for each
    control night, from a :func:`night_split_rates` table."""
    rows = []
    d = split_rates
    for ctrl in control_nights:
        for tag in sorted(d["shortid"].unique()):
            dr = d[(d["night"] == rain_night) & (d["shortid"] == tag)]["delta"]
            dc = d[(d["night"] == ctrl) & (d["shortid"] == tag)]["delta"]
            if len(dr) and len(dc):
                rows.append({"shortid": tag, "rain_night": rain_night,
                             "control_night": ctrl,
                             "delta_rain": float(dr.iloc[0]),
                             "delta_control": float(dc.iloc[0]),
                             "did": float(dr.iloc[0] - dc.iloc[0]),
                             "buffer_min": int(d["buffer_min"].iloc[0])})
    return pd.DataFrame(rows)


def did_confidence(did_table: pd.DataFrame, *, n_boot: int = 2000,
                   seed: int = 0) -> pd.DataFrame:
    """
    Bootstrap 95% CI of the mean rain difference-in-differences **across rats**,
    per buffer. Aggregates each rat to its mean DiD (over control nights) first,
    then resamples rats with replacement. With n=5 rats the CI is deliberately
    wide — that honesty is the point (the promotion blocker is data + confounds,
    not a point estimate). Returns one row per ``buffer_min``:
    ``n_rats, mean_did, ci_lo, ci_hi``.
    """
    if did_table is None or did_table.empty:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    out = []
    for buf, g in did_table.groupby("buffer_min"):
        per_rat = g.groupby("shortid")["did"].mean().to_numpy()
        n = len(per_rat)
        if n == 0:
            continue
        boots = np.array([rng.choice(per_rat, n, replace=True).mean()
                          for _ in range(n_boot)])
        out.append({"buffer_min": int(buf), "n_rats": n,
                    "mean_did": float(per_rat.mean()),
                    "ci_lo": float(np.percentile(boots, 2.5)),
                    "ci_hi": float(np.percentile(boots, 97.5))})
    return pd.DataFrame(out)


def cumulative_night_distance(win: pd.DataFrame, *, moving_thr_inps: float,
                              bin_s: int = 60, tz_offset_hours: int = LOCAL_TZ_OFFSET_HOURS
                              ) -> pd.DataFrame:
    """Per-tag x night cumulative active distance (m) vs minutes-since-21:00,
    downsampled to ``bin_s`` for plotting the through-the-night curves."""
    parts = []
    for (night, tag), g in win.groupby(["night", "shortid"]):
        g = g.sort_values("datetime")
        sp = g["speed_inps_smooth"].to_numpy()
        step = g["step_in_smooth"].to_numpy()
        moving = np.isfinite(sp) & (sp > moving_thr_inps)
        act_m = np.where(moving, np.nan_to_num(step), 0.0) * IN_TO_CM / 100.0
        cum = np.cumsum(act_m)
        start = _roi_time_utc(_night_bounds(str(night), 21, 24, tz_offset_hours)[0])
        t_min = (g["datetime"].to_numpy() - start) / np.timedelta64(1, "m")
        tb = (t_min // (bin_s / 60)).astype(int)
        d = pd.DataFrame({"tb": tb, "t_min": t_min, "cum_m": cum})
        d = d.groupby("tb").last().reset_index(drop=True)
        d["night"] = str(night)
        d["shortid"] = tag
        parts.append(d[["night", "shortid", "t_min", "cum_m"]])
    return (pd.concat(parts, ignore_index=True) if parts
            else pd.DataFrame(columns=["night", "shortid", "t_min", "cum_m"]))


# --- Nightly behavior & social (home use, exploration, cohesion, graph, geometry) ---

def _roi_category_map(roi_cfg):
    """name -> {home, resource, tunnel} from ROI type; else 'open'."""
    out = {}
    for r in (roi_cfg or {}).get("rois", []):
        t = r.get("type")
        out[r["name"]] = ("home" if t == "refuge" else
                          "resource" if t in ("water", "food") else
                          "tunnel" if t == "tunnel" else "open")
    return out


def _valid_only(g):
    return g[g["valid"]] if "valid" in g.columns else g


def nightly_roi_use(win, roi_cfg) -> pd.DataFrame:
    """Per tag x night: time fractions by category (home/resource/tunnel/open) and
    the **home<->open transition rate per valid hour** (explore out and back)."""
    catmap = _roi_category_map(roi_cfg)
    d = assign_roi(_valid_only(win), roi_cfg)
    d = d.assign(cat=d["roi"].map(lambda n: catmap.get(n, "open")))
    rows = []
    for (night, tag), g in d.groupby(["night", "shortid"]):
        g = g.sort_values("datetime")
        n = len(g)
        fr = {c: float((g["cat"] == c).mean()) for c in ("home", "resource", "tunnel", "open")}
        state = np.where(g["cat"].to_numpy() == "home", 1,
                         np.where(g["cat"].to_numpy() == "open", 0, -1))
        ho = state[state >= 0]
        n_trans = int((ho[1:] != ho[:-1]).sum()) if len(ho) > 1 else 0
        dt = g["dt_s"].to_numpy()
        vh = float(np.nansum(np.where(np.isfinite(dt) & (dt <= 2.0), dt, 0.0))) / 3600.0
        rows.append({"night": str(night), "shortid": tag, "n_valid": n,
                     "home_frac": fr["home"], "resource_frac": fr["resource"],
                     "tunnel_frac": fr["tunnel"], "open_frac": fr["open"],
                     "home_open_transitions_per_h": n_trans / vh if vh > 0 else np.nan})
    return pd.DataFrame(rows)


def nightly_movement_by_cat(win, roi_cfg, *, moving_thr_inps) -> pd.DataFrame:
    """Per tag x night movement rate + active fraction computed on **open** fixes
    only (does the outside movement pattern change over nights)."""
    d = assign_roi(_valid_only(win), roi_cfg)
    d = d.assign(is_open=d["roi"].isin(["open", "edge"]))
    rows = []
    for (night, tag), g in d.groupby(["night", "shortid"]):
        rr = _rate_from_df(g[g["is_open"]], moving_thr_inps=moving_thr_inps)
        if len(rr):
            rows.append({"night": str(night), "shortid": tag,
                         "open_rate_m_per_valid_h": float(rr["active_distance_m_per_valid_hour"].iloc[0]),
                         "open_active_frac": float(rr["active_frac"].iloc[0]),
                         "open_valid_h": float(rr["valid_time_h"].iloc[0])})
    return pd.DataFrame(rows)


def nightly_social(win, *, jitter_floor_in, bin_s: float = 1.0) -> pd.DataFrame:
    """Per night: cohesion (mean pairwise distance, <=0.5/1/2 m fractions with
    jitter-floor reliability, clustering) and leave-one-out occupancy similarity."""
    rows = []
    for night, g in win.groupby("night"):
        gv = _valid_only(g)
        grid = resample_common_grid(gv, bin_s=bin_s)
        dl = pairwise_distances(grid)
        prox = proximity_summary(dl, jitter_floor_in=jitter_floor_in)
        ci = clustering_index(dl)
        ext = observed_extent(gv)
        hists = {t: occupancy_hist(gv[gv["shortid"] == t], ext, bin_in=4.0)[0]
                 for t in sorted(gv["shortid"].unique())}
        loo = occupancy_similarity_loo(hists)
        row = {"night": str(night),
               "mean_pair_dist_in": float(dl["dist_in"].mean()) if len(dl) else np.nan,
               "clustering_mean_pair_dist_in": float(ci["mean_pair_dist_in"].mean()) if len(ci) else np.nan,
               "loo_occupancy_cosine_mean": float(loo["loo_cosine"].mean()) if len(loo) else np.nan}
        for thr, sub in prox.groupby("threshold_in"):
            row[f"frac_below_{thr:.0f}in"] = float(sub["frac_below"].mean())
            row[f"reliable_{thr:.0f}in"] = bool(sub["reliable"].iloc[0]) if sub["reliable"].iloc[0] is not None else None
        rows.append(row)
    return pd.DataFrame(rows)


def nightly_graph_structure(win, roi_cfg):
    """Per-night exploration graph (nodes = ROIs incl. open; edges = transitions):
    (structure_df, night_to_night_similarity_df). Structure = distinct edges, total
    transitions, node count, graph density, out-hub. Similarity = consecutive-night
    edge-usage cosine + Jaccard (does the network stabilize)."""
    struct_rows, edge_vecs = [], {}
    for night, g in win.groupby("night"):
        pt = per_tag_transitions(_valid_only(g), roi_cfg, named_only=False)
        if pt.empty:
            struct_rows.append({"night": str(night), "n_nodes": 0, "n_distinct_edges": 0,
                                "n_transitions": 0, "graph_density": np.nan, "hub_out": None})
            edge_vecs[str(night)] = {}
            continue
        agg = pt.groupby(["from_roi", "to_roi"])["count"].sum()
        nodes = set(pt["from_roi"]) | set(pt["to_roi"])
        nn = len(nodes)
        struct_rows.append({
            "night": str(night), "n_nodes": nn, "n_distinct_edges": int(len(agg)),
            "n_transitions": int(agg.sum()),
            "graph_density": float(len(agg) / (nn * (nn - 1))) if nn > 1 else np.nan,
            "hub_out": agg.groupby(level=0).sum().idxmax()})
        edge_vecs[str(night)] = {f"{a}->{b}": c for (a, b), c in agg.items()}
    nights = sorted(edge_vecs)
    sim_rows = []
    for a, b in zip(nights[:-1], nights[1:]):
        keys = sorted(set(edge_vecs[a]) | set(edge_vecs[b]))
        va = np.array([edge_vecs[a].get(k, 0) for k in keys], float)
        vb = np.array([edge_vecs[b].get(k, 0) for k in keys], float)
        cos = float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb))) if va.any() and vb.any() else np.nan
        ua, ub = va > 0, vb > 0
        sim_rows.append({"night_a": a, "night_b": b, "edge_cosine": cos,
                         "jaccard": float((ua & ub).sum() / max((ua | ub).sum(), 1))})
    return pd.DataFrame(struct_rows), pd.DataFrame(sim_rows)


def nightly_geometry(win, extent, *, bin_in: float = 4.0,
                     corridor_pct: float = 80.0) -> pd.DataFrame:
    """Per-night space-use geometry: occupancy coverage, concentration, spatial
    dispersion, and corridor/skeleton cell counts (over a common extent)."""
    rows = []
    for night, g in win.groupby("night"):
        gv = _valid_only(g)
        H, _, _ = occupancy_hist(gv, extent, bin_in=bin_in)
        counts = np.sort(H[H > 0])[::-1]
        tot = counts.sum()
        conc = (1 - int(np.searchsorted(np.cumsum(counts) / tot, 0.5) + 1) / len(counts)
                if len(counts) else np.nan)
        mask, _ = corridor_mask(H, pct=corridor_pct)
        rows.append({"night": str(night),
                     "coverage_frac": float((H > 0).sum() / H.size),
                     "occupied_cells": int((H > 0).sum()),
                     "concentration": float(conc),
                     "dispersion_in": float(np.hypot(gv["x"].std(), gv["y"].std())),
                     "corridor_cells": int(mask.sum()),
                     "skeleton_cells": int(skeletonize_mask(mask).sum())})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Leader-follower / route-following (CANDIDATE route use, not social following)
#
# Following is defined as **time-lagged path reuse**, NOT proximity: for an
# ordered pair A->B, B follows A if B arrives at A's earlier position after a
# positive lag while both are moving and their headings are aligned. Everything
# is computed on a common 1 Hz grid of smoothed positions and validated against
# circular-shift null controls. The spatial threshold R must be compared to the
# stationary jitter floor before any close-following claim.
# ---------------------------------------------------------------------------

DEFAULT_FOLLOW_LAGS_S = range(1, 31)     # candidate lags (seconds)
DEFAULT_FOLLOW_COS = 0.5                 # heading-alignment cosine threshold
DEFAULT_FOLLOW_MIN_R_IN = 24.0           # floor for R (inches)


def _shift_left(arr: np.ndarray, lag: int) -> np.ndarray:
    """Align ``arr[t+lag]`` to index ``t`` (shift left, pad tail with NaN)."""
    out = np.full(arr.shape, np.nan, dtype=float)
    if 0 <= lag < len(arr):
        out[:len(arr) - lag] = arr[lag:]
    return out


def _shift_left_bool(arr: np.ndarray, lag: int) -> np.ndarray:
    """Boolean variant of :func:`_shift_left` (pad tail with False)."""
    out = np.zeros(len(arr), dtype=bool)
    if 0 <= lag < len(arr):
        out[:len(arr) - lag] = arr[lag:]
    return out


def follow_radius_in(jitter_floor_in: float | None,
                     min_r_in: float = DEFAULT_FOLLOW_MIN_R_IN) -> float:
    """R = max(3 x stationary jitter radius, ``min_r_in``). With a ~7 in floor
    this is 24 in; close following is only interpretable when R >= 3x the floor."""
    floor = float(jitter_floor_in) if jitter_floor_in else 0.0
    return float(max(3.0 * floor, min_r_in))


def grid_speed_noise_floor(stationary_df: pd.DataFrame, *, bin_s: float = 1.0,
                           smooth_s: float = 5.0, pct: int = 99) -> float:
    """
    Conservative *moving* threshold for the following analysis: the p99 of grid
    speed on the **stationary** baseline (which is not moving, so any grid speed
    is noise), computed with the *same* 1 Hz resample + smoothing pipeline as
    :func:`build_following_grid`. Mirrors :func:`speed_noise_floor` but on the
    common grid.
    """
    grid = build_following_grid(stationary_df, bin_s=bin_s, smooth_s=smooth_s,
                                moving_thr_inps=0.0)
    sp = grid["SP"][np.isfinite(grid["SP"])]
    return float(np.percentile(sp, pct)) if sp.size else 0.0


def build_following_grid(df: pd.DataFrame, *, bin_s: float = 1.0,
                         smooth_s: float = 5.0,
                         moving_thr_inps: float = 0.0) -> dict:
    """
    Common-grid, smoothed, kinematic arrays for the following analysis.

    Steps 1-4 of the spec: resample to a ``bin_s``-second grid (median position
    per tag per bin), reindex to a **contiguous** time axis so a shift by L bins
    is exactly an L-second lag, smooth positions with a ``smooth_s``-second
    rolling median, then derive per-bin velocity, speed, heading unit vector and a
    ``moving`` mask (speed > ``moving_thr_inps``). Returns aligned 2-D arrays
    ``X, Y, SP, UX, UY`` (float, T x n_tags) and ``MOV`` (bool), plus ``tags``,
    ``tbin``, ``elapsed_s`` and ``bin_s``. Missing seconds are NaN / not-moving.
    """
    grid = resample_common_grid(df, bin_s=bin_s, valid_only=True)
    tags = sorted(grid["shortid"].unique())
    if not tags:
        empty = np.zeros((0, 0))
        return {"tags": [], "tbin": np.array([]), "elapsed_s": np.array([]),
                "bin_s": bin_s, "X": empty, "Y": empty, "SP": empty,
                "UX": empty, "UY": empty, "MOV": empty.astype(bool),
                "moving_thr_inps": float(moving_thr_inps)}
    piv = grid.pivot_table(index="tbin", columns="shortid", values=["x", "y"])
    full = np.arange(int(piv.index.min()), int(piv.index.max()) + 1)
    piv = piv.reindex(full)
    win = max(1, int(round(smooth_s / bin_s)))
    T, n = len(full), len(tags)
    X = np.full((T, n), np.nan)
    Y = np.full((T, n), np.nan)
    for j, tag in enumerate(tags):
        X[:, j] = piv[("x", tag)].rolling(win, center=True, min_periods=1).median()
        Y[:, j] = piv[("y", tag)].rolling(win, center=True, min_periods=1).median()
    VX = np.full((T, n), np.nan)
    VY = np.full((T, n), np.nan)
    VX[1:, :] = (X[1:, :] - X[:-1, :]) / bin_s
    VY[1:, :] = (Y[1:, :] - Y[:-1, :]) / bin_s
    SP = np.hypot(VX, VY)
    with np.errstate(invalid="ignore", divide="ignore"):
        UX = VX / SP
        UY = VY / SP
    MOV = np.isfinite(SP) & (SP > float(moving_thr_inps))
    return {"tags": tags, "tbin": full, "elapsed_s": full * bin_s, "bin_s": bin_s,
            "X": X, "Y": Y, "SP": SP, "UX": UX, "UY": UY, "MOV": MOV,
            "moving_thr_inps": float(moving_thr_inps)}


def _follow_masks(la: tuple, fo: tuple, lag: int, R: float, cos_thresh: float):
    """Per-bin (follow, valid, dist, cosalign) from raw 1-D leader/follower arrays.
    ``la = (xa, ya, uxa, uya, mova)``, ``fo = (xb, yb, uxb, uyb, movb)``. The
    follower is read at t+lag (it arrives where the leader was)."""
    xa, ya, uxa, uya, mova = la
    xb, yb, uxb, uyb, movb = fo
    xbL = _shift_left(xb, lag)
    ybL = _shift_left(yb, lag)
    uxbL = _shift_left(uxb, lag)
    uybL = _shift_left(uyb, lag)
    movbL = _shift_left_bool(movb, lag)
    dist = np.hypot(xbL - xa, ybL - ya)
    cosal = uxa * uxbL + uya * uybL
    valid = mova & movbL & np.isfinite(dist) & np.isfinite(cosal)
    follow = valid & (dist < R) & (cosal > cos_thresh)
    return follow, valid, dist, cosal


def _leader_cols(grid: dict, i: int) -> tuple:
    return (grid["X"][:, i], grid["Y"][:, i], grid["UX"][:, i],
            grid["UY"][:, i], grid["MOV"][:, i])


def _pair_follow(grid: dict, ia: int, ib: int, lag: int,
                 R: float, cos_thresh: float):
    """Per-bin masks for leader ``ia``, follower ``ib`` at one lag (grid wrapper)."""
    return _follow_masks(_leader_cols(grid, ia), _leader_cols(grid, ib),
                         lag, R, cos_thresh)


def _peak_over_lags_arrays(la: tuple, fo: tuple, lags, R, cos_thresh) -> float:
    """Peak follow score over lags from raw arrays (NaN-safe)."""
    best = np.nan
    for L in lags:
        follow, valid, _, _ = _follow_masks(la, fo, L, R, cos_thresh)
        nv = valid.sum()
        if nv:
            sc = float(follow.sum()) / nv
            if np.isnan(best) or sc > best:
                best = sc
    return best


def follow_scores_all(grid: dict, lags=DEFAULT_FOLLOW_LAGS_S, *, R: float,
                      cos_thresh: float = DEFAULT_FOLLOW_COS) -> pd.DataFrame:
    """
    follow_score(A->B, lag) for every ordered pair and lag: the fraction of valid
    moving timepoints where the follower B(t+lag) is within ``R`` of the leader
    A(t) and their headings align (cosine > ``cos_thresh``). Long table
    ``[leader, follower, lag, score, n_valid]``.
    """
    tags = grid["tags"]
    lags = list(lags)
    rows = []
    for ia, a in enumerate(tags):
        for ib, b in enumerate(tags):
            if ia == ib:
                continue
            for L in lags:
                follow, valid, _, _ = _pair_follow(grid, ia, ib, L, R, cos_thresh)
                nv = int(valid.sum())
                score = float(follow.sum()) / nv if nv else np.nan
                rows.append({"leader": a, "follower": b, "lag": L,
                             "score": score, "n_valid": nv})
    return pd.DataFrame(rows)


def following_peaks(scores: pd.DataFrame) -> pd.DataFrame:
    """Per ordered pair: peak score and the lag (s) that achieves it."""
    rows = []
    for (a, b), g in scores.groupby(["leader", "follower"]):
        g2 = g.dropna(subset=["score"])
        if g2.empty:
            rows.append({"leader": a, "follower": b, "peak_score": np.nan,
                         "best_lag_s": np.nan, "n_valid": int(g["n_valid"].max())})
            continue
        i = g2["score"].idxmax()
        rows.append({"leader": a, "follower": b,
                     "peak_score": float(g2.loc[i, "score"]),
                     "best_lag_s": int(g2.loc[i, "lag"]),
                     "n_valid": int(g2.loc[i, "n_valid"])})
    return pd.DataFrame(rows)


def following_asymmetry(peaks: pd.DataFrame, eps: float = 1e-9) -> pd.DataFrame:
    """
    Directional asymmetry per ordered pair:
    ``(score(A->B) - score(B->A)) / (score(A->B) + score(B->A) + eps)``.
    Positive => A leads B more than the reverse.
    """
    pk = {(r.leader, r.follower): (r.peak_score if pd.notna(r.peak_score) else 0.0)
          for r in peaks.itertuples()}
    tags = sorted(set(peaks["leader"]) | set(peaks["follower"]))
    rows = []
    for a, b in itertools.permutations(tags, 2):
        s_ab, s_ba = pk.get((a, b), 0.0), pk.get((b, a), 0.0)
        rows.append({"leader": a, "follower": b, "score": s_ab, "rev_score": s_ba,
                     "asymmetry": (s_ab - s_ba) / (s_ab + s_ba + eps)})
    return pd.DataFrame(rows)


def following_null(grid: dict, peaks: pd.DataFrame | None = None, *,
                   lags=DEFAULT_FOLLOW_LAGS_S, R: float,
                   cos_thresh: float = DEFAULT_FOLLOW_COS,
                   n_shuffles: int = 100, shift_range_s=(300, 1200),
                   seed: int = 0) -> pd.DataFrame:
    """
    Circular-shift null: roll the **follower**'s trajectory by a random 5-20 min
    offset and recompute the peak-over-lags score, ``n_shuffles`` times per
    ordered pair. Returns ``shuffled_mean``, ``shuffled_p95``, ``shuffled_sd`` and
    the real-vs-null ``z_score`` (NaN if ``peaks`` not given).
    """
    rng = np.random.default_rng(seed)
    tags = grid["tags"]
    lags = list(lags)
    T = grid["X"].shape[0]
    bin_s = grid["bin_s"]
    lo = int(shift_range_s[0] / bin_s)
    hi = min(int(shift_range_s[1] / bin_s), max(T - 1, 1))
    real = ({(r.leader, r.follower): r.peak_score for r in peaks.itertuples()}
            if peaks is not None else {})
    rows = []
    for ia, a in enumerate(tags):
        la = _leader_cols(grid, ia)
        for ib, b in enumerate(tags):
            if ia == ib:
                continue
            xb, yb, uxb, uyb, movb = _leader_cols(grid, ib)
            shuff = np.full(n_shuffles, np.nan)
            for s in range(n_shuffles):
                k = int(rng.integers(lo, hi + 1)) if hi >= lo else 0
                fo = (np.roll(xb, k), np.roll(yb, k), np.roll(uxb, k),
                      np.roll(uyb, k), np.roll(movb, k))   # roll follower only
                shuff[s] = _peak_over_lags_arrays(la, fo, lags, R, cos_thresh)
            sm = float(np.nanmean(shuff)) if np.isfinite(shuff).any() else np.nan
            sd = float(np.nanstd(shuff)) if np.isfinite(shuff).any() else np.nan
            sp = (float(np.nanpercentile(shuff, 95))
                  if np.isfinite(shuff).any() else np.nan)
            rp = real.get((a, b), np.nan)
            z = (rp - sm) / sd if (sd and np.isfinite(rp) and np.isfinite(sm)) else np.nan
            rows.append({"leader": a, "follower": b, "shuffled_mean": sm,
                         "shuffled_p95": sp, "shuffled_sd": sd, "z_score": z})
    return pd.DataFrame(rows)


def following_events(grid: dict, leader, follower, lag: int, *, R: float,
                     cos_thresh: float = DEFAULT_FOLLOW_COS,
                     min_bout_s: float = 3.0) -> pd.DataFrame:
    """
    Contiguous following bouts for one ordered pair at ``lag``: runs of
    consecutive following timepoints lasting >= ``min_bout_s``. Columns include
    start/end elapsed seconds, duration, mean distance and mean heading alignment,
    plus the grid indices (for trajectory snippets).
    """
    tags = grid["tags"]
    idx = {t: i for i, t in enumerate(tags)}
    if leader not in idx or follower not in idx:
        return pd.DataFrame()
    follow, _, dist, cosal = _pair_follow(grid, idx[leader], idx[follower],
                                          int(lag), R, cos_thresh)
    els = grid["elapsed_s"]
    bin_s = grid["bin_s"]
    min_len = max(1, int(round(min_bout_s / bin_s)))
    rows = []
    i, nT = 0, len(follow)
    while i < nT:
        if follow[i]:
            j = i
            while j < nT and follow[j]:
                j += 1
            if j - i >= min_len:
                seg = slice(i, j)
                rows.append({"leader": leader, "follower": follower,
                             "lag_s": int(lag),
                             "t_start_s": float(els[i]), "t_end_s": float(els[j - 1]),
                             "duration_s": float((j - i) * bin_s),
                             "mean_dist_in": float(np.nanmean(dist[seg])),
                             "mean_cosalign": float(np.nanmean(cosal[seg])),
                             "i_start": int(i), "i_end": int(j - 1)})
            i = j
        else:
            i += 1
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Route-structure (CANDIDATE corridor/route use; check vs jitter floor + baseline)
#
# Do rats reuse the same corridors/routes? Built on cleaned points only and
# always cross-checked against the stationary fixed-position baseline so apparent
# "routes" that are really WISER anchor-geometry artifacts are caught.
# ---------------------------------------------------------------------------

def select_route_window(df: pd.DataFrame, *, clock_start: int = 21,
                        clock_end: int = 23,
                        tz_offset_hours: int = LOCAL_TZ_OFFSET_HOURS,
                        dates=None, valid_only: bool = True) -> pd.DataFrame:
    """
    Cleaned fixes inside the local-time block ``[clock_start, clock_end)`` across
    all dates present (or a ``dates`` subset), pooled into one "trunk" and tagged
    with a ``night`` date for the per-night consistency check. ``valid_only`` keeps
    only ``valid`` rows (post add_validity_flags + apply_tag_cutoffs).
    """
    d = df.dropna(subset=["datetime"]).copy()
    if valid_only and "valid" in d.columns:
        d = d[d["valid"]]
    loc = d["datetime"] + pd.Timedelta(hours=tz_offset_hours)
    d["night"] = loc.dt.date.astype(str)
    d["clock_hour"] = loc.dt.hour
    d = d[(d["clock_hour"] >= clock_start) & (d["clock_hour"] < clock_end)]
    if dates:
        d = d[d["night"].isin([str(x) for x in dates])]
    return d.reset_index(drop=True)


def corridor_mask(H: np.ndarray, *, pct: float = 80.0, blur_passes: int = 2):
    """Smoothed occupancy >= the ``pct`` percentile of non-zero cells -> binary
    corridor mask. Returns ``(mask, smoothed_H)`` (same [x_bin, y_bin] indexing as
    :func:`occupancy_hist`)."""
    Hs = _box_blur(H, passes=blur_passes)
    nz = Hs[Hs > 0]
    if nz.size == 0:
        return np.zeros(Hs.shape, dtype=bool), Hs
    thr = np.percentile(nz, pct)
    return (Hs >= thr) & (Hs > 0), Hs


def _mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = int((a & b).sum())
    union = int((a | b).sum())
    return float(inter / union) if union else np.nan


def skeletonize_mask(mask: np.ndarray) -> np.ndarray:
    """
    Morphological skeleton via **Zhang-Suen thinning** (numpy only; skimage is not
    in the env). A 1-cell zero pad keeps the wrap-around in ``np.roll`` harmless
    (the padded ring is always 0), so border corridors thin correctly.
    """
    img = np.pad(mask.astype(np.uint8), 1)

    def nb(I):  # P2..P9 (N, NE, E, SE, S, SW, W, NW)
        return [np.roll(np.roll(I, dy, 0), dx, 1) for dy, dx in
                ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))]

    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            P2, P3, P4, P5, P6, P7, P8, P9 = nb(img)
            seq = [P2, P3, P4, P5, P6, P7, P8, P9, P2]
            C = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8)
                    for i in range(8))
            N = P2 + P3 + P4 + P5 + P6 + P7 + P8 + P9
            if step == 0:
                m1, m2 = (P2 * P4 * P6 == 0), (P4 * P6 * P8 == 0)
            else:
                m1, m2 = (P2 * P4 * P8 == 0), (P2 * P6 * P8 == 0)
            cond = (img == 1) & (C == 1) & (N >= 2) & (N <= 6) & m1 & m2
            if cond.any():
                img[cond] = 0
                changed = True
    return img[1:-1, 1:-1].astype(bool)


def _bin_indices(x, y, extent, bin_in, shape):
    xmin, xmax, ymin, ymax = extent
    xe = np.arange(xmin, xmax + bin_in, bin_in)
    ye = np.arange(ymin, ymax + bin_in, bin_in)
    xi = np.clip(np.digitize(x, xe) - 1, 0, shape[0] - 1)
    yi = np.clip(np.digitize(y, ye) - 1, 0, shape[1] - 1)
    return xi, yi


def route_reuse_index(df: pd.DataFrame, extent, group_mask: np.ndarray, *,
                      bin_in: float = 4.0) -> pd.DataFrame:
    """
    Per-rat route reuse: ``self_concentration`` (1 - fraction of the rat's
    occupied cells that hold 50% of its time; high = few, repeated paths),
    ``corridor_adherence`` (fraction of the rat's fixes on the group corridor
    mask), and normalised occupancy ``entropy`` (0 concentrated .. 1 uniform).
    """
    rows = []
    for tag, g in df.groupby("shortid"):
        H, _, _ = occupancy_hist(g, extent, bin_in=bin_in)
        counts = H[H > 0].astype(float)
        n_valid = int(len(g))
        if counts.sum() <= 0:
            rows.append({"shortid": tag, "n_valid": n_valid,
                         "self_concentration": np.nan, "corridor_adherence": np.nan,
                         "occ_entropy": np.nan})
            continue
        tot = counts.sum()
        s = np.sort(counts)[::-1]
        cum = np.cumsum(s) / tot
        k = int(np.searchsorted(cum, 0.5) + 1)
        self_conc = 1.0 - k / len(counts)
        p = counts / tot
        ent = float(-(p * np.log(p)).sum() / np.log(len(counts))) if len(counts) > 1 else 0.0
        xi, yi = _bin_indices(g["x"].to_numpy(), g["y"].to_numpy(), extent, bin_in,
                              group_mask.shape)
        adh = float(group_mask[xi, yi].mean())
        rows.append({"shortid": tag, "n_valid": n_valid,
                     "self_concentration": float(self_conc),
                     "corridor_adherence": adh, "occ_entropy": ent})
    return pd.DataFrame(rows)


def occupancy_similarity_loo(per_tag_hist: dict, blur_passes: int = 2) -> pd.DataFrame:
    """Leave-one-out occupancy similarity: each rat's (smoothed, flattened)
    occupancy vs the summed map of all **other** rats (cosine + Pearson)."""
    tags = sorted(per_tag_hist)
    flat = {t: _box_blur(per_tag_hist[t], passes=blur_passes).ravel().astype(float)
            for t in tags}
    total = sum(flat.values())
    rows = []
    for t in tags:
        a, b = flat[t], total - flat[t]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        cos = float(a @ b / (na * nb)) if na > 0 and nb > 0 else np.nan
        corr = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else np.nan
        rows.append({"shortid": t, "loo_cosine": cos, "loo_corr": corr})
    return pd.DataFrame(rows)


def movement_bouts(df: pd.DataFrame, *, moving_thr_inps: float,
                   min_bout_s: float = 3.0, max_gap_s: float = 2.0,
                   smooth_window: int = DEFAULT_SMOOTH_WINDOW) -> pd.DataFrame:
    """
    Per-tag contiguous moving bouts and their **straightness** =
    ``||end - start|| / path length``. Recomputes speed/step on the passed rows
    (so it is correct on filtered subsets); a bout breaks on non-moving samples or
    a time gap > ``max_gap_s``. Straightness is clipped to [0, 1]; because path
    length is jitter-inflated, straightness is a *conservative* (low-biased) route
    measure.
    """
    d = add_speed(df, smooth_window=smooth_window)
    rows = []
    for tag, g in d.groupby("shortid", sort=False):
        g = g.reset_index(drop=True)
        sp = g["speed_inps_smooth"].to_numpy()
        dt = g["dt_s"].to_numpy()
        step = g["step_in_smooth"].to_numpy()
        x, y = g["x"].to_numpy(), g["y"].to_numpy()
        t = (g["datetime"] - g["datetime"].iloc[0]).dt.total_seconds().to_numpy()
        moving = np.isfinite(sp) & (sp > moving_thr_inps)
        i, n = 0, len(g)
        while i < n:
            if not moving[i]:
                i += 1
                continue
            j = i + 1
            while j < n and moving[j] and np.isfinite(dt[j]) and dt[j] <= max_gap_s:
                j += 1
            dur = t[j - 1] - t[i]
            if dur >= min_bout_s and (j - i) >= 2:
                path = float(np.nansum(step[i + 1:j]))
                disp = float(np.hypot(x[j - 1] - x[i], y[j - 1] - y[i]))
                straight = float(np.clip(disp / path, 0, 1)) if path > 0 else np.nan
                rows.append({"shortid": tag, "i_start": i, "i_end": j - 1,
                             "t_start_s": float(t[i]), "duration_s": float(dur),
                             "path_in": path, "disp_in": disp,
                             "straightness": straight})
            i = j
    return pd.DataFrame(rows)


def straightness_summary(bouts: pd.DataFrame) -> pd.DataFrame:
    """Per-tag bout count + straightness/path/duration summary."""
    if bouts.empty:
        return pd.DataFrame(columns=["shortid", "n_bouts", "median_straightness",
                                     "p90_straightness", "median_path_in",
                                     "median_bout_s"])
    return (bouts.groupby("shortid").agg(
        n_bouts=("straightness", "size"),
        median_straightness=("straightness", "median"),
        p90_straightness=("straightness", lambda s: s.quantile(0.9)),
        median_path_in=("path_in", "median"),
        median_bout_s=("duration_s", "median")).reset_index())


def per_tag_transitions(df: pd.DataFrame, roi_cfg: dict,
                        named_only: bool = True) -> pd.DataFrame:
    """
    Per-tag node-to-node route transitions ``[shortid, from_roi, to_roi, count]``.

    With ``named_only`` (default), a transition is an edge between two **named**
    ROIs in the order they are *visited* — intervening ``open``/``edge`` samples
    are skipped, so a rat travelling house_1 → (open) → refuge_1 yields the route
    edge house_1 → refuge_1 (not the trivial co-located flips only). With
    ``named_only=False`` every adjacent label change is counted (open/edge
    included).
    """
    d = assign_roi(df, roi_cfg)
    rows = []
    for tag, g in d.sort_values(["shortid", "datetime"]).groupby("shortid", sort=False):
        seq = g["roi"].to_numpy()
        if named_only:
            seq = seq[(seq != "open") & (seq != "edge")]
        # compress consecutive duplicates -> the sequence of distinct visits
        if len(seq) < 2:
            continue
        keep = np.concatenate(([True], seq[1:] != seq[:-1]))
        visits = seq[keep]
        for fr, to in zip(visits[:-1], visits[1:]):
            rows.append({"shortid": tag, "from_roi": fr, "to_roi": to})
    if not rows:
        return pd.DataFrame(columns=["shortid", "from_roi", "to_roi", "count"])
    return (pd.DataFrame(rows).groupby(["shortid", "from_roi", "to_roi"])
            .size().reset_index(name="count"))


def edge_usage_similarity(per_tag_trans: pd.DataFrame):
    """Do different rats use the same route edges? Returns ``(similarity, shared)``:
    per-rat-pair edge-weight cosine + Jaccard, and a shared-edge table
    (edge -> n_rats using it, total count)."""
    if per_tag_trans.empty:
        return pd.DataFrame(), pd.DataFrame()
    t = per_tag_trans.copy()
    t["edge"] = t["from_roi"].astype(str) + " -> " + t["to_roi"].astype(str)
    piv = t.pivot_table(index="edge", columns="shortid", values="count",
                        fill_value=0)
    tags = list(piv.columns)
    rows = []
    for a, b in itertools.combinations(tags, 2):
        va, vb = piv[a].to_numpy(float), piv[b].to_numpy(float)
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        cos = float(va @ vb / (na * nb)) if na > 0 and nb > 0 else np.nan
        ua, ub = va > 0, vb > 0
        jac = float((ua & ub).sum() / max((ua | ub).sum(), 1))
        rows.append({"tag_a": a, "tag_b": b, "edge_cosine": cos, "jaccard": jac})
    shared = piv.gt(0).sum(axis=1).rename("n_rats").to_frame()
    shared["total_count"] = piv.sum(axis=1).astype(int)
    shared = shared.reset_index().sort_values(["n_rats", "total_count"],
                                              ascending=False)
    return pd.DataFrame(rows), shared


def route_robustness(df: pd.DataFrame, base_mask: np.ndarray, extent, *,
                     moving_thr_inps: float, bin_in: float = 4.0,
                     corridor_pct: float = 80.0) -> pd.DataFrame:
    """
    Do apparent straight routes survive stricter QC? Recompute bout straightness
    and the corridor mask under progressively stricter filters and report the
    corridor-mask IoU vs the unfiltered ``base_mask``.
    """
    oob = df.get("outside_provisional_bounds")
    filters = {"all_valid": df}
    if "anchors_used" in df:
        filters["anchors>=6"] = df[df["anchors_used"].astype(float) >= 6]
    if "calculation_error" in df and df["calculation_error"].notna().any():
        thr = df["calculation_error"].median()
        filters["calc_err<=p50"] = df[df["calculation_error"] <= thr]
    if oob is not None:
        filters["in_bounds"] = df[~oob.fillna(False)]
    rows = []
    for name, sub in filters.items():
        if sub.empty:
            continue
        b = movement_bouts(sub, moving_thr_inps=moving_thr_inps)
        H, _, _ = occupancy_hist(sub, extent, bin_in=bin_in)
        m, _ = corridor_mask(H, pct=corridor_pct)
        rows.append({"filter": name, "n_fixes": int(len(sub)),
                     "n_bouts": int(len(b)),
                     "median_straightness": float(b["straightness"].median())
                     if len(b) else np.nan,
                     "corridor_iou_vs_base": _mask_iou(m, base_mask)})
    return pd.DataFrame(rows)


def baseline_route_compare(stationary_df: pd.DataFrame, free_bouts: pd.DataFrame, *,
                           moving_thr_inps: float, bin_in: float = 4.0,
                           corridor_pct: float = 80.0) -> pd.DataFrame:
    """
    Rule out WISER geometry artifacts: run occupancy + bout straightness on the
    **stationary** baseline. Stationary tags do not move, so corridor-like
    structure or high straightness there flags an anchor-geometry artifact.
    """
    ext = observed_extent(stationary_df)
    H, _, _ = occupancy_hist(stationary_df, ext, bin_in=bin_in)
    m, _ = corridor_mask(H, pct=corridor_pct)
    b = movement_bouts(stationary_df, moving_thr_inps=moving_thr_inps)
    free_med = float(free_bouts["straightness"].median()) if len(free_bouts) else np.nan
    base_med = float(b["straightness"].median()) if len(b) else np.nan
    flag = (np.isfinite(base_med) and np.isfinite(free_med) and base_med >= free_med)
    note = ("stationary straightness >= free -> GEOMETRY-ARTIFACT RISK"
            if flag else
            "stationary straightness < free -> routes not explained by geometry")
    rows = [
        {"metric": "median_straightness", "free": free_med, "stationary": base_med},
        {"metric": "n_bouts", "free": float(len(free_bouts)), "stationary": float(len(b))},
        {"metric": "corridor_cells", "free": np.nan, "stationary": float(int(m.sum()))},
    ]
    out = pd.DataFrame(rows)
    out["geometry_artifact_risk"] = flag
    out["note"] = note
    return out


def jitter_straightness_null(stationary_df: pd.DataFrame, free_bouts: pd.DataFrame,
                             *, n: int = 600, seed: int = 0) -> pd.DataFrame:
    """
    Displacement-matched straightness null from the stationary baseline. The
    n≈6 "moving" stationary bouts are too few and too spread to set an artifact
    threshold (a tag-relocation reads ~1.0, a jitter wobble ~0.1). Instead slide
    **random windows** (sample-length drawn from the free-bout lengths) over each
    stationary tag and compute straightness — a large-n jitter distribution to
    compare against the free bouts **at matched net displacement**.
    """
    d = add_speed(stationary_df)
    rng = np.random.default_rng(seed)
    if len(free_bouts):
        lens = (free_bouts["i_end"] - free_bouts["i_start"] + 1).to_numpy()
        lens = lens[lens >= 2]
    else:
        lens = np.array([15])
    groups = [g.reset_index(drop=True) for _, g in d.groupby("shortid")]
    rows = []
    for _ in range(n):
        g = groups[int(rng.integers(len(groups)))]
        L = int(rng.choice(lens))
        if len(g) <= L + 1:
            continue
        i = int(rng.integers(0, len(g) - L))
        seg = g.iloc[i:i + L + 1]
        path = float(np.nansum(seg["step_in_smooth"].to_numpy()[1:]))
        x, y = seg["x"].to_numpy(), seg["y"].to_numpy()
        disp = float(np.hypot(x[-1] - x[0], y[-1] - y[0]))
        if path > 0:
            rows.append({"straightness": float(np.clip(disp / path, 0, 1)),
                         "disp_in": disp, "path_in": path, "n_samples": int(L)})
    return pd.DataFrame(rows)


def straightness_vs_null_summary(free_bouts: pd.DataFrame, jitter_null: pd.DataFrame,
                                 disp_edges=(0, 24, 48, 96, 1e9)) -> pd.DataFrame:
    """Per net-displacement bin, compare free-bout straightness to the jitter null
    (median + the fraction of free bouts above the jitter p90 in that bin)."""
    rows = []
    fb, jn = free_bouts.copy(), jitter_null.copy()
    for lo, hi in zip(disp_edges[:-1], disp_edges[1:]):
        f = fb[(fb["disp_in"] >= lo) & (fb["disp_in"] < hi)]
        j = jn[(jn["disp_in"] >= lo) & (jn["disp_in"] < hi)]
        j90 = float(j["straightness"].quantile(0.9)) if len(j) else np.nan
        rows.append({
            "disp_bin_in": f"[{lo:g},{hi:g})" if hi < 1e8 else f">={lo:g}",
            "n_free": int(len(f)), "n_jitter": int(len(j)),
            "free_median_straight": float(f["straightness"].median()) if len(f) else np.nan,
            "jitter_median_straight": float(j["straightness"].median()) if len(j) else np.nan,
            "jitter_p90_straight": j90,
            "free_frac_above_jitter_p90": float((f["straightness"] > j90).mean())
            if len(f) and np.isfinite(j90) else np.nan,
        })
    return pd.DataFrame(rows)


def _edge_vec_cosine(e1: pd.DataFrame, e2: pd.DataFrame) -> float:
    """Cosine of two single-tag edge-count tables ([from_roi,to_roi,count])."""
    if e1.empty or e2.empty:
        return np.nan
    d1 = {(r.from_roi, r.to_roi): r.count for r in e1.itertuples()}
    d2 = {(r.from_roi, r.to_roi): r.count for r in e2.itertuples()}
    keys = sorted(set(d1) | set(d2))
    v1 = np.array([d1.get(k, 0) for k in keys], float)
    v2 = np.array([d2.get(k, 0) for k in keys], float)
    if v1.any() and v2.any():
        return float(v1 @ v2 / (np.linalg.norm(v1) * np.linalg.norm(v2)))
    return np.nan


def self_route_reuse(win: pd.DataFrame, roi_cfg: dict, extent, *,
                     bin_in: float = 4.0, min_fixes: int = 50) -> pd.DataFrame:
    """
    Within-individual route reuse across the two nights (a memory proxy): per rat,
    occupancy cosine and **own** edge-usage cosine between night 1 and night 2.
    High self-similarity — especially above the cross-rat level — suggests the rat
    re-treads its own routes rather than just sharing the environment. Sova (night
    1 only) returns NaN.
    """
    nights = sorted(win["night"].unique())
    if len(nights) < 2:
        return pd.DataFrame()
    n1, n2 = nights[0], nights[1]
    rows = []
    for tag, g in win.groupby("shortid"):
        g1, g2 = g[g["night"] == n1], g[g["night"] == n2]
        if len(g1) < min_fixes or len(g2) < min_fixes:
            rows.append({"shortid": tag, "occ_self_cosine": np.nan,
                         "edge_self_cosine": np.nan, "n_night1": int(len(g1)),
                         "n_night2": int(len(g2))})
            continue
        H1 = _box_blur(occupancy_hist(g1, extent, bin_in=bin_in)[0]).ravel()
        H2 = _box_blur(occupancy_hist(g2, extent, bin_in=bin_in)[0]).ravel()
        occ = (float(H1 @ H2 / (np.linalg.norm(H1) * np.linalg.norm(H2)))
               if H1.any() and H2.any() else np.nan)
        edge = _edge_vec_cosine(per_tag_transitions(g1, roi_cfg),
                                per_tag_transitions(g2, roi_cfg))
        rows.append({"shortid": tag, "occ_self_cosine": occ,
                     "edge_self_cosine": edge, "n_night1": int(len(g1)),
                     "n_night2": int(len(g2))})
    return pd.DataFrame(rows)


def load_exclude_regions(path: Path | str) -> list:
    """Load user-drawn exclude/edge polygons from ``wiser_exclude.json`` (written
    by ``scripts/place_exclude_region.py``). Returns a list of (N,2) inch arrays;
    empty list if absent. Points inside any polygon count as wall/edge."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [np.asarray(p, dtype=float) for p in d.get("polygons", [])
            if len(p) >= 3]


def points_in_polygons(x, y, regions) -> np.ndarray:
    """Boolean: each (x, y) is inside ANY of the ``regions`` polygons."""
    from matplotlib.path import Path as _MplPath
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    inside = np.zeros(x.shape, dtype=bool)
    if not regions:
        return inside
    pts = np.column_stack([x, y])
    for poly in regions:
        if len(poly) >= 3:
            inside |= _MplPath(np.asarray(poly, float)).contains_points(pts)
    return inside


def region_cell_mask(extent, shape, bin_in, regions) -> np.ndarray:
    """Grid mask of cells whose centre falls inside any exclude polygon
    (aligned to :func:`occupancy_hist`)."""
    xmin, _, ymin, _ = extent
    xc = xmin + (np.arange(shape[0]) + 0.5) * bin_in
    yc = ymin + (np.arange(shape[1]) + 0.5) * bin_in
    X, Y = np.meshgrid(xc, yc, indexing="ij")
    inside = points_in_polygons(X.ravel(), Y.ravel(), regions)
    return inside.reshape(shape)


def add_edge_distance(win: pd.DataFrame, boundary_rect) -> pd.DataFrame:
    """Add ``dist_edge_in`` (distance to the nearest boundary edge) to the frame."""
    win = win.copy()
    win["dist_edge_in"] = distance_to_edge(win, boundary_rect).to_numpy()
    return win


def edge_mask_points(win: pd.DataFrame, *, regions=None, boundary_rect=None,
                     edge_band_in: float = 12.0) -> np.ndarray:
    """Per-fix boolean 'in the edge / exclude zone'. Uses user-drawn ``regions``
    (polygons) if given; otherwise a band within ``edge_band_in`` of the
    rectangular ``boundary_rect``."""
    if regions:
        return points_in_polygons(win["x"].to_numpy(), win["y"].to_numpy(), regions)
    if boundary_rect is not None:
        return distance_to_edge(win, boundary_rect).to_numpy() < edge_band_in
    return np.zeros(len(win), dtype=bool)


def thigmotaxis_index(win: pd.DataFrame, *, regions=None, boundary_rect=None,
                      edge_band_in: float = 12.0) -> pd.DataFrame:
    """Per-rat thigmotaxis: fraction of fixes in the edge / exclude zone (user
    polygons if ``regions`` given, else the boundary band)."""
    edge = edge_mask_points(win, regions=regions, boundary_rect=boundary_rect,
                            edge_band_in=edge_band_in)
    w2 = win.assign(_edge=edge)
    rows = []
    for tag, g in w2.groupby("shortid"):
        rows.append({"shortid": tag,
                     "thigmotaxis_index": float(g["_edge"].mean()),
                     "median_dist_edge_in": (float(g["dist_edge_in"].median())
                                             if "dist_edge_in" in g else np.nan)})
    return pd.DataFrame(rows)


def edge_band_cell_mask(extent, shape, boundary_rect, bin_in, edge_band_in):
    """Boolean grid mask of cells whose centre is within ``edge_band_in`` of the
    boundary (the wall-running band), aligned to :func:`occupancy_hist`."""
    xmin, _, ymin, _ = extent
    bx0, bx1, by0, by1 = boundary_rect
    xc = xmin + (np.arange(shape[0]) + 0.5) * bin_in
    yc = ymin + (np.arange(shape[1]) + 0.5) * bin_in
    X, Y = np.meshgrid(xc, yc, indexing="ij")
    d = np.minimum.reduce([X - bx0, bx1 - X, Y - by0, by1 - Y])
    return d < edge_band_in


def interior_route_summary(win: pd.DataFrame, full_mask: np.ndarray, extent,
                           boundary_rect, *, edge_band_in: float = 12.0,
                           bin_in: float = 4.0, corridor_pct: float = 80.0,
                           regions=None) -> dict:
    """
    Edge-effect control: recompute the corridor on **interior** fixes only (those
    outside the edge / exclude zone — user ``regions`` if given, else the boundary
    band) and report how much of the full corridor is perimeter (edge-cell
    fraction) and whether interior route structure survives (IoU vs full corridor).
    """
    edge_pts = edge_mask_points(win, regions=regions, boundary_rect=boundary_rect,
                                edge_band_in=edge_band_in)
    interior = win[~edge_pts]
    if regions:
        edge_cells = region_cell_mask(extent, full_mask.shape, bin_in, regions)
    else:
        edge_cells = edge_band_cell_mask(extent, full_mask.shape, boundary_rect,
                                         bin_in, edge_band_in)
    full_edge_frac = (float((full_mask & edge_cells).sum() / full_mask.sum())
                      if full_mask.any() else np.nan)
    if interior.empty:
        return {"interior_fixes": 0, "full_corridor_edge_fraction": full_edge_frac,
                "_edge_cells": edge_cells}
    Hi, _, _ = occupancy_hist(interior, extent, bin_in=bin_in)
    mask_int, _ = corridor_mask(Hi, pct=corridor_pct)
    return {
        "interior_fixes": int(len(interior)),
        "interior_frac_of_window": float(len(interior) / len(win)),
        "full_corridor_edge_fraction": full_edge_frac,
        "interior_corridor_cells": int(mask_int.sum()),
        "interior_vs_full_corridor_iou": _mask_iou(mask_int, full_mask),
        "_interior_mask": mask_int, "_edge_cells": edge_cells,
    }


# ---------------------------------------------------------------------------
# Plotting (inch-correct; reuses plotting._tag_colors / _save_or_show)
# ---------------------------------------------------------------------------

import matplotlib.pyplot as plt   # noqa: E402  (plotting sets Agg backend)


def plot_gap_histogram(df, save_path=None):
    """Per-tag distribution of inter-sample dt (log-y), to see dropouts."""
    tags = sorted(df["shortid"].unique())
    colors = plotting._tag_colors(tags)
    fig, ax = plt.subplots(figsize=(10, 5))
    for tag in tags:
        dt = df[df["shortid"] == tag]["dt_s"].dropna()
        dt = dt[(dt > 0) & (dt < dt.quantile(0.999))]
        ax.hist(dt, bins=120, histtype="step", color=colors[tag], label=str(tag))
    ax.set_yscale("log")
    ax.set_xlabel("inter-sample dt (s)")
    ax.set_ylabel("count (log)")
    ax.set_title("Inter-sample interval per tag (gaps = long-dt tail)")
    ax.legend(fontsize=7)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_trajectories(df, save_path=None, valid_only=False, title_suffix=""):
    """Small-multiple x/y trajectories per tag (inches)."""
    tags = sorted(df["shortid"].unique())
    colors = plotting._tag_colors(tags)
    ncols = min(3, len(tags))
    nrows = int(np.ceil(len(tags) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 4 * nrows),
                             squeeze=False)
    flat = axes.ravel()
    for i, tag in enumerate(tags):
        ax = flat[i]
        sub = df[df["shortid"] == tag]
        if valid_only and "valid" in sub.columns:
            sub = sub[sub["valid"]]
        xv = sub["x"].to_numpy(dtype=float).copy()
        yv = sub["y"].to_numpy(dtype=float).copy()
        # Break the drawn line across dropouts: a pause between two plotted fixes
        # would otherwise render as a straight streak across the paddock (the same
        # artifact as the impossible speeds). Cut where the gap to the previous
        # plotted point is well above this tag's median sampling interval.
        if "datetime" in sub.columns and len(sub) > 2:
            ts = sub["datetime"].to_numpy()
            gaps = np.empty(len(ts), dtype=float)
            gaps[0] = 0.0
            gaps[1:] = (ts[1:] - ts[:-1]) / np.timedelta64(1, "s")
            pos = gaps[gaps > 0]
            if pos.size:
                brk = gaps > max(5.0 * float(np.median(pos)), 1.0)
                xv[brk] = np.nan
                yv[brk] = np.nan
        ax.plot(xv, yv, linewidth=0.4, alpha=0.6, color=colors[tag])
        ax.set_title(f"Tag {tag} (n={len(sub):,})", fontsize=9)
        ax.set_xlabel("X (in)", fontsize=8)
        ax.set_ylabel("Y (in)", fontsize=8)
        ax.set_aspect("equal", "datalim")
        ax.grid(True, linestyle="--", alpha=0.3)
    for i in range(len(tags), nrows * ncols):
        flat[i].set_visible(False)
    fig.suptitle(f"Trajectories {title_suffix}".strip(), fontsize=12)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_speed_timeseries(df, save_path=None,
                          active_speed_inps=DEFAULT_ACTIVE_SPEED_INPS,
                          bin_s=60.0):
    """
    Per-tag smoothed speed over elapsed time (in/s) with the active-threshold line.

    Plotting every fix (hundreds of thousands per tag) overplots into a solid band
    of noise; a per-``bin_s``-second **median** of the smoothed speed shows the
    actual activity bouts while staying faithful to the data.
    """
    tags = sorted(df["shortid"].unique())
    colors = plotting._tag_colors(tags)
    fig, ax = plt.subplots(figsize=(12, 5))
    for tag in tags:
        sub = df[df["shortid"] == tag].dropna(subset=["speed_inps_smooth",
                                                       "elapsed_s"])
        if sub.empty:
            continue
        hbin = (np.floor(sub["elapsed_s"] / bin_s) * bin_s) / 3600.0   # hours
        binned = sub.assign(_h=hbin).groupby("_h")["speed_inps_smooth"].median()
        ax.plot(binned.index, binned.values, linewidth=0.9, alpha=0.85,
                color=colors[tag], label=str(tag))
    ax.axhline(active_speed_inps, color="black", linestyle="--", linewidth=1,
               label=f"active thr {active_speed_inps:g} in/s")
    ax.set_xlabel("elapsed (h)")
    ax.set_ylabel("median smoothed speed (in/s)")
    ax.set_title(f"Smoothed speed over time per tag ({bin_s:g}-s median)")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_jitter_clouds_inches(df, ground_truth=None, save_path=None):
    """Per-tag static jitter cloud centred on median (inch labels)."""
    tags = sorted(df["shortid"].unique())
    colors = plotting._tag_colors(tags)
    ncols = min(3, len(tags))
    nrows = int(np.ceil(len(tags) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 4 * nrows),
                             squeeze=False)
    flat = axes.ravel()
    gt = ground_truth.set_index("shortid") if ground_truth is not None else None
    for i, tag in enumerate(tags):
        ax = flat[i]
        sub = df[df["shortid"] == tag]
        mx, my = sub["x"].median(), sub["y"].median()
        ax.scatter(sub["x"] - mx, sub["y"] - my, s=4, alpha=0.4, color=colors[tag])
        ax.axhline(0, color="grey", lw=0.8, ls="--")
        ax.axvline(0, color="grey", lw=0.8, ls="--")
        if gt is not None and tag in gt.index:
            ax.scatter(gt.loc[tag, "true_x"] - mx, gt.loc[tag, "true_y"] - my,
                       marker="*", s=150, color="black", zorder=5)
        ax.set_title(f"Tag {tag} (n={len(sub):,})", fontsize=9)
        ax.set_xlabel("ΔX (in)", fontsize=8)
        ax.set_ylabel("ΔY (in)", fontsize=8)
        ax.set_aspect("equal", "datalim")
        ax.grid(True, linestyle="--", alpha=0.3)
    for i in range(len(tags), nrows * ncols):
        flat[i].set_visible(False)
    fig.suptitle("Static jitter clouds (centred on median, inches)", fontsize=12)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_pairwise_distance_heatmap(dist_long, save_path=None):
    """Mean pairwise distance matrix (inches) across all tag pairs."""
    if dist_long.empty:
        return
    tags = sorted(set(dist_long["tag_a"]) | set(dist_long["tag_b"]))
    M = pd.DataFrame(np.nan, index=tags, columns=tags)
    for (a, b), g in dist_long.groupby(["tag_a", "tag_b"]):
        M.loc[a, b] = M.loc[b, a] = g["dist_in"].mean()
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(M.to_numpy(dtype=float), cmap="viridis")
    ax.set_xticks(range(len(tags)), tags, rotation=45, fontsize=7)
    ax.set_yticks(range(len(tags)), tags, fontsize=7)
    fig.colorbar(im, ax=ax, label="mean pairwise distance (in)")
    ax.set_title("Mean inter-tag distance")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_clustering_index(ci, save_path=None):
    """Group clustering index (mean pairwise distance) over time."""
    if ci.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(ci["tbin"] / 60, ci["mean_pair_dist_in"], linewidth=0.8)
    ax.set_xlabel("elapsed (min, 1-s bins)")
    ax.set_ylabel("mean pairwise distance (in)")
    ax.set_title("Group dispersion over time (lower = more clustered)")
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def hourly_clock_summary(by_clock_per_tag: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse the per-tag × clock-hour table to **between-rat mean ± SD** per clock
    hour (active fraction + active distance in metres). Used by the plot and
    written out as a provenance CSV.
    """
    g = by_clock_per_tag.copy()
    g["active_distance_m"] = g["active_distance_in"] * IN_TO_CM / 100.0
    out = (g.groupby("clock_hour")
           .agg(active_frac_mean=("active_frac", "mean"),
                active_frac_sd=("active_frac", "std"),
                active_distance_m_mean=("active_distance_m", "mean"),
                active_distance_m_sd=("active_distance_m", "std"),
                n_tags=("shortid", "nunique"))
           .reset_index())
    return out


def plot_hourly_activity_by_clock(by_clock_per_tag, weather=None, save_path=None):
    """
    Activity by local clock-hour, as **between-rat mean ± SD** across the tags
    (hourly / diel **exploratory** — NOT circadian: the pilot is < 24 h, so each
    clock hour occurs once and the error bars are between-rat spread, not
    across-day). Two stacked panels share the x-axis:

    - top: active fraction (mean ± SD), with temperature + solar overlays;
    - bottom: active distance per hour in metres (path length over above-noise
      movement only; mean ± SD).

    ``by_clock_per_tag`` is the ``hourly_activity()['by_clock_per_tag']`` table.
    """
    s = hourly_clock_summary(by_clock_per_tag)
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)

    ax.bar(s["clock_hour"], s["active_frac_mean"],
           yerr=s["active_frac_sd"].fillna(0.0), capsize=3,
           color="steelblue", alpha=0.7, ecolor="black",
           error_kw={"linewidth": 1}, label="active fraction (mean ± SD, n tags)")
    ax.set_ylabel("active fraction")
    ax.set_title("Hourly activity by clock-hour (EXPLORATORY, <24 h — not circadian; "
                 "error bars = between-rat SD)")

    if weather is not None and not weather.empty:
        wx = weather.copy()
        wx["clock_hour"] = wx["datetime_local"].dt.hour
        agg = wx.groupby("clock_hour").mean(numeric_only=True)
        ax2 = ax.twinx()
        if "temp_c" in agg:
            ax2.plot(agg.index, agg["temp_c"], color="firebrick", marker="o",
                     label="temp (°C)")
            ax2.set_ylabel("temp (°C)")
        if "solar_wm2" in agg and agg["solar_wm2"].max() > 0:
            ax3 = ax.twinx()
            ax3.spines["right"].set_position(("axes", 1.08))
            ax3.plot(agg.index, agg["solar_wm2"], color="goldenrod",
                     linestyle="--", label="solar (W/m²)")
            ax3.set_ylabel("solar (W/m²)")
        ax2.legend(loc="upper right", fontsize=8)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.3)

    axd.bar(s["clock_hour"], s["active_distance_m_mean"],
            yerr=s["active_distance_m_sd"].fillna(0.0), capsize=3,
            color="seagreen", alpha=0.7, ecolor="black",
            error_kw={"linewidth": 1})
    axd.set_xlabel("local clock hour (EDT)")
    axd.set_ylabel("active distance (m/h, > noise floor)")
    axd.set_xticks(range(0, 24))
    axd.grid(True, linestyle="--", alpha=0.3)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_activity_vs_temperature(merged, save_path=None):
    """EXPLORATORY scatter of hourly group activity vs temperature + Spearman."""
    if merged.empty or "temp_c" not in merged.columns:
        return None
    m = merged.dropna(subset=["temp_c", "active_frac"])
    if len(m) < 3:
        return None
    # Spearman rho = Pearson on ranks (scipy-free); p-value via scipy if present.
    rho = float(m["temp_c"].rank().corr(m["active_frac"].rank()))
    p = None
    try:                                       # optional, not required
        from scipy.stats import spearmanr
        _, p = spearmanr(m["temp_c"], m["active_frac"])
        p = float(p)
    except Exception:
        p = None
    p_txt = f"{p:.3f}" if p is not None else "n/a"
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(m["temp_c"], m["active_frac"], s=40, alpha=0.8)
    ax.set_xlabel("temperature (°C)")
    ax.set_ylabel("group active fraction (hourly)")
    ax.set_title(f"EXPLORATORY: activity vs temperature\n"
                 f"Spearman ρ={rho:.2f}, p={p_txt}, n={len(m)} h "
                 f"(unverified ~5-min alignment)")
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)
    return {"spearman_rho": rho, "p_value": p, "n_hours": int(len(m))}


def plot_weather_timeseries(weather, save_path=None):
    """Temperature / solar / humidity / wind over time (local)."""
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    t = weather["datetime_local"]
    if "temp_c" in weather:
        axes[0].plot(t, weather["temp_c"], color="firebrick")
    axes[0].set_ylabel("temp (°C)")
    if "solar_wm2" in weather:
        axes[1].plot(t, weather["solar_wm2"], color="goldenrod")
    axes[1].set_ylabel("solar (W/m²)")
    if "humidity" in weather:
        axes[2].plot(t, weather["humidity"], color="steelblue", label="humidity %")
    if "wind_mph" in weather:
        axes[2].plot(t, weather["wind_mph"], color="seagreen", label="wind mph")
    axes[2].set_ylabel("humidity / wind")
    axes[2].set_xlabel("local time (EDT)")
    axes[2].legend(fontsize=8)
    for ax in axes:
        ax.grid(True, linestyle="--", alpha=0.4)
    fig.suptitle("Weather over the recording window", fontsize=12)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_roi_transition_graph(roi_cfg, transitions, time_df, save_path=None):
    """Simple node-link graph of ROI↔ROI transitions over the ROI layout."""
    fig, ax = plt.subplots(figsize=(9, 6))
    # Node positions: ROI centers; pseudo-nodes for edge/open placed sensibly.
    pos = {r["name"]: (r["x"], r["y"]) for r in (roi_cfg or {}).get("rois", [])}
    ext = (roi_cfg or {}).get("boundary", {}).get("rect")
    if ext:
        xmin, xmax, ymin, ymax = ext
        pos.setdefault("edge", (xmin + (xmax - xmin) * 0.05, ymin + (ymax - ymin) * 0.5))
        pos.setdefault("open", ((xmin + xmax) / 2, (ymin + ymax) / 2))
    # Total dwell per ROI for node size.
    dwell = (time_df.groupby("roi")["seconds"].sum().to_dict()
             if time_df is not None and not time_df.empty else {})
    if not transitions.empty:
        mx = transitions["count"].max()
        for _, r in transitions.iterrows():
            if r["from_roi"] in pos and r["to_roi"] in pos:
                x0, y0 = pos[r["from_roi"]]
                x1, y1 = pos[r["to_roi"]]
                ax.plot([x0, x1], [y0, y1], color="grey",
                        linewidth=0.5 + 3 * r["count"] / mx, alpha=0.5)
    placed: list[tuple[float, float]] = []
    for name, (x, y) in pos.items():
        s = 120 + (dwell.get(name, 0) ** 0.5)
        ax.scatter([x], [y], s=s, zorder=5)
        # Co-located ROIs (e.g. food inside a house) share a point; drop the second
        # label below the node so the two don't overprint.
        below = any(abs(x - px) < 25 and abs(y - py) < 25 for px, py in placed)
        dy, va = (-14, "top") if below else (10, "bottom")
        ax.annotate(name, (x, y), xytext=(0, dy), textcoords="offset points",
                    fontsize=8, ha="center", va=va, zorder=6,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              ec="none", alpha=0.7))
        placed.append((x, y))
    ax.set_xlabel("X (in)")
    ax.set_ylabel("Y (in)")
    ax.set_aspect("equal", "datalim")
    ax.set_title("ROI transition graph (edge width ∝ transition count)")
    ax.grid(True, linestyle="--", alpha=0.3)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


# --- Leader-follower / route-following plots -------------------------------

def _directed_matrix(df, value, fill=np.nan):
    """Square leader(row) x follower(col) matrix from a directed-pair table."""
    tags = sorted(set(df["leader"]) | set(df["follower"]))
    M = pd.DataFrame(fill, index=tags, columns=tags, dtype=float)
    for r in df.itertuples():
        M.loc[r.leader, r.follower] = getattr(r, value)
    return M, tags


def _annotate_cells(ax, M, fmt, tags):
    for i in range(len(tags)):
        for j in range(len(tags)):
            v = M.iloc[i, j]
            if pd.notna(v):
                ax.text(j, i, format(v, fmt), ha="center", va="center",
                        fontsize=6, color="0.9")


def plot_following_heatmap(peaks, save_path=None):
    """Peak follow-score heatmap, **leader (row) -> follower (col)** (directed)."""
    M, tags = _directed_matrix(peaks, "peak_score")
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(M.to_numpy(dtype=float), cmap="magma", vmin=0)
    ax.set_xticks(range(len(tags)), tags, rotation=45, fontsize=7)
    ax.set_yticks(range(len(tags)), tags, fontsize=7)
    ax.set_xlabel("follower (B)")
    ax.set_ylabel("leader (A)")
    _annotate_cells(ax, M, ".2f", tags)
    fig.colorbar(im, ax=ax, label="peak follow score")
    ax.set_title("Candidate route-following: peak follow score (A→B)")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_following_best_lag_heatmap(peaks, save_path=None):
    """Best-lag (s) heatmap for the peak follow score, leader -> follower."""
    M, tags = _directed_matrix(peaks, "best_lag_s")
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(M.to_numpy(dtype=float), cmap="viridis")
    ax.set_xticks(range(len(tags)), tags, rotation=45, fontsize=7)
    ax.set_yticks(range(len(tags)), tags, fontsize=7)
    ax.set_xlabel("follower (B)")
    ax.set_ylabel("leader (A)")
    _annotate_cells(ax, M, ".0f", tags)
    fig.colorbar(im, ax=ax, label="best lag (s)")
    ax.set_title("Lag of peak follow score (A→B)")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_following_asymmetry_heatmap(asym, save_path=None):
    """Directional asymmetry heatmap (red: row leads col more than the reverse)."""
    M, tags = _directed_matrix(asym, "asymmetry", fill=np.nan)  # diagonal stays NaN
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(M.to_numpy(dtype=float), cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(tags)), tags, rotation=45, fontsize=7)
    ax.set_yticks(range(len(tags)), tags, fontsize=7)
    ax.set_xlabel("follower (B)")
    ax.set_ylabel("leader (A)")
    _annotate_cells(ax, M, "+.2f", tags)
    fig.colorbar(im, ax=ax, label="asymmetry (A→B vs B→A)")
    ax.set_title("Directional asymmetry (red: A leads B)")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_following_lag_curves(scores, peaks, null_df=None, top_pairs=4,
                              save_path=None):
    """Follow score vs lag for the strongest ordered pairs; dotted line = that
    pair's shuffled 95th-percentile null when ``null_df`` is supplied."""
    top = peaks.dropna(subset=["peak_score"]).sort_values(
        "peak_score", ascending=False).head(top_pairs)
    fig, ax = plt.subplots(figsize=(10, 5))
    for r in top.itertuples():
        g = scores[(scores["leader"] == r.leader) &
                   (scores["follower"] == r.follower)].sort_values("lag")
        line, = ax.plot(g["lag"], g["score"], marker="o", ms=3,
                        label=f"{r.leader}→{r.follower}")
        if null_df is not None:
            nd = null_df[(null_df["leader"] == r.leader) &
                         (null_df["follower"] == r.follower)]
            if len(nd):
                ax.axhline(float(nd["shuffled_p95"].iloc[0]),
                           color=line.get_color(), ls=":", lw=0.9, alpha=0.7)
    ax.set_xlabel("lag (s)")
    ax.set_ylabel("follow score")
    ax.set_title("Lag curves, strongest ordered pairs (dotted = shuffled p95)")
    ax.legend(fontsize=7)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_following_raster(events, save_path=None):
    """Raster of detected following bouts over elapsed time, one row per pair."""
    if events is None or events.empty:
        return
    pairs = sorted(set(zip(events["leader"], events["follower"])))
    fig, ax = plt.subplots(figsize=(12, 0.4 * len(pairs) + 2))
    for k, (a, b) in enumerate(pairs):
        g = events[(events["leader"] == a) & (events["follower"] == b)]
        for r in g.itertuples():
            ax.plot([r.t_start_s / 3600, r.t_end_s / 3600], [k, k], lw=5,
                    solid_capstyle="butt", color="tab:purple")
    ax.set_yticks(range(len(pairs)), [f"{a}→{b}" for a, b in pairs], fontsize=7)
    ax.set_ylim(-1, len(pairs))
    ax.set_xlabel("elapsed (h)")
    ax.set_title("Detected following bouts (candidate route-following)")
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_following_snippets(grid, events, top_n=6, pad_s=5, t0_datetime=None,
                            save_path=None):
    """Trajectory snippets for the longest following bouts: leader path (blue) and
    the follower path lag-aligned (red), with a start-direction arrow and the bout
    start timestamp."""
    if events is None or events.empty:
        return
    ev = events.sort_values("duration_s", ascending=False).head(top_n)
    tags = grid["tags"]
    idx = {t: i for i, t in enumerate(tags)}
    X, Y, els, bin_s = grid["X"], grid["Y"], grid["elapsed_s"], grid["bin_s"]
    T = X.shape[0]
    pad = max(0, int(round(pad_s / bin_s)))
    ncols = min(3, len(ev))
    nrows = int(np.ceil(len(ev) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 4 * nrows),
                             squeeze=False)
    flat = axes.ravel()
    for k, r in enumerate(ev.itertuples()):
        ax = flat[k]
        ia, ib, L = idx[r.leader], idx[r.follower], int(r.lag_s)
        i0, i1 = max(0, r.i_start - pad), min(T - 1, r.i_end + pad)
        j0, j1 = min(T - 1, i0 + L), min(T - 1, i1 + L)
        ax.plot(X[i0:i1 + 1, ia], Y[i0:i1 + 1, ia], "-o", ms=2, lw=1,
                color="tab:blue", label=f"leader {r.leader}")
        ax.plot(X[j0:j1 + 1, ib], Y[j0:j1 + 1, ib], "-s", ms=2, lw=1,
                color="tab:red", label=f"follower {r.follower} (+{L}s)")
        # start-direction arrow on the leader path
        if r.i_start + 1 <= T - 1:
            ax.annotate("", xytext=(X[r.i_start, ia], Y[r.i_start, ia]),
                        xy=(X[r.i_start + 1, ia], Y[r.i_start + 1, ia]),
                        arrowprops=dict(arrowstyle="->", color="tab:blue"))
        if t0_datetime is not None:
            ts = (t0_datetime + pd.Timedelta(seconds=float(r.t_start_s))
                  ).strftime("%H:%M:%S")
        else:
            ts = f"t+{r.t_start_s:.0f}s"
        ax.set_title(f"{r.leader}→{r.follower}  lag {L}s  {r.duration_s:.0f}s\n{ts}",
                     fontsize=8)
        ax.set_xlabel("X (in)", fontsize=8)
        ax.set_ylabel("Y (in)", fontsize=8)
        ax.set_aspect("equal", "datalim")
        ax.legend(fontsize=6)
        ax.grid(True, linestyle="--", alpha=0.3)
    for k in range(len(ev), nrows * ncols):
        flat[k].set_visible(False)
    fig.suptitle("Top following bouts — trajectory snippets (candidate)", fontsize=12)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


# --- Route-structure plots -------------------------------------------------

def _occ_cell_centers(extent, shape, bin_in):
    xmin, _, ymin, _ = extent
    xc = xmin + (np.arange(shape[0]) + 0.5) * bin_in
    yc = ymin + (np.arange(shape[1]) + 0.5) * bin_in
    return xc, yc


def _name_of(tag):
    info = plotting.load_rat_identities().get(str(tag))
    return (info or {}).get("name", f"Tag {tag}")


def plot_corridor_map(H, extent, mask, skeleton, bin_in=4.0, save_path=None):
    """Group occupancy (log) + corridor-mask contour (cyan) + skeleton (white)."""
    from matplotlib.colors import LogNorm
    xmin, xmax, ymin, ymax = extent
    Hs = _box_blur(H, passes=2)
    fig, ax = plt.subplots(figsize=(8, 6))
    masked = np.ma.masked_where(Hs <= 0, Hs)
    ax.imshow(masked.T, origin="lower", extent=(xmin, xmax, ymin, ymax),
              aspect="equal", cmap="magma",
              norm=LogNorm(vmin=1, vmax=max(Hs.max(), 1)))
    xc, yc = _occ_cell_centers(extent, mask.shape, bin_in)
    if mask.any():
        ax.contour(xc, yc, mask.T.astype(float), levels=[0.5], colors="cyan",
                   linewidths=1.0)
    sy, sx = np.where(skeleton.T)
    ax.scatter(xc[sx], yc[sy], s=4, color="white", marker="s",
               label="route skeleton")
    ax.set_xlabel("X (in)")
    ax.set_ylabel("Y (in)")
    ax.set_title("Group occupancy + corridor mask (cyan) + skeleton "
                 "(CANDIDATE routes)")
    ax.legend(fontsize=7, loc="upper right")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_per_rat_occupancy(per_tag_hist, extent, mask, bin_in=4.0, save_path=None):
    """Per-rat occupancy small-multiples with the shared group corridor overlaid."""
    from matplotlib.colors import LogNorm
    tags = sorted(per_tag_hist)
    xmin, xmax, ymin, ymax = extent
    xc, yc = _occ_cell_centers(extent, mask.shape, bin_in)
    ncols = min(3, len(tags))
    nrows = int(np.ceil(len(tags) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 4 * nrows),
                             squeeze=False)
    flat = axes.ravel()
    for i, t in enumerate(tags):
        ax = flat[i]
        Hs = _box_blur(per_tag_hist[t], passes=1)
        masked = np.ma.masked_where(Hs <= 0, Hs)
        ax.imshow(masked.T, origin="lower", extent=(xmin, xmax, ymin, ymax),
                  aspect="equal", cmap="magma",
                  norm=LogNorm(vmin=1, vmax=max(Hs.max(), 1)))
        if mask.any():
            ax.contour(xc, yc, mask.T.astype(float), levels=[0.5], colors="cyan",
                       linewidths=0.7)
        ax.set_title(f"{_name_of(t)}  ({t})", fontsize=9)
        ax.set_xlabel("X (in)", fontsize=8)
        ax.set_ylabel("Y (in)", fontsize=8)
    for i in range(len(tags), nrows * ncols):
        flat[i].set_visible(False)
    fig.suptitle("Per-rat occupancy with group corridor (cyan)", fontsize=12)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_route_reuse(rr, save_path=None):
    """Per-rat self-concentration + corridor-adherence bars."""
    rr = rr.sort_values("shortid")
    labels = [_name_of(t) for t in rr["shortid"]]
    x = np.arange(len(rr))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - 0.2, rr["self_concentration"], 0.4, label="self concentration",
           color="steelblue")
    ax.bar(x + 0.2, rr["corridor_adherence"], 0.4, label="corridor adherence",
           color="seagreen")
    ax.set_xticks(x, labels, rotation=30, fontsize=8)
    ax.set_ylabel("index")
    ax.set_ylim(0, 1)
    ax.set_title("Per-rat route reuse (high = repeated paths / on shared corridor)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_occupancy_similarity(sim, save_path=None):
    """Leave-one-out occupancy similarity (cosine vs the other rats)."""
    sim = sim.sort_values("shortid")
    labels = [_name_of(t) for t in sim["shortid"]]
    x = np.arange(len(sim))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x, sim["loo_cosine"], color="slateblue")
    ax.set_xticks(x, labels, rotation=30, fontsize=8)
    ax.set_ylabel("cosine vs other rats")
    ax.set_ylim(0, 1)
    ax.set_title("Leave-one-out occupancy similarity to the group (excl. self)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_straightness(bouts, baseline_bouts=None, save_path=None):
    """Per-rat bout-straightness boxplots; red line = stationary-jitter baseline."""
    if bouts.empty:
        return
    tags = sorted(bouts["shortid"].unique())
    data = [bouts[bouts["shortid"] == t]["straightness"].dropna().to_numpy()
            for t in tags]
    labels = [f"{_name_of(t)}\n(n={len(d)})" for t, d in zip(tags, data)]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.boxplot(data, tick_labels=labels, showfliers=False)
    if baseline_bouts is not None and len(baseline_bouts):
        bm = float(baseline_bouts["straightness"].median())
        ax.axhline(bm, color="red", linestyle="--", linewidth=1,
                   label=f"stationary baseline median {bm:.2f}")
        ax.legend(fontsize=8)
    ax.set_ylabel("straightness (disp / path)")
    ax.set_ylim(0, 1.02)
    ax.set_title("Movement-bout straightness per rat "
                 "(red = stationary-jitter baseline → artifact check)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_shared_edge_graph(roi_cfg, edges, save_path=None):
    """Route edges over the ROI layout: width = total use, colour = # rats sharing."""
    pos = {r["name"]: (r["x"], r["y"]) for r in (roi_cfg or {}).get("rois", [])}
    if edges is None or edges.empty or not pos:
        return
    fig, ax = plt.subplots(figsize=(9, 6))
    mx = max(edges["total_count"].max(), 1)
    nmax = max(int(edges["n_rats"].max()), 1)
    cmap = plt.get_cmap("viridis")
    for r in edges.itertuples():
        if r.from_roi in pos and r.to_roi in pos:
            x0, y0 = pos[r.from_roi]
            x1, y1 = pos[r.to_roi]
            ax.plot([x0, x1], [y0, y1], color=cmap(r.n_rats / nmax),
                    linewidth=0.5 + 4 * r.total_count / mx, alpha=0.75, zorder=2)
    placed = []
    for name, (x, y) in pos.items():
        ax.scatter([x], [y], s=160, color="lightgray", edgecolor="k", zorder=5)
        below = any(abs(x - px) < 25 and abs(y - py) < 25 for px, py in placed)
        dy, va = (-14, "top") if below else (10, "bottom")
        ax.annotate(name, (x, y), xytext=(0, dy), textcoords="offset points",
                    fontsize=8, ha="center", va=va, zorder=6,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none",
                              alpha=0.7))
        placed.append((x, y))
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(1, nmax))
    sm.set_array([])
    fig.colorbar(sm, ax=ax, label="# rats using edge")
    ax.set_xlabel("X (in)")
    ax.set_ylabel("Y (in)")
    ax.set_aspect("equal", "datalim")
    ax.set_title("Shared route edges (width = total use, colour = # rats)")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_edge_usage_heatmap(sim, save_path=None):
    """Cross-rat edge-usage cosine matrix (do different rats use the same edges?)."""
    if sim is None or sim.empty:
        return
    tags = sorted(set(sim["tag_a"]) | set(sim["tag_b"]))
    M = pd.DataFrame(np.nan, index=tags, columns=tags, dtype=float)
    for r in sim.itertuples():
        M.loc[r.tag_a, r.tag_b] = M.loc[r.tag_b, r.tag_a] = r.edge_cosine
    A = M.to_numpy(dtype=float).copy()
    np.fill_diagonal(A, 1.0)
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(A, cmap="viridis", vmin=0, vmax=1)
    labels = [_name_of(t) for t in tags]
    ax.set_xticks(range(len(tags)), labels, rotation=45, fontsize=7)
    ax.set_yticks(range(len(tags)), labels, fontsize=7)
    for i in range(len(tags)):
        for j in range(len(tags)):
            if np.isfinite(A[i, j]):
                ax.text(j, i, f"{A[i, j]:.2f}", ha="center", va="center",
                        fontsize=6, color="0.9")
    fig.colorbar(im, ax=ax, label="edge-usage cosine")
    ax.set_title("Do rats use the same route edges? (cosine)")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_baseline_compare(stationary_df, free_bouts, baseline_bouts,
                          bin_in=4.0, save_path=None):
    """Stationary-baseline occupancy + free-vs-stationary straightness (artifact check)."""
    from matplotlib.colors import LogNorm
    ext = observed_extent(stationary_df)
    H, _, _ = occupancy_hist(stationary_df, ext, bin_in=bin_in)
    Hs = _box_blur(H, passes=2)
    xmin, xmax, ymin, ymax = ext
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    masked = np.ma.masked_where(Hs <= 0, Hs)
    ax1.imshow(masked.T, origin="lower", extent=(xmin, xmax, ymin, ymax),
               aspect="equal", cmap="magma",
               norm=LogNorm(vmin=1, vmax=max(Hs.max(), 1)))
    ax1.set_title("Stationary baseline occupancy\n(should be blobs, not corridors)")
    ax1.set_xlabel("X (in)")
    ax1.set_ylabel("Y (in)")
    fm = free_bouts["straightness"].dropna().to_numpy() if len(free_bouts) else np.array([])
    bm = (baseline_bouts["straightness"].dropna().to_numpy()
          if len(baseline_bouts) else np.array([]))
    ax2.boxplot([fm, bm], tick_labels=[f"free\n(n={len(fm)})",
                                       f"stationary\n(n={len(bm)})"],
                showfliers=False)
    ax2.set_ylabel("straightness")
    ax2.set_ylim(0, 1.02)
    ax2.set_title("Bout straightness: free vs stationary\n(overlap → geometry artifact)")
    ax2.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_straightness_vs_displacement(free_bouts, jitter_null, save_path=None):
    """Straightness vs net displacement: free bouts (blue) over the displacement-
    matched jitter null (grey). If free bouts sit above the jitter cloud at the
    same displacement, the straightness is not just a geometry artifact."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    if jitter_null is not None and len(jitter_null):
        ax.scatter(jitter_null["disp_in"], jitter_null["straightness"], s=8,
                   alpha=0.25, color="0.6", label="stationary jitter (null)")
    if free_bouts is not None and len(free_bouts):
        ax.scatter(free_bouts["disp_in"], free_bouts["straightness"], s=18,
                   alpha=0.8, color="tab:blue", edgecolor="k", linewidths=0.3,
                   label="free-moving bouts")
    ax.set_xlabel("net displacement (in)")
    ax.set_ylabel("straightness (disp / path)")
    ax.set_ylim(0, 1.02)
    ax.set_title("Straightness vs displacement — free bouts vs displacement-"
                 "matched jitter null")
    ax.legend(fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_self_route_reuse(self_df, cross_edge_mean=None, save_path=None):
    """Per-rat night1-vs-night2 self route reuse (occupancy + own edges); dashed
    line = the cross-rat mean edge cosine (self above it -> individual memory)."""
    if self_df is None or self_df.empty:
        return
    s = self_df.sort_values("shortid")
    labels = [_name_of(t) for t in s["shortid"]]
    x = np.arange(len(s))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - 0.2, s["occ_self_cosine"], 0.4, label="occupancy self-cosine (N1 vs N2)",
           color="teal")
    ax.bar(x + 0.2, s["edge_self_cosine"], 0.4, label="route-edge self-cosine (N1 vs N2)",
           color="darkorange")
    if cross_edge_mean is not None and np.isfinite(cross_edge_mean):
        ax.axhline(cross_edge_mean, color="black", linestyle="--", linewidth=1,
                   label=f"cross-rat edge cosine mean {cross_edge_mean:.2f}")
    ax.set_xticks(x, labels, rotation=30, fontsize=8)
    ax.set_ylabel("cosine (night 1 vs night 2)")
    ax.set_ylim(0, 1)
    ax.set_title("Within-rat route reuse across nights (memory proxy)")
    ax.legend(fontsize=7)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_edge_effect(thig_df, H, extent, full_mask, interior_info, boundary_rect,
                     bin_in=4.0, save_path=None):
    """Left: per-rat thigmotaxis index. Right: occupancy + full corridor (cyan) with
    the wall band (red) and interior-only corridor (lime) — how much is perimeter."""
    from matplotlib.colors import LogNorm
    fig, (axb, axm) = plt.subplots(1, 2, figsize=(13, 5.5))
    t = thig_df.sort_values("thigmotaxis_index", ascending=False)
    labels = [_name_of(x) for x in t["shortid"]]
    axb.bar(np.arange(len(t)), t["thigmotaxis_index"], color="indianred")
    axb.set_xticks(np.arange(len(t)), labels, rotation=30, fontsize=8)
    axb.set_ylabel("fraction of fixes near the wall")
    axb.set_ylim(0, 1)
    axb.set_title("Thigmotaxis index (wall-running) per rat")
    axb.grid(axis="y", linestyle="--", alpha=0.4)

    xmin, xmax, ymin, ymax = extent
    Hs = _box_blur(H, passes=2)
    masked = np.ma.masked_where(Hs <= 0, Hs)
    axm.imshow(masked.T, origin="lower", extent=(xmin, xmax, ymin, ymax),
               aspect="equal", cmap="magma", norm=LogNorm(vmin=1, vmax=max(Hs.max(), 1)))
    xc = xmin + (np.arange(full_mask.shape[0]) + 0.5) * bin_in
    yc = ymin + (np.arange(full_mask.shape[1]) + 0.5) * bin_in
    if full_mask.any():
        axm.contour(xc, yc, full_mask.T.astype(float), levels=[0.5], colors="cyan",
                    linewidths=1.0)
    edge_cells = interior_info.get("_edge_cells")
    if edge_cells is not None and edge_cells.any():
        axm.contour(xc, yc, edge_cells.T.astype(float), levels=[0.5], colors="red",
                    linewidths=0.8, linestyles="--")
    int_mask = interior_info.get("_interior_mask")
    if int_mask is not None and int_mask.any():
        axm.contour(xc, yc, int_mask.T.astype(float), levels=[0.5], colors="lime",
                    linewidths=1.0)
    if boundary_rect is not None:
        bx0, bx1, by0, by1 = boundary_rect
        axm.plot([bx0, bx1, bx1, bx0, bx0], [by0, by0, by1, by1, by0],
                 color="white", linewidth=0.8, alpha=0.6)
    ef = interior_info.get("full_corridor_edge_fraction")
    axm.set_title(f"Corridor: full (cyan) vs interior-only (lime); exclude zone (red)\n"
                  f"{ef:.0%} of the full corridor is in the exclude zone" if ef is not None
                  else "Corridor full vs interior")
    axm.set_xlabel("X (in)")
    axm.set_ylabel("Y (in)")
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_window_scatter(win, boundary_rect=None, save_path=None, point_s=2,
                        alpha=0.18, grid_in=50.0):
    """
    All-rats position scatter for the pooled window (coloured by rat, names in the
    legend) with the WISER boundary and a coordinate reference grid — a reference
    image for marking the real edge / an exclude region by eye.
    """
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MultipleLocator
    tags = sorted(win["shortid"].unique())
    colors = plotting._tag_colors(tags)
    fig, ax = plt.subplots(figsize=(12, 8))
    for t in tags:
        g = win[win["shortid"] == t]
        ax.scatter(g["x"], g["y"], s=point_s, alpha=alpha, color=colors[t],
                   linewidths=0)
    if boundary_rect is not None:
        bx0, bx1, by0, by1 = boundary_rect
        ax.plot([bx0, bx1, bx1, bx0, bx0], [by0, by0, by1, by1, by0],
                color="k", lw=1.3, ls="--")
    handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=colors[t],
                      markersize=8, label=f"{_name_of(t)} ({t}) n={int((win['shortid'] == t).sum()):,}")
               for t in tags]
    if boundary_rect is not None:
        handles.append(Line2D([0], [0], color="k", ls="--", label="WISER boundary"))
    ax.legend(handles=handles, fontsize=8, loc="upper right", framealpha=0.9)
    ax.set_aspect("equal", "box")
    ax.set_xlabel("X (in)")
    ax.set_ylabel("Y (in)")
    ax.xaxis.set_major_locator(MultipleLocator(grid_in))
    ax.yaxis.set_major_locator(MultipleLocator(grid_in))
    ax.tick_params(labelsize=8)
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.set_title("All-rats positions — 9–11 pm pooled (cleaned) — "
                 f"reference for the edge/exclude region ({grid_in:g}-in grid)")
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


# --- Nightly progression plots --------------------------------------------

def plot_nightly_trajectories(win, save_path=None):
    """Per-rat night trajectories (rows = rat, cols = night)."""
    tags = sorted(win["shortid"].unique())
    nights = sorted(win["night"].unique())
    colors = plotting._tag_colors(tags)
    fig, axes = plt.subplots(len(tags), len(nights),
                             figsize=(3.2 * len(nights), 3 * len(tags)),
                             squeeze=False)
    for r, t in enumerate(tags):
        for c, night in enumerate(nights):
            ax = axes[r][c]
            g = win[(win["shortid"] == t) & (win["night"] == night)]
            if "valid" in g.columns:
                g = g[g["valid"]]
            ax.plot(g["x"], g["y"], lw=0.3, alpha=0.5, color=colors[t])
            if r == 0:
                ax.set_title(f"{night}" + ("  (wet)" if night == nights[-1] else ""),
                             fontsize=9)
            if c == 0:
                ax.set_ylabel(str(t), fontsize=8)
            ax.set_aspect("equal", "datalim")
            ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle("Per-rat night trajectories 9pm-12am (rows=rat, cols=night)", fontsize=12)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_nightly_rate_lines(nr, value="active_distance_m_per_valid_hour",
                            save_path=None):
    """Per-rat nightly rate (paired lines) + mean±SD — the candidate habituation."""
    nights = sorted(nr["night"].unique())
    tags = sorted(nr["shortid"].unique())
    colors = plotting._tag_colors(tags)
    piv = nr.pivot(index="night", columns="shortid", values=value).reindex(nights)
    fig, ax = plt.subplots(figsize=(8, 6))
    x = range(len(nights))
    for t in tags:
        ax.plot(x, piv[t].values, "-o", ms=4, color=colors[t], alpha=0.8, label=str(t))
    ax.errorbar(x, piv.mean(axis=1).values, yerr=piv.std(axis=1).values,
                color="black", lw=2.5, capsize=5, label="mean±SD", zorder=5)
    ax.set_xticks(list(x), nights)
    ax.set_ylabel("active distance (m / valid hour)")
    ax.set_title("Nightly movement rate per rat — candidate habituation\n"
                 "(6/28 & 6/29 dry; 6/30 wet ground — confounded)")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_cumulative_night(cum, rain_band_min=None, save_path=None):
    """Through-the-night cumulative active distance per rat, one panel per night;
    ``rain_band_min`` shades the observed in-window rain on the last (wet) night."""
    nights = sorted(cum["night"].unique())
    tags = sorted(cum["shortid"].unique())
    colors = plotting._tag_colors(tags)
    fig, axes = plt.subplots(1, len(nights), figsize=(5 * len(nights), 4.5),
                             squeeze=False, sharey=True)
    for i, night in enumerate(nights):
        ax = axes[0][i]
        for t in tags:
            g = cum[(cum["night"] == night) & (cum["shortid"] == t)].sort_values("t_min")
            ax.plot(g["t_min"], g["cum_m"], color=colors[t], alpha=0.85,
                    label=str(t) if i == 0 else None)
        if rain_band_min is not None and night == nights[-1]:
            ax.axvspan(rain_band_min[0], rain_band_min[1], color="tab:blue",
                       alpha=0.18, label="observed rain")
            ax.legend(fontsize=7, loc="upper left")
        ax.set_title(f"{night}" + ("  (wet)" if night == nights[-1] else "  (dry)"),
                     fontsize=10)
        ax.set_xlabel("minutes since 21:00")
        ax.grid(True, linestyle="--", alpha=0.3)
    axes[0][0].set_ylabel("cumulative active distance (m)")
    axes[0][0].legend(fontsize=7, loc="upper left")
    fig.suptitle("Through-the-night movement (rain 22:30-22:50 shaded on the wet night)",
                 fontsize=12)
    fig.tight_layout()
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_rain_timeline(weather, day="2026-06-30", night_hours=(21, 24),
                       obs_band_hhmm=None, save_path=None):
    """Station rain-rate for the rain day, with the night window + observed
    in-window rain band marked."""
    w = weather.copy()
    wd = w[w["datetime_local"].dt.date.astype(str) == day]
    fig, ax = plt.subplots(figsize=(11, 4))
    if not wd.empty and "rain_rate_mmhr" in wd.columns:
        ax.plot(wd["datetime_local"], wd["rain_rate_mmhr"], color="tab:blue", marker=".")
        ax.fill_between(wd["datetime_local"], 0, wd["rain_rate_mmhr"].fillna(0),
                        color="tab:blue", alpha=0.3)
    base = pd.Timestamp(day)
    ax.axvspan(base + pd.Timedelta(hours=night_hours[0]),
               base + pd.Timedelta(hours=night_hours[1]),
               color="grey", alpha=0.12, label="analysis window 21-24")
    if obs_band_hhmm is not None:
        h0, m0 = map(int, obs_band_hhmm[0].split(":"))
        h1, m1 = map(int, obs_band_hhmm[1].split(":"))
        ax.axvspan(base + pd.Timedelta(hours=h0, minutes=m0),
                   base + pd.Timedelta(hours=h1, minutes=m1),
                   color="tab:red", alpha=0.25, label="observed in-window rain")
    ax.set_ylabel("rain rate (mm/hr)")
    ax.set_title(f"{day} station rain: 17:20 burst wets the ground; "
                 "22:30-22:50 in-window rain observed (station sparse there)")
    ax.legend(fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_rain_did(did_variants: dict, save_path=None):
    """DiD dot plot: per-rat DiD (Δ rain-night − Δ control) for each control night
    and buffer variant; black bar = mean."""
    fig, ax = plt.subplots(figsize=(9, 5))
    labels, pos = [], 0
    xticks, xticklabels = [], []
    for vlabel, did in did_variants.items():
        if did is None or did.empty:
            continue
        for ctrl, g in did.groupby("control_night"):
            vals = g["did"].to_numpy()
            ax.scatter([pos] * len(vals), vals, color="tab:purple", alpha=0.7, zorder=3)
            ax.plot([pos - 0.2, pos + 0.2], [vals.mean()] * 2, color="black", lw=2.5)
            xticks.append(pos)
            xticklabels.append(f"{vlabel}\nvs {ctrl[5:]}")
            pos += 1
        pos += 0.5
    ax.axhline(0, color="grey", lw=1, ls="--")
    ax.set_xticks(xticks, xticklabels, fontsize=8)
    ax.set_ylabel("DiD  (Δ 6/30 − Δ control)   m/valid-hr")
    ax.set_title("Rain difference-in-differences per rat (n=5; >0 = 6/30 rose more "
                 "than control)\nexploratory — candidate rain effect")
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


# --- Nightly behavior plots -----------------------------------------------

def plot_nightly_paired(df, value, *, ylabel, title, save_path=None):
    """Per-rat paired lines across nights + mean±SD (generic)."""
    nights = sorted(df["night"].unique())
    tags = sorted(df["shortid"].unique())
    colors = plotting._tag_colors(tags)
    piv = df.pivot(index="night", columns="shortid", values=value).reindex(nights)
    fig, ax = plt.subplots(figsize=(8, 6))
    x = range(len(nights))
    for t in tags:
        ax.plot(x, piv[t].values, "-o", ms=4, color=colors[t], alpha=0.8, label=str(t))
    ax.errorbar(x, piv.mean(axis=1).values, yerr=piv.std(axis=1).values,
                color="black", lw=2.5, capsize=5, label="mean±SD", zorder=5)
    ax.set_xticks(list(x), nights)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_nightly_timebudget(roi_use, save_path=None):
    """Stacked time-budget by area per night (mean over rats)."""
    cats = ["home", "resource", "tunnel", "open"]
    cols = {"home": "tab:green", "resource": "tab:orange",
            "tunnel": "tab:purple", "open": "lightgrey"}
    m = roi_use.groupby("night")[[f"{c}_frac" for c in cats]].mean()
    nights = list(m.index)
    fig, ax = plt.subplots(figsize=(7, 5))
    bottom = np.zeros(len(nights))
    for c in cats:
        v = m[f"{c}_frac"].to_numpy()
        ax.bar(nights, v, bottom=bottom, label=c, color=cols[c])
        bottom += v
    ax.set_ylabel("fraction of valid time")
    ax.set_title("Night time-budget by area (mean over rats; tunnel 6/28 only)")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


def plot_nightly_lines(df, cols, *, ylabel, title, save_path=None):
    """One or more per-night scalar metrics vs night."""
    nights = sorted(df["night"].unique())
    d = df.set_index("night").reindex(nights)
    fig, ax = plt.subplots(figsize=(8, 5))
    x = range(len(nights))
    for c in cols:
        if c in d.columns:
            ax.plot(x, d[c].to_numpy(dtype=float), "-o", label=c)
    ax.set_xticks(list(x), nights)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.4)
    plotting._save_or_show(fig, Path(save_path) if save_path else None)


# ---------------------------------------------------------------------------
# Direction 3 — daytime sleep / rest-site location and its change
# ---------------------------------------------------------------------------
# Rats are nocturnal (active ~21:00->~05:00); the daytime rest period is
# 05:00-21:00 local. "Sleep" here is a LOW-SPEED proxy (smoothed speed below the
# stationary p99 noise floor), NOT validated against ephys — the CV shelter cams
# (CH05/CH06) are the intended cross-check. WISER's ~7 in jitter means only
# well-separated sites (>> the floor; the two shelters are ~5 ft apart) are
# distinguishable — sub-shelter site distinctions are not.

def rest_mask(df: pd.DataFrame, *, moving_thr_inps: float) -> pd.DataFrame:
    """
    Add a boolean ``resting`` column: smoothed locomotion speed below the
    stationary speed-noise floor ``moving_thr_inps`` (from :func:`speed_noise_floor`).
    Requires ``speed_inps_smooth`` (run :func:`add_speed` first). NaN smoothed
    speed (a tracking artifact set by ``add_speed``) is treated as **not** resting.
    """
    df = df.copy()
    sp = df.get("speed_inps_smooth")
    if sp is None:
        raise KeyError("Run add_speed() before rest_mask().")
    df["resting"] = sp.lt(moving_thr_inps).fillna(False)
    return df


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, float).ravel(); b = np.asarray(b, float).ravel()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else np.nan


def _peak_cell_center(g: pd.DataFrame, extent, bin_in: float) -> tuple[float, float, np.ndarray]:
    """Dominant (box-blurred) occupancy cell centre for one group + its flat map."""
    H, xe, ye = occupancy_hist(g, extent, bin_in)
    Hs = _box_blur(H)
    ix, iy = np.unravel_index(int(np.argmax(Hs)), Hs.shape)
    xc = 0.5 * (xe[ix] + xe[ix + 1])
    yc = 0.5 * (ye[iy] + ye[iy + 1])
    return float(xc), float(yc), Hs.ravel()


def daytime_primary_site(win: pd.DataFrame, *, extent, roi_cfg: dict | None = None,
                         bin_in: float = 4.0, site_radius_in: float = 24.0,
                         min_fixes: int = 50, transform: dict | None = None
                         ) -> tuple[pd.DataFrame, dict]:
    """
    Per **(night, shortid)** primary rest site over resting fixes.

    ``win`` must have ``resting`` (from :func:`rest_mask`) and ``night`` (local
    date, from :func:`select_route_window`). For each animal-day the dominant
    occupancy cell of its resting fixes is the site; ``site_concentration`` is the
    fraction of that day's resting fixes within ``site_radius_in`` (default 24 in,
    well above the ~7 in jitter floor) of the site. Adds ``site_x_field_cm/…`` when
    a confirmed ``transform`` is given, and ``site_roi`` (via :func:`assign_roi`)
    when ``roi_cfg`` is given. All occupancy maps use the shared ``extent`` so the
    per-tag day-to-day cosine in :func:`rest_site_stability` is comparable.

    Returns ``(sites_df, occ_hists)`` where ``occ_hists`` maps ``(night, shortid)``
    -> the flattened blurred occupancy map.
    """
    rest = win[win["resting"]] if "resting" in win.columns else win
    rows: list[dict] = []
    hists: dict = {}
    for (night, sid), g in rest.groupby(["night", "shortid"]):
        g = g.dropna(subset=["x", "y"])
        row = {"night": night, "shortid": sid, "n_rest_fixes": int(len(g))}
        if len(g) < min_fixes:
            row.update({"site_x": np.nan, "site_y": np.nan,
                        "site_concentration": np.nan, "low_coverage": True})
            rows.append(row)
            continue
        xc, yc, hflat = _peak_cell_center(g, extent, bin_in)
        conc = float((np.hypot(g["x"] - xc, g["y"] - yc) <= site_radius_in).mean())
        row.update({"site_x": xc, "site_y": yc, "site_concentration": conc,
                    "low_coverage": False})
        if transform is not None:
            cm = field_transform.apply_transform(transform["matrix"], [[xc, yc]])[0]
            row["site_x_field_cm"], row["site_y_field_cm"] = float(cm[0]), float(cm[1])
        if roi_cfg is not None:
            rep_dt = g["datetime"].iloc[len(g) // 2] if "datetime" in g.columns else None
            one = pd.DataFrame({"x": [xc], "y": [yc]})
            if rep_dt is not None:
                one["datetime"] = [rep_dt]
            row["site_roi"] = assign_roi(one, roi_cfg)["roi"].iloc[0]
        rows.append(row)
        hists[(night, sid)] = hflat
    return pd.DataFrame(rows), hists


def rest_site_stability(sites_df: pd.DataFrame, *, occ_hists: dict | None = None
                        ) -> pd.DataFrame:
    """
    **Across-day** rest-site change, per animal. For each consecutive pair of
    animal-days with a defined site: ``site_shift_in`` (distance between the two
    primary sites) and, when ``occ_hists`` is supplied, ``occ_cosine`` (similarity
    of the two days' resting occupancy maps — high = same spatial use). A shift ≫
    the ~7 in jitter floor is a genuine sleep-site relocation.
    """
    out: list[dict] = []
    have = sites_df.dropna(subset=["site_x", "site_y"])
    for sid, g in have.sort_values("night").groupby("shortid"):
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            d0, d1 = g["night"][i - 1], g["night"][i]
            shift = float(np.hypot(g["site_x"][i] - g["site_x"][i - 1],
                                   g["site_y"][i] - g["site_y"][i - 1]))
            row = {"shortid": sid, "night_prev": d0, "night": d1,
                   "site_shift_in": shift}
            if occ_hists is not None and (d0, sid) in occ_hists and (d1, sid) in occ_hists:
                row["occ_cosine"] = _cosine(occ_hists[(d0, sid)], occ_hists[(d1, sid)])
            out.append(row)
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Direction 3 — tiered relocation labels (avoid headlining a single 3x-jitter cut)
# ---------------------------------------------------------------------------
# Absolute-inch tiers, all well above the ~7 in jitter floor, plus a shelter-
# IDENTITY switch (house_1 <-> house_2) that escalates to "major" regardless of the
# raw distance. This keeps the biological headline cautious: a 22-28 in shift is
# jitter-scale, not a relocation.
DAYTIME_SHELTERS = ("house_1", "house_2")
RELOCATION_TIERS = {"stable": 30.0, "marginal": 75.0, "borderline": 100.0, "major": 180.0}


def nearest_shelter(sites_df: pd.DataFrame, roi_cfg: dict | None,
                    shelters=DAYTIME_SHELTERS) -> pd.DataFrame:
    """
    Add ``nearest_shelter`` + ``dist_nearest_shelter_in`` (centre distance to the
    named shelter ROIs, in the WISER inch frame) to a copy of ``sites_df``. Rows with
    no defined site (NaN ``site_x``) or with no shelter ROIs get ``None`` / NaN.
    Membership is frame-safe (inch offset frame); no physical/directional claim.
    """
    rois = {r["name"]: r for r in (roi_cfg.get("rois", []) if roi_cfg else [])}
    cents = {s: (rois[s]["x"], rois[s]["y"]) for s in shelters if s in rois}
    out = sites_df.copy()
    names: list = []
    dists: list = []
    for _, r in out.iterrows():
        if pd.isna(r.get("site_x")) or not cents:
            names.append(None); dists.append(np.nan); continue
        best_s, best_d = None, np.inf
        for s, (cx, cy) in cents.items():
            d = float(np.hypot(r["site_x"] - cx, r["site_y"] - cy))
            if d < best_d:
                best_d, best_s = d, s
        names.append(best_s); dists.append(best_d)
    out["nearest_shelter"] = names
    out["dist_nearest_shelter_in"] = dists
    return out


def relocation_tier(shift_in: float, switched: bool, *,
                    thresholds: dict = RELOCATION_TIERS) -> str:
    """
    Tiered across-day relocation label for one animal-day pair. ``switched`` is a
    nearest-shelter identity change (house_1 <-> house_2). Bins (inches):
    ``stable`` < 30 · ``marginal`` 30–75 · ``borderline`` 75–100 ·
    ``robust_relocation`` 100–180 · ``major_shelter_switch`` ≥ 180 **or** an identity
    switch with shift > 75 (escalates regardless of distance).
    """
    if shift_in is None or not np.isfinite(shift_in):
        return "undefined"
    t = thresholds
    if (switched and shift_in > t["marginal"]) or shift_in >= t["major"]:
        return "major_shelter_switch"
    if shift_in >= t["borderline"]:
        return "robust_relocation"
    if shift_in >= t["marginal"]:
        return "borderline"
    if shift_in >= t["stable"]:
        return "marginal"
    return "stable"


def classify_across_day(stab: pd.DataFrame, sites: pd.DataFrame,
                        roi_cfg: dict | None, shelters=DAYTIME_SHELTERS
                        ) -> pd.DataFrame:
    """
    Enrich the :func:`rest_site_stability` table with nearest-shelter identity per
    night and a tiered :func:`relocation_tier` label. Adds ``nearest_shelter_prev``,
    ``nearest_shelter``, ``shelter_switch`` (bool), ``relocation_tier``.
    """
    if stab.empty:
        return stab.assign(nearest_shelter_prev=None, nearest_shelter=None,
                           shelter_switch=False, relocation_tier="undefined")
    s = nearest_shelter(sites, roi_cfg, shelters)
    key = {(str(n), str(sid)): sh for n, sid, sh
           in zip(s["night"], s["shortid"], s["nearest_shelter"])}
    out = stab.copy()
    prev = [key.get((str(r.night_prev), str(r.shortid))) for r in out.itertuples()]
    now = [key.get((str(r.night), str(r.shortid))) for r in out.itertuples()]
    out["nearest_shelter_prev"] = prev
    out["nearest_shelter"] = now
    out["shelter_switch"] = [(p is not None and q is not None and p != q)
                             for p, q in zip(prev, now)]
    out["relocation_tier"] = [relocation_tier(sh, sw) for sh, sw
                              in zip(out["site_shift_in"], out["shelter_switch"])]
    return out


# ---------------------------------------------------------------------------
# Direction 3 (Stage B) — within-day rest bouts, day windows, relocation events
# ---------------------------------------------------------------------------
# Sustained low-speed REST bouts (a proxy for sleep/rest, NOT ephys-validated),
# gap-aware (a WISER dropout is 'unknown', not 'awake'/'left'), tagged with the
# nearest ROI/zone and the time-of-day window. Used to ask whether rest-site choice
# follows a within-day (temperature-linked) pattern. Frame is the UNVERIFIED inch
# offset, so claims are ROI-identity + outside-air-temperature/time proxies only.
DAY_WINDOWS = (("early_morning", 5, 9), ("late_morning", 9, 12),
               ("midday_heat", 12, 15), ("afternoon", 15, 18),
               ("evening_transition", 18, 21))
ZONE_CLASS = {"house_1": "shelter", "house_2": "shelter",
              "refuge_1": "refuge", "refuge_2": "refuge", "refuge_3": "refuge",
              "refuge_4": "refuge", "food_1": "resource", "food_2": "resource",
              "water_1": "resource", "water_2": "resource", "tunnel_1": "tunnel",
              "edge": "wall", "open": "open"}


def day_window(hour) -> str:
    """Time-of-day window label for a local clock hour (see :data:`DAY_WINDOWS`)."""
    for name, a, b in DAY_WINDOWS:
        if a <= hour < b:
            return name
    return "off_window"


def zone_class(roi_label) -> str:
    """Coarse zone class for an :func:`assign_roi` label (shelter/refuge/resource/
    tunnel/wall/open)."""
    return ZONE_CLASS.get(str(roi_label), "open")


def rest_bouts(win: pd.DataFrame, *, roi_cfg: dict | None = None,
               shelters=DAYTIME_SHELTERS, bin_s: int = 60, enter_s: float = 120,
               exit_s: float = 180, min_bout_s: float = 300, rest_hi: float = 0.6,
               rest_lo: float = 0.4, near_shelter_in: float = 48.0) -> pd.DataFrame:
    """
    Segment each **(night, shortid)** into sustained daytime REST bouts.

    ``win`` must carry ``resting`` (:func:`rest_mask`), ``night``
    (:func:`select_route_window`), ``datetime`` (naive UTC), ``x``, ``y``; ``roi`` is
    assigned from ``roi_cfg`` if absent. Per ``bin_s`` bin: ``frac_rest`` =
    mean(resting); reindex onto the contiguous within-night grid so **dropout bins
    (no fix) are explicit NaN** — a gap holds state (dropout ≠ awake ≠ left).
    Hysteresis (:func:`_hysteresis_state`, enter after ``enter_s`` rest, exit after
    ``exit_s`` active, uncertain holds) yields bouts; bouts < ``min_bout_s`` dropped.

    Per bout: ``start_utc``/``end_utc``/``duration_s``, ``n_fix``, centroid
    (median), ``spread_in``, ``dominant_roi`` + ``zone_class``, ``dist_house_1_in`` /
    ``dist_house_2_in``, ``nearest_shelter``, ``near_shelter`` (≤ ``near_shelter_in``),
    ``dropout_frac`` (share of the bout's bin grid with no fix), and ``window`` (of
    the bout midpoint). Returns a long bouts frame.
    """
    cols = ["night", "shortid", "start_utc", "end_utc", "duration_s", "n_bins",
            "n_fix", "centroid_x", "centroid_y", "spread_in", "dominant_roi",
            "zone_class", "dist_house_1_in", "dist_house_2_in", "nearest_shelter",
            "near_shelter", "dropout_frac", "window"]
    d = win.dropna(subset=["x", "y", "datetime"]).copy()
    if "resting" not in d.columns:
        raise KeyError("rest_bouts needs 'resting' (run rest_mask first).")
    if "night" not in d.columns:
        raise KeyError("rest_bouts needs 'night' (run select_route_window first).")
    if "roi" not in d.columns and roi_cfg is not None:
        d = assign_roi(d, roi_cfg)
    rois = {r["name"]: r for r in (roi_cfg.get("rois", []) if roi_cfg else [])}
    cents = {s: (rois[s]["x"], rois[s]["y"]) for s in shelters if s in rois}
    binns = int(bin_s) * 1_000_000_000
    d["bin_utc"] = _bin_utc_ns(d["datetime"], bin_s)
    n_enter = max(1, int(np.ceil(enter_s / bin_s)))
    n_exit = max(1, int(np.ceil(exit_s / bin_s)))
    min_bins = max(1, int(np.ceil(min_bout_s / bin_s)))

    rows: list[dict] = []
    for (night, sid), g in d.groupby(["night", "shortid"]):
        agg = g.groupby("bin_utc").agg(frac_rest=("resting", "mean")).sort_index()
        full = np.arange(int(agg.index.min()), int(agg.index.max()) + binns, binns)
        fr = agg["frac_rest"].reindex(full).to_numpy()
        near = np.where(np.isnan(fr), np.nan,
                        np.where(fr >= rest_hi, 1.0, np.where(fr <= rest_lo, 0.0, np.nan)))
        state = _hysteresis_state(near, n_enter, n_exit)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], state.astype(np.int8), [0]))))
        for a, b in zip(edges[0::2], edges[1::2]):
            if (b - a) < min_bins:
                continue
            start, end = int(full[a]), int(full[b - 1])
            fx = g[(g["bin_utc"] >= start) & (g["bin_utc"] <= end)]
            if fx.empty:
                continue
            cx, cy = float(fx["x"].median()), float(fx["y"].median())
            spread = float(np.median(np.hypot(fx["x"] - cx, fx["y"] - cy)))
            dom_roi = str(fx["roi"].value_counts().idxmax()) if "roi" in fx.columns and len(fx) else "open"
            if cents:
                ds = {s: float(np.hypot(cx - c[0], cy - c[1])) for s, c in cents.items()}
                nsh_name = min(ds, key=ds.get)
                nsh = ds[nsh_name]
                d_h1, d_h2 = ds.get("house_1", np.nan), ds.get("house_2", np.nan)
            else:
                nsh_name, nsh, d_h1, d_h2 = None, np.nan, np.nan, np.nan
            span = b - a
            present = int(np.count_nonzero(~np.isnan(fr[a:b])))
            dropout = float(1.0 - present / span) if span else np.nan
            mid_local = pd.Timestamp((start + end) // 2) + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
            rows.append({
                "night": night, "shortid": sid, "start_utc": start, "end_utc": end + binns,
                "duration_s": int((b - a) * bin_s), "n_bins": int(b - a), "n_fix": int(len(fx)),
                "centroid_x": cx, "centroid_y": cy, "spread_in": spread,
                "dominant_roi": dom_roi, "zone_class": zone_class(dom_roi),
                "dist_house_1_in": d_h1, "dist_house_2_in": d_h2, "nearest_shelter": nsh_name,
                "near_shelter": bool(np.isfinite(nsh) and nsh <= near_shelter_in),
                "dropout_frac": dropout, "window": day_window(mid_local.hour)})
    return pd.DataFrame(rows, columns=cols)


def within_day_sequence(win: pd.DataFrame, roi_cfg: dict | None = None, *,
                        shelters=DAYTIME_SHELTERS, near_shelter_in: float = 48.0
                        ) -> pd.DataFrame:
    """
    Per **(night, shortid, window)** daytime REST **site**: dominant zone/ROI by
    resting-fix count **plus** the window's median centroid, nearest shelter, and a
    representative time — so it is an ordered within-day *site sequence*, not just a
    zone tally. (Rats rest ~90% of the day, so speed-bouts collapse to ~1/day;
    within-day relocation is a LOCATION change inside one long rest state, captured
    here at window granularity.) ``win`` needs ``resting`` + ``clock_hour`` +
    ``datetime``; ``roi`` assigned from ``roi_cfg`` if absent.

    Columns: ``night, shortid, window, window_order, start_utc, dominant_zone_class,
    dominant_roi, centroid_x, centroid_y, nearest_shelter, dist_nearest_shelter_in,
    near_shelter, n_rest_fix``. Windows ordered per :data:`DAY_WINDOWS`.
    """
    d = win[win["resting"]] if "resting" in win.columns else win
    d = d.dropna(subset=["x", "y"]).copy()
    if "roi" not in d.columns and roi_cfg is not None:
        d = assign_roi(d, roi_cfg)
    d["window"] = d["clock_hour"].map(day_window)
    d["zc"] = d["roi"].map(zone_class) if "roi" in d.columns else "open"
    rois = {r["name"]: r for r in (roi_cfg.get("rois", []) if roi_cfg else [])}
    cents = {s: (rois[s]["x"], rois[s]["y"]) for s in shelters if s in rois}
    order = {ww[0]: i for i, ww in enumerate(DAY_WINDOWS)}
    rows = []
    for (night, sid, wname), g in d.groupby(["night", "shortid", "window"]):
        if wname == "off_window":
            continue
        zc = g["zc"].value_counts()
        rc = g["roi"].value_counts() if "roi" in g.columns else pd.Series(dtype=int)
        cx, cy = float(g["x"].median()), float(g["y"].median())
        if cents:
            ds = {s: float(np.hypot(cx - c[0], cy - c[1])) for s, c in cents.items()}
            nsh_name = min(ds, key=ds.get); nsh = ds[nsh_name]
        else:
            nsh_name, nsh = None, np.nan
        rows.append({"night": night, "shortid": sid, "window": wname,
                     "window_order": order.get(wname, 99),
                     "start_utc": int(g["datetime"].min().value),
                     "dominant_zone_class": str(zc.idxmax()) if len(zc) else "open",
                     "dominant_roi": str(rc.idxmax()) if len(rc) else "open",
                     "centroid_x": cx, "centroid_y": cy, "nearest_shelter": nsh_name,
                     "dist_nearest_shelter_in": nsh,
                     "near_shelter": bool(np.isfinite(nsh) and nsh <= near_shelter_in),
                     "n_rest_fix": int(len(g))})
    seq = pd.DataFrame(rows)
    if not seq.empty:
        seq = seq.sort_values(["night", "shortid", "window_order"]).reset_index(drop=True)
    return seq


def relocation_events(seq: pd.DataFrame, *, order_col: str = "window_order",
                      min_shift_in: float = 100.0, zone_floor_in: float = 30.0
                      ) -> pd.DataFrame:
    """
    **Within-day** rest-site relocation events between consecutive rows of an ordered
    per-(night, shortid) site sequence (from :func:`within_day_sequence`, or any frame
    with ``centroid_x/centroid_y``, ``zone_class``, ``nearest_shelter``,
    ``near_shelter`` and an ``order_col``). An event fires when the centroid shift ≥
    ``min_shift_in``, OR the nearest-shelter identity changes (both rows near a
    shelter — a house_1↔house_2 switch), OR the ``zone_class`` changes with a shift ≥
    ``zone_floor_in``. Jitter-scale wiggles (< ``zone_floor_in``, no identity change)
    are excluded. ``kind`` ∈ {shelter_switch, zone_change, displacement}.
    """
    if seq.empty:
        return pd.DataFrame(columns=["night", "shortid", "shift_in", "kind"])
    wlab = "window" if "window" in seq.columns else order_col
    zcol = "zone_class" if "zone_class" in seq.columns else "dominant_zone_class"
    out = []
    for (night, sid), g in seq.sort_values(order_col).groupby(["night", "shortid"]):
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            p, q = g.iloc[i - 1], g.iloc[i]
            shift = float(np.hypot(q["centroid_x"] - p["centroid_x"],
                                   q["centroid_y"] - p["centroid_y"]))
            sh_switch = bool(p["near_shelter"] and q["near_shelter"]
                             and p["nearest_shelter"] != q["nearest_shelter"])
            zone_change = bool(p[zcol] != q[zcol])
            if shift >= min_shift_in or sh_switch or (zone_change and shift >= zone_floor_in):
                kind = ("shelter_switch" if sh_switch
                        else "zone_change" if zone_change else "displacement")
                row = {"night": night, "shortid": sid,
                       "from": p[wlab], "to": q[wlab],
                       "from_zone": p[zcol], "to_zone": q[zcol],
                       "from_roi": p.get("dominant_roi"), "to_roi": q.get("dominant_roi"),
                       "from_shelter": p["nearest_shelter"], "to_shelter": q["nearest_shelter"],
                       "shift_in": shift, "kind": kind}
                if "start_utc" in seq.columns:
                    row["start_utc"] = int(q["start_utc"])
                out.append(row)
    return pd.DataFrame(out)


def intraday_site_drift(win: pd.DataFrame, *, extent,
                        blocks=((5, 11), (11, 15), (15, 21)),
                        bin_in: float = 4.0, min_fixes: int = 30,
                        transform: dict | None = None) -> pd.DataFrame:
    """
    **Within-day** rest-site drift. Splits the rest period into ``blocks`` (local
    clock-hour ranges; default morning / midday / afternoon) and, per
    (night, shortid, block), finds the primary resting site and its shift from the
    previous block that day. ``win`` needs ``resting`` and ``clock_hour`` (both from
    the rest_mask + select_route_window pipeline). A large ``shift_from_prev_in``
    (≫ jitter) means the animal moved its rest spot during the day.
    """
    rest = win[win["resting"]] if "resting" in win.columns else win
    out: list[dict] = []
    for (night, sid), g in rest.groupby(["night", "shortid"]):
        prev = None
        for b0, b1 in blocks:
            sub = g[(g["clock_hour"] >= b0) & (g["clock_hour"] < b1)].dropna(subset=["x", "y"])
            label = f"{b0:02d}-{b1:02d}"
            row = {"night": night, "shortid": sid, "block": label,
                   "n_rest_fixes": int(len(sub))}
            if len(sub) < min_fixes:
                # low coverage this block: record NaN site but KEEP prev, so the
                # next populated block still measures its shift from the last known
                # site (a morning->afternoon move survives an empty midday block).
                row.update({"site_x": np.nan, "site_y": np.nan,
                            "shift_from_prev_in": np.nan})
                out.append(row)
                continue
            xc, yc, _ = _peak_cell_center(sub, extent, bin_in)
            row["site_x"], row["site_y"] = xc, yc
            row["shift_from_prev_in"] = (float(np.hypot(xc - prev[0], yc - prev[1]))
                                         if prev is not None else np.nan)
            if transform is not None:
                cm = field_transform.apply_transform(transform["matrix"], [[xc, yc]])[0]
                row["site_x_field_cm"], row["site_y_field_cm"] = float(cm[0]), float(cm[1])
            prev = (xc, yc)
            out.append(row)
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Direction 3 cross-modal: WISER shelter-ROI presence <-> CV shelter occupancy
# ---------------------------------------------------------------------------
# WISER (UWB, whole paddock, Unix-ms UTC) vs the CV shelter cams (CH05 = left
# shelter, CH06 = right; viewed through IR glass; timestamped in LOCAL NVR
# wallclock from the recording filename). The two device clocks are UNVERIFIED
# against each other: we shift CV to UTC by +|LOCAL_TZ_OFFSET_HOURS| h and then
# SCAN a small residual lag, reporting the best-fitting offset (never asserting a
# verified sync). CV counts undercount (huddles + the wall-edge blind zone), so
# occupancy (boolean) is the primary metric and head-count is a lower bound.

CV_OCCUPIED_STATES = ("occupied_low_motion", "occupied_high_motion")


def _coerce_bool(s: pd.Series, default: bool) -> pd.Series:
    """Object/NaN-tolerant boolean coercion (NaN -> ``default``); avoids the pandas
    fillna-downcast FutureWarning on mixed-vintage CV columns."""
    return s.map(lambda v: default if pd.isna(v) else bool(v)).astype(bool)


def load_cv_shelter_sleep(paths) -> pd.DataFrame:
    """
    Load + concat CV ``shelter_sleep.py`` output CSVs and add a UTC timestamp.

    **Schema-tolerant across CV vintages.** The current schema has underscored
    states + `view_quality_inside` + `usable_*` + `n_inside_estimated`; an older
    one (e.g. 2026-06-29) is just ``channel,file,t,n_rats,roi_motion,state`` with
    **hyphenated** states and no glass QC. This normalizes both:
    - ``state`` hyphens -> underscores; ``occupied`` = state in
      :data:`CV_OCCUPIED_STATES`;
    - ``n_inside_estimated`` falls back to the old ``n_rats`` when absent;
    - missing ``usable_for_headline_summary`` -> False (no glass QC = can't claim
      clear), missing ``usable_for_coarse_activity`` -> True (the occupancy call
      still exists), missing ``view_quality_inside`` -> "unknown".

    CV ``t`` is naive **local** NVR wallclock; WISER is naive **UTC**, so
    ``t_utc = t + |LOCAL_TZ_OFFSET_HOURS| h`` (EDT = UTC-4). Empty frame if no file.
    """
    frames = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            warnings.warn(f"[cv-crossval] missing CV file {p}")
            continue
        frames.append(pd.read_csv(p))
    if not frames:
        return pd.DataFrame()
    cv = pd.concat(frames, ignore_index=True)
    cv["t"] = pd.to_datetime(cv["t"])                              # naive local
    cv["t_utc"] = cv["t"] + pd.Timedelta(hours=abs(LOCAL_TZ_OFFSET_HOURS))
    cv["state"] = cv["state"].astype(str).str.replace("-", "_", regex=False)
    cv["occupied"] = cv["state"].isin(CV_OCCUPIED_STATES)
    if "n_inside_estimated" not in cv.columns:
        cv["n_inside_estimated"] = np.nan
    if "n_rats" in cv.columns:                                     # older column name
        cv["n_inside_estimated"] = cv["n_inside_estimated"].fillna(cv["n_rats"])
    if "usable_for_headline_summary" not in cv.columns:
        cv["usable_for_headline_summary"] = False
    cv["usable_for_headline_summary"] = _coerce_bool(cv["usable_for_headline_summary"], False)
    if "usable_for_coarse_activity" not in cv.columns:
        cv["usable_for_coarse_activity"] = True
    cv["usable_for_coarse_activity"] = _coerce_bool(cv["usable_for_coarse_activity"], True)
    if "view_quality_inside" not in cv.columns:
        cv["view_quality_inside"] = "unknown"
    cv["view_quality_inside"] = cv["view_quality_inside"].fillna("unknown")
    return cv


def _bin_utc_ns(dt: pd.Series, bin_s: int) -> np.ndarray:
    """
    Resolution-agnostic time-bin key. Floor naive-UTC ``datetime`` values to
    ``bin_s``-second bins and return **int64 nanoseconds since the Unix epoch**.

    ``.astype("int64")`` on a datetime Series returns the raw integer in the
    Series' OWN unit — nanoseconds only when the dtype is ``datetime64[ns]``.
    Under pandas >= 2.0 a SQLite/CSV load can yield ``datetime64[ms]`` (or
    ``[us]``), so the old ``astype("int64") // (bin_s * 1e9)`` under-divided and
    collapsed an entire window into ONE bin (the 2026-07-02 cross-val failure).
    Flooring via ``.dt.floor`` is unit-aware, and the explicit ``datetime64[ns]``
    cast pins the epoch integer to nanoseconds, so ns/us/ms inputs bin
    identically. ``bin_utc`` stays int64-ns to preserve the downstream contract
    (grid ``np.arange`` step, joins, ``start_utc`` / ``end_utc`` arithmetic).
    """
    floored = pd.to_datetime(dt).dt.floor(f"{int(bin_s)}s")
    return floored.astype("datetime64[ns]").astype("int64").to_numpy()


def wiser_shelter_presence(win: pd.DataFrame, roi_cfg: dict, shelter_names,
                           *, bin_s: int = 60, resting_only: bool = False
                           ) -> pd.DataFrame:
    """
    **RAW point-wise ROI occupancy — a DIAGNOSTIC, not the primary state.**

    Per UTC time-bin × shelter, the number of **distinct rats** with a fix inside
    that shelter's (possibly rotated) rectangle, plus ``occupied`` (n_rats > 0).
    Because WISER jitters (~7 in median, p95 ~15 in) around the small ~36 × 27 in
    shelter, a single jittered fix landing outside the rectangle flips a bin to
    ``occupied=False`` even when the animal never left — i.e. this over-reports
    exits during a rest period. Use :func:`wiser_shelter_state` /
    :func:`shelter_occupancy_bins` for the biological occupancy signal; keep this
    only to quantify how much the point-wise definition over-splits.

    Every bin in which WISER observed **any** valid fix gets a row per shelter
    (n_rats = 0 when none are inside) — so ``occupied=False`` bins are explicit
    observations, not missing data. Requires ``datetime`` (naive UTC) + ``x,y``
    (and ``resting`` if ``resting_only``). Reuses :func:`_point_in_rect`.
    Returns long: ``bin_utc, shelter, n_rats, occupied``.
    """
    rois = {r["name"]: r for r in (roi_cfg.get("rois", []) if roi_cfg else [])}
    d = win.dropna(subset=["x", "y", "datetime"]).copy()
    if resting_only and "resting" in d.columns:
        d = d[d["resting"]]
    d["bin_utc"] = _bin_utc_ns(d["datetime"], bin_s)   # int64-ns, unit-safe
    all_bins = np.sort(d["bin_utc"].unique())
    out = []
    for sname in shelter_names:
        roi = rois.get(sname)
        if roi is None:
            warnings.warn(f"[cv-crossval] shelter ROI '{sname}' not in roi_cfg")
            continue
        inside = _point_in_rect(d["x"].to_numpy(), d["y"].to_numpy(), roi)
        counts = d[inside].groupby("bin_utc")["shortid"].nunique()
        g = pd.DataFrame({"bin_utc": all_bins})
        g["n_rats"] = g["bin_utc"].map(counts).fillna(0).astype(int)
        g["shelter"] = sname
        out.append(g)
    if not out:
        return pd.DataFrame(columns=["bin_utc", "shelter", "n_rats", "occupied"])
    res = pd.concat(out, ignore_index=True)
    res["occupied"] = res["n_rats"] > 0
    return res


def _hysteresis_state(near: np.ndarray, n_enter: int, n_exit: int) -> np.ndarray:
    """
    Debounced boolean state from a per-bin evidence array ``near`` whose values are
    ``1.0`` (near/inside), ``0.0`` (far/outside), or ``NaN`` (uncertain).

    Enter the state after ``n_enter`` **consecutive** near bins; exit only after
    ``n_exit`` consecutive far bins. Uncertain bins **hold** the current state and
    reset neither run counter — so boundary jitter (which reads uncertain, never
    far) can't flicker the state off. Returns a boolean array the length of ``near``.
    """
    state = False
    run_near = run_far = 0
    out = np.empty(near.shape[0], dtype=bool)
    for i, v in enumerate(near):
        if v == 1.0:
            run_near += 1
            run_far = 0
        elif v == 0.0:
            run_far += 1
            run_near = 0
        # NaN (uncertain): hold both counters, hold state.
        if not state and run_near >= n_enter:
            state = True
        elif state and run_far >= n_exit:
            state = False
        out[i] = state
    return out


def wiser_shelter_state(win: pd.DataFrame, roi_cfg: dict, shelter_names,
                        *, bin_s: int = 60, buffer_in: float = 18.0,
                        enter_s: float = 120, exit_s: float = 120,
                        near_frac: float = 0.5, far_frac: float = 0.2,
                        hc_min_s: float = 1200, hc_max_spread_in: float = 24.0
                        ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Smoothed, hysteretic, buffer-tolerant **shelter-occupancy state** per rat — the
    biological occupancy signal that replaces raw point-wise ROI inclusion
    (:func:`wiser_shelter_presence`). WISER jitters at the point level, but a
    *sustained cluster* of positions near a shelter is high-confidence occupancy.

    Per **(night, shortid, shelter)** — ``win`` must carry ``night`` (from
    :func:`select_route_window`) so episodes never cross the overnight gap:

    1. Bin the rat's fixes at ``bin_s`` s; per bin take ``frac_core`` /
       ``frac_near`` = fraction of that bin's fixes inside the shelter core /
       inside the core∪buffer (buffer = ROI grown by ``buffer_in`` in;
       :func:`_rect_membership`). Reindex onto the contiguous within-night grid so
       dropout bins are explicit NaN evidence.
    2. Per-bin evidence: **near** (1.0) if ``frac_near >= near_frac``; **far** (0.0)
       if ``frac_near <= far_frac``; else **uncertain** (NaN) — near-boundary /
       buffer-straddling bins are uncertain, never forced outside.
    3. Hysteresis (:func:`_hysteresis_state`): enter after ``ceil(enter_s/bin_s)``
       consecutive near bins, exit after ``ceil(exit_s/bin_s)`` consecutive far
       bins, uncertain holds — yielding a per-bin boolean ``state``.
    4. **Episodes** = contiguous ``state`` runs. Per episode: ``duration_s``,
       ``n_fix``, centroid (median x,y of the episode's fixes), ``spread_in``
       (median distance of those fixes to the centroid), ``frac_core``,
       ``centroid_in_buffer``, and ``high_confidence`` = ``duration_s >= hc_min_s``
       **and** ``spread_in <= hc_max_spread_in`` **and** ``centroid_in_buffer``.
       ("No continuous trajectory away" is intrinsic: a sustained departure would
       have tripped the hysteretic exit and ended the episode.)

    Returns ``(grid_df, episodes_df)``:
      - ``grid_df``: ``night, shortid, shelter, bin_utc, frac_core, frac_near,
        state, hc`` (``hc`` = bin lies in a high-confidence episode).
      - ``episodes_df``: one row per episode with the metrics above.
    """
    rois = {r["name"]: r for r in (roi_cfg.get("rois", []) if roi_cfg else [])}
    d = win.dropna(subset=["x", "y", "datetime"]).copy()
    if "night" not in d.columns:
        raise KeyError("wiser_shelter_state needs a 'night' column "
                       "(run select_route_window first).")
    binns = int(bin_s) * 1_000_000_000                 # ns grid step (bin_utc is int64-ns)
    d["bin_utc"] = _bin_utc_ns(d["datetime"], bin_s)   # unit-safe; see _bin_utc_ns
    n_enter = max(1, int(np.ceil(enter_s / bin_s)))
    n_exit = max(1, int(np.ceil(exit_s / bin_s)))

    grid_rows: list[pd.DataFrame] = []
    epi_rows: list[dict] = []
    for sname in shelter_names:
        roi = rois.get(sname)
        if roi is None:
            warnings.warn(f"[shelter-state] shelter ROI '{sname}' not in roi_cfg")
            continue
        in_core, in_buf = _rect_membership(d["x"].to_numpy(), d["y"].to_numpy(),
                                           roi, buffer_in)
        d_s = d.assign(_core=in_core, _buf=in_buf)
        for (night, sid), g in d_s.groupby(["night", "shortid"]):
            agg = (g.groupby("bin_utc")
                   .agg(frac_core=("_core", "mean"), frac_near=("_buf", "mean"))
                   .sort_index())
            full = np.arange(int(agg.index.min()),
                             int(agg.index.max()) + binns, binns)
            agg = agg.reindex(full)
            fn = agg["frac_near"].to_numpy()
            near = np.where(np.isnan(fn), np.nan,
                            np.where(fn >= near_frac, 1.0,
                                     np.where(fn <= far_frac, 0.0, np.nan)))
            state = _hysteresis_state(near, n_enter, n_exit)
            hc = np.zeros(state.shape[0], dtype=bool)

            # segment contiguous state runs -> episodes ([a, b) grid indices)
            edges = np.flatnonzero(np.diff(
                np.concatenate(([0], state.astype(np.int8), [0]))))
            for a, b in zip(edges[0::2], edges[1::2]):        # [a, b) grid indices
                start, end = int(full[a]), int(full[b - 1])
                # the episode's actual fixes (present bins only)
                fx = g[(g["bin_utc"] >= start) & (g["bin_utc"] <= end)]
                fx = fx.dropna(subset=["x", "y"])
                cx = float(fx["x"].median()); cy = float(fx["y"].median())
                spread = float(np.median(np.hypot(fx["x"] - cx, fx["y"] - cy))) \
                    if len(fx) else np.nan
                cin_core, cin_buf = _rect_membership(np.array([cx]), np.array([cy]),
                                                     roi, buffer_in)
                dur_s = (b - a) * bin_s
                high = bool(dur_s >= hc_min_s and (spread == spread)
                            and spread <= hc_max_spread_in and bool(cin_buf[0]))
                if high:
                    hc[a:b] = True
                epi_rows.append({
                    "night": night, "shortid": sid, "shelter": sname,
                    "start_utc": start, "end_utc": end + binns,
                    "duration_s": int(dur_s), "n_bins": int(b - a),
                    "n_fix": int(len(fx)),
                    "centroid_x": cx, "centroid_y": cy, "spread_in": spread,
                    "frac_core": float(fx["_core"].mean()) if len(fx) else np.nan,
                    "centroid_in_core": bool(cin_core[0]),
                    "centroid_in_buffer": bool(cin_buf[0]),
                    "high_confidence": high,
                })
            grid_rows.append(pd.DataFrame({
                "night": night, "shortid": sid, "shelter": sname,
                "bin_utc": full, "frac_core": agg["frac_core"].to_numpy(),
                "frac_near": fn, "state": state, "hc": hc}))

    grid_df = (pd.concat(grid_rows, ignore_index=True) if grid_rows
               else pd.DataFrame(columns=["night", "shortid", "shelter", "bin_utc",
                                          "frac_core", "frac_near", "state", "hc"]))
    episodes_df = (pd.DataFrame(epi_rows) if epi_rows
                   else pd.DataFrame(columns=["night", "shortid", "shelter",
                                              "start_utc", "end_utc", "duration_s",
                                              "n_bins", "n_fix", "centroid_x",
                                              "centroid_y", "spread_in", "frac_core",
                                              "centroid_in_core", "centroid_in_buffer",
                                              "high_confidence"]))
    return grid_df, episodes_df


def shelter_occupancy_bins(grid_df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse the per-rat :func:`wiser_shelter_state` grid to per ``(shelter,
    bin_utc)`` occupancy: ``n_state`` (rats in the in-shelter state), ``occupied``
    (``n_state > 0``), ``n_hc`` (rats in a high-confidence episode), ``hc_occupied``
    (``n_hc > 0``). ``occupied`` is the smoothed reference used for CV precision;
    ``hc_occupied`` is the high-confidence reference used for CV recall.
    """
    if grid_df.empty:
        return pd.DataFrame(columns=["shelter", "bin_utc", "n_state", "occupied",
                                     "n_hc", "hc_occupied"])
    g = (grid_df.groupby(["shelter", "bin_utc"])
         .agg(n_state=("state", "sum"), n_hc=("hc", "sum"))
         .reset_index())
    g["occupied"] = g["n_state"] > 0
    g["hc_occupied"] = g["n_hc"] > 0
    return g


def cohen_kappa(a, b) -> float:
    """Cohen's kappa for two boolean sequences (chance-corrected agreement)."""
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    n = a.size
    if n == 0:
        return float("nan")
    po = float(np.mean(a == b))
    pa, pb = a.mean(), b.mean()
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return float((po - pe) / (1 - pe))


def _cv_bins(cv_cam: pd.DataFrame, lag_s: float, bin_s: int,
             stratum_col: str | None) -> pd.DataFrame:
    """CV camera rows -> per-bin occupancy at a given lag (s) + stratum filter."""
    d = cv_cam
    if stratum_col:
        d = d[d[stratum_col].astype(bool)]
    if d.empty:
        return pd.DataFrame(columns=["bin_utc", "cv_occupied", "cv_n_inside"])
    shifted = pd.to_datetime(d["t_utc"]) + pd.to_timedelta(lag_s, unit="s")
    b = _bin_utc_ns(shifted, bin_s)                    # unit-safe; lag applied as a timedelta
    grp = pd.DataFrame({"bin_utc": b, "occ": d["occupied"].to_numpy(),
                        "n": pd.to_numeric(d["n_inside_estimated"], errors="coerce").to_numpy()})
    return (grp.groupby("bin_utc")
            .agg(cv_occupied=("occ", "max"), cv_n_inside=("n", "max"))
            .reset_index())


def best_lag_agreement(wiser_shelter: pd.DataFrame, cv_cam: pd.DataFrame, *,
                       lag_grid_s, bin_s: int = 60,
                       stratum_col: str | None = "usable_for_headline_summary"
                       ) -> tuple[dict, pd.DataFrame]:
    """
    Scan ``lag_grid_s`` (seconds added to the CV UTC time) for the offset that
    maximizes WISER↔CV occupancy agreement for one shelter↔camera pair.

    WISER presence bins (:func:`wiser_shelter_presence`) are inner-joined to the
    lagged CV occupancy bins (:func:`_cv_bins`, filtered to ``stratum_col``); at
    each lag we compute Cohen's kappa, raw % agreement, and the number of joined
    bins. Returns ``(best, curve)`` where ``best`` has the max-kappa lag and
    ``curve`` is the full per-lag table. Kappa is NaN when a lag yields no shared
    bins or a degenerate (all-same) stratum.
    """
    w = wiser_shelter[["bin_utc", "occupied"]].rename(columns={"occupied": "w_occupied"})
    rows = []
    for lag in lag_grid_s:
        cb = _cv_bins(cv_cam, lag, bin_s, stratum_col)
        if cb.empty:
            rows.append({"lag_s": lag, "kappa": np.nan, "agreement": np.nan, "n_bins": 0})
            continue
        m = w.merge(cb, on="bin_utc", how="inner")
        if m.empty:
            rows.append({"lag_s": lag, "kappa": np.nan, "agreement": np.nan, "n_bins": 0})
            continue
        k = cohen_kappa(m["w_occupied"], m["cv_occupied"])
        agree = float(np.mean(m["w_occupied"].to_numpy() == m["cv_occupied"].to_numpy()))
        rows.append({"lag_s": lag, "kappa": k, "agreement": agree, "n_bins": int(len(m))})
    curve = pd.DataFrame(rows)
    valid = curve.dropna(subset=["kappa"])
    if valid.empty:
        best = {"lag_s": np.nan, "kappa": np.nan, "agreement": np.nan, "n_bins": 0}
    else:
        best = valid.loc[valid["kappa"].idxmax()].to_dict()
    return best, curve


def cv_detection_metrics(ref: pd.DataFrame, cv_bins: pd.DataFrame, *,
                         ref_col: str = "occupied") -> dict:
    """
    CV shelter-detection performance against WISER **as the reference sensor**.

    WISER (UWB) is unaffected by fog/rain/glass; the CV shelter cam is the optically
    degraded sensor under test — so the two are not symmetric witnesses. Inner-join
    the WISER reference bins ``ref`` (from :func:`shelter_occupancy_bins`; the
    reference truth is column ``ref_col`` — use ``hc_occupied`` for recall,
    ``occupied`` for precision) to the lagged CV occupancy bins ``cv_bins`` (from
    :func:`_cv_bins`) on ``bin_utc`` and score:

    - ``recall`` = TP/(TP+FN) = P(CV occupied | WISER occupied) — how much
      WISER-confirmed occupancy CV recovers (low ⇒ CV misses rats, e.g. wet glass);
    - ``precision`` = TP/(TP+FP) = P(WISER occupied | CV occupied) — is CV right when
      it fires;
    - ``specificity`` = TN/(TN+FP).

    Returns counts + rates + ``n_bins`` and the two occupancy fractions; rates are
    NaN when their denominator is 0. Symmetric κ is intentionally *not* the headline
    here (it would blame WISER for CV's fog misses); it stays a lag-alignment
    diagnostic in :func:`best_lag_agreement`.
    """
    keep = ["bin_utc", ref_col] + (["occupied"] if ref_col != "occupied" else [])
    m = ref[keep].merge(cv_bins, on="bin_utc", how="inner")
    if m.empty:
        return {"n_bins": 0, "TP": 0, "FP": 0, "FN": 0, "TN": 0,
                "recall": np.nan, "precision": np.nan, "specificity": np.nan,
                "wiser_occ_frac": np.nan, "cv_occ_frac": np.nan}
    wref = m[ref_col].to_numpy().astype(bool)
    cv = m["cv_occupied"].to_numpy().astype(bool)
    tp = int(np.sum(wref & cv)); fn = int(np.sum(wref & ~cv))
    fp = int(np.sum(~wref & cv)); tn = int(np.sum(~wref & ~cv))
    return {
        "n_bins": int(len(m)), "TP": tp, "FP": fp, "FN": fn, "TN": tn,
        "recall": (tp / (tp + fn)) if (tp + fn) else np.nan,
        "precision": (tp / (tp + fp)) if (tp + fp) else np.nan,
        "specificity": (tn / (tn + fp)) if (tn + fp) else np.nan,
        "wiser_occ_frac": float(wref.mean()), "cv_occ_frac": float(cv.mean()),
    }


# ---------------------------------------------------------------------------
# Provenance: manifest, filtering log, report
# ---------------------------------------------------------------------------

def _git_hash() -> str:
    try:
        here = Path(__file__).resolve().parent
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(here),
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "uncommitted-or-unavailable"


def make_output_dir(root: Path | str, prefix: str = "wiser_pilot_output") -> Path:
    """Create a timestamped output folder ``<prefix>_YYYYMMDD_HHMM`` (+ figures/)."""
    root = Path(root)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    out = root / f"{prefix}_{stamp}"
    (out / "figures").mkdir(parents=True, exist_ok=True)
    return out


def write_run_manifest(out_dir: Path | str, info: dict) -> Path:
    """Write run_manifest.json with provenance + the runtime data snapshot."""
    out_dir = Path(out_dir)
    info = {"git_commit": _git_hash(),
            "generated_utc": pd.Timestamp.utcnow().tz_localize(None).isoformat(),
            "units": "inches",
            "timestamp_method": "WISER Unix-ms UTC via time_utils.convert_timestamps; "
                                "weather AWN local EDT(-04:00) -> UTC",
            **info}
    path = out_dir / "run_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, default=str)
    return path


def write_filtering_log(out_dir: Path | str, lines: list[str]) -> Path:
    """Write filtering_log.txt — every threshold/exclusion/assumption."""
    out_dir = Path(out_dir)
    path = out_dir / "filtering_log.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def build_pilot_report(sections: dict) -> str:
    """
    Assemble the final 8-section pilot conclusion from a dict of section -> text.
    Expected keys: data_quality, tracking_reliability, artifacts, activity,
    spatial_structure, refuge_evidence, ready_for_formal, fix_before_next.
    """
    order = [
        ("1. Data quality", "data_quality"),
        ("2. Tracking reliability by tag", "tracking_reliability"),
        ("3. Major artifacts", "artifacts"),
        ("4. Activity pattern", "activity"),
        ("5. Spatial structure", "spatial_structure"),
        ("6. Evidence for refuge-centered behavior", "refuge_evidence"),
        ("7. Ready for formal behavior analysis?", "ready_for_formal"),
        ("8. What to fix before the next recording", "fix_before_next"),
    ]
    out = ["=" * 72, "WISER PILOT STUDY — CONCLUSION", "=" * 72]
    for title, key in order:
        out.append(f"\n## {title}\n{sections.get(key, '(not computed)')}")
    return "\n".join(out)

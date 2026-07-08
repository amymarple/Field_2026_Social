r"""
trajectory_stereotypy.py — Phase-A helpers for the trajectory-stereotypy /
stabilization / inter-animal-correlation analysis (nights 2026-06-28 → 07-05).

This is a THIN analysis layer on top of :mod:`wiser_analysis_utils` (imported as
``w``). It adds only what that module does not already have:

- a multi-day incremental-backup loader that **dedups on ``reportid``** (the daily
  ``*.csv.gz`` files overlap: ``06-30`` is a cumulative dump, ``07-01…`` are true
  increments), preserving the QC columns;
- a **cross-midnight night window** (21:00→05:00) — ``w.select_route_window`` only
  handles a same-day block;
- per-night per-animal occupancy maps, a **day-to-day stabilization curve**, a
  **pooled shared-corridor map + residual individual maps**, and the **control
  battery** (animal-label permutation, shared-density/residual expectation,
  time-shuffle circular-shift null, day-shuffle null, synchronous time-coupling).

Everything reuses ``w``'s primitives (``occupancy_hist``, ``corridor_mask``,
``skeletonize_mask``, ``resample_common_grid``, ``_box_blur``…) so the numbers stay
consistent with the rest of the pilot analysis. Units are **inches**; the WISER
frame is the UNVERIFIED offset-origin inch frame (no directional claims). Nothing
here writes to source data.
"""

from __future__ import annotations

from pathlib import Path
import glob
import warnings

import numpy as np
import pandas as pd

try:                                                  # package / flat import
    from . import wiser_analysis_utils as w
    from . import wiser_io
except ImportError:                                   # src on sys.path
    import wiser_analysis_utils as w                  # type: ignore
    import wiser_io                                    # type: ignore


# Raw columns we actually need from the wide (21-col) incremental CSV. Keeping
# usecols small holds memory down across ~12M rows.
_USECOLS = ["reportid", "shortid", "calculation_error", "location_x",
            "location_y", "location_z", "anchors_used", "timestamp",
            "battery_voltage"]


# ---------------------------------------------------------------------------
# 1. Multi-day incremental loader (dedup on reportid)
# ---------------------------------------------------------------------------

_DEDUP_KEY = ["shortid", "ts_raw", "x", "y"]


def load_incremental_days(incremental_dir: Path | str,
                          dates: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """
    Load the daily ``1stcohort_2026_<date>.csv.gz`` incremental backups and
    concatenate them, **deduplicating on ``(shortid, ts_raw, x, y)``**.

    The per-day files overlap (``06-30`` is a cumulative dump that already
    contains 06-28/06-29; ``07-01…`` are true daily increments), so a naive
    concat double-counts. NOTE: ``reportid`` is **NOT** a per-fix key — one report
    cycle covers all tags, so a single ``reportid`` is shared by several animals'
    fixes (verified: 82k reportid groups span different ``shortid``). Deduping on
    ``reportid`` would drop ~94k *distinct* fixes. The composite
    ``(shortid, ts_raw, x, y)`` is unique per fix (every row of a single file is
    distinct on it) and collapses only the exact backfill copies.

    Returns ``(df, log)`` where ``df`` has the canonical rich schema
    (``shortid, ts_raw, x, y[, z]`` + QC cols incl. ``reportid``) and ``log``
    records per-file row counts and how many duplicate rows dedup removed.
    """
    incremental_dir = Path(incremental_dir)
    files = sorted(glob.glob(str(incremental_dir / "1stcohort_2026_*.csv.gz")))
    if dates is not None:
        want = {str(d) for d in dates}
        files = [f for f in files if any(d in Path(f).name for d in want)]
    if not files:
        raise FileNotFoundError(f"No incremental gz files in {incremental_dir}")

    frames: list[pd.DataFrame] = []
    per_file: list[dict] = []
    for f in files:
        name = Path(f).name
        raw = pd.read_csv(f, compression="gzip", usecols=lambda c: c in _USECOLS)
        std = w._standardise_rich(raw, name)
        if std is None:
            per_file.append({"file": name, "rows_raw": int(len(raw)), "rows_kept": 0})
            continue
        frames.append(std)
        per_file.append({"file": name, "rows_raw": int(len(raw)),
                         "rows_kept": int(len(std))})

    combined = pd.concat(frames, ignore_index=True)
    n_before = len(combined)
    combined = combined.drop_duplicates(subset=_DEDUP_KEY).reset_index(drop=True)
    dedup_key = "+".join(_DEDUP_KEY)
    n_after = len(combined)

    log = {"files": per_file, "dedup_key": dedup_key,
           "rows_concatenated": int(n_before), "rows_after_dedup": int(n_after),
           "duplicate_rows_removed": int(n_before - n_after)}
    return combined, log


# ---------------------------------------------------------------------------
# 2. Cross-midnight night window (21:00 -> 05:00 local)
# ---------------------------------------------------------------------------

def add_night_label(df: pd.DataFrame, *, night_start: int = 21, night_end: int = 5,
                    tz_offset_hours: int = w.LOCAL_TZ_OFFSET_HOURS) -> pd.DataFrame:
    """
    Add ``local_dt``, ``clock_hour``, ``in_night`` and a ``night`` label that
    spans midnight: night *N* = local date *D* ``night_start``:00 → *D+1*
    ``night_end``:00. Early-morning fixes (hour < ``night_end``) are attributed to
    the *previous* calendar day's night. Requires ``datetime`` (naive UTC).
    """
    df = df.copy()
    loc = df["datetime"] + pd.Timedelta(hours=tz_offset_hours)
    df["local_dt"] = loc
    hour = loc.dt.hour
    df["clock_hour"] = hour
    df["in_night"] = (hour >= night_start) | (hour < night_end)
    # anchor date: same local day, except pre-dawn hours belong to the prior night
    anchor = loc.dt.normalize()
    early = hour < night_end
    anchor = anchor.mask(early, anchor - pd.Timedelta(days=1))
    df["night"] = anchor.dt.date.astype(str)
    return df


def select_night_window(df: pd.DataFrame, *, night_start: int = 21, night_end: int = 5,
                        tz_offset_hours: int = w.LOCAL_TZ_OFFSET_HOURS,
                        dates: list[str] | None = None,
                        valid_only: bool = True) -> pd.DataFrame:
    """Cleaned fixes inside the cross-midnight night window, tagged with ``night``.
    Mirrors ``w.select_route_window`` but for the 21:00→05:00 block."""
    d = df.dropna(subset=["datetime"]).copy()
    if valid_only and "valid" in d.columns:
        d = d[d["valid"]]
    d = add_night_label(d, night_start=night_start, night_end=night_end,
                        tz_offset_hours=tz_offset_hours)
    d = d[d["in_night"]]
    if dates:
        d = d[d["night"].isin([str(x) for x in dates])]
    return d.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Per-night per-animal occupancy maps
# ---------------------------------------------------------------------------

def night_animal_hists(win: pd.DataFrame, extent, *, bin_in: float = 8.0,
                       moving_thr_inps: float | None = None) -> dict:
    """
    Occupancy histograms per ``(night, shortid)`` on a shared ``extent``/``bin_in``
    (bin ≥ jitter floor). Returns ``{(night, shortid): {"all": H, "moving": Hm,
    "n": n_fixes, "n_moving": m}}``. ``moving`` uses ``speed_inps_smooth`` when
    present and a threshold is given (path-density proxy).
    """
    out: dict = {}
    have_speed = moving_thr_inps is not None and "speed_inps_smooth" in win.columns
    for (night, tag), g in win.groupby(["night", "shortid"]):
        H, _, _ = w.occupancy_hist(g, extent, bin_in=bin_in)
        rec = {"all": H, "n": int(len(g))}
        if have_speed:
            gm = g[g["speed_inps_smooth"] > moving_thr_inps]
            Hm, _, _ = w.occupancy_hist(gm, extent, bin_in=bin_in)
            rec["moving"] = Hm
            rec["n_moving"] = int(len(gm))
        else:
            rec["moving"] = H
            rec["n_moving"] = int(len(g))
        out[(night, str(tag))] = rec
    return out


def sum_hists(hists: list[np.ndarray]) -> np.ndarray:
    """Elementwise sum of a list of same-shaped histograms (0 if empty)."""
    hists = [h for h in hists if h is not None]
    if not hists:
        return None
    acc = np.zeros_like(hists[0], dtype=float)
    for h in hists:
        acc += h
    return acc


# ---------------------------------------------------------------------------
# 4. Map similarity + spatial-use scalars
# ---------------------------------------------------------------------------

def map_cosine(A: np.ndarray, B: np.ndarray, *, blur_passes: int = 1) -> float:
    """Cosine similarity of two (optionally box-blurred) occupancy maps."""
    if A is None or B is None:
        return np.nan
    a = w._box_blur(A, passes=blur_passes).ravel().astype(float)
    b = w._box_blur(B, passes=blur_passes).ravel().astype(float)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else np.nan


def map_corr(A: np.ndarray, B: np.ndarray, *, blur_passes: int = 1) -> float:
    """Pearson correlation of two flattened (optionally blurred) maps."""
    if A is None or B is None:
        return np.nan
    a = w._box_blur(A, passes=blur_passes).ravel().astype(float)
    b = w._box_blur(B, passes=blur_passes).ravel().astype(float)
    if a.std() <= 0 or b.std() <= 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def occ_entropy(H: np.ndarray) -> float:
    """Normalized Shannon entropy of an occupancy map (0 concentrated .. 1 uniform)."""
    if H is None:
        return np.nan
    c = H[H > 0].astype(float)
    if c.sum() <= 0 or len(c) <= 1:
        return 0.0
    p = c / c.sum()
    return float(-(p * np.log(p)).sum() / np.log(len(c)))


def occupied_area_cells(H: np.ndarray) -> int:
    """Number of occupied cells (a coarse coverage-area proxy)."""
    if H is None:
        return 0
    return int((H > 0).sum())


# ---------------------------------------------------------------------------
# 5. Stabilization curve (per animal, day-to-day)
# ---------------------------------------------------------------------------

def stabilization_table(hists: dict, animals: list[str], nights: list[str], *,
                        which: str = "all", ref_k: int = 2,
                        blur_passes: int = 1) -> pd.DataFrame:
    """
    Per animal × night stabilization metrics on the ``which`` map ("all" or
    "moving"):

    - ``cos_prev`` / ``corr_prev`` — similarity to that animal's *previous*
      populated night (day-to-day reproducibility rising ⇒ stabilizing);
    - ``cos_ref`` / ``corr_ref`` — similarity to a **late-window reference** (mean
      of the animal's last ``ref_k`` populated nights); the reference nights get
      ``cos_ref`` vs the reference itself (≈ upper bound);
    - ``entropy`` and ``area_cells`` per night (spatial-use spread over days).

    ``nights`` must be in chronological order.
    """
    rows = []
    for tag in animals:
        seq = [(n, hists[(n, tag)][which]) for n in nights if (n, tag) in hists]
        if not seq:
            continue
        ref_maps = [H for _, H in seq[-ref_k:]]
        ref = sum_hists(ref_maps)
        for i, (night, H) in enumerate(seq):
            prev_H = seq[i - 1][1] if i > 0 else None
            rows.append({
                "shortid": tag, "night": night, "which": which,
                "n_cells": occupied_area_cells(H),
                "entropy": occ_entropy(H),
                "area_cells": occupied_area_cells(H),
                "cos_prev": map_cosine(H, prev_H, blur_passes=blur_passes),
                "corr_prev": map_corr(H, prev_H, blur_passes=blur_passes),
                "cos_ref": map_cosine(H, ref, blur_passes=blur_passes),
                "corr_ref": map_corr(H, ref, blur_passes=blur_passes),
            })
    return pd.DataFrame(rows)


def stabilization_date(stab: pd.DataFrame, *, metric: str = "cos_ref",
                       plateau_frac: float = 0.9) -> dict:
    """
    Estimate a per-animal stabilization night: the first night whose ``metric``
    (similarity to the late reference) reaches ``plateau_frac`` of that animal's
    own maximum. Returns ``{shortid: night_or_None}``. Descriptive only.
    """
    out: dict = {}
    for tag, g in stab.groupby("shortid"):
        g = g.sort_values("night")
        vals = g[metric].to_numpy()
        nights = g["night"].to_numpy()
        finite = np.isfinite(vals)
        if not finite.any():
            out[tag] = None
            continue
        thr = plateau_frac * np.nanmax(vals)
        hit = np.where(finite & (vals >= thr))[0]
        out[tag] = str(nights[hit[0]]) if hit.size else None
    return out


# ---------------------------------------------------------------------------
# 6. Pooled shared-corridor map + residual individual maps
# ---------------------------------------------------------------------------

def pooled_corridor(all_hists: list[np.ndarray], *, pct: float = 80.0):
    """
    Pooled occupancy over all animals×nights → the paddock "road" map. Returns
    ``(pooled_H, mask, skeleton)`` (mask/skeleton via ``w.corridor_mask`` /
    ``w.skeletonize_mask``). This is the shared environmental-corridor reference.
    """
    pooled = sum_hists(all_hists)
    if pooled is None:
        return None, None, None
    mask, _ = w.corridor_mask(pooled, pct=pct)
    skel = w.skeletonize_mask(mask)
    return pooled, mask, skel


def residual_occupancy(animal_H: np.ndarray, pooled_H: np.ndarray, *,
                       blur_passes: int = 1, eps: float = 1e-9) -> np.ndarray:
    """
    Remove the shared road from an animal's map: return the animal's occupancy
    **divided by the pooled density** (both blurred, each L1-normalized first), so
    a cell where the animal is over-represented *relative to the group's* use of
    that cell stays high, while pure shared-road cells wash out to ~1. Cells the
    group never uses are 0.
    """
    if animal_H is None or pooled_H is None:
        return None
    a = w._box_blur(animal_H, passes=blur_passes).astype(float)
    p = w._box_blur(pooled_H, passes=blur_passes).astype(float)
    a = a / a.sum() if a.sum() > 0 else a
    p = p / p.sum() if p.sum() > 0 else p
    res = np.where(p > eps, a / (p + eps), 0.0)
    return res


def residual_concentration(res: np.ndarray, *, top_frac: float = 0.05) -> float:
    """
    How concentrated is the residual map? Fraction of total residual mass held by
    the top ``top_frac`` of non-zero cells. ~``top_frac`` ⇒ flat (no individual
    preference beyond the road); ≫``top_frac`` ⇒ the animal favours specific
    off-road cells (individual-preference evidence).
    """
    if res is None:
        return np.nan
    v = res[res > 0].astype(float)
    if v.size == 0 or v.sum() <= 0:
        return np.nan
    k = max(1, int(np.ceil(top_frac * v.size)))
    top = np.sort(v)[::-1][:k]
    return float(top.sum() / v.sum())


# ---------------------------------------------------------------------------
# 7. Inter-animal similarity + control battery
# ---------------------------------------------------------------------------

def pairwise_map_similarity(hist_by_animal: dict, *, blur_passes: int = 1,
                            label: str = "raw") -> pd.DataFrame:
    """Pairwise occupancy cosine/corr between animals' pooled maps. ``label`` tags
    the rows (e.g. "raw" or "residual")."""
    tags = sorted(hist_by_animal)
    rows = []
    import itertools
    for a, b in itertools.combinations(tags, 2):
        rows.append({"tag_a": a, "tag_b": b, "kind": label,
                     "cosine": map_cosine(hist_by_animal[a], hist_by_animal[b],
                                          blur_passes=blur_passes),
                     "corr": map_corr(hist_by_animal[a], hist_by_animal[b],
                                      blur_passes=blur_passes)})
    return pd.DataFrame(rows)


def label_permutation_null(night_hists: dict, animals: list[str], nights: list[str], *,
                           which: str = "all", n_perm: int = 200, seed: int = 0,
                           blur_passes: int = 1) -> pd.DataFrame:
    """
    Animal-label permutation null for pairwise map similarity. Each animal's map
    is the sum of its per-night maps; the null shuffles which *(night-map)* belongs
    to which animal (preserving the pool of night-maps and per-animal night counts)
    and recomputes the mean pairwise cosine. Returns one row per permutation stat
    per pair plus the observed value → z / percentile.
    """
    rng = np.random.default_rng(seed)
    # per-animal observed pooled map + list of that animal's night maps
    per_animal_nightmaps = {t: [night_hists[(n, t)][which] for n in nights
                                if (n, t) in night_hists] for t in animals}
    per_animal_nightmaps = {t: v for t, v in per_animal_nightmaps.items() if v}
    tags = sorted(per_animal_nightmaps)
    counts = {t: len(per_animal_nightmaps[t]) for t in tags}
    pool = [H for t in tags for H in per_animal_nightmaps[t]]
    observed = {t: sum_hists(per_animal_nightmaps[t]) for t in tags}

    import itertools
    pairs = list(itertools.combinations(tags, 2))
    obs_cos = {(a, b): map_cosine(observed[a], observed[b], blur_passes=blur_passes)
               for a, b in pairs}

    null = {p: [] for p in pairs}
    for _ in range(n_perm):
        order = rng.permutation(len(pool))
        shuffled = [pool[i] for i in order]
        assigned, k = {}, 0
        for t in tags:
            assigned[t] = sum_hists(shuffled[k:k + counts[t]])
            k += counts[t]
        for a, b in pairs:
            null[(a, b)].append(
                map_cosine(assigned[a], assigned[b], blur_passes=blur_passes))

    rows = []
    for a, b in pairs:
        arr = np.array(null[(a, b)], dtype=float)
        arr = arr[np.isfinite(arr)]
        mu, sd = (float(arr.mean()), float(arr.std())) if arr.size else (np.nan, np.nan)
        obs = obs_cos[(a, b)]
        z = (obs - mu) / sd if sd and np.isfinite(sd) and sd > 0 else np.nan
        pctile = float((arr < obs).mean()) if arr.size else np.nan
        rows.append({"tag_a": a, "tag_b": b, "control": "label_permutation",
                     "observed_cosine": obs, "null_mean": mu, "null_sd": sd,
                     "z": z, "percentile": pctile, "n_perm": int(arr.size)})
    return pd.DataFrame(rows)


# ---- time-resolved coupling (synchronous positions) -----------------------

def sync_grid(win: pd.DataFrame, *, bin_s: float = 2.0) -> pd.DataFrame:
    """
    Synchronous per-tag position grid for time-coupling, built per night so bins
    never bridge a night gap. Reuses ``w.resample_common_grid`` semantics but keys
    the time bin by absolute ``elapsed_s`` so different animals share bins. Adds a
    ``night`` column. Assumes ``win`` already has ``elapsed_s`` and ``night``.
    """
    d = win.dropna(subset=["x", "y", "elapsed_s"]).copy()
    d["tbin"] = np.floor(d["elapsed_s"] / bin_s).astype("int64")
    grid = (d.groupby(["night", "shortid", "tbin"])
              .agg(x=("x", "median"), y=("y", "median"),
                   clock_hour=("clock_hour", "first"))
              .reset_index())
    return grid


def _pair_series(grid: pd.DataFrame, a: str, b: str):
    """Aligned (same tbin) position arrays for two tags across all nights."""
    ga = grid[grid["shortid"].astype(str) == str(a)][["tbin", "x", "y", "clock_hour"]]
    gb = grid[grid["shortid"].astype(str) == str(b)][["tbin", "x", "y"]]
    m = ga.merge(gb, on="tbin", suffixes=("_a", "_b"))
    return m


def pair_time_coupling(m: pd.DataFrame, *, within_r_in: float = 39.37) -> dict:
    """
    Time-coupling scalars for one aligned pair table (from :func:`_pair_series`):

    - ``xy_corr`` — mean of Pearson corr(x_a,x_b) and corr(y_a,y_b) (co-movement);
    - ``mean_dist_in`` — mean synchronous separation;
    - ``frac_within_r`` — fraction of synchronous bins closer than ``within_r_in``
      (default 1 m = 39.37 in, the jitter-floor-safe proximity radius);
    - ``n_bins`` — number of synchronous bins.

    Returns NaNs if too few synchronous bins.
    """
    if len(m) < 10:
        return {"xy_corr": np.nan, "mean_dist_in": np.nan,
                "frac_within_r": np.nan, "n_bins": int(len(m))}
    xa, ya = m["x_a"].to_numpy(), m["y_a"].to_numpy()
    xb, yb = m["x_b"].to_numpy(), m["y_b"].to_numpy()
    cx = np.corrcoef(xa, xb)[0, 1] if xa.std() > 0 and xb.std() > 0 else np.nan
    cy = np.corrcoef(ya, yb)[0, 1] if ya.std() > 0 and yb.std() > 0 else np.nan
    dist = np.hypot(xa - xb, ya - yb)
    return {"xy_corr": float(np.nanmean([cx, cy])),
            "mean_dist_in": float(dist.mean()),
            "frac_within_r": float((dist < within_r_in).mean()),
            "n_bins": int(len(m))}


def circular_shift_null(grid: pd.DataFrame, a: str, b: str, *, n_shuffles: int = 100,
                        min_shift: int = 30, seed: int = 0,
                        within_r_in: float = 39.37) -> dict:
    """
    Time-shuffle (within-animal) null: circularly roll tag ``b``'s time series by a
    random offset (≥ ``min_shift`` bins) and recompute coupling ``n_shuffles``
    times. Real-time coupling should exceed this null; a shared diurnal rhythm or
    shared road (which survives a time shift) should not. Returns observed +
    null summary + z for ``xy_corr`` and ``frac_within_r``.
    """
    rng = np.random.default_rng(seed)
    m = _pair_series(grid, a, b)
    obs = pair_time_coupling(m, within_r_in=within_r_in)
    ga = grid[grid["shortid"].astype(str) == str(a)][["tbin", "x", "y"]].copy()
    gb = grid[grid["shortid"].astype(str) == str(b)][["tbin", "x", "y"]].copy()
    gb = gb.sort_values("tbin").reset_index(drop=True)
    nb = len(gb)
    null_corr, null_prox = [], []
    if nb >= 2 * min_shift and len(m) >= 10:
        for _ in range(n_shuffles):
            k = int(rng.integers(min_shift, nb - min_shift))
            rolled = gb.copy()
            rolled[["x", "y"]] = np.roll(gb[["x", "y"]].to_numpy(), k, axis=0)
            mm = ga.merge(rolled, on="tbin", suffixes=("_a", "_b"))
            c = pair_time_coupling(mm, within_r_in=within_r_in)
            null_corr.append(c["xy_corr"])
            null_prox.append(c["frac_within_r"])
    return _null_pack(a, b, "circular_shift", obs, null_corr, null_prox)


def dayshuffle_null(grid: pd.DataFrame, a: str, b: str, *, seed: int = 0,
                    within_r_in: float = 39.37) -> dict:
    """
    Day-shuffle null: pair tag ``a``'s night with tag ``b``'s **other** nights
    (all mismatched night pairings), breaking real-time synchrony while preserving
    each animal's own spatial/diurnal habits. If observed coupling is real-time it
    exceeds this null; if it is just shared space/rhythm it does not. Uses the
    per-night ``tbin`` offset within each night so mismatched nights still align by
    within-night position.
    """
    # index tbin within each night (position in the night), so nights can be compared.
    # Use groupby.cumcount (not .apply) — robust across pandas versions.
    ga = grid[grid["shortid"].astype(str) == str(a)].sort_values(["night", "tbin"]).copy()
    gb = grid[grid["shortid"].astype(str) == str(b)].sort_values(["night", "tbin"]).copy()
    ga["k"] = ga.groupby("night").cumcount()
    gb["k"] = gb.groupby("night").cumcount()
    nights_a = sorted(ga["night"].unique())
    nights_b = sorted(gb["night"].unique())
    # observed: same night
    obs_corr, obs_prox = [], []
    null_corr, null_prox = [], []
    for na in nights_a:
        sa = ga[ga["night"] == na][["k", "x", "y"]]
        for nb_ in nights_b:
            sb = gb[gb["night"] == nb_][["k", "x", "y"]]
            mm = sa.merge(sb, on="k", suffixes=("_a", "_b"))
            c = pair_time_coupling(mm, within_r_in=within_r_in)
            if not np.isfinite(c["xy_corr"]):
                continue
            if na == nb_:
                obs_corr.append(c["xy_corr"]); obs_prox.append(c["frac_within_r"])
            else:
                null_corr.append(c["xy_corr"]); null_prox.append(c["frac_within_r"])
    obs = {"xy_corr": float(np.nanmean(obs_corr)) if obs_corr else np.nan,
           "frac_within_r": float(np.nanmean(obs_prox)) if obs_prox else np.nan,
           "mean_dist_in": np.nan, "n_bins": len(obs_corr)}
    return _null_pack(a, b, "day_shuffle", obs, null_corr, null_prox)


def _null_pack(a, b, control, obs, null_corr, null_prox) -> dict:
    """Package observed + null (corr & proximity) into a flat record with z-scores."""
    def _stat(obs_v, arr):
        arr = np.array([v for v in arr if np.isfinite(v)], dtype=float)
        if arr.size == 0 or not np.isfinite(obs_v):
            return np.nan, np.nan, np.nan, 0
        mu, sd = float(arr.mean()), float(arr.std())
        z = (obs_v - mu) / sd if sd > 0 else np.nan
        pctile = float((arr < obs_v).mean())
        return mu, sd, z, arr.size
    cmu, csd, cz, cn = _stat(obs["xy_corr"], null_corr)
    pmu, psd, pz, pn = _stat(obs["frac_within_r"], null_prox)
    return {"tag_a": a, "tag_b": b, "control": control,
            "obs_xy_corr": obs["xy_corr"], "null_xy_corr_mean": cmu,
            "null_xy_corr_sd": csd, "z_xy_corr": cz,
            "obs_frac_within_r": obs["frac_within_r"], "null_prox_mean": pmu,
            "null_prox_sd": psd, "z_frac_within_r": pz, "n_null": max(cn, pn)}

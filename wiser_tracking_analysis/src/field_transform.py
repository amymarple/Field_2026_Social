"""
field_transform.py — Georeference the WISER frame to the physical paddock.

Fits and applies a 2-D coordinate transform that maps WISER-native positions
(**inches**, offset origin) onto the surveyed physical field frame the CV
pipeline uses (**centimetres**, origin at corner pole A0; see
``preprocessing/computer_vision/field_coords.py``).

The physically correct model is a **similarity** (rotation + uniform scale +
translation, 4 DoF) — two metric frames differ only by rigid pose and a unit
scale that must come out near ``2.54`` cm/inch. It is fit with the reflection-free
**Umeyama (1991)** closed form and made robust to WISER noise (~7 in median
jitter, occasional gross outliers) by iterative residual-based point rejection.
A full **affine** (6 DoF) is available as a *diagnostic only* — its shear /
anisotropy should be negligible; large values signal a bad survey, not real
non-uniform scaling.

Transforms are stored as a 2x3 affine matrix ``[[a, b, tx], [c, d, ty]]`` acting
on Nx2 points: ``out = [a*x + b*y + tx, c*x + d*y + ty]``. This one representation
covers both the similarity and affine cases, and inverts cleanly.

Pure numpy; no new dependencies. Units are the caller's responsibility — the fit
is unit-agnostic, but by convention ``src`` is WISER inches and ``dst`` is field cm.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

IN_TO_CM = 2.54          # expected similarity scale (cm per WISER inch); sanity target


# ---------------------------------------------------------------------------
# Apply / invert a 2x3 affine
# ---------------------------------------------------------------------------

def apply_transform(matrix, xy) -> np.ndarray:
    """Apply a 2x3 affine ``[[a,b,tx],[c,d,ty]]`` to Nx2 points. Returns Nx2."""
    M = np.asarray(matrix, dtype=float).reshape(2, 3)
    p = np.asarray(xy, dtype=float).reshape(-1, 2)
    x, y = p[:, 0], p[:, 1]
    out_x = M[0, 0] * x + M[0, 1] * y + M[0, 2]
    out_y = M[1, 0] * x + M[1, 1] * y + M[1, 2]
    return np.stack([out_x, out_y], axis=1)


def invert_transform(matrix) -> np.ndarray:
    """Invert a 2x3 affine. Returns the 2x3 matrix of the inverse map."""
    M = np.asarray(matrix, dtype=float).reshape(2, 3)
    A = M[:, :2]
    t = M[:, 2]
    Ainv = np.linalg.inv(A)
    tinv = -Ainv @ t
    return np.hstack([Ainv, tinv.reshape(2, 1)])


def _residuals_cm(matrix, src, dst) -> np.ndarray:
    """Per-point reprojection residual magnitude (same units as ``dst``)."""
    pred = apply_transform(matrix, src)
    return np.hypot(pred[:, 0] - dst[:, 0], pred[:, 1] - dst[:, 1])


def _matrix_scale_rotation(matrix) -> tuple[float, float]:
    """(uniform scale, rotation degrees) implied by a similarity 2x3 matrix."""
    M = np.asarray(matrix, dtype=float).reshape(2, 3)
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    rot_deg = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    return scale, rot_deg


# ---------------------------------------------------------------------------
# Fits
# ---------------------------------------------------------------------------

def fit_similarity(src, dst) -> dict:
    """
    Fit a 2-D similarity (rotation + uniform scale + translation) ``src -> dst``
    with the reflection-free Umeyama closed form. Needs >= 2 correspondences.

    Returns a dict: ``matrix`` (2x3 list), ``scale``, ``rotation_deg``,
    ``translation`` [tx,ty], ``rmse`` (in ``dst`` units), and ``residuals``
    (per-point, in ``dst`` units).
    """
    src = np.asarray(src, dtype=float).reshape(-1, 2)
    dst = np.asarray(dst, dtype=float).reshape(-1, 2)
    n = src.shape[0]
    if n < 2 or dst.shape[0] != n:
        raise ValueError(f"need >=2 matched correspondences, got {n} src / "
                         f"{dst.shape[0]} dst")

    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst

    cov = (dst_c.T @ src_c) / n                       # 2x2
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:      # forbid reflection
        S[-1, -1] = -1.0
    R = U @ S @ Vt
    var_src = (src_c ** 2).sum() / n
    scale = float(np.trace(np.diag(D) @ S) / var_src) if var_src > 0 else 1.0
    t = mu_dst - scale * (R @ mu_src)
    matrix = np.array([[scale * R[0, 0], scale * R[0, 1], t[0]],
                       [scale * R[1, 0], scale * R[1, 1], t[1]]], dtype=float)

    res = _residuals_cm(matrix, src, dst)
    sc, rot = _matrix_scale_rotation(matrix)
    return {
        "matrix": matrix.tolist(),
        "scale": sc,
        "rotation_deg": rot,
        "translation": [float(t[0]), float(t[1])],
        "rmse": float(np.sqrt((res ** 2).mean())),
        "residuals": res.tolist(),
    }


def fit_affine(src, dst) -> dict:
    """
    Fit a full 6-DoF affine ``src -> dst`` by least squares. **Diagnostic only** —
    reports ``shear`` and ``anisotropy`` (|sx-sy|/mean) that should be ~0 for two
    metric frames; non-trivial values indicate a bad survey. Needs >= 3 points.
    """
    src = np.asarray(src, dtype=float).reshape(-1, 2)
    dst = np.asarray(dst, dtype=float).reshape(-1, 2)
    n = src.shape[0]
    if n < 3 or dst.shape[0] != n:
        raise ValueError(f"affine needs >=3 correspondences, got {n}")

    G = np.column_stack([src[:, 0], src[:, 1], np.ones(n)])     # N x 3
    coef_x, *_ = np.linalg.lstsq(G, dst[:, 0], rcond=None)
    coef_y, *_ = np.linalg.lstsq(G, dst[:, 1], rcond=None)
    matrix = np.array([coef_x, coef_y], dtype=float)            # 2 x 3

    A = matrix[:, :2]
    sx = float(np.hypot(A[0, 0], A[1, 0]))
    sy = float(np.hypot(A[0, 1], A[1, 1]))
    # shear angle away from 90 deg between the two mapped basis vectors
    col0 = A[:, 0] / (sx or 1.0)
    col1 = A[:, 1] / (sy or 1.0)
    shear_deg = float(90.0 - np.degrees(np.arccos(np.clip(col0 @ col1, -1, 1))))
    res = _residuals_cm(matrix, src, dst)
    return {
        "matrix": matrix.tolist(),
        "scale_x": sx,
        "scale_y": sy,
        "anisotropy": abs(sx - sy) / (0.5 * (sx + sy)) if (sx + sy) else 0.0,
        "shear_deg": shear_deg,
        "rmse": float(np.sqrt((res ** 2).mean())),
        "residuals": res.tolist(),
    }


def robust_fit_similarity(src, dst, *, k_mad: float = 3.0,
                          floor: float = 0.0, max_iter: int = 5) -> dict:
    """
    Similarity fit robust to WISER noise. Fit, then iteratively drop points whose
    residual exceeds ``max(k_mad * MAD, floor)`` and refit, until stable or too
    few points remain. ``floor`` should be set to the jitter floor (in ``dst``
    units, e.g. ~18 cm) so genuine jitter is never treated as an outlier.

    Returns the :func:`fit_similarity` dict plus ``inlier_mask`` (bool per input
    point), ``dropped`` (indices of rejected points), and ``n_used``.
    """
    src = np.asarray(src, dtype=float).reshape(-1, 2)
    dst = np.asarray(dst, dtype=float).reshape(-1, 2)
    n = src.shape[0]
    mask = np.ones(n, dtype=bool)

    fit = fit_similarity(src, dst)
    for _ in range(max_iter):
        res = np.asarray(_residuals_cm(fit["matrix"], src, dst))
        r_in = res[mask]
        med = float(np.median(r_in))
        mad = float(np.median(np.abs(r_in - med))) * 1.4826      # ~sigma
        thr = max(k_mad * mad, floor)
        new_mask = res <= thr
        # never drop below the 2 points a similarity needs
        if new_mask.sum() < 2:
            break
        if np.array_equal(new_mask, mask):
            break
        mask = new_mask
        fit = fit_similarity(src[mask], dst[mask])

    # residuals reported for ALL input points against the final matrix
    res_all = _residuals_cm(fit["matrix"], src, dst)
    fit["residuals"] = res_all.tolist()
    fit["rmse"] = float(np.sqrt((res_all[mask] ** 2).mean()))
    fit["inlier_mask"] = mask.tolist()
    fit["dropped"] = np.where(~mask)[0].tolist()
    fit["n_used"] = int(mask.sum())
    return fit


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def save_transform(path: Path | str, payload: dict) -> None:
    """Write a transform config JSON (pretty-printed, utf-8)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_transform(path: Path | str) -> dict | None:
    """Load a transform config JSON, or None if absent."""
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))

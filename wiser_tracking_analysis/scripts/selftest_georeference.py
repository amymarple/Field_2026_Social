"""
selftest_georeference.py — offline check of the WISER georeference transform core.

No field data, no DB, no network. Builds a KNOWN similarity (scale ≈ 2.54 cm/in,
a rotation, a translation), synthesises pole correspondences, and confirms:

  1. noise-free  — the fit recovers the transform exactly (rmse ≈ 0; scale/rotation),
                   and apply -> invert round-trips.
  2. noisy       — with ~7 in WISER jitter injected on the source points plus one
                   gross outlier, the robust fit still recovers the scale within
                   tolerance, per-point residuals sit at the injected-noise level
                   (NOT ~0), and the outlier is flagged and dropped.

Run:  python scripts/selftest_georeference.py    (-> prints PASS/FAIL, exit code)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import field_transform as ft   # noqa: E402

IN_TO_CM = 2.54


def _known_matrix(scale_cm_per_in: float, rot_deg: float, tx: float, ty: float):
    th = np.radians(rot_deg)
    c, s = np.cos(th), np.sin(th)
    return np.array([[scale_cm_per_in * c, -scale_cm_per_in * s, tx],
                     [scale_cm_per_in * s,  scale_cm_per_in * c, ty]], dtype=float)


def _field_poles_cm() -> np.ndarray:
    """A well-distributed pole set in field cm (B row + two far corners)."""
    return np.array([
        [0.0, 304.8], [304.8, 304.8], [609.6, 304.8],
        [914.4, 304.8], [1219.2, 304.8],          # centre-line B0..B4
        [0.0, 0.0], [1219.2, 609.6],              # corners A0, C4
        [609.6, 0.0], [609.6, 609.6],             # A2, C2
    ], dtype=float)


def main() -> int:
    rng = np.random.default_rng(20260701)
    ok = True

    # Ground-truth transform: WISER inches -> field cm.
    M_true = _known_matrix(IN_TO_CM, rot_deg=6.5, tx=-812.0, ty=143.0)
    dst_cm = _field_poles_cm()
    # Source WISER-inch points = inverse of the truth applied to the field poles.
    src_in = ft.apply_transform(ft.invert_transform(M_true), dst_cm)

    # ---- 1. noise-free ------------------------------------------------------
    fit = ft.fit_similarity(src_in, dst_cm)
    scale_err = abs(fit["scale"] - IN_TO_CM)
    print(f"[noise-free] rmse={fit['rmse']:.4f} cm  scale={fit['scale']:.5f} "
          f"cm/in  rot={fit['rotation_deg']:.3f} deg")
    if fit["rmse"] > 1e-6 or scale_err > 1e-6 or abs(fit["rotation_deg"] - 6.5) > 1e-4:
        print("  FAIL: did not recover the known transform"); ok = False

    # round-trip apply -> invert
    back = ft.apply_transform(ft.invert_transform(fit["matrix"]),
                              ft.apply_transform(fit["matrix"], src_in))
    if not np.allclose(back, src_in, atol=1e-6):
        print("  FAIL: apply/invert round-trip"); ok = False

    # ---- 2. noisy + one gross outlier --------------------------------------
    jitter_in = 7.0 / 1.4826           # so per-axis sigma gives ~7 in radial median
    noisy = src_in + rng.normal(0.0, jitter_in, size=src_in.shape)
    outlier = 3                        # displace one pole read by ~2 ft (edge/GDOP blunder)
    noisy[outlier] += np.array([24.0, -18.0])

    floor_cm = 7.0 * IN_TO_CM          # ~18 cm jitter floor -> residual acceptance floor
    rfit = ft.robust_fit_similarity(noisy, dst_cm, k_mad=3.0, floor=floor_cm)
    res = np.asarray(rfit["residuals"])
    inlier_med = float(np.median(res[np.asarray(rfit["inlier_mask"])]))
    print(f"[noisy] scale={rfit['scale']:.4f} cm/in  n_used={rfit['n_used']}/"
          f"{len(dst_cm)}  dropped={rfit['dropped']}  inlier median res="
          f"{inlier_med:.2f} cm (floor {floor_cm:.1f})")

    if abs(rfit["scale"] - IN_TO_CM) > 0.15:            # within ~6% of 2.54
        print("  FAIL: scale not recovered under noise"); ok = False
    if outlier not in rfit["dropped"]:
        print(f"  FAIL: gross outlier (idx {outlier}) not flagged"); ok = False
    if inlier_med < 1.0:
        print("  FAIL: residuals implausibly ~0 (noise should show at the floor)"); ok = False
    if inlier_med > floor_cm * 2.5:
        print("  FAIL: inlier residuals far exceed the jitter floor"); ok = False

    # affine diagnostic should see negligible shear/anisotropy on inliers
    afit = ft.fit_affine(noisy, dst_cm)
    print(f"[affine diag] shear={afit['shear_deg']:.2f} deg  "
          f"anisotropy={afit['anisotropy']:.3f}")

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

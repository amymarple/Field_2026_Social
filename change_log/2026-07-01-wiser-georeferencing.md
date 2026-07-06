# Change log — WISER frame → physical paddock georeferencing (tooling)

**Date:** 2026-07-01
**Commit:** uncommitted at time of writing.
**Plan:** [implementation_plan/2026-07-01-wiser-georeferencing.md](../implementation_plan/2026-07-01-wiser-georeferencing.md)
**Status tracker:** [wiser_tracking_analysis/ANALYSIS_STATUS.md](../wiser_tracking_analysis/ANALYSIS_STATUS.md) (P0 blocker).

## What changed

Built the tooling to georeference the WISER native-inch frame into the CV pipeline's surveyed
physical field frame (cm, origin at pole A0). This is the #1 (P0) blocker in the status tracker —
it gates every spatial WISER claim (wall/thigmotaxis, route-vs-boundary) and WISER×CV
cross-validation.

- **New `wiser_tracking_analysis/src/field_transform.py`** — pure-numpy transform core:
  `fit_similarity` (reflection-free Umeyama, rotation + uniform scale + translation),
  `robust_fit_similarity` (iterative MAD-based outlier rejection with a jitter-floor residual
  floor), `fit_affine` (6-DoF diagnostic → shear/anisotropy), `apply_transform`,
  `invert_transform`, `save_transform`/`load_transform`. Transform stored as a 2×3 affine
  (WISER inch → field cm).
- **New `wiser_tracking_analysis/scripts/georeference_wiser.py`** — CLI: reads the pole survey,
  resolves each pole's physical cm via `field_coords.resolve_landmark`/`field_layout.json`,
  extracts each dwell's **validity-filtered median** WISER (x,y) read-only from the DB (reusing
  `wiser_io.load_sqlite_window` + `time_utils` + `add_speed`/`add_validity_flags`), robustly fits
  the similarity, writes `configs/wiser_to_field_transform.json` (matrix, scale, rotation,
  per-point residuals, correspondences, QC, `confirmed`), and a validation overlay PNG. Manual
  `wiser_x,wiser_y` per row bypass the DB read.
- **New `wiser_tracking_analysis/scripts/selftest_georeference.py`** — offline verification (no
  DB/field data).
- **New `wiser_tracking_analysis/configs/wiser_georef_survey.csv`** — survey input template
  (commented examples; parses to zero rows until filled).
- **Edited `wiser_tracking_analysis/src/wiser_analysis_utils.py`** — added `load_field_transform`
  (returns `None` unless a `confirmed` transform exists), `apply_field_transform`
  (adds `x_field_cm,y_field_cm`), `verified_boundary_in_wiser` (inverse-maps the A0–C4 rectangle
  into WISER inches as a confirmed boundary for `add_validity_flags`/`distance_to_edge`/
  `thigmotaxis_index`). All strictly guarded — no-ops until a confirmed transform exists.
- **Edited `wiser_tracking_analysis/.gitignore`** — ignore `outputs/*.png` (the QC overlay is
  derived data).
- **Docs:** new implementation plan + index row; README georeferencing section; ANALYSIS_STATUS
  P0 blocker/next-step/config-inventory updates; data-manifest `georeference_*` status.

## Why

The WISER frame had an unverified offset origin (`data_manifests/2026-06-29-wiser-pilot.yaml`
`georeferenced_to_paddock: false`), and the only prior "ground truth" was self-referential
(median of a tag's own estimates), so it could not anchor an absolute frame. A surveyed
correspondence + a fitted transform is required.

## Design notes — WISER noise is the governing constraint

~7 in median jitter (p95 ~15 in), ~2% impossible-jump/dropout fixes, worst at edges/corners
(geometric dilution). The transform fixes only the *frame*; per-fix noise is unchanged, so
downstream spatial claims stay gated by the ~7 in floor and fit residuals bottom out at ~18 cm,
not zero. Handled by: validity-filtered long-dwell medians, MAD-based cross-point outlier
rejection with the residual floor tied to the jitter floor, ≥6 well-distributed survey points,
and QC framed relative to the floor (not ~0). Inch units and all validated inch thresholds
(7 in jitter, 12.5 in/s speed, inch ROIs) are preserved; field-cm is additive.

## Verification

- `python scripts/selftest_georeference.py` → **PASS**: noise-free case recovers a known
  similarity exactly (rmse 0.0000 cm, scale 2.54000 cm/in, rot 6.500°); noisy case (~7 in jitter +
  one gross outlier) recovers scale 2.5409 cm/in, drops the outlier (idx 3), inlier median
  residual 8.09 cm (below the 17.8 cm floor); affine shear 1.97°, anisotropy 0.060.
- CLI end-to-end on a synthetic manual survey (9 poles, ~7 in noise + 1 outlier; run off-repo):
  recovered scale 2.557 cm/in, rot 6.47°, dropped the outlier, all QC gates passed, `confirmed:true`,
  wrote the transform JSON + overlay PNG. (Verified with anaconda base Python 3.13; the WISER
  modules require ≥3.10 — the `cv` env is on the field PC.)
- Non-regression: with no confirmed transform, `load_field_transform()` returns `None` and the
  helpers are no-ops, so existing analyses are unchanged.

## Known limitations / next steps

- **Awaiting the field survey.** No `confirmed` transform exists yet; run the pole-dwell survey to
  produce one. Until then the frame stays unverified and spatial claims keep their caveat.
- The verified boundary is an axis-aligned bound of the (possibly rotated) paddock; edge/thigmotaxis
  bands should stay ≥ the jitter floor thick.
- Drivers currently *can* opt into the verified boundary via the new helpers; wiring it as the
  default in `analyze_route_structure.py` / `analyze_nightly_behavior.py` is deferred until a
  confirmed transform exists (keeps non-regression trivial).
- Full re-expression of the pipeline in cm is out of scope (future work).

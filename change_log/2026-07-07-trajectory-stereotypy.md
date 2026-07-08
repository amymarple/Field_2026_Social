# Change log — Trajectory stereotypy, stabilization & inter-animal correlation (Phase A)

**Date:** 2026-07-07
**Status:** ⚠️ candidate (Phase A implemented, self-tested, run on all 8 nights; audited). Phase B
(DTW/Fréchet route motifs + leader-follower lag) deferred pending review.
**Plan:** [implementation_plan/2026-07-07-trajectory-stereotypy.md](../implementation_plan/2026-07-07-trajectory-stereotypy.md)

## What was added

- **New module** `wiser_tracking_analysis/src/trajectory_stereotypy.py` — a thin analysis layer on
  `wiser_analysis_utils` (does not modify that ~3700-line module): multi-day incremental-backup
  loader with dedup, a cross-midnight night window, per-night per-animal occupancy maps, a
  day-to-day stabilization curve, a pooled shared-corridor map + residual individual maps, and the
  control battery (animal-label permutation, residual/shared-density expectation, circular-shift
  time-shuffle null, day-shuffle null, synchronous time-coupling).
- **New driver** `wiser_tracking_analysis/scripts/analyze_trajectory_stereotypy.py` (Phase A).
- **New self-test** `wiser_tracking_analysis/scripts/selftest_trajectory_stereotypy.py` (offline,
  synthetic, exit-coded) — PASS.
- Outputs → `wiser_tracking_analysis/outputs/trajectory_stereotypy_2026-06-28_to_2026-07-06/`.

## Method (Phase A)

Nights 2026-06-28 → 07-05, night window **21:00–05:00 EDT**, 5 core animals (Sova/12409 removed
2026-06-29 15:00, excluded; tunnel ROI auto-expires 2026-06-29 07:00). Pipeline: multi-day dedup
load → `convert_timestamps` → `add_speed` → `add_validity_flags` → `apply_tag_cutoffs` →
cross-midnight night window. Then per-night per-animal occupancy/path-density maps, stabilization
similarity (consecutive-night + vs a late-window reference), a pooled corridor "road" map, residual
individual maps (animal occupancy ÷ pooled density), and inter-animal similarity with the shuffle/
permutation control battery. Working **jitter floor = documented ~7 in median (p95 ~15 in)**; the
transferred `tag_reports_2026-06-30` baseline measures per-tag p50 ~3.4 in / p95 ~14.7 in (precision-
optimistic — reported as a detail, not cited as the floor). Occupancy bin 8 in (≥ floor).
Weather/fireworks/truncation flagged, not regressed out.

## Load-correctness finding (important)

The daily `*.csv.gz` incremental backups **overlap** (`06-30` is a cumulative snapshot dump that
already contains 06-28/06-29; `07-01…` are true increments). Deduping must use the composite key
**`(shortid, ts_raw, x, y)`**, NOT `reportid`: verified that `reportid` is per *report cycle* and is
shared across different animals' fixes (82k reportid groups span multiple `shortid`), so deduping on
`reportid` would silently drop ~94k distinct fixes. With the composite key the 9-file load yields
**12,459,676 unique fixes** (matches the 07-06 snapshot total 12,459,691 to within 15 rows);
2,235,668 backfill duplicates removed. Logged in `cleaning_log.md`.

## Key results (candidate)

- **Stabilization: yes.** Mean occupancy similarity-to-late-reference rises **0.14 → 0.96** from the
  06-28 release night to 07-05; big jump by 06-29, a transient dip ~07-02, convergence to ~0.95+ by
  07-04/07-05. `gap_frac` stays <1.5% on every night (incl. wet nights) so the curve is **not**
  dropout-driven. **Classify: behavioral (spatial reuse), not proof of memory.**
- **Shared vs individual: mostly SHARED.** Raw inter-animal occupancy cosine ~0.90 and ROI-edge
  cosine ~0.95, but **residual Pearson collapses to ~ −0.01** after dividing out the pooled corridor,
  and animal-label permutation shows **0/10 pairs above the shared-pool null**, **4/10 below** (all
  four are Dormi pairs → Dormi is the one mild individual outlier). **Classify: shared-road /
  environment is the primary driver; individual route habit weak/candidate (Dormi only).**
- **Real-time coupling: present but environmental, not dyadic.** All 10 pairs beat the circular-shift
  null on synchronous xy-correlation and proximity (z≈4–5), but **0/10 beat the day-shuffle null**
  (which preserves each animal's diurnal/spatial habit). Uniform across pairs, no standout dyad ⇒
  common-drive/shared-road, **not** specific social following. **Classify: mixed → environment.**
- **Caveats:** the top ROI-transition edges (house↔food) are an artifact — `food_1/2` are co-located
  with `house_1/2` in the inch frame (jitter flips, not travel); trust house↔house / house↔refuge
  edges. Night 07-05 truncated (~25% fewer fixes). 07-04 fireworks excluded from time-coupling. All
  spatial structure is in the **UNVERIFIED inch frame** — no directional/physical claims.

## Verification

- `python scripts/selftest_trajectory_stereotypy.py` → PASS (loader dedup on the composite key incl.
  a shared-reportid multi-tag survivor; cross-midnight night labeling; residual test collapses a
  planted shared road while a planted individual survives; stabilization curve rises; a planted
  coupled pair beats the circular-shift and day-shuffle nulls while an independent pair does not).
- Full run on all 8 nights (~3 min in the `cv` env; Python 3.11 / pandas 3.0 / numpy 2.4). Coverage
  cross-checked against `Wiser_backup/backup_log.txt` (unique-fix total matches the 07-06 snapshot).
- Audited by the `wiser-measurement-auditor` subagent →
  `outputs/audit/wiser_audit_trajectory_stereotypy_2026-06-28_to_2026-07-06.{md,json}`. Verdict:
  *partially auditable / weaker provenance than CV, but a disciplined run — headline supported, no
  over-claim.* Every number reproduced from the CSVs (residual Pearson −0.0147; label-perm 0/10 above,
  4/10 below, all Dormi pairs; circular-shift 10/10; day-shuffle 0/10 proximity). Its two text fixes
  (cite the documented ~7 in floor not the measured 3.39; day-shuffle 2/10 marginal on xy-corr) are
  applied above. Provenance gaps it flagged: no per-run `measurement_context` sidecar / per-row
  `mc_run_id` (WISER-wide, not this run); `calculation_error`/`battery_voltage` loaded but not gated.

## Follow-ups (Phase B, deferred)

DTW/Fréchet route-motif clustering (validated vs the displacement-matched jitter null); leader-
follower lead/lag via the `following_*` suite on non-fireworks nights; gradual-corridor (trampled-
road) emergence per night. Needs `scipy` in the run env (absent from `cv` today).

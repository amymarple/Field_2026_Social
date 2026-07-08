# Implementation plan — Trajectory stereotypy, stabilization & inter-animal correlation

**Date:** 2026-07-07
**Status:** planned (Phase A first; Phase B deferred behind Phase-A review).

## Goal and motivation

Determine, across the first ~9 days in the paddock (**nights 2026-06-28 → 2026-07-05**), whether the
tagged rats develop **stereotypical trajectories / repeated route motifs**, whether those routes
**stabilize** over days, and — once stable — whether animals' trajectories are **correlated**. The
scientific crux is keeping three competing explanations *separate*, with quantitative evidence for
each rather than one conflated story:

1. **Individual route habit / memory** — an animal reuses its own route; consistency survives after
   controlling for global occupancy + shared corridors.
2. **Social coupling** — two animals' trajectories correlate in time; one follows another at a lag;
   correlation exceeds shuffled controls.
3. **Environmental corridor / "road" effect** — shared paths are just physically convenient, so
   apparent similarity is a shared road, not memory or following.

This extends the existing single-window route-structure analysis (which already found cross-rat edge
similarity ~0.88 > within-rat night-to-night ~0.35) to **per-day stabilization across all 9 days**
plus the user's explicit control battery.

## Scope (confirmed with user)

- **Window:** night-active **~21:00–05:00 EDT**, night *N* = local date *D* 21:00 → *D+1* 05:00.
- **Staged:** **Phase A** (core stabilization + shared-road controls) ships an intermediate report;
  **Phase B** (DTW/Fréchet motifs + following-lag) only after Phase A. Motif clustering must not
  block the first result.

## Governing constraints — WISER noise & regime (from the regime-aware-wiser-tracking skill)

- **Inch frame is UNVERIFIED** (no georeference) → no directional/physical claims. ROI *membership*
  is usable (the actual `wiser_rois.json` has `confirmed:true` per ROI in the inch frame), so motif
  endpoints may be labeled house/food/water/refuge but **flagged provisional**.
- **Jitter floor ~7 in** → occupancy/similarity bins ≥ floor; raw path length is jitter-inflated
  (reuse the displacement-matched null for any straightness/motif claim); proximity/following ≥ 1 m.
- **Gaps ≠ absence** → mark gaps; no interpolation; quantify dropout fraction per animal per night.
- **Pooling boundaries** → Sova (12409) removed 2026-06-29 15:00; tunnel removed 2026-06-29 07:00.
- **Weather on both paths** → rain nights 06-30 / 07-01 / 07-04 attenuate UWB *and* change behavior;
  never regress it out; check whether shared-corridor signal concentrates in dropout-heavy strata.
- **07-04 fireworks** drove a group movement/following spike — stratify out of the social analysis.
- Every result classified: behavioral · measurement artifact · mixed · lower-bound only.

## Data

- **Read-only** incremental backups `D:\Reolink_record\audio_in\Wiser_backup\incremental\1stcohort_2026_<date>.csv.gz`
  (all 9 days present). `06-30.csv.gz` is a **cumulative** dump through 06-30; `07-01`…`07-06` are
  true daily increments. **Loader dedups on `reportid`** (present in the raw schema) and assigns each
  fix to a night by **local** timestamp — robust to the overlap. Verified col schema includes
  `reportid, shortid, calculation_error, location_x/y/z, anchors_used, timestamp (unix-ms),
  battery_voltage`.
- **Stationary baseline** (jitter floor + nulls): `D:\Wiser\data\tag_reports.sqlite` if reachable
  else transferred `…\Wiser_backup\snapshots\tag_reports_2026-06-30.sqlite`; fall back to the
  documented ~7 in floor if neither loads.
- **Weather:** `D:\Reolink_record\audio_in\weather_data\AWN-*.csv` (wet-night flag + dropout check).
- **Env:** Anaconda3 base — Python 3.9.7, pandas 1.3.4, numpy 1.20.3, scipy 1.7.1, matplotlib 3.4.3.

## Affected files

- **New module** `wiser_tracking_analysis/src/trajectory_stereotypy.py` — new analysis helpers only
  (keeps the ~3700-line `wiser_analysis_utils.py` untouched to avoid regression):
  multi-day dedup loader, `select_night_window` (cross-midnight wrapper), per-night per-animal map
  builder, `stabilization_curve`, `pooled_corridor` + `residual_occupancy`, and the control battery
  (`shuffle_null`, `daylabel_permutation_null`, `shared_density_expectation`, `time_of_day_control`).
- **New driver** `wiser_tracking_analysis/scripts/analyze_trajectory_stereotypy.py` (Phase A;
  Phase B section added later).
- **New self-test** `wiser_tracking_analysis/scripts/selftest_trajectory_stereotypy.py`.
- Docs: this plan + index; change log + index; `ANALYSIS_STATUS.md` row (and fix the stale line-30
  ROI status in the same edit).

## Method — Phase A

Pipeline mirrors the nightly drivers: multi-day dedup load → `convert_timestamps` → `add_speed` →
`add_validity_flags` → `apply_tag_cutoffs` → `select_night_window`. Then, on cleaned night fixes:

1. **Coverage** (`coverage_summary.csv`): per animal × night — n_fixes, valid_frac, mean_anchors,
   gap/dropout_frac, median_dt, n_rats_present, wet/fireworks/truncated flags.
2. **Cleaning log** (`cleaning_log.md`): dedup counts (dupes removed = double-count proof), every
   threshold (`min_anchors`, jump, `gap_factor`, `smooth_window`, jitter_floor_in), per-flag drop
   fractions. Gaps marked; no interpolation.
3. **Per-day maps** (`plots/`): occupancy heatmap, path-density (box-blurred) map, speed
   distribution, route entropy (`route_reuse_index.occ_entropy`), transition matrix (spatial-bin→bin
   and ROI→ROI). Shared fixed extent + bin ≥ jitter floor across all days.
4. **Stabilization** (`stabilization_metrics.csv` + plots): per animal, occupancy/path-density
   similarity (cosine + Pearson) consecutive-night and vs a late-window reference (mean of last 2–3
   nights); stabilization-date estimate = first plateau; entropy/coverage-area over days; wet vs dry
   stratified.
5. **Pooled corridor** (`pooled_corridor_map.*`): sum occupancy over all animals×nights →
   `corridor_mask` + `skeletonize_mask`; also per-night to test the trampled-road (gradual-corridor)
   hypothesis.
6. **Residual individual maps** (`residual_individual_maps/`): per-animal occupancy with pooled
   density divided out; residual concentration scalar = evidence of individual preference beyond the
   road.
7. **Inter-animal similarity + controls** (`pairwise_similarity_matrix.csv`,
   `shuffled_controls.csv`): pairwise occupancy/path-density/transition (`edge_usage_similarity`)
   cosine; then per-pair null via time-shuffle (circular), day-shuffle, animal-label permutation,
   shared-density expectation, and time-of-day control (bin by clock hour). z / percentile per pair.

**Phase A intermediate report** (`trajectory_stereotypy_report.md`) answers, cautiously, tagging each
answer behavioral/artifact/mixed/lower-bound and naming which figures to trust: (1) do trajectories
stabilize and when; (2) are stabilized routes shared or animal-specific; (3) does inter-animal
similarity survive the controls.

## Method — Phase B (deferred)

8. **Motifs:** `movement_bouts` → resample bouts to fixed length → DTW cluster (scipy) with
   Hausdorff/Fréchet cross-check; top motifs, users, days, frequency-over-time; validate each vs the
   displacement-matched jitter null. `route_motif_summary.csv` + `motif_plots/`.
9. **Following:** reuse the `following_*` suite (grid/scores/peaks/asymmetry/circular-shift null/
   events) at R = `follow_radius_in`; fireworks window stratified out. `following_lag.csv` + plots.

## Assumptions / non-goals

- Units stay **inches**; no field-cm (transform absent). ROI labels provisional. Candidate/
  exploratory — not a promotion to confirmed.
- No modification of `wiser_analysis_utils.py` or the live DB; source data read-only.
- Phase B DTW O(N²) → subsample per animal/night with a logged cap (never silent truncation).

## Verification

- `python scripts/selftest_trajectory_stereotypy.py` — PASS: synthetic planted-shared-corridor +
  one planted-individual-route + ~7 in jitter; assert coverage counts, controls collapse the
  planted-shared similarity toward null while the individual residual survives, stabilization curve
  rises as the route is reinforced.
- Real-data smoke on 2 nights; cross-check coverage fix counts vs `Wiser_backup\backup_log.txt`.
- Sanity gates: label-permutation null centered on no-structure; shared-density explains most
  cross-animal similarity iff the road dominates (matches the prior 0.88 vs 0.35); wet-night
  dropout_frac elevated on 06-30/07-01/07-04.
- Dispatch the `wiser-measurement-auditor` subagent on the Phase-A output dir before promoting.
- Full Phase A run on all 8 nights → user review → Phase B.

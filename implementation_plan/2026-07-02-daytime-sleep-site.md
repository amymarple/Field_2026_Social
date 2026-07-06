# Implementation plan — Direction 3: daytime sleep/rest-site & its change

**Date:** 2026-07-02
**Status:** implemented + self-tested; awaiting a run on real rest-day data.

## Goal and motivation

One of the three current WISER research directions: characterise **where each animal rests
during the daytime rest period (05:00–21:00 local)** and how that site changes **within a day**
(morning→afternoon drift) and **across days**. Rats are nocturnal (active ~21:00→~05:00), so the
daytime block is the sleep/rest phase; the nightly analyses (Directions 1–2) already cover the
active phase.

## Current problem

No WISER analysis addressed the daytime rest phase. `infer_candidate_zones` finds *group*
high-occupancy clusters but nothing assigned a **per-animal** primary rest site, tracked its
day-to-day stability, or looked within a day. The nightly drivers are night-window only.

## Governing constraint — WISER noise

~7 in median jitter. Rest *sites* are distinguishable only when separated by ≫ the floor: the two
shelters are ~5 ft apart (robust), sub-shelter distinctions are not. "Sleep" is a **low-speed
proxy** (smoothed speed < the stationary p99 noise floor), NOT ephys-validated — the CV shelter
cams (CH05/CH06) are the intended cross-check (a follow-up). Site shifts are reported against the
jitter floor (a "real relocation" = > 3× floor).

## Affected files

- **New functions** in `wiser_tracking_analysis/src/wiser_analysis_utils.py`:
  `rest_mask`, `daytime_primary_site`, `rest_site_stability`, `intraday_site_drift`
  (+ helpers `_cosine`, `_peak_cell_center`).
- **New driver** `wiser_tracking_analysis/scripts/analyze_daytime_sleep_site.py`.
- **New self-test** `wiser_tracking_analysis/scripts/selftest_daytime_sleep_site.py`.
- Docs: this plan + index; change log + index; `ANALYSIS_STATUS.md` inventory row.

## Method

1. Rest window via `select_route_window(clock_start=5, clock_end=21)` (labels by local date
   `night`, adds `clock_hour`; no midnight cross). Pipeline mirrors the nightly driver:
   rich load → `convert_timestamps` → `add_speed` → `add_validity_flags` → `apply_tag_cutoffs`
   (Sova/12409 removed). Rest cutoff = p99 of the **stationary baseline** smoothed speed
   (`speed_noise_floor`); jitter floor from `metrics.compute_summary`.
2. `rest_mask` flags resting fixes (smoothed speed < the cutoff).
3. `daytime_primary_site` — per (night, shortid): dominant box-blurred occupancy cell of resting
   fixes over a **shared extent** → `site_x, site_y`; `site_concentration` = frac of that day's
   rest fixes within `site_radius_in` (24 in) of the site; optional `site_x_field_cm/…` (confirmed
   transform) and `site_roi` (ROIs, provisional). Returns per-day occupancy maps for cosine.
4. `rest_site_stability` — consecutive-day `site_shift_in` + per-tag day-to-day occupancy
   `occ_cosine`.
5. `intraday_site_drift` — primary site per (night, shortid, block) for blocks
   `((5,11),(11,15),(15,21))`; `shift_from_prev_in` vs the last populated block (survives an empty
   block).

## Inputs / outputs

- Inputs: `D:\Wiser\data\1stcohort_2026.sqlite` (live, read-only), `tag_reports.sqlite`
  (baseline), `configs/fixed_position_ground_truth.csv`, `configs/wiser_rois.json` (provisional),
  and — when confirmed — `configs/wiser_to_field_transform.json`.
- Outputs to `D:\Wiser_plot\daytime_sleep_site_YYYYMMDD_HHMM\`: `daytime_primary_site.csv`,
  `rest_site_stability.csv`, `intraday_drift.csv`, `daytime_qc.csv`, figures `S1_rest_sites` /
  `S2_across_day_shift` / `S3_intraday_drift`, `run_manifest.json`, `sleep_site_conclusion.txt`.

## Assumptions / non-goals

- Units stay **inches**; field-cm is additive only when a confirmed georeference transform exists;
  ROI names are provisional until `wiser_rois.json` is confirmed.
- Sleep = low-speed proxy, not ephys/CV-validated (CV cross-check deferred).
- No new statistical modelling; descriptive site + shift metrics. Candidate/exploratory.

## Verification

- `python scripts/selftest_daytime_sleep_site.py` — PASS (rest_mask; primary-site recovery within
  jitter; stable ~0 vs relocated large shift + cosine ordering; within-day move detected across an
  empty midday block).
- End-to-end on a synthetic 2-tag/2-day DB (off-repo): sites, stability (stable 4 in vs relocated
  636 in), intraday afternoon shift 364 in, all CSVs/figures/manifest produced.

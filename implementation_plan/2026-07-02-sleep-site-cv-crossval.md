# Implementation plan — WISER ↔ CV shelter cross-validation (Direction 3 follow-up)

**Date:** 2026-07-02
**Status:** planned; to implement + run on 6/29–6/30.

## Goal and motivation

Cross-validate the WISER daytime sleep-site inference (`analyze_daytime_sleep_site.py`) against the
independent CV shelter-occupancy signal (`preprocessing/computer_vision/shelter_sleep.py`): when
WISER places rats resting inside a shelter ROI, does the shelter camera report the shelter
occupied? This is the primary follow-up needed to move Direction 3 from "candidate" toward a
validated finding, because WISER "sleep" is only a low-speed proxy in an unverified frame.

## Current problem

The two modalities have never been compared. WISER (UWB, whole paddock) and CV (per-shelter camera
through IR glass) are independent sensors of the same event; agreement would corroborate the
sleep-site claim, disagreement would bound it. Nothing joins them today.

## Inputs (all present on the analysis PC)

- WISER snapshot `D:\Reolink_record\audio_in\Wiser_backup\snapshots\1stcohort_2026_2026-07-01.sqlite`
  (+ `tag_reports_2026-06-30.sqlite` baseline for the moving/rest threshold).
- CV `preprocessing/computer_vision/outputs/CH05_sleep_{2026-06-29,2026-06-30}.csv` (left shelter)
  and `CH06_sleep_*` (right shelter). Columns: `t, state, n_inside_estimated, view_quality_inside,
  usable_for_headline_summary, usable_for_coarse_activity, …`.
- Shelter ROIs `house_1`,`house_2` in `wiser_tracking_analysis/configs/wiser_rois.json` (placed).

## Governing constraints

1. **Clocks unverified.** WISER = naive **UTC**; CV `t` = naive **local NVR wallclock** (filename).
   `t_utc(CV) = t + |LOCAL_TZ_OFFSET_HOURS| h` (+4), then **scan a residual lag** and report the
   best-fitting offset. Label everything *timestamp-aligned, unverified*.
2. **ROI↔camera mapping is a hypothesis** (CH05=left, CH06=right; `house_1` x≈411 < `house_2`
   x≈613). Test **both** orientations; the better-fitting one is the inferred mapping.
3. **CV sees only the two shelters** → validates only shelter-resident WISER rest, not the rest
   that occurs elsewhere.
4. **CV undercounts** (huddles + wall-edge blind zone) → **occupancy boolean is primary**;
   head-count concordance is secondary / lower-bound.
5. **view_quality strata:** headline = clear only (`usable_for_headline_summary`); degraded =
   coarse (reported separately); `unusable`/`indeterminate` dropped. Report n_bins with every kappa.
6. Dates: **6/29, 6/30** only.

## Design

New functions in `src/wiser_analysis_utils.py`:
- `load_cv_shelter_sleep(paths)` → tidy CV frame with `t_utc`.
- `wiser_shelter_presence(win, roi_cfg, shelter_names, *, bin_s=60, resting_only=False)` → per UTC
  bin × shelter: distinct-rat count + `occupied` bool (reuse `_point_in_rect`).
- `cohen_kappa(a_bool, b_bool)` → agreement statistic (numpy).
- `best_lag_agreement(wiser_occ, cv_occ, *, lag_grid_s)` → lag maximizing kappa + kappa/agreement
  curve, for one shelter↔camera pair.

Driver `scripts/analyze_sleep_site_cv_crossval.py`: WISER load → speed/flags/cutoffs →
`select_route_window(5,21)` → `wiser_shelter_presence`; CV load; for both mappings run
`best_lag_agreement` over `range(-600,601,30)` s; pick best clear-view mapping+lag; write per
shelter/day/stratum 2×2 agreement + kappa + % agreement + head-count concordance; figures
(occupancy overlay + kappa-vs-lag); manifest + verdict → `D:\Wiser_plot\sleep_site_cv_crossval_*`.

Self-test `scripts/selftest_cv_crossval.py`: synthetic known-lag noisy copy for the correct camera
+ unrelated series for the other → assert lag recovery, mapping pick, kappa sanity.

## Assumptions / non-goals

- Boolean occupancy is the headline; counts are lower-bound context.
- Lag is reported, not trusted as a verified sync.
- Not validating non-shelter rest; not a physiological sleep validation (spatial co-occupancy).
- No georeference dependency (mapping test substitutes); a confirmed transform would tighten ROI
  membership later.

## Verification

- Offline self-test PASS.
- Real run on 6/29–6/30 prints best mapping + lag (unverified) + clear-view kappa/agreement per
  shelter/day (+ n_bins) + head-count concordance; overlay + kappa-vs-lag figures spot-checked.
- Outputs only to `D:\Wiser_plot`; no writes to source DBs or CV outputs.

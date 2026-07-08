# Change log — WISER ↔ CV shelter cross-validation (Direction 3 follow-up)

**Date:** 2026-07-02 (run 2026-07-03)
**Commit:** uncommitted at time of writing.
**Plan:** [implementation_plan/2026-07-02-sleep-site-cv-crossval.md](../implementation_plan/2026-07-02-sleep-site-cv-crossval.md)
**Tracker:** [wiser_tracking_analysis/ANALYSIS_STATUS.md](../wiser_tracking_analysis/ANALYSIS_STATUS.md)

## What changed

New cross-modal analysis: does WISER "rat inside a shelter ROI" agree with the CV shelter
camera's occupancy call? Validates the Direction-3 sleep-site inference against an independent
sensor.

- **`src/wiser_analysis_utils.py`** — added `load_cv_shelter_sleep` (schema-tolerant CV loader),
  `wiser_shelter_presence` (per-UTC-bin distinct-rat count inside each shelter rect),
  `cohen_kappa`, and `best_lag_agreement` (clock-lag scan). `CV_OCCUPIED_STATES` constant.
- **`scripts/analyze_sleep_site_cv_crossval.py`** — new driver: tests both ROI↔camera mappings,
  scans a **shared** clock lag maximizing joint (bin-weighted) kappa across both shelters, and
  writes per shelter/day/stratum agreement + kappa-vs-lag and occupancy-overlay figures +
  manifest + verdict to `D:\Wiser_plot\sleep_site_cv_crossval_*`.
- **`scripts/selftest_cv_crossval.py`** — offline synthetic verification.

## Findings (dates 6/29–6/30, exploratory)

- **The nominal ROI↔camera mapping is confirmed:** mapping A (house_1↔CH05/left,
  house_2↔CH06/right) gives joint Cohen's κ = **0.66** vs 0.33 for the swapped mapping — so the
  WISER `house_1`/`house_2` ROIs and the camera assignment (and the WISER frame *orientation*)
  are consistent.
- **Clocks line up at ~0 residual:** the joint κ-vs-lag curve peaks sharply at **lag 0 s** on top
  of the +4 h nominal (EDT = UTC−4), i.e. the NVR wallclock ≈ EDT to within the 300 s scan grid.
  Still reported as *timestamp-aligned, unverified* (inferred from occupancy co-variation, not a
  shared event).
- **Strong agreement on 6/29:** house_1/CH05 κ = **0.82** (93 % raw agreement, n = 791),
  house_2/CH06 κ = **0.68** (85 %, n = 794) — WISER shelter presence and CV occupancy track each
  other well on the day with usable glass.
- **6/30 is a complementary-failure case, not a contradiction:** WISER shows all 5 rats sheltering
  in house_1 essentially all day (wet day → huddling), while CV CH05 mostly reads *empty* — CV is
  **missing rats through degraded/rainy glass + the wall-edge blind zone** (documented CV limits).
  So κ→0 there from lack of contrast + CV false-empties; on the wet day WISER is the more reliable
  sensor. CH06 6/30 had almost no CV data.
- Head-counts: when both say occupied, CV `n_inside` (~1.5–2.5) ≤ WISER n-rats (~1.9–3.2),
  consistent with CV undercounting (huddles + blind zone) — occupancy boolean is the headline;
  counts are a lower bound.

## Design / bugs found during the real run

- **CV schema heterogeneity:** the 6/29 CV CSVs are an older `shelter_sleep.py` vintage
  (`channel,file,t,n_rats,roi_motion,state`, **hyphenated** states, no glass QC) vs the current
  15-column underscored schema (6/30). The first run silently marked all of 6/29 unoccupied →
  `load_cv_shelter_sleep` now normalizes state hyphens→underscores, falls back `n_rats`→
  `n_inside_estimated`, and defaults missing glass-QC columns (headline-usable=False,
  coarse-usable=True).
- **Lag grid too narrow:** an initial ±600 s scan railed at the edge; widened to a coarse ±4.5 h
  (300 s) scan to *discover* the offset (found ~0 residual).
- **Selection logic:** the clock offset is shared across cameras, so the driver now picks **one
  joint lag** on the large-n **coarse** stratum (the sparse clear-glass "headline" subset — here
  degenerate — is a confirmation only, never the selector).
- Console `cp1252` can't encode `↔`/`κ`; printed/verdict strings are ASCII (figures keep unicode).

## Verification

- `python scripts/selftest_cv_crossval.py` → **PASS** (kappa sanity; presence counts; lag
  recovered exactly with correct-camera mapping picked).
- Real run on the transferred snapshot `1stcohort_2026_2026-07-01.sqlite` + CV `CH0{5,6}_sleep_
  {2026-06-29,2026-06-30}.csv`: mapping A, joint lag 0 s, joint κ 0.66; figures X1 (κ-vs-lag,
  sharp peak at 0) and X2 (occupancy overlay) spot-checked; CSVs + manifest + verdict written.
  (anaconda base Python 3.13; WISER modules need ≥3.10 — `cv` env on the field PC.)

## Known limitations / next steps

- Only the **two shelters** are cross-validated (most daytime rest is elsewhere).
- Alignment residual is ~0 but still *unverified* (300 s grid; occupancy-inferred).
- Clear-glass ("headline") agreement is degenerate on these two days (no empty/occupied contrast);
  more days with clear glass would give a QC-clean number.
- Georeference confirmation would tighten shelter-ROI membership; a shared physical event (e.g. a
  synchronized light) would upgrade the clock alignment from *inferred* to *verified*.

# Change log — Direction 3: daytime sleep/rest-site & its change

**Date:** 2026-07-02
**Commit:** uncommitted at time of writing.
**Plan:** [implementation_plan/2026-07-02-daytime-sleep-site.md](../implementation_plan/2026-07-02-daytime-sleep-site.md)
**Tracker:** [wiser_tracking_analysis/ANALYSIS_STATUS.md](../wiser_tracking_analysis/ANALYSIS_STATUS.md)

## What changed

New WISER analysis for the daytime rest phase (05:00–21:00 local): per-animal primary sleep/rest
site and how it changes within a day and across days. Third of the three research directions
(alongside rain-influenced behaviour and social-influenced trace).

- **`src/wiser_analysis_utils.py`** — added `rest_mask` (low-speed "resting" flag vs the stationary
  p99 floor), `daytime_primary_site` (per night×tag dominant occupancy site + concentration, with
  optional field-cm and ROI name), `rest_site_stability` (across-day site shift + occupancy
  cosine), `intraday_site_drift` (per-block site + shift, robust to an empty block), plus helpers
  `_cosine` / `_peak_cell_center`. Reuses `select_route_window`, `add_speed`, `speed_noise_floor`,
  `add_validity_flags`, `apply_tag_cutoffs`, `occupancy_hist`, `_box_blur`, `assign_roi`,
  `observed_extent`, and the georeference helpers.
- **`scripts/analyze_daytime_sleep_site.py`** — new driver mirroring
  `analyze_nightly_progression.py` (read-only load → thresholds from the stationary baseline →
  rest window → rest_mask → the three site metrics → CSVs + S1–S3 figures + manifest + verdict,
  to `D:\Wiser_plot\daytime_sleep_site_*`). Georeference/ROI are optional and no-op/provisional
  until confirmed.
- **`scripts/selftest_daytime_sleep_site.py`** — offline synthetic verification.

## Why

The nightly drivers cover only the active phase; nothing described where animals sleep by day or
whether that site moves. This adds the rest-phase view and completes the three-direction roadmap.

## Design notes

Sleep is a **low-speed proxy** (smoothed speed < stationary p99), not ephys-validated; CV shelter
(CH05/CH06) is the intended cross-check (follow-up). Spatial precision is gated by the ~7 in
jitter floor — only sites separated by ≫ the floor are trustworthy (the two shelters are ~5 ft
apart; sub-shelter is not). A "real relocation" is flagged at > 3× the floor. Units stay inches;
field-cm and ROI names are additive/provisional until the georeference survey and ROI placement
are confirmed.

## Verification

- `python scripts/selftest_daytime_sleep_site.py` → **PASS**: `rest_mask` flags low-speed only;
  `daytime_primary_site` recovers a site to (202,702) vs truth (200,700); `rest_site_stability`
  gives a stable animal 4 in shift (cos 0.84) vs a relocated animal 641 in (cos 0.00);
  `intraday_site_drift` detects a 363 in within-day move across an empty midday block.
- End-to-end on a synthetic 2-tag × 2-day SQLite (off-repo, since the live DB is on the field PC):
  rest cutoff 8.31 in/s, jitter floor 6.66 in; stable tag shift 4 in vs relocated tag 636 in;
  intraday afternoon shift 364 in; all CSVs, S1–S3 figures, `run_manifest.json`, and the verdict
  produced. Verified with anaconda base Python 3.13 (WISER modules need ≥3.10; the `cv` env is on
  the field PC).
- **Real-data run (2026-07-03, analysis PC)** on the transferred snapshot
  `D:\Reolink_record\audio_in\Wiser_backup\snapshots\1stcohort_2026_2026-07-01.sqlite` (+ the
  `tag_reports_2026-06-30` baseline): 5 tags × 3 rest days (6/28–6/30); rest cutoff 12.46 in/s,
  jitter floor 7.04 in; resting fraction 0.89–0.92 of daytime fixes (consistent with a nocturnal
  animal). Median site concentration 0.62. Two rats (12386, 12407) show clear across-day
  relocations (~185–212 in ≈ 15–17 ft, ≫ floor) — house_1 area → house_2 on 6/29 — while
  12378/12380/12395 stay within ~8–28 in (near the floor). All CSVs + S1–S3 figures + manifest
  written to `D:\Wiser_plot\daytime_sleep_site_*`.
  Note: 6/28's daytime window is truncated (RTSP/WISER recording began 6/28 ~19:20 EDT), so its
  "rest site" reflects only the ~19:20–21:00 tail — treat 6/29–6/30 as the full-day comparison.
- **Fix during the real run:** `_fig_intraday` used an invalid matplotlib color `tab:teal`
  (there is no such T10 name) → changed to `teal`. The bug was latent in the synthetic run (its
  intraday table had no non-null shift, so the bar call was skipped).

## Known limitations / next steps

- Candidate/exploratory; run on the real rest days once available (field PC / `cv` env).
- Sleep proxy needs validation against CV shelter occupancy (CH05/CH06) — the cross-modal link
  (time-sync + spatial match to shelter ROIs) is the main follow-up.
- Spatial precision and ROI naming depend on the georeference survey + ROI confirmation (both
  tooled, awaiting field input).

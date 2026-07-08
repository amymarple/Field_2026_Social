# Change log — WISER three-direction refresh (rain CIs + route boundary + tracker)

**Date:** 2026-07-02
**Commit:** uncommitted at time of writing.
**Tracker:** [wiser_tracking_analysis/ANALYSIS_STATUS.md](../wiser_tracking_analysis/ANALYSIS_STATUS.md)

## What changed

Phase B of organizing the WISER analysis into three research directions (Direction 3, the new
daytime sleep-site build, is its own [change log](2026-07-02-daytime-sleep-site.md)). This entry
refreshes Directions 1 and 2 and the tracker.

- **Direction 1 (rain) — `scripts/analyze_nightly_progression.py`:** the rain difference-in-
  differences is now reported with a **bootstrap 95% CI across the 5 rats** (new
  `wiser_analysis_utils.did_confidence`, → `rain_did_confidence.csv`) and a per-night **covariate
  table** flagging the known confounds — wet-ground (6/30), tunnel present (6/28 only), Sova
  removed — (`night_covariates.csv`). The verdict string carries the CIs; prior point estimates
  are unchanged. Makes the candidate's uncertainty explicit (the promotion blocker is data +
  confounds, per [the nightly change log](2026-06-30-nightly-progression.md)).
- **Direction 2 (route) — `scripts/analyze_route_structure.py`:** when a **confirmed** georeference
  transform exists, the driver adopts the surveyed paddock boundary
  (`wiser_analysis_utils.verified_boundary_in_wiser`) for out-of-bounds QC / thigmotaxis / interior
  corridor analysis (superseding the provisional ROI-file rectangle), and adds `x_field_cm,
  y_field_cm` for CV cross-checks. **Guarded — a no-op until a survey is confirmed** (today
  `load_field_transform()` returns `None`), so current output is byte-identical.
- **Tracker — `ANALYSIS_STATUS.md`:** the "Next steps" section is reorganized under the three
  directions (status / blocker / path-to-publishable each), plus a cross-cutting prerequisites
  block (georeference survey, ROI confirmation) and a new Direction-3 inventory row.

## Why

Directions 1 and 2 were already implemented as candidates; their promotion is gated on data +
confounds (D1) and the frame + replication (D2). This surfaces the D1 uncertainty honestly and
lets D2 consume the georeference the moment a survey lands, without a further code change.

## Verification

- `did_confidence` unit check: 5-rat synthetic table → buffer 0 mean +17.6 [95% CI +11.6, +23.6],
  buffer 20 mean +7.5 [+1.5, +13.5] (sensible, wide CIs at n=5).
- All three drivers + utils compile (Python 3.13).
- **Non-regression (D2):** with no confirmed transform, `load_field_transform()` → `None` and
  `verified_boundary_in_wiser(None)` → `None`, so the route driver falls back to the ROI-file
  boundary exactly as before.
- Both self-tests still PASS (`selftest_georeference.py`, `selftest_daytime_sleep_site.py`).
- The rain/route drivers need the field DB (on the field PC) for a full data run; use the `cv` env
  there.

## Known limitations / next steps

- Per the tracker: D1 needs more dry-night data; D2 needs the georeference survey + multi-night
  replication; D3 needs CV-shelter validation of the sleep proxy. Full mixed-effects modelling
  across sessions remains future work.

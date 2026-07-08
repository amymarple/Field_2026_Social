# Implementation plan — WISER shelter occupancy as a smoothed *state*, not raw point-wise ROI

**Date:** 2026-07-03
**Scope:** medium/large — changes the primary shelter-occupancy definition used by the
Direction-3 sleep-site cross-validation.
**Subsystem:** `wiser_tracking_analysis/`
**Follows:** [2026-07-02-sleep-site-cv-crossval.md](2026-07-02-sleep-site-cv-crossval.md)
**Tracker:** [../wiser_tracking_analysis/ANALYSIS_STATUS.md](../wiser_tracking_analysis/ANALYSIS_STATUS.md)

## Motivation

The 2026-07-02 cross-validation used **raw point-wise ROI inclusion** as the WISER shelter
state: a rat is "in the shelter" in a 60 s bin iff a fix landed inside the shelter rectangle.
The hourly scatter (2026-07-03 16:00–17:00) shows why that is wrong for a rest period: rats are
behaviourally stationary inside a shelter, but WISER positions **jitter** (~7 in median, p95 ~15
in, worse near paddock edges) and spread well beyond the ~36 × 27 in shelter footprint. Point-level
ROI crossing is therefore **too strict** — it manufactures false exits every time a jittered fix
lands just outside the rectangle, even though the animal never left.

WISER is a **UWB** sensor: fog / rain / condensation / IR glass do **not** attenuate it (unlike the
CV shelter cameras). So the correct reading is: *WISER can be noisy at the point level, but a
sustained cluster of positions near a shelter during a known rest period is high-confidence
evidence of shelter occupancy.* WISER should be evaluated at the **shelter-occupancy-state** scale,
not as point-accurate pose tracking. And in a WISER↔CV comparison the two are **not symmetric
witnesses**: WISER is the fog-immune reference; CV is the optically degraded sensor under test.

## What changes

Two things:
1. Replace the raw point-wise shelter occupancy with a **smoothed, hysteretic, buffer-tolerant
   shelter *state*** per rat, plus a **high-confidence shelter episode** definition. Keep raw
   point-wise ROI occupancy only as a **diagnostic**.
2. Reframe the CV cross-validation as **WISER-as-reference**: report CV **recall** during
   WISER-confirmed (high-confidence) shelter occupancy and CV **precision** when CV reports
   occupied, stratified by glass quality and day. Demote symmetric Cohen's κ to a **lag-alignment
   diagnostic** only (still used to pick the clock lag + ROI↔camera mapping, never as the headline).

### Governing rules (unchanged, still honored)
- Clocks are **UNVERIFIED**: WISER = naive UTC, CV `t` = local NVR wallclock (+4 h nominal); the
  residual lag is *scanned and reported, never trusted*.
- ROI↔camera mapping is a **hypothesis** — both orientations tested; the fit picks one.
- CV sees only the **two shelters**; this validates the shelter-resident subset of WISER rest only.
- CV counts **undercount** (huddles + wall-edge blind zone) → occupancy boolean is primary,
  head-count is a lower bound.
- Read-only on the WISER snapshot + CV CSVs; outputs to git-ignored `D:\Wiser_plot\`.

## New / changed functions in `src/wiser_analysis_utils.py`

- `_rect_membership(x, y, roi, buffer_in)` → `(in_core, in_buffer)` boolean masks for a rotated
  rect: `in_core` = inside the ROI; `in_buffer` = inside the ROI grown by `buffer_in` on every
  side (⊇ core). Reuses the rotated-local-coordinate math of `_point_in_rect`.
- `wiser_shelter_state(win, roi_cfg, shelter_names, *, bin_s=60, buffer_in=18.0, enter_s=120,
  exit_s=120, near_frac=0.5, far_frac=0.2, hc_min_s=1200, hc_max_spread_in=24.0)`
  → `(grid_df, episodes_df)`. **Per (night, shortid, shelter):**
  - Bin the rat's fixes at `bin_s`; per bin compute `frac_core` and `frac_near` (fraction of fixes
    in core / in core∪buffer). Reindex onto the **contiguous within-night grid** (dropout bins →
    NaN evidence).
  - Per-bin evidence: **near** if `frac_near ≥ near_frac`, **far** if `frac_near ≤ far_frac`, else
    **uncertain** (NaN) — near-boundary/buffer straddling counts as uncertain, never forced out.
  - **Hysteresis / debounce** over the grid: enter the in-shelter state after `⌈enter_s/bin_s⌉`
    consecutive *near* bins; exit only after `⌈exit_s/bin_s⌉` consecutive *far* bins; *uncertain*
    bins **hold** the current state (no flicker). Yields a per-bin boolean `state`.
  - **Episodes** = contiguous `state==True` runs. Per episode: `start/end`, `duration_s`, `n_fix`,
    centroid (median x,y of the episode's fixes), `spread_in` (median distance of those fixes to the
    centroid), `frac_core`, `centroid_in_buffer`, and `high_confidence` =
    `duration_s ≥ hc_min_s AND spread_in ≤ hc_max_spread_in AND centroid_in_buffer`. ("No continuous
    trajectory away" is intrinsic: a sustained departure would have triggered the hysteretic exit
    and ended the episode.)
  - `grid_df` columns: `night, shortid, shelter, bin_utc, frac_core, frac_near, state, hc` (`hc` =
    bin belongs to a high-confidence episode).
- `shelter_occupancy_bins(grid_df)` → per `(shelter, bin_utc)`: `n_state` (rats in-shelter),
  `occupied` (`n_state > 0`), `n_hc`, `hc_occupied` (`n_hc > 0`). The smoothed replacement for
  `wiser_shelter_presence`'s `occupied`.
- `cv_detection_metrics(ref, cv_bins, *, ref_col)` → inner-join WISER reference bins to CV bins;
  return `n_bins, TP, FP, FN, TN, recall, precision, specificity, wiser_occ_frac, cv_occ_frac`.
  **Recall** uses `ref_col="hc_occupied"` (CV detection during WISER-confirmed occupancy);
  **precision** uses `ref_col="occupied"` (is CV right when it says occupied).
- **Kept as lag-alignment diagnostics only:** `wiser_shelter_presence` (now labelled the raw
  point-wise diagnostic), `cohen_kappa`, `_cv_bins`, `best_lag_agreement`.

## Driver `scripts/analyze_sleep_site_cv_crossval.py`

- Build the smoothed state: `wiser_shelter_state(win, roi_cfg, SHELTERS, …)` →
  `shelter_occupancy_bins` → per-shelter reference bins (`occupied`, `hc_occupied`).
- **Lag + mapping selection unchanged in mechanism**, but fed the **state-based** `occupied` bins:
  `best_lag_agreement` (joint bin-weighted κ across both shelters, coarse-glass stratum) picks one
  shared lag + the mapping. This κ curve is the **alignment diagnostic**.
- **Headline = detection metrics**: per shelter/day/stratum, `cv_detection_metrics` →
  recall (vs `hc_occupied`) + precision (vs `occupied`) + specificity, stratified by
  `usable_for_headline_summary` (clear) and `usable_for_coarse_activity` (all usable). κ + raw
  agreement kept as secondary columns.
- **Raw point-wise diagnostic**: also emit `wiser_raw_presence_diagnostic.csv` from
  `wiser_shelter_presence` and report, per day, how many bins flip occupied→empty between raw and
  smoothed (quantifies the false-exit problem the plot shows).
- **Episodes + biological anchor**: write `wiser_shelter_episodes.csv` and a per-day
  high-confidence-episode summary (count, total duration, mean spread). Frame these as **QC/
  validation anchors** for CV — explicitly *not* circular proof of the sleep-site claim (the
  sleep-site claim itself rests on WISER; the anchors test CV's detector).
- **Wet/degraded-glass call-out**: flag (day, shelter) where WISER `hc_occupied` is sustained but CV
  reads mostly empty under degraded/unusable glass → **likely CV optical failure**, not a WISER
  error.
- Figures: `X1` κ-vs-lag (relabelled "clock-lag alignment diagnostic"); `X2` occupancy overlay
  (WISER smoothed state + hc episodes vs CV occupied, view_quality shaded, raw point-wise as a faint
  underlay); `X3` recall/precision bars by day × glass stratum.
- Verdict + manifest reframed: recall/precision headline, κ as alignment diagnostic, all the
  standing caveats, WISER-as-reference framing.

## Self-test `scripts/selftest_cv_crossval.py` (extend)

Add, keeping the existing κ / presence / best_lag checks:
- `_rect_membership`: a point in the buffer ring is `in_buffer` but not `in_core`; a far point is
  neither.
- `wiser_shelter_state` hysteresis: a synthetic rat sitting in a shelter with jitter that
  **repeatedly crosses the ROI edge** but never sustains a departure → **one** episode spanning the
  stay (no false exits), `high_confidence=True`; brief near-blips shorter than `enter_s` never open
  a state; a sustained walk-away closes it.
- `cv_detection_metrics`: hand-built confusion counts → recall/precision/specificity match.

## Docs (AGENTS.md)
- This plan (before code) + `implementation_plan/README.md` index row.
- `change_log/2026-07-03-wiser-shelter-occupancy-state.md` (after verify) + `change_log/README.md`
  row.
- Update the Direction-3 cross-val row in `ANALYSIS_STATUS.md` (smoothed state; recall/precision).
- Update `data_manifests/2026-06-29-wiser-pilot.yaml` `alignment.wiser_to_cv_shelter` note (state-
  based reference; recall/precision; κ demoted to alignment diagnostic).

## Verification
- **Offline:** `python scripts/selftest_cv_crossval.py` → PASS (new + existing checks).
- **Real run (analysis PC, anaconda base 3.13 / `cv` env)** on 6/29 & 6/30: prints chosen mapping,
  scanned lag (UNVERIFIED), per-shelter/day recall+precision by glass stratum, raw→smoothed
  false-exit reduction, high-confidence anchor summary, wet-glass CV-failure call-outs; writes CSVs +
  X1–X3 + manifest + verdict. Spot-check X2/X3.
- **Git surface:** only the new/changed script + utils + docs; outputs to `D:\Wiser_plot`
  (git-ignored); no writes to source DBs / CV outputs; unrelated working-tree changes untouched.

## Non-goals
- Not asserting verified time sync (lag reported, not trusted). Not validating rest *outside*
  shelters (CV can't see it). Not a physiological sleep validation. No georeference dependency
  (mapping test substitutes); a confirmed transform would later tighten ROI membership.

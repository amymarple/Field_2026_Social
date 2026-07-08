# Change log — WISER shelter occupancy as a smoothed *state* (method)

**Date:** 2026-07-03
**Commit:** method introduced in `6c703c5`; binning hardened in `670272c`; CV reporting reframed in
`26540b9` (see the cross-references below). This log documents the **smoothed-state method** that
pairs with [implementation_plan/2026-07-03-wiser-shelter-occupancy-state.md](../implementation_plan/2026-07-03-wiser-shelter-occupancy-state.md)
(the plan was committed without a paired change log; this fills that gap).
**Tracker:** [wiser_tracking_analysis/ANALYSIS_STATUS.md](../wiser_tracking_analysis/ANALYSIS_STATUS.md)
**CV interpretation is authoritatively recorded in:**
[2026-07-06-cv-wiser-reconciliation-reframe.md](2026-07-06-cv-wiser-reconciliation-reframe.md)
(+ binning fix [2026-07-06-wiser-binning-resolution-fix.md](2026-07-06-wiser-binning-resolution-fix.md)).
Do not re-derive the CV verdict here.

## Why

The pre-existing cross-val defined WISER shelter occupancy by **raw point-wise ROI inclusion** (a
rat is "in" a 60 s bin iff a fix lands inside the shelter rectangle). The 2026-07-03 hourly scatter
shows this is wrong for a rest period: rats sit still inside a shelter, but WISER **jitters** (~7 in
median, p95 ~15 in) well beyond the ~36 × 27 in footprint, so point-level ROI crossing manufactures
false exits. WISER is **UWB** — fog / rain / condensation / IR glass don't attenuate it — so it
should be read at the **shelter-occupancy-state** scale, not as point-accurate tracking.

## What changed (method)

- **`src/wiser_analysis_utils.py`**
  - `_rect_membership(x, y, roi, buffer_in)` — core + buffer-grown rect masks (buffer absorbs jitter).
  - `_hysteresis_state(near, n_enter, n_exit)` — debounced state machine; *uncertain* (buffer /
    boundary-straddling) bins **hold** state so jitter can't flicker occupancy off.
  - `wiser_shelter_state(win, roi_cfg, shelter_names, …)` → `(grid_df, episodes_df)`: smoothed,
    hysteretic, buffer-tolerant per-rat shelter **state** + **high-confidence episodes** (sustained
    ≥ `hc_min_s`, low `spread_in`, centroid in-shelter). Per (night, shortid, shelter); episodes
    never cross the overnight gap.
  - `shelter_occupancy_bins(grid_df)` → per (shelter, bin): `occupied` (smoothed) + `hc_occupied`.
  - `cv_detection_metrics(ref, cv_bins, ref_col)` — CV recall / precision / specificity vs a WISER
    reference column.
  - `wiser_shelter_presence` re-labelled the **raw point-wise DIAGNOSTIC** (kept only to quantify
    over-splitting); `cohen_kappa` / `best_lag_agreement` retained as **lag-alignment diagnostics**.
- **`scripts/analyze_sleep_site_cv_crossval.py`** — WISER reference is now the smoothed state
  (`wiser_shelter_state` → `shelter_occupancy_bins`); raw presence kept as a diagnostic underlay.
- **`scripts/selftest_cv_crossval.py`** — added `_rect_membership`, hysteresis/episode, and
  `cv_detection_metrics` checks (later extended with the `[ns]==[us]==[ms]` resolution-invariance
  regression in the binning fix).

## Findings — deferred to the reconciliation, NOT an optical-failure claim

The CV-vs-WISER interpretation was finalized by the **2026-07-06 reconciliation** (asymmetric
measurement semantics) after the binning fix; the authoritative headline (dataset 2026-07-02,
per-shelter, coarse glass) is:

- ROI↔camera mapping **A** confirmed; best-fit lag **+0 s** (alignment adequate, sweep flat).
- house_1/CH05 **precision 1.00 / recall (lower bound) 0.49** (WISER presence 0.99, n=192);
  house_2/CH06 **precision 0.99 / recall 0.67** (presence 0.75, n=191).
- **The recall gap is the wall-edge blind zone / definition mismatch, NOT fog.** It is flat across
  strata — view_quality clear 0.56 ≈ degraded 0.50, fog_risk low 0.55 ≈ high 0.58, glass
  antifog_film 0.50 ≈ lift_1cm 0.60 — so CV-miss/WISER-present bins do **not** concentrate in
  degraded/foggy/low-validity strata. CV visible-inside is therefore a **lower bound** on WISER
  near-shelter occupancy; the CH05 gap on 2026-07-02 sits on **clear** glass.
- Cohen's **κ (joint A=0.20)** is an **alignment diagnostic only** and base-rate sensitive: WISER
  presence prevalence ≈ 1.0 drives the kappa paradox, so a low value is prevalence + definition
  mismatch, not misalignment. **Never the headline.**

> ⚠️ A superseded interpretation ("6/30 degraded glass → CV optical failure") was drafted against a
> stale pre-binning-fix, pre-reconciliation baseline. It is **wrong** and is retained nowhere: the
> gap does not track glass condition. Use the reconciliation numbers above.

## Verification

- `python scripts/selftest_cv_crossval.py` → **PASS** (κ; `_rect_membership`; hysteresis = one
  high-confidence episode with no false exits + a sub-enter blip that opens nothing;
  `cv_detection_metrics` hand values; `best_lag` recovery; `[ns]==[us]==[ms]` resolution invariance).
- Canonical real run: `D:\Wiser_plot\sleep_site_cv_crossval_20260706_2303` (verdict + reconciliation
  strata + `cv_recall_gap_flags.csv` + manifest). Read-only on the WISER snapshot + CV CSVs;
  outputs to git-ignored `D:\Wiser_plot`.

## Known limitations / next steps

- Only the **two shelters** (CV can't see rest elsewhere). Alignment residual ~0 s but *unverified*.
- Buffer / enter / exit / hc thresholds are configurable defaults; a georeference-confirmed frame
  would let them be set in physical cm and tighten ROI membership.
- High-confidence WISER episodes are **CV validation anchors**, not independent proof of the
  Direction-3 sleep-site claim (which itself rests on WISER).

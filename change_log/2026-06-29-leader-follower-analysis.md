# Leader-follower / route-following analysis (candidate route use)

## Date

2026-06-29. Change is currently uncommitted.

## Plan

Implemented from
[`implementation_plan/2026-06-29-leader-follower-analysis.md`](../implementation_plan/2026-06-29-leader-follower-analysis.md).
Extends [`2026-06-29-wiser-pilot-analysis.md`](2026-06-29-wiser-pilot-analysis.md); reuses the same
data manifest ([`data_manifests/2026-06-29-wiser-pilot.yaml`](../data_manifests/2026-06-29-wiser-pilot.yaml)).

## What changed

- `wiser_tracking_analysis/src/wiser_analysis_utils.py` — new "Leader-follower / route-following"
  section. Following is **time-lagged path reuse**, not proximity: B follows A if B reaches A's
  earlier position after a positive lag while both move with aligned heading.
  - `build_following_grid` (1 Hz median grid → contiguous axis → `smooth_s`-second rolling-median →
    velocity/speed/heading-unit/`moving`), `grid_speed_noise_floor` (moving threshold = p99 grid
    speed of the stationary baseline), `follow_radius_in` (`R = max(3×jitter, 24 in)`).
  - `follow_scores_all` (per ordered pair × lag 1–30 s), `following_peaks`, `following_asymmetry`,
    `following_null` (circular-shift the follower 5–20 min, peak-over-lags, n_shuffles=100 →
    shuffled mean/p95/sd + z), `following_events` (contiguous bouts).
  - Six plots: peak-score heatmap (leader→follower), best-lag heatmap, asymmetry heatmap, lag curves
    (with shuffled-p95 line), event raster, trajectory snippets (leader vs lag-aligned follower,
    arrows + timestamps).
- `wiser_tracking_analysis/notebooks/wiser_pilot_analysis.ipynb` — new **Section J**: runs the
  analysis, writes `following_scores.csv` + `following_events.csv`, saves `J1…J6`, prints a candidate
  verdict; the run manifest gains a `route_following` block. The 8-section report is unchanged.

## Why

The pilot quantified *where* and *how active* the rats are but not whether they **reuse each other's
routes**. We needed a leader-follower metric that is robust to WISER jitter and not confounded by
mere co-location, and that is honestly hedged against shared-corridor use via a proper null model.

## Source data used for verification

Read **read-only**: `D:\Wiser\data\1stcohort_2026.sqlite` (free-moving, live; ~1.45M rows / ~20 h at
run time), `D:\Wiser\data\tag_reports.sqlite` (stationary baseline → grid moving threshold + jitter
floor). No new raw data.

## Verification performed

conda env `cv` (no jupyter → executed the notebook code cells in order):
```
python -m py_compile src/wiser_analysis_utils.py
python <run_nb>.py        # ALL CELLS OK
```
Observed (free DB is live; counts are a runtime snapshot):
- Thresholds: stationary grid moving threshold **6.31 in/s**, jitter floor **7.0 in**, **R = 24 in**
  (= max(3×7, 24); `reliable` since R ≥ 3× floor).
- `following_scores.csv`: 30 ordered pairs, `peak_score ∈ [0.01, 0.10]`, `best_lag_s ∈ [1, 28]`,
  `asymmetry ∈ [-0.5, 0.5]`. Strongest pairs peak at **lag 1 s** and exceed the circular-shift null
  (z ≈ 9–21). Tag **12395 is the dominant leader** (positive asymmetry vs 12380/12407/12378).
- Null sanity: shuffled means ≈ 0.02–0.03 (« real peaks); **22/30** ordered pairs above shuffled p95.
- Verdict (conservative): "Above-null path reuse but weak short-lag/asymmetry → candidate **shared
  route / corridor use**" — strongest-pair median best-lag 1 s, median |asymmetry| ≈ 0.20 (boundary).
  Labeled candidate route-following, **not** confirmed social following.
- 6 figures (J1–J6) + `following_scores.csv` + `following_events.csv` (41 bouts) written; lag curves
  show the real-vs-null short-lag peak; heatmaps are directional (not symmetric). Notebook committed
  output-free.

## QC output

Appended to `D:\Wiser_plot\wiser_pilot_output_YYYYMMDD_HHMM\`: `following_scores.csv`,
`following_events.csv`, `figures/J1_follow_heatmap.png … J6_snippets.png`; `run_manifest.json` gains a
`route_following` block (R, moving threshold, lag range, n_shuffles, n pairs above null).

## Known limitations / next steps

- **Candidate** route-following only: a single < 24 h session, WISER frame unverified vs the paddock,
  and the shared-corridor vs leader-follower call rests on the modest asymmetry. Not territory or
  social-rank evidence.
- Following is computed at the **valid-fix** scale; the moving threshold (6.31 in/s on the 1 Hz grid)
  and `R = 24 in` are conservative but configurable.
- The null circularly shifts the follower only; it preserves each animal's own movement statistics
  but not finer environmental autocorrelation. `n_shuffles=100` (~2.5 min) is configurable.

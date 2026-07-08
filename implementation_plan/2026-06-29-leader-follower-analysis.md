# Leader-follower / route-following analysis (candidate route use)

## Goal

Add a leader-follower analysis to the WISER pilot, defined rigorously as **time-lagged path reuse**
(not proximity): for an ordered pair A→B, B follows A if B reaches A's earlier position after a
positive lag while both are moving with aligned heading. Validate against circular-shift null
controls and report strictly as **candidate route-following**.

## Approach (all in `wiser_tracking_analysis/`, units = inches)

Reuses `resample_common_grid` (1 Hz median position), the pivot-on-`tbin` alignment pattern, the
stationary jitter floor, and the `imshow` / `plotting._tag_colors` / `_save_or_show` plot helpers.

New functions in `src/wiser_analysis_utils.py` ("Leader-follower / route-following" section):
- `build_following_grid(df, *, bin_s=1.0, smooth_s=5, moving_thr_inps)` — 1 Hz grid → contiguous
  time axis → rolling-median smooth → per-bin velocity/speed/heading-unit + `moving` mask
  (2-D aligned arrays `X,Y,SP,UX,UY,MOV`).
- `grid_speed_noise_floor(stationary_df, …)` — conservative moving threshold = p99 grid speed of the
  **stationary** baseline (same pipeline; mirrors `speed_noise_floor`).
- `follow_radius_in(jitter_floor_in)` — `R = max(3 × jitter, 24 in)`.
- `follow_scores_all` (score per ordered pair × lag 1–30 s), `following_peaks` (peak + best lag),
  `following_asymmetry` ((A→B − B→A)/(sum+ε)), `following_null` (circular-shift the follower by a
  random 5–20 min offset, peak-over-lags, n_shuffles=100 → shuffled mean/p95/sd + z), and
  `following_events` (contiguous bouts for raster + snippets).
- Plots: `plot_following_heatmap`, `plot_following_best_lag_heatmap`,
  `plot_following_asymmetry_heatmap`, `plot_following_lag_curves` (with shuffled-p95 line),
  `plot_following_raster`, `plot_following_snippets` (leader vs lag-aligned follower, arrows +
  timestamps).

Notebook: new **Section J** computes the above, writes `following_scores.csv` + `following_events.csv`,
saves `J1…J6`, and prints a **candidate verdict** applying the interpretation rules (real vs null,
short-lag, asymmetry, R vs jitter floor). `n_shuffles` is configurable; the 8-section report is
unchanged.

## Definition of "following" at a timepoint t (ordered pair A→B, lag L)

Both moving; `dist(A(t), B(t+L)) < R`; heading-alignment cosine `> 0.5`. `follow_score(A→B, L)` =
fraction of valid moving timepoints meeting all three.

## Interpretation rules (hard requirements)

- Label "candidate route-following", never confirmed social following.
- Always compare `R` to the stationary jitter floor (close following only when `R ≥ 3×` floor).
- Real ≈ shuffled → shared corridor / shared route use. Real short-lag > shuffled **and**
  directionally asymmetric → stronger leader-follower evidence.

## Verification

conda env `cv`; py_compile the module; run the notebook code cells in order (no jupyter in `cv`).
Confirm: read-only on `D:\Wiser\data`; `R_in == 24`, `moving_thr_inps > 0`; `following_scores.csv`
has 30 ordered pairs, `0 ≤ peak_score ≤ 1`, `best_lag_s ∈ [1,30]`, `asymmetry ∈ [-1,1]`; real peak ≳
shuffled for most pairs (z recorded); J1/J3 directional (not symmetric); J4 shows real-vs-null band;
raster + snippets render. Outputs to `D:\Wiser_plot\wiser_pilot_output_*` (off-repo).

## Non-goals

No confirmed-social-following claims; no proximity-only definition; no georeferencing/day-night
change; no writes under `D:\Wiser`.

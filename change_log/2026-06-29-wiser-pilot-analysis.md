# WISER UWB Pilot Analysis Pipeline

## Date

2026-06-29. Change is currently uncommitted.

## Plan

Implemented from
[`implementation_plan/2026-06-29-wiser-pilot-analysis.md`](../implementation_plan/2026-06-29-wiser-pilot-analysis.md).
Data manifest: [`data_manifests/2026-06-29-wiser-pilot.yaml`](../data_manifests/2026-06-29-wiser-pilot.yaml).

## What changed

- Added `wiser_tracking_analysis/src/wiser_analysis_utils.py` — reusable pilot layer on top of
  `wiser_io`/`time_utils`/`metrics`/`plotting`:
  - `load_wiser_session` — read-only loader that **preserves** the QC columns
    (`anchors_used`, `calculation_error`, `battery_voltage`, `reportid`) the canonical loader drops;
    `session_snapshot` — runtime row-count/time-bounds snapshot for a possibly-live DB.
  - `add_speed` (raw + smoothed), `add_validity_flags` (low-anchor / gap / jump / provisional-bounds,
    flags only), `resample_common_grid`, `pairwise_distances`, `proximity_summary` (per-threshold
    reliability vs jitter floor), `clustering_index`, `occupancy_hist`, `infer_candidate_zones`
    (scipy-free), `assign_roi`, `roi_time_and_transitions`, `distance_to_edge`, `hourly_activity`,
    `movement_summary`, `acclimation_windows`, `load_weather`, `merge_activity_weather`.
  - Plots (inch-correct): gaps, raw/clean trajectories, speed, jitter clouds, pairwise heatmap,
    clustering index, hourly-by-clock (+temp/solar), activity-vs-temperature, weather, ROI graph.
  - Provenance: `make_output_dir`, `write_run_manifest`, `write_filtering_log`, `build_pilot_report`.
  - No hard dependency on scipy/IPython (Spearman via Pearson-on-ranks; scipy used only if present).
- Added `wiser_tracking_analysis/scripts/place_wiser_rois.py` — matplotlib ROI **editor**,
  **separate** from the CV `place_cameras.py`. Edits the paddock boundary + 4 refuge / 2 water /
  2 food (+ optional tunnel) on a real occupancy background and writes `configs/wiser_rois.json`.
  Fully **drag-to-edit**: loads the existing `wiser_rois.json` on launch so markers are pre-placed
  and re-editable (drag boundary corners to resize, drag the box to move, drag ROIs, scroll/`[`/`]`
  to resize, arrow keys to nudge, `d` to delete, `s`/`q` to save). Read-only on the source data.
- **ROI shapes:** circle (refuge/water/food) and **rect** (the 2 big rectangular houses). Rect house
  size defaults to **24.63 x 18 in (62.55 x 45.72 cm)** taken from
  `preprocessing/computer_vision/configs/field_layout.json` (`shelters`); only the size is reused —
  position/orientation are placed in the WISER frame via the GUI (`,`/`.` rotate a rect). The
  analysis layer (`assign_roi` + `_point_in_rect`) handles rotated-rectangle membership; default
  layout is 2 rect houses + 4 small circular refuges + 2 water + 2 food (10 ROIs).
- **Time-varying ROIs:** ROI entries accept optional `valid_from` / `valid_until`; `assign_roi` only
  labels points whose timestamp is inside the window. Each time may be written either as **local with
  offset** (e.g. `2026-06-29T07:00:00-04:00`, reads as 7am) or naive **UTC** (`...T11:00:00`); both
  denote the same instant and `_roi_time_utc` converts to UTC to compare against the WISER Unix-ms
  (UTC) timestamps. Used for `tunnel_1` — present overnight, physically removed 07:00 EDT 2026-06-29 —
  so tunnel occupancy is counted only before removal, "open" afterward. The GUI carries the validity
  fields and offers a tunnel slot with the removal time preset; place it on the overnight cluster.
- Added `wiser_tracking_analysis/notebooks/wiser_pilot_analysis.ipynb` — QC-first Jupyter notebook
  (thin client; markdown per step; `DATA_DIR`/`OUTPUT_ROOT` config; analyses A–I; final 8-section
  report). Honors the explicit `.ipynb` request (repo default is marimo — noted).
- Added `wiser_tracking_analysis/configs/wiser_rois.json` — provisional placeholder (confirmed=false).
- Added `data_manifests/2026-06-29-wiser-pilot.yaml` + `data_manifests/README.md`; updated
  implementation_plan / change_log indexes.

## Update (2026-06-29, later) — robust smoothed speed + streak-free trajectories

Field inspection of the trajectory plots showed impossible locomotion (raw frame-to-frame speed
median ~29 in/s, **max ~168,000 in/s**). Root cause: WISER sampling is bursty, so two fixes a few
milliseconds apart divide a small jittered step by a tiny `dt` and explode the speed. The original
`speed_inps_smooth` still divided by that raw `dt`.

- `add_speed` now computes `speed_inps_smooth` as the displacement of the **jitter-suppressed
  position** (centred rolling-median, window raised `5 → 7` samples) over a **fixed
  `DEFAULT_SPEED_WINDOW_S = 1.0 s` window**, divided by that window — the denominator can no longer be
  near-zero, and the window averages out residual jitter. Smoothed speeds above
  `MAX_PLAUSIBLE_SPEED_INPS = 150 in/s` (~3.8 m/s, a generous rat sprint) are set to NaN as residual
  teleport artifacts. `speed_inps_raw` is unchanged (still feeds `jump_flag`); `step_in_smooth` is
  unchanged (still the non-overlapping per-sample step summed for path distance).
- Result on the live DB: smoothed-speed median **3.3 in/s**, p99 25, **max 147.5 in/s** (was max
  168,103); 7.6% of windows capped as artifacts. Active fraction (>2 in/s) ~0.64 — still flagged as
  threshold-relative.
- `plot_trajectories` now breaks the drawn line wherever the gap to the previous plotted fix exceeds
  ~5× the tag's median sampling interval, so a dropout no longer renders as a straight streak across
  the paddock (the visual form of the same artifact). Cleaned-trajectory plots are now streak-free.

## Update (2026-06-29, later 2) — data-driven activity threshold from the speed-noise floor

Field follow-up: even 100 in/s is impossible for these rats, and at ~3.5 in position resolution and
~4.4 Hz sampling the *noise alone* produces ~10 in/s of apparent speed — so a fixed 2 in/s "active"
threshold was below the noise and inflated the active fraction to a meaningless ~0.64.

- New `speed_noise_floor(stationary_df)` runs the same `add_speed` estimator on the **stationary**
  tags (which are not moving, so every smoothed speed is pure tracking noise) and returns its
  percentiles. Measured: median 1.77, p95 6.4, **p99 12.5 in/s**.
- The notebook now derives `ACTIVE_SPEED_INPS` from that floor (p99 ≈ **12.5 in/s**) instead of the
  fixed 2 in/s; "active" = smoothed speed above the level motionless tags exceed only 1% of the time.
  `DEFAULT_ACTIVE_SPEED_INPS` fallback raised 2 → 12; the value and the full floor are written to the
  run manifest + filtering log.
- `MAX_PLAUSIBLE_SPEED_INPS` lowered **150 → 60 in/s (~1.5 m/s)** — above the 99.9th pct of observed
  real movement (47 in/s) but removing the impossible tail; smoothed speeds above it become NaN.
- Effect on the live DB: per-tag active fraction drops from ~0.64 to **~0.06** (rats locomoting at
  >0.3 m/s ~6% of an 18 h evening→afternoon span — sensible for nocturnal animals); smoothed-speed
  plot now bounded at 60 in/s with the 12.5 in/s active line drawn.
- Interpretation caveat (report + manifest): speeds below the ~12.5 in/s floor are indistinguishable
  from jitter; mean speed still carries a noise component, so the floor-thresholded active fraction is
  the defensible activity metric, and it remains relative (one session, one jitter baseline).

## Update (2026-06-29, later 3) — plot readability

All figures regenerated; two were reworked:
- `plot_speed_timeseries` (A4) now plots a **per-minute (60-s) median** of smoothed speed per tag
  instead of every fix — the previous version overplotted ~230k points/tag into a solid noise band.
  Activity bouts (evening peak, overnight decline, quiet day) are now visible, with the data-driven
  active line drawn.
- `plot_roi_transition_graph` (F1) labels are offset above the nodes with a translucent background,
  and **co-located ROIs** (food sits inside a house, so `food_N` shares `house_N`'s point) get their
  labels staggered (house above / food below) so they no longer overprint.

## Update (2026-06-29, later 4) — hourly activity: between-rat error bars + active-distance panel

The hourly clock-hour bar was a single **pooled** active fraction over all 6 tags' fixes with no
between-rat variability shown, and the user asked whether travel distance per hour would be clearer.

- `hourly_activity` now also returns `by_clock_per_tag` (one row per shortid × clock-hour) and an
  `active_distance_in` column (path length summed **only over above-threshold samples**). New
  `hourly_clock_summary()` collapses that to between-rat **mean ± SD** per clock hour.
- `plot_hourly_activity_by_clock` (I2) is now **two stacked panels**: (top) active fraction as
  between-rat mean ± SD with the temp/solar overlays; (bottom) active distance per hour in **metres**,
  mean ± SD. Notebook cell 13 passes the per-tag table and writes `hourly_activity_by_clock.csv`.
- Distance decision (recorded for provenance): raw distance/hour was **rejected** as a primary metric
  because path length rectifies jitter and is positively biased — a **stationary** tag accumulates
  ~270 m/h vs ~473 m/h free-moving (~55% noise), and per-second gridding does not fix it. "Active
  distance" (above the ~12.5 in/s noise floor) is the intuitive, noise-rejected alternative: it peaks
  at ~241 m/h at 21:00 and falls to ~10–50 m/h midday, tracking the active fraction.
- Active fraction and active distance are different quantities (fraction of time moving vs path length
  during movement), so both panels are shown; error bars are between-rat SD (each clock hour occurs
  once in this <24 h session — not across-day).

## Why

We had a multi-hour free-moving WISER session and matching weather but no pipeline that first
establishes *at what spatial/temporal scale the tracking is trustworthy* and only then interprets
behavior. The existing loader also dropped the QC columns needed for that verdict.

## Source data used for verification

Read **read-only**: `D:\Wiser\data\1stcohort_2026.sqlite` (free-moving, **live**),
`D:\Wiser\data\tag_reports.sqlite` (stationary baseline), `configs/fixed_position_ground_truth.csv`,
`D:\weather_data\AWN-F8B3B78DEAC9-20260628-20260629.csv`. `test20260622.csv` deliberately excluded
(overlaps `tag_reports.sqlite`).

## Verification performed

Commands (conda env `cv`; no jupyter/scipy/IPython in that env, so the notebook was run by executing
its code cells in order — IPython display is guarded):

```bash
python -m py_compile src/wiser_analysis_utils.py scripts/place_wiser_rois.py
# headless smoke of loader/speed/flags/weather on the real data
python <smoke>.py
# regenerate notebook, then run every code cell in order on the live DB
python <build_nb>.py notebooks/wiser_pilot_analysis.ipynb
python <run_nb>.py        # ALL CELLS OK
```

Observed behavior:

- All 15 code cells ran end-to-end. The free DB is **live** — row count rose across runs
  (1,018,179 → 1,033,335) while min timestamp stayed fixed → reads are read-only and counts must be
  runtime snapshots (as designed). Timestamps parse as Unix-ms **UTC** (session min 2026-06-28
  23:20:52Z = 19:20 EDT; the earlier inspection that called it "19:20 UTC" mislabeled local as UTC).
- QC on ~1.03M fixes: **valid 0.967**, dropouts 0.009, impossible jumps 0.021 (raw speed > 200 in/s),
  low-anchor 0.004. Stationary **jitter floor ≈ 7.0 in (≈18 cm)** median RMS.
- Proximity reliability: all three thresholds (0.5/1/2 m = 19.7/39.4/78.7 in) flagged **reliable**
  because the 7.0-in floor < ½·19.7 in. Exploratory activity-vs-temperature Spearman ρ≈0.34 (n=13 h,
  p n/a without scipy), labeled exploratory with unverified ~5-min alignment.
- Cleaned CSV row count == raw row count (flags added, nothing deleted).
- GUI helpers verified headless (8 ROI slots; read-only occupancy background; valid JSON written).

## QC output

Outputs written to `D:\Wiser_plot\wiser_pilot_output_YYYYMMDD_HHMM\` (off C:, off git, never
overwrites): `wiser_cleaned_pilot.csv` (~214 MB), `tag_qc_summary.csv`, `occupancy_summary.csv`,
`movement_summary.csv`, `fixed_position_error_summary.csv`, `activity_weather_hourly.csv`,
12 figures under `figures/`, `pilot_conclusion.txt`, `run_manifest.json`, `filtering_log.txt`.

## Known limitations / next steps

- **WISER frame is not georeferenced** to the physical 40×20 ft paddock (offset origin); edge/wall and
  ROI results are provisional until real ROIs/boundary are placed via `place_wiser_rois.py`.
- The shipped `wiser_rois.json` is a placeholder (confirmed=false) → out-of-bounds is informational.
- Session is < 24 h (one night) → analysis is **hourly/diel exploratory, NOT circadian**; acclimation
  windows are descriptive, not evidence of stable territory.
- Active-fraction is sensitive to the speed threshold vs the jitter floor — treat as relative.
- `cv` env lacks scipy/jupyter/IPython; the notebook needs a Jupyter kernel with
  pandas/numpy/matplotlib (scipy optional, only for the activity-vs-temp p-value).
- Per-tag → animal mapping is intentionally absent (tags anonymous).

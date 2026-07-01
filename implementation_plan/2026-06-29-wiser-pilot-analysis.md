# WISER UWB Pilot Analysis Pipeline

## Goal and motivation

Build a reusable pilot-study pipeline that decides whether a WISER UWB rat-tracking dataset is usable
*before* any behavioral interpretation, then produces QC, spatial, social, refuge, acclimation, and
exploratory activity-vs-weather analyses for the 6-rat outdoor paddock pilot. The pipeline must be
rerunnable on future WISER folders and must protect the (possibly live) source data.

## Current problem at time of planning

- We have a first multi-hour free-moving WISER session but no end-to-end pipeline that establishes, in
  one place, *at what spatial/temporal scale the tracking is trustworthy* and feeds that verdict into
  the behavioral analyses.
- The existing `src/` library standardises only `shortid, ts_raw, x, y, z` and **drops** the QC-bearing
  columns (`anchors_used`, `calculation_error`, `battery_voltage`) — so a new richer loader is needed.
- The data folder mixes a live session sqlite, a stationary-test sqlite, and an overlapping CSV
  extract; naive folder loading double-counts.
- The WISER coordinate frame is **not** verified against the physical paddock, so out-of-bounds and
  ROI logic must be conservative.

## Why now

Pilot data is in hand (`D:\Wiser\data`) plus matching weather (`D:\weather_data`). A QC-first verdict
is needed to decide whether to proceed to formal behavior analysis and what to fix before the next
recording.

## Affected modules / files

New (in `wiser_tracking_analysis/`):
- `src/wiser_analysis_utils.py` — reusable pilot functions (builds on `wiser_io`, `time_utils`,
  `metrics`, `plotting`; adds a rich loader that preserves QC columns).
- `scripts/place_wiser_rois.py` — matplotlib ROI-placement GUI (separate from CV `place_cameras.py`).
- `notebooks/wiser_pilot_analysis.ipynb` — QC-first Jupyter notebook (thin client over the module).
- `configs/wiser_rois.json` — provisional ROI/boundary placeholder; real one written by the GUI.

Docs: `data_manifests/2026-06-29-wiser-pilot.yaml`, `change_log/2026-06-29-wiser-pilot-analysis.md`,
index updates.

## Expected inputs / outputs

Inputs (read-only): `D:\Wiser\data\1stcohort_2026.sqlite` (free-moving, table `Position`, **may be
live**), `tag_reports.sqlite` (stationary baseline, table `reports`),
`configs/fixed_position_ground_truth.csv`, `D:\weather_data\AWN-*.csv`. **Exclude** `test20260622.csv`
(overlaps `tag_reports.sqlite`).

Outputs to a timestamped folder `D:\Wiser_plot\wiser_pilot_output_YYYYMMDD_HHMM\` (never under
`D:\Wiser`, never overwrites): `wiser_cleaned_pilot.csv`, `tag_qc_summary.csv`,
`occupancy_summary.csv`, `movement_summary.csv`, `fixed_position_error_summary.csv`,
`activity_weather_hourly.csv`, `figures/*.png`, `run_manifest.json`, `filtering_log.txt`.

## Public parameters / API (key functions in `wiser_analysis_utils.py`)

- `load_wiser_session(path, table=None)` → rich read-only loader keeping
  `shortid, ts_raw, x, y, z, calculation_error, anchors_used, battery_voltage, reportid`.
- `add_speed(df)` → `dt_s, speed_inps_raw, speed_inps_smooth` (smoothed via rolling-median position).
- `add_validity_flags(df, *, boundary=None, jitter_floor=None, max_speed_inps, gap_factor)` →
  `low_anchor_flag, gap_flag, jump_flag, outside_provisional_bounds, valid`. Out-of-bounds enters
  `valid` only when `boundary` comes from a confirmed ROI file. Flags only; never deletes rows.
- `resample_common_grid(df, bin_s)`, `pairwise_distances(grid)`,
  `proximity_summary(dist_long, thresholds_in, jitter_floor)` (per-threshold reliability flag).
- `occupancy_map(...)`, `infer_candidate_zones(df, k)` ("candidate high-occupancy clusters" only).
- `roi_time_and_transitions(df, roi_cfg)`, `hourly_activity(df)`, `acclimation_windows(df)`,
  `distance_to_edge(df, boundary)`.
- `load_weather(path)`, `merge_activity_weather(hourly, weather, bin)`,
  `plot_hourly_activity_by_clock(...)`, `plot_activity_vs_temperature(...)`,
  `plot_weather_timeseries(...)`.
- `write_run_manifest(...)`, `write_filtering_log(...)`, `build_pilot_report(...)`.

## Units, timestamp, coordinate assumptions

- **Units = INCHES** (repo convention; `fixed_position_ground_truth.csv` in inches). Metric social
  thresholds converted via 1 in = 2.54 cm: 0.5/1/2 m → 19.7/39.4/78.7 in.
- WISER timestamps: **Unix ms, UTC**, clock = WISER acquisition PC. Weather timestamps: AWN station,
  local **EDT (−04:00)**, 5-min interval → converted to UTC. WISER↔weather alignment is by wall-clock
  UTC only, **unverified** to better than ~5 min.
- WISER frame has an **offset origin** (x not from 0); mapping to the physical 40×20 ft paddock and to
  the CV `field_layout.json` frame is **UNVERIFIED**. Paddock boundary and ROIs are defined in the
  WISER inch frame (GUI or inferred). Reference paddock size 480×240 in is configurable, not assumed
  aligned.

## Expected behavior

QC gates interpretation: the notebook first reports the trustworthy spatial/temporal scale (jitter
floor from the stationary baseline, dropout rate, jump rate, usable bins). Behavior/social/refuge/
activity sections come after and restate caveats. The live DB is re-queried at runtime (no hard-coded
counts); the analysis snapshot time + actual row count + min/max timestamp are recorded.

## Verification

- `py_compile` the module + GUI; execute the notebook end-to-end in conda env `cv`.
- Read the live DB read-only; record runtime snapshot; verify structural facts only (6 tags, Unix-ms
  UTC timestamps, per-tag monotonic after sort); confirm no writes under `D:\Wiser`.
- Stationary-baseline jitter ≈ existing `outputs/fixed_position_summary.csv`.
- Weather parses tz-aware, overlaps the session; merged hourly table non-null temperature; all
  required CSVs + figures produced and non-empty; cleaned rows == raw rows.
- GUI smoke test writes a valid `wiser_rois.json`.

## Non-goals

No social-preference claims; no verified paddock georeferencing / WISER↔CV transform; no "circadian"
labeling (< 24 h → "hourly / diel exploratory"); no day/night split (hourly only, per user); no writes
to `D:\Wiser` or the live DB; do not modify the CV camera-merging GUI; do not load CSV + sqlite of the
same session together. Units stay inches.

# Hourly WISER Occupancy Maps

## Goal And Motivation

Produce, for each completed 1-hour window, a single PNG occupancy map from the live WISER UWB
tracking database — a multi-panel figure with one position-density heatmap per tag plus a combined
overlay panel of all animals. First validate on a single completed hour, then generate one PNG per
hour automatically. This gives a quick, comparable visual of where each animal spent its time, hour
by hour, throughout the field season.

## Current Problem

`wiser_tracking_analysis/` can analyse a finished session folder, but has no hour-resolved spatial
view and nothing safe to point at the *live* database. The existing folder loader
(`load_wiser_folder`) is also unsuitable here: `D:\Wiser\data` holds two `.sqlite` files plus a
`.csv`, which it would load together and double-count, and it opens SQLite with a read-write
connection — unacceptable against a database being actively written by the recorder.

## Why This Is Needed Now

The 1st-cohort recorder is streaming continuously into `D:\Wiser\data\1stcohort_2026.sqlite`
(~84k rows/hour across 6 tags). Hourly occupancy maps provide an ongoing QC and behavioural-overview
trail during the season. Reads must not disturb the live writer — protecting the raw stream is the
top priority.

## Relevant State

- Live DB: `D:\Wiser\data\1stcohort_2026.sqlite`, single table `Position`, WAL mode, growing live.
- Schema maps to the canonical `wiser_io` aliases: `location_x→x`, `location_y→y`, `location_z→z`,
  `timestamp→ts_raw` (Unix ms), `shortid→shortid`.
- 6 tags: `12378, 12380, 12386, 12395, 12407, 12409`.
- Coordinates are in inches in WISER's own frame (observed extent x≈165–786, y≈540–930); not zeroed
  at the paddock corner. WISER spatial resolution is ~3–4 inches.
- `time_utils.convert_timestamps` already handles `unix_ms`; `plotting._tag_colors` gives stable
  per-tag colours.

## Affected Files

- Update `wiser_tracking_analysis/src/wiser_io.py` — read-only SQLite open + `load_sqlite_window`.
- Update `wiser_tracking_analysis/src/plotting.py` — add `plot_occupancy_grid`.
- Add `wiser_tracking_analysis/scripts/plot_hourly_occupancy.py`.
- Add `wiser_tracking_analysis/install_wiser_occupancy_task.ps1` (hourly scheduled task).
- Add cached extent `wiser_tracking_analysis/configs/arena_extent.json` (generated at runtime).
- Update implementation/change-log indexes.

## Inputs And Outputs

Inputs:
- One bounded timestamp window of the `Position` table per plotted hour, read strictly read-only.

Outputs:
- `wiser_tracking_analysis/outputs/occupancy/occupancy_<tz>_YYYY-MM-DD_HH.png`, one per completed
  hour (git-ignored). ~0.3–0.7 MB each; ~10–17 MB/day; ~1–2 GB for the season.
- `configs/arena_extent.json` — fixed plot extent, computed once and reused.

## Timestamp And Synchronization Assumptions

- `timestamp` is Unix milliseconds (UTC). Hour buckets are labelled in a chosen tz (`local` default,
  `utc` option); the tz is encoded in the filename to avoid ambiguity.
- "Completed hour" is decided from the **DB's own `MAX(timestamp)`**, never the system wall clock:
  the in-progress hour is the bucket containing `MAX(timestamp)`; only strictly earlier buckets are
  plotted. This is single-device WISER data — no cross-device clock alignment is claimed.

## Expected Behavior

- Open the live DB read-only: `file:<path>?mode=ro` + `PRAGMA query_only=ON`. Never `immutable=1` /
  `nolock=1` (would ignore the writer's WAL/locks and risk torn reads).
- Query a single bounded hour window at a time — never the whole table, no full-table percentile
  scans. Extent computed once from one cheap bounded read (or CLI override) and cached.
- Per hour, render 6 per-tag panels + 1 all-animals overlay. Two `--style` options share the same
  fixed extent: `scatter` (default — per-tag points coloured by time within the hour, fastest QC) and
  `occupancy` (2-D density heatmap, 4-inch bins, log-scaled). `scatter` was made the default after
  review: for a fast "is each tag healthy / where is it" check it reads faster than the heatmap.
- Phase 1: `--hour` plots one specified hour. Phase 2: `--backfill` plots every completed hour,
  skipping the in-progress hour and any hour whose PNG already exists (idempotent; `--force`
  overwrites). Hourly scheduled task runs backfill.
- Refuse to run if `--output` resolves under `D:\Wiser` or the source DB's parent directory.

## Verification

- Run `--hour` on a completed bucket; inspect the PNG (6 tag panels + overlay, ~14k fixes/tag).
- Confirm the writer is undisturbed: `SELECT COUNT(*)` keeps climbing before/after the run.
- Run `--backfill` twice: one PNG per completed hour, current hour skipped, second run a no-op.
- Register the hourly task **only after** the Phase-1 single-hour run is verified; confirm a new PNG
  appears automatically after the next top-of-hour.

## Non-Goals

- No writes of any kind to `D:\Wiser`; no deletion/rename/rewrite of source data.
- No cross-device time alignment or trajectory/behaviour modelling in this change.
- No use of `load_wiser_folder` against the live data folder (double-count / read-write risk).

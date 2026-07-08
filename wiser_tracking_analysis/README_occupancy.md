# Hourly WISER position / occupancy maps

One PNG per completed 1-hour window from the **live** WISER tracking database — a per-tag grid of
panels plus an all-tags overlay. Two styles:

- `--style scatter` (default): per-tag position scatter, points coloured by time within the hour
  (early → late). Fast QC: shows movement, coverage, dropouts, and frozen tags at a glance.
- `--style occupancy`: per-tag 2-D position-density heatmaps (log-scaled, 4-inch bins). Shows dwell.

## Safety (do not weaken)

- Reads are strictly **read-only**: `file:<db>?mode=ro` + `PRAGMA query_only=ON`, one bounded
  one-hour query at a time. Never `immutable=1` / `nolock=1` (the DB is a live WAL writer).
- The **in-progress hour is never plotted** — "completed" is decided from the DB's own
  `MAX(timestamp)`, not the system clock.
- The script **refuses** any `--output` equal to or under the source DB tree `D:\Wiser`
  (a sibling like `D:\Wiser_plot` is fine).
- Nothing is ever written to `D:\Wiser`. Outputs default to **`D:\Wiser_plot`** — off the C: drive
  and outside the git repo. The plot-extent cache (`arena_extent.json`) is written there too.

## Run (conda env `cv`: pandas, numpy, matplotlib)

```bash
cd wiser_tracking_analysis

# Verify a single completed hour first (UTC or local):
python scripts/plot_hourly_occupancy.py --hour 2026-06-29T01 --tz utc

# Plot every completed hour not yet plotted (idempotent; default mode):
python scripts/plot_hourly_occupancy.py --backfill

# Custom window (either side optional: omit --from for DB start, --to for DB end):
python scripts/plot_hourly_occupancy.py --to 2026-06-28T20:00            # beginning -> 8 PM
python scripts/plot_hourly_occupancy.py --from 2026-06-28T19:30 --to 2026-06-28T21:15

# Occupancy heatmap instead of scatter:
python scripts/plot_hourly_occupancy.py --backfill --style occupancy

# Continuous fallback (prefer the scheduled task below):
python scripts/plot_hourly_occupancy.py --watch
```

Key flags: `--db` (default `D:\Wiser\data\1stcohort_2026.sqlite`), `--output`
(default `D:\Wiser_plot`), `--tz local|utc`, `--style scatter|occupancy`, `--bin-inches`
(occupancy), `--xmin/--xmax/--ymin/--ymax` + `--refresh-extent` (override/recompute the cached plot
extent `<output>/arena_extent.json`), `--force` (overwrite / allow the in-progress hour).

Output files: `D:\Wiser_plot\<style>_<tz>_YYYY-MM-DD_HH.png`
(~0.3–0.7 MB each; ~10–17 MB/day; ~1–2 GB per season).

## Hourly scheduled task (field PC)

Install **after** a manual single-hour run looks correct. From an elevated PowerShell:

```powershell
.\install_wiser_occupancy_task.ps1 -RunNow
# options: -Style scatter|occupancy  -Tz local|utc  -DbPath <path>  -OutputDir <path>  -PythonExe <path>
```

Runs `--backfill` hourly as SYSTEM. Point `-DbPath` at a new `.sqlite` for later cohorts.

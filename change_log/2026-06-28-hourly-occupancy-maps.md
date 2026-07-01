# Hourly WISER Occupancy / Position Maps

## Date

2026-06-28. Change is currently uncommitted.

## Plan

Implemented from
[`implementation_plan/2026-06-28-hourly-occupancy-maps.md`](../implementation_plan/2026-06-28-hourly-occupancy-maps.md).

## What Changed

- Updated `wiser_tracking_analysis/src/wiser_io.py`:
  - Added `_connect_readonly()` — opens SQLite with `file:<path>?mode=ro` URI plus
    `PRAGMA query_only=ON`. `load_wiser_sqlite()` now uses it, so all SQLite reads are read-only.
    Deliberately avoids `immutable=1` / `nolock=1` (unsafe against a live WAL writer).
  - Added `load_sqlite_window(path, start_ms, end_ms, table, ts_col)` — one bounded
    `WHERE timestamp >= ? AND timestamp < ?` query, returns a standardised DataFrame.
  - Added `sqlite_time_bounds(path, table, ts_col)` — cheap read-only `MIN/MAX(timestamp)` used to
    decide the in-progress hour from the DB's own data, not the wall clock.
- Updated `wiser_tracking_analysis/src/plotting.py`:
  - Added `plot_hourly_scatter()` — per-tag position scatter (points coloured by time within the
    hour) + all-tags overlay. Fast QC default.
  - Added `plot_occupancy_grid()` — per-tag 2-D position-density heatmaps (log-scaled, 4-inch bins) +
    all-tags overlay, over a fixed extent.
- Added `wiser_tracking_analysis/scripts/plot_hourly_occupancy.py`:
  - `--style scatter|occupancy` (scatter default), `--hour` (single hour), `--backfill` (default,
    every completed hour not yet plotted), `--watch` (hourly loop fallback), `--tz local|utc`.
  - Completed-hour logic driven by `sqlite_time_bounds`; in-progress hour skipped; idempotent
    skip-existing; output-path guard refuses anything under the source DB tree / `D:\Wiser`.
  - Plot extent computed once from a bounded read and cached to `configs/arena_extent.json`.
- Added `wiser_tracking_analysis/install_wiser_occupancy_task.ps1` — registers an hourly SYSTEM
  scheduled task running the script in `--backfill` mode.
- Added `wiser_tracking_analysis/README_occupancy.md`.
- Added implementation/change-log index rows.

## Why

The WISER recorder streams 6 tags continuously into a live SQLite DB. A fast, hour-resolved spatial
view (where each animal is / whether each tag is healthy and moving) was missing, and the existing
folder loader was unsafe here — it opens SQLite read-write and would double-count the two `.sqlite`
files plus the `.csv` in `D:\Wiser\data`. Protecting the live capture is the priority, so every read
is strictly read-only and bounded to one hour.

## Verification

Commands run (conda env `cv`, against the live DB):

```bash
python scripts/plot_hourly_occupancy.py --hour 2026-06-29T01 --tz utc --style scatter
python scripts/plot_hourly_occupancy.py --hour 2026-06-29T01 --tz utc --style occupancy
python scripts/plot_hourly_occupancy.py --backfill --tz utc        # ran twice
python scripts/plot_hourly_occupancy.py --backfill --output "D:\Wiser\occ"   # guard
python scripts/plot_hourly_occupancy.py --hour 2026-06-29T02 --tz utc        # in-progress guard
```

Observed behavior:

- Single-hour scatter and occupancy PNGs rendered: 6 per-tag panels (~13.9k fixes/tag) + all-tags
  overlay + colorbar. Extent derived once (x[198,798] y[517,962] in) and cached.
- Live writer undisturbed: `Position` row count rose 229,677 → 230,547 across the reads.
- Backfill plotted hours 23 (Jun 28, partial first bucket) and 00 (Jun 29), skipped the existing 01,
  skipped the in-progress hour 02. A second backfill was a full no-op (idempotent).
- Output-path guard refused `D:\Wiser\occ` (exit 1). In-progress-hour `--hour 02` refused without
  `--force` (exit 1).
- `py_compile` clean on all Python; PowerShell installer passes `Parser::ParseFile`.

## QC Output

PNGs default to `D:\Wiser_plot` (off the C: drive, outside the git repo; the `arena_extent.json`
cache lives there too), named `<style>_<tz>_YYYY-MM-DD_HH.png`. ~0.3–0.7 MB each; ~10–17 MB/day;
~1–2 GB for the season. The installer's `-OutputDir` defaults to the same path.

## Update (2026-06-29) — rat identities on per-tag panel titles

The 6 WISER `shortid`s now resolve to animal identities on the per-tag scatter/occupancy panels.

- Added `wiser_tracking_analysis/configs/rat_identities.csv` (the explicit mapping CLAUDE.md calls
  for): `shortid, name, physical_tag_id, coband_color, pattern, ink_color` for Siesta(305a/12378),
  Sen(306b/12395), Dormi(3077/12407), Nox(3062/12386), Sova(3079/12409), Hypnos(305c/12380).
- `src/plotting.py`: added `load_rat_identities()` (cached CSV loader) + `_tag_panel_title()`; the
  per-tag panel title in `plot_hourly_scatter` and `plot_occupancy_grid` now reads e.g.
  `"Siesta  (305a / 12378)\nBlue · Vertical Line   n=11,783"`. Falls back to `"Tag <id>"` if the
  mapping file is missing; an `N/A` pattern (Dormi) is omitted. The all-tags overlay still uses
  shortids. The anonymous pilot notebook is unchanged.
- Verified: `scatter_local_2026-06-29_02` renders all 6 names/colours/patterns correctly; `py_compile`
  clean.
- **Sova (3079 / 12409) is deceased (died on/around 2026-06-29; RIP).** The tag was struck through on
  the source identity sheet. Its WISER `shortid` is retained in the mapping for provenance, but any
  post-mortem fixes from tag 12409 are not valid behavioral data and should be treated with caution /
  excluded when interpreting occupancy, activity, social, and route-following results that cover the
  death window.

## Known Limitations

- The first/last buckets of a recording can be partial hours (plotted as completed once the DB max
  has moved past them); they simply contain fewer fixes.
- Local-tz bucketing does not special-case DST transitions (irrelevant for UTC mode).
- The hourly scheduled task targets `1stcohort_2026.sqlite` by default; point `-Db` at a new file for
  later cohorts. Install the task only after a manual single-hour run is confirmed.

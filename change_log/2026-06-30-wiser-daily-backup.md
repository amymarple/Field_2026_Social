# Daily WISER backup — SQLite snapshot + incremental CSV, to E:

## Date

2026-06-30. Change is currently uncommitted.

## Plan

Implemented from
[`implementation_plan/2026-06-30-wiser-daily-backup.md`](../implementation_plan/2026-06-30-wiser-daily-backup.md).

## What changed

- `wiser_tracking_analysis/scripts/backup_wiser_daily.py` — once-a-day backup of the live WISER DB.
  Standard-library only. **Reads the live DB exactly once per run** (an online-backup snapshot via
  `sqlite3` `Connection.backup`, source opened `mode=ro` + `PRAGMA query_only=ON`, temp file + atomic
  rename); the incremental CSV and all checks are then derived from the **snapshot**, never the live
  DB. Streams rows with `timestamp > last_max` (state in `backup_state.json`) to a gzip CSV with all
  raw columns. Retention keeps the last `--keep-snapshots` snapshots (default 7); incremental CSVs are
  all kept. Refuses a dest under `D:\Wiser`; `--also-baseline` snapshots `tag_reports.sqlite` once;
  `--dry-run`, `--force`. Never overwrites an existing day's CSV (a `--force` re-run gets a timestamp
  suffix).
- `wiser_tracking_analysis/install_wiser_backup_task.ps1` — registers a SYSTEM **daily** task
  (default 03:30) running the script, mirroring `install_wiser_occupancy_task.ps1` (elevation check,
  dest guard refusing `D:\Wiser`, `-RunNow`).
- `wiser_tracking_analysis/README_backup.md` — usage + restore steps.
- Index rows added to `change_log/README.md` and `implementation_plan/README.md`.

## Why

The WISER tracking data is a single, continuously-written SQLite file kept for a Jun–Oct season — a
single point of failure. A daily snapshot bounds data loss to ≤1 day and the incremental CSVs give a
portable, schema-independent archive. The user specifically wanted the live DB read **at most once a
day** so the recorder is not disturbed; the snapshot-first / derive-from-snapshot design guarantees
exactly one read of the live source per run.

## Source data used for verification

Read **read-only**: `D:\Wiser\data\1stcohort_2026.sqlite` (live). Outputs to a scratch `--dest` (never
`E:`/`D:\Wiser`) during testing.

## Verification performed

conda env `cv`:
```
python -m py_compile scripts/backup_wiser_daily.py
python scripts/backup_wiser_daily.py --dest <scratch> --dry-run     # plan only, no writes
python scripts/backup_wiser_daily.py --dest <scratch>               # run 1
python scripts/backup_wiser_daily.py --dest <scratch> --force       # run 2
```
Observed (live DB grew across runs, as expected):
- Run 1: snapshot `1stcohort_2026_2026-06-30.sqlite` (356.9 MB), **quick_check=ok**, 3,572,702 rows;
  bootstrap incremental CSV 3,572,702 rows (97.5 MB gz).
- Run 2 (`--force`): incremental exported only the **2,327 new rows** since the snapshot max; state
  advanced (`last_max_ts` updated); the prior day's CSV was preserved (timestamp-suffixed second
  file), nothing overwritten.
- Snapshot reopens via `wiser_io.load_wiser_sqlite` (3,578,146 rows, correct `sqlite_time_bounds`) →
  analysis-ready. Incremental gz reloads via `pandas.read_csv(compression="gzip")` with all raw
  columns (`location_x/y`, `anchors_used`, `calculation_error`, `timestamp`, …).
- Installer `Parser::ParseFile` clean.

## QC output

`E:\Wiser_backup\{snapshots,incremental}\…`, `backup_state.json`, `backup_log.txt` (one line per run).

## Update (2026-06-30b) — strategy review: daily snapshot chosen over hourly

Considered making the hourly occupancy task also store per-hour rows (merge per day) as a "safer,
lighter" alternative. Investigated and **kept the daily snapshot**:
- The `Position` table has **no `timestamp` index** (`EXPLAIN QUERY PLAN` → `SCAN Position`), so a
  one-hour query is a **full table scan** (0.53 s on 3.5M rows now, growing). 24 scans/day would hold
  the read lock *more* in total (~13 s/day) than the single snapshot, not less.
- The snapshot is a raw page-copy (index-independent), measured **~0.74 s** — well under the writer's
  5 s `busy_timeout`, so it drops no fixes. It is the least, shortest live-DB contact per day.
- An index would fix the scan cost but requires writing to the live DB (locks the writer during
  CREATE INDEX) — not done.

Tweaks for "safe + proper + no piles of duplicated files":
- `--keep-snapshots` default **7 → 2** (few restore points; the append-only, non-overlapping
  incremental CSVs are the complete archive — each row stored once).
- Default schedule **03:30 → 13:00** (rats' low-activity window → least WISER writing; no collision
  with the hourly occupancy task at :05).

## Update (2026-06-30c) — backup drive placement verified (keep E:)

Checked whether the backup should move to D: (since E: is the busy RTSP drive). Disk topology
(read-only `Get-Partition`/`Get-PhysicalDisk`): **D:** = Disk 1 WD_BLACK SN7100 NVMe SSD (the source,
99% free); **E:** = Disk 0 WD Purple WD221PURP 20 TB SATA surveillance HDD (92% / 18.8 TB free); plus a
4 TB SanDisk USB SSD (Disk 3). D: and E: are **separate physical disks**, so E: survives a D: failure —
D: would co-locate the backup with the source and is rejected. The ~360 MB/day write is trivial on a
20 TB surveillance HDD. **Kept E:**; documented the rationale + the disk facts in `README_backup.md`
(and flagged the USB SSD as the future off-machine tier). No code/schedule/retention change.

## Update (2026-06-30d) — off-machine USB tier wired into copy_day_to_usb.ps1

Added an off-machine copy so the tracking DB survives losing the whole PC (fire/theft/PSU), not just a
D: disk failure. `reolink_record/copy_day_to_usb.ps1` now also mirrors `E:\Wiser_backup` ->
`<USB>\Wiser_backup` on every USB hand-off:
- New `Invoke-WiserBackupSweep` + params `-SkipWiserBackup` / `-WiserSource` (default `E:\Wiser_backup`).
- Runs **before** the video copy so it happens even on a no-video day; **additive** (copies
  missing/changed files, never deletes on the USB — the USB keeps the full history even after E:
  prunes old snapshots); verified per file (size, or SHA-256 with `-Hash`); guarded to write only under
  `<USB>\Wiser_backup`; skips if the WISER source is on the USB drive. Independent of the video exit
  code (a WISER failure warns, doesn't change it). Read-only at the source.
- Verified on the real `E:\Wiser_backup`: dry-run lists the 5 files; a real sweep copied all 5
  (608.6 MB: 2 snapshots + the incremental CSV + state + log) into `<USB>\Wiser_backup` preserving the
  tree; re-run was idempotent (copied 0, already-present 5). `Parser::ParseFile` clean.

## Update (2026-06-30e) — `--backfill-day` for past days

Added a `--backfill-day yyyy-mm-dd` mode: extracts ONE past **local (EDT)** calendar day's rows from
the newest snapshot into `incremental/<stem>_<day>.csv.gz` and logs it — no snapshot, no state change,
no live-DB read. `--tz-offset-hours` (default -4) sets the day boundary; `--force` overwrites.
Used to backfill the days captured in the late first run:
- `2026-06-28.csv.gz` — 381,109 rows (10.5 MB gz; recording start 19:20 EDT → midnight).
- `2026-06-29.csv.gz` — 1,854,559 rows (50.7 MB gz; full day).
Both verified (gz reloads, row counts + ts ranges match) and logged in `backup_log.txt`.

Note: the first-run bootstrap `2026-06-30.csv.gz` still contains 28+29+30 together, so 28/29 now exist
both there and in their own per-day files — fine for hand-off/restore, but rebuild the full history
from the per-day files OR the bootstrap, not both. Re-cut the bootstrap to June-30-only with
`--backfill-day 2026-06-30 --force` if a strictly non-overlapping archive is wanted.

## Known limitations / next steps

- E: is the video-recording drive; backups are tiny (~100 MB bootstrap, then a few MB/day) vs
  ~400 GB/day video, and `reolink_record\disk_space_check.ps1` already guards E:. Still single-site
  (E: only) — an off-site/USB copy would add a second tier.
- Daily full snapshots of a growing DB cost O(DB size) each; bounded by `--keep-snapshots`. The
  incremental CSVs remain the complete history.
- Restore is manual (documented in README_backup.md). Install the task only after a manual run is
  confirmed.

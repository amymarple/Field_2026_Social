# WISER daily backup

Once-a-day backup of the live WISER tracking database, to **E:** (a different
physical disk from the **D:** source, so it survives a D: disk failure).

## What it produces (under `E:\Wiser_backup`)

- `snapshots/1stcohort_2026_YYYY-MM-DD.sqlite` — a full, consistent SQLite copy
  (restore-grade; open it directly with any tool). Only the last `--keep-snapshots`
  are kept (**default 2**) — deliberately few, so full-DB copies don't pile up.
- `incremental/1stcohort_2026_YYYY-MM-DD.csv.gz` — gzipped CSV of that day's **new**
  rows (all raw columns). Kept **forever**. The incrementals are append-only with
  **no overlap** — each row stored once — so they are the complete, non-duplicated
  history and few snapshots are needed.
- `backup_state.json` — per-DB last exported timestamp (drives the incremental).
- `backup_log.txt` — one line per run.

## Design / safety

- **Reads the live DB exactly once per run** (the snapshot, via the SQLite online
  backup API). The incremental CSV and all checks are derived from the *snapshot*,
  never the live DB — so the recorder is not disturbed. The snapshot is a raw
  page-copy (index-independent), measured at **~0.74 s** for a 360 MB DB — far
  under the writer's 5 s `busy_timeout`, so no fixes are dropped. (An hour-by-hour
  approach was considered but rejected: the table has **no `timestamp` index**, so
  each "one hour" query is a full table scan — 24 scans/day would hold the lock
  *more* in total, not less.)
- Scheduled at **13:00** (rats' low-activity window → least WISER writing); does
  not collide with the hourly occupancy task (:05, a bounded read).
- Strictly **read-only** on the source (`mode=ro` + `PRAGMA query_only=ON`); never
  writes under `D:\Wiser`. Standard-library only (`sqlite3`/`gzip`/`csv`).
- Snapshots are verified with `PRAGMA quick_check`; the incremental keys off the
  stored max timestamp, so a missed day self-heals (no gap).

## Run manually (verify first)

```bat
conda activate cv
cd wiser_tracking_analysis
python scripts\backup_wiser_daily.py --dry-run                 :: plan only, no writes
python scripts\backup_wiser_daily.py --also-baseline           :: real run -> E:\Wiser_backup
```
The first real run also writes a full bootstrap incremental CSV (~100 MB gz);
subsequent days are small deltas.

## Install the daily task (run once, elevated)

```powershell
# Administrator PowerShell
cd wiser_tracking_analysis
.\install_wiser_backup_task.ps1 -RunNow          # SYSTEM task, daily 13:00
```

## Restore

- **Full restore:** copy the chosen `snapshots\..._YYYY-MM-DD.sqlite` back to
  `D:\Wiser\data\` (with the recorder stopped), or just point analysis scripts at
  the snapshot path — it loads exactly like the live DB
  (`wiser_io.load_wiser_sqlite`).
- **From CSVs:** `pandas.read_csv(path, compression="gzip")`; concatenate the daily
  files in date order for the full history. Raw column names are preserved
  (`location_x/y`, `anchors_used`, `calculation_error`, `timestamp`, …).

## Backup drive placement (verified 2026-06-30)

Why **E:**, not D: — confirmed from the actual disk topology:

| Vol | Physical disk | Type | Size / free | Role |
|-----|---------------|------|-------------|------|
| D:  | Disk 1 — WD_BLACK SN7100 | NVMe SSD | 3.7 TB / 99% | WISER **source** DB |
| E:  | Disk 0 — WD Purple WD221PURP | SATA HDD (surveillance) | 20 TB / 92% (18.8 TB) | video / RTSP |
| USB | Disk 3 — SanDisk Extreme Pro | USB SSD | 4 TB | removable |

- D: and E: are **separate physical disks**, so a backup on E: **survives a D: failure** — the whole
  point. A backup on D: would sit on the *same* disk as the source and be lost with it → **do not use
  D:**.
- E: being the busy video drive is not a problem: it is a 20 TB surveillance-grade HDD built for 24/7
  multi-stream writes; the backup adds a single ~360 MB write once a day at 13:00 (~2 GB total vs
  18.8 TB free). No meaningful I/O or space impact, and `reolink_record\disk_space_check.ps1` already
  guards E:.
- **Off-machine tier (enabled):** `reolink_record\copy_day_to_usb.ps1` now also mirrors
  `E:\Wiser_backup` -> `<USB>\Wiser_backup` on every USB hand-off (additive, verified, never deletes;
  `-SkipWiserBackup` to opt out). So whenever a day's video is copied to the SanDisk USB SSD, the
  tracking-DB snapshots + incremental CSVs ride along and get a copy that survives losing the whole PC.

## Notes

- `tag_reports.sqlite` (static fixed-position baseline) is snapshotted once with
  `--also-baseline`.

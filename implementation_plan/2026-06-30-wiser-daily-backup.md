# Daily WISER backup — SQLite snapshot + incremental CSV, to E:

## Goal

Protect the single, continuously-written WISER SQLite DB (`D:\Wiser\data\1stcohort_2026.sqlite`,
~180 MB/day, growing over a Jun–Oct season) against corruption / power loss / disk failure with a
**once-a-day** backup to **E:** (a different physical disk from the D: source).

## Approach

Two artifacts per day under `E:\Wiser_backup`: a full **SQLite snapshot** (restore-grade) and a
gzipped **incremental CSV** of the day's new rows (portable archive). **Hard constraint: read the
live DB exactly once per run** — the snapshot is the single read; the incremental CSV and all
checks are derived from the snapshot.

- `wiser_tracking_analysis/scripts/backup_wiser_daily.py` — stdlib-only (`sqlite3`/`gzip`/`csv`).
  Snapshot via the SQLite **online backup API** (`src_ro.backup(dst)`; source `mode=ro` +
  `query_only=ON`; temp file + atomic rename). Then from the snapshot: `PRAGMA quick_check`,
  `COUNT(*)`, `MIN/MAX(timestamp)`, and a streamed incremental export of rows `> last_max`
  (state in `backup_state.json`) to `incremental/<stem>_YYYY-MM-DD.csv.gz`. Retention keeps the last
  `--keep-snapshots` snapshots (default 7); all incremental CSVs are kept. Refuses a dest under
  `D:\Wiser`. `--also-baseline` snapshots `tag_reports.sqlite` once. `--dry-run`, `--force`.
- `wiser_tracking_analysis/install_wiser_backup_task.ps1` — SYSTEM **daily** task (default 03:30),
  mirrors `install_wiser_occupancy_task.ps1` (elevation check, dest guard, `-RunNow`).
- `wiser_tracking_analysis/README_backup.md` — what it does + restore steps.

CSV-only was rejected (bigger than SQLite; loses schema; a CSV in `D:\Wiser\data` is the documented
double-count footgun). Reuse: `wiser_io.load_wiser_sqlite`/`sqlite_time_bounds` to verify the snapshot
loads like the live DB.

## Verification

conda `cv`, into a scratch `--dest`: dry-run (no writes) → real run (snapshot quick_check ok + full
bootstrap CSV) → second run (only new rows, state advanced). Confirm: source DB only read once;
snapshot reopens via `wiser_io`; gz reloads via `pandas` with all raw columns; no writes under
`D:\Wiser`; retention prunes; installer `Parser::ParseFile` clean + dest guard rejects `D:\Wiser`.

## Non-goals

No recorder/analysis changes; no writes to the source DB; no full daily CSV (incremental only); no
offsite/cloud (E: only, per the user); restore is manual (README).

r"""
backup_wiser_daily.py — once-a-day backup of the live WISER SQLite database.

Two artifacts per day, written to E: (a different physical disk from the D:
source, so they survive a D: failure):
  1. a consistent SQLite **snapshot** (full, restorable as-is), via the SQLite
     online backup API — only the last few are kept (restore points); and
  2. a gzipped **incremental CSV** of that day's new rows (append-only by
     timestamp) — kept forever; together these are the complete, **non-duplicated**
     archive (each row is stored once), so few full snapshots are needed.

Read-once design: the live source DB is read **exactly once** per run (the
snapshot). Everything else — quick_check, row counts, timestamp bounds and the
incremental CSV — is derived from the SNAPSHOT (a static local copy), so the
recorder's database is never re-read. Strictly read-only on the source
(`mode=ro` + `PRAGMA query_only=ON`); it never writes under the source tree.

Self-contained (standard library only): sqlite3, gzip, csv — no pandas / analysis
imports, so it stays a minimal, dependency-light backup tool.

Usage:
    conda activate cv
    cd wiser_tracking_analysis
    python scripts/backup_wiser_daily.py --dry-run          # show plan, no writes
    python scripts/backup_wiser_daily.py                    # snapshot + incremental CSV -> E:\Wiser_backup
    python scripts/backup_wiser_daily.py --also-baseline    # also snapshot tag_reports.sqlite once

Layout under --dest (default E:\Wiser_backup):
    snapshots/<stem>_YYYY-MM-DD.sqlite
    incremental/<stem>_YYYY-MM-DD.csv.gz
    backup_state.json          (per-DB last exported max timestamp)
    backup_log.txt             (one line per run)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

DEFAULT_DB = Path(r"D:\Wiser\data\1stcohort_2026.sqlite")
DEFAULT_BASELINE = Path(r"D:\Wiser\data\tag_reports.sqlite")
DEFAULT_DEST = Path(r"E:\Wiser_backup")
SOURCE_TREE = Path(r"D:\Wiser")          # never write under here


# ---------------------------------------------------------------------------
# Read-only source access (the ONLY contact with the live DB is open_source_ro
# + the backup() call in snapshot_db).
# ---------------------------------------------------------------------------

def open_source_ro(path: Path) -> sqlite3.Connection:
    """Open a SQLite file strictly read-only (safe against the live writer)."""
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA query_only=ON;")
    return con


def detect_table(con: sqlite3.Connection) -> str:
    """Table holding the fixes: prefer 'Position', else the first table with a
    'timestamp' column."""
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for preferred in ("Position", "reports"):
        if preferred in names:
            return preferred
    for name in names:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")').fetchall()]
        if "timestamp" in cols:
            return name
    raise SystemExit(f"[backup] No table with a 'timestamp' column in {names}")


# ---------------------------------------------------------------------------
# Snapshot (single live-DB read) + snapshot-derived helpers
# ---------------------------------------------------------------------------

def snapshot_db(src: Path, dst: Path, dry_run: bool) -> None:
    """Online-backup the live DB to ``dst`` (temp file then atomic rename).
    This is the only read of the live source."""
    if dry_run:
        print(f"  [dry-run] would snapshot {src.name} -> {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".sqlite.part", dir=str(dst.parent))
    os.close(fd)
    tmp = Path(tmp)
    try:
        src_con = open_source_ro(src)
        dst_con = sqlite3.connect(str(tmp))
        try:
            src_con.backup(dst_con)          # consistent online backup
        finally:
            dst_con.close()
            src_con.close()
        os.replace(tmp, dst)                 # atomic
    finally:
        if tmp.exists():
            tmp.unlink()


def snapshot_stats(snap: Path, table: str) -> dict:
    """quick_check + row count + timestamp bounds, all read from the SNAPSHOT."""
    con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    try:
        ok = con.execute("PRAGMA quick_check;").fetchone()[0]
        n, tmin, tmax = con.execute(
            f'SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM "{table}"'
        ).fetchone()
    finally:
        con.close()
    return {"quick_check": ok, "rows": int(n or 0),
            "ts_min": tmin, "ts_max": tmax}


def export_incremental_csv(snap: Path, table: str, out_gz: Path,
                           last_max, dry_run: bool) -> dict:
    """Stream rows with ``timestamp > last_max`` from the SNAPSHOT to a gzip CSV
    (all columns; bounded memory). Returns {rows, ts_min, ts_max}."""
    if dry_run:
        print(f"  [dry-run] would export rows with timestamp > {last_max} -> {out_gz}")
        return {"rows": 0, "ts_min": None, "ts_max": None}
    out_gz.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    rows = 0
    tmin = tmax = None
    fd, tmp = tempfile.mkstemp(suffix=".csv.gz.part", dir=str(out_gz.parent))
    os.close(fd)
    tmp = Path(tmp)
    try:
        where = "" if last_max is None else "WHERE timestamp > :lm"
        cur = con.execute(
            f'SELECT * FROM "{table}" {where} ORDER BY timestamp',
            {"lm": last_max} if last_max is not None else {})
        cols = [d[0] for d in cur.description]
        ts_i = cols.index("timestamp") if "timestamp" in cols else None
        with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(cols)
            for row in cur:
                wr.writerow(row)
                rows += 1
                if ts_i is not None:
                    t = row[ts_i]
                    tmin = t if tmin is None else min(tmin, t)
                    tmax = t if tmax is None else max(tmax, t)
        con.close()
        if rows:
            os.replace(tmp, out_gz)
        else:
            tmp.unlink()                     # nothing new -> no empty file
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"rows": rows, "ts_min": tmin, "ts_max": tmax}


def export_range_csv(snap: Path, table: str, out_gz: Path,
                     start_ms: int, end_ms: int) -> dict:
    """Stream rows with ``start_ms <= timestamp < end_ms`` from the SNAPSHOT to a
    gzip CSV (all columns; bounded memory). Used by --backfill-day."""
    out_gz.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    rows = 0
    tmin = tmax = None
    fd, tmp = tempfile.mkstemp(suffix=".csv.gz.part", dir=str(out_gz.parent))
    os.close(fd)
    tmp = Path(tmp)
    try:
        cur = con.execute(
            f'SELECT * FROM "{table}" WHERE timestamp >= :a AND timestamp < :b '
            'ORDER BY timestamp', {"a": start_ms, "b": end_ms})
        cols = [d[0] for d in cur.description]
        ts_i = cols.index("timestamp") if "timestamp" in cols else None
        with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(cols)
            for row in cur:
                wr.writerow(row)
                rows += 1
                if ts_i is not None:
                    t = row[ts_i]
                    tmin = t if tmin is None else min(tmin, t)
                    tmax = t if tmax is None else max(tmax, t)
        con.close()
        if rows:
            os.replace(tmp, out_gz)
        else:
            tmp.unlink()
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"rows": rows, "ts_min": tmin, "ts_max": tmax}


def backfill_day(db: Path, dest: Path, day_str: str, tz_offset_hours: int,
                 force: bool, dry_run: bool) -> str:
    """
    Backfill one **past** local (EDT) calendar day into
    ``incremental/<stem>_<day>.csv.gz``, read from the newest snapshot (no live-DB
    read). Does NOT snapshot or advance state. For days whose data already exists
    but was never cut into its own daily file (e.g. captured in a late first run).
    """
    stem = db.stem
    snaps = sorted((dest / "snapshots").glob(f"{stem}_*.sqlite"))
    if not snaps:
        return (f"{stem}: BACKFILL {day_str} FAILED - no snapshot in "
                f"{dest / 'snapshots'} (run a normal backup first).")
    snap = snaps[-1]                                  # newest snapshot has all data
    try:
        d = dt.datetime.strptime(day_str, "%Y-%m-%d")
    except ValueError:
        return f"BACKFILL bad --backfill-day '{day_str}' (use yyyy-mm-dd)."
    tz = dt.timezone(dt.timedelta(hours=tz_offset_hours))
    start = dt.datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(dt.timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = start_ms + 86_400_000
    out_gz = dest / "incremental" / f"{stem}_{day_str}.csv.gz"
    if out_gz.exists() and not force:
        return (f"{stem}: BACKFILL {day_str} skipped - {out_gz.name} already "
                f"exists (use --force to overwrite).")
    table = snap_con_table(snap)
    if dry_run:
        con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
        n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE timestamp >= ? '
                        'AND timestamp < ?', (start_ms, end_ms)).fetchone()[0]
        con.close()
        return (f"{stem}: [dry-run] BACKFILL {day_str} would export {n:,} rows "
                f"(local {day_str} 00:00-24:00 = UTC {start_ms}..{end_ms}) "
                f"from {snap.name}.")
    inc = export_range_csv(snap, table, out_gz, start_ms, end_ms)
    mb = (out_gz.stat().st_size / 1e6) if (inc["rows"] and out_gz.exists()) else 0.0
    return (f"{stem}: BACKFILL {day_str} -> {out_gz.name} (from {snap.name}); "
            f"rows={inc['rows']:,} ({mb:.1f} MB gz) "
            f"ts({inc['ts_min']}..{inc['ts_max']}).")


# ---------------------------------------------------------------------------
# State + retention + safety
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def prune_snapshots(snap_dir: Path, stem: str, keep: int, dry_run: bool) -> list:
    snaps = sorted(snap_dir.glob(f"{stem}_*.sqlite"))
    old = snaps[:-keep] if keep > 0 and len(snaps) > keep else []
    for p in old:
        if dry_run:
            print(f"  [dry-run] would prune old snapshot {p.name}")
        else:
            p.unlink()
    return old


def assert_safe_dest(dest: Path) -> None:
    d = dest.resolve()
    src = SOURCE_TREE.resolve()
    if d == src or src in d.parents:
        raise SystemExit(f"[backup] Refusing dest under the source tree {src}: {d}")


# ---------------------------------------------------------------------------
# One DB
# ---------------------------------------------------------------------------

def backup_one(db: Path, dest: Path, *, keep: int, force: bool, dry_run: bool,
               once_only: bool = False) -> str:
    """Snapshot + incremental CSV for a single DB. ``once_only`` (baseline):
    snapshot only if none exists yet, no incremental."""
    stem = db.stem
    today = dt.date.today().isoformat()
    snap_dir = dest / "snapshots"
    snap = snap_dir / f"{stem}_{today}.sqlite"

    if once_only:
        if any(snap_dir.glob(f"{stem}_*.sqlite")) and not force:
            return f"{stem}: baseline snapshot already present; skip."
        snapshot_db(db, snap, dry_run)                 # single read of the source
        if dry_run:
            return f"{stem}: [dry-run] baseline snapshot planned."
        s = snapshot_stats(snap, snap_con_table(snap))   # stats from the snapshot
        return (f"{stem}: baseline snapshot -> {snap.name} "
                f"(rows={s['rows']:,}, quick_check={s['quick_check']}).")

    if snap.exists() and not force:
        return f"{stem}: today's snapshot already exists ({snap.name}); skip (use --force)."

    # 1) THE single read of the live DB
    snapshot_db(db, snap, dry_run)
    if dry_run:
        export_incremental_csv(snap, "Position", dest / "incremental" /
                               f"{stem}_{today}.csv.gz", None, True)
        return f"{stem}: [dry-run] snapshot + incremental planned."

    # 2) everything else from the snapshot
    table = snap_con_table(snap)
    sstats = snapshot_stats(snap, table)

    state_path = dest / "backup_state.json"
    state = load_state(state_path)
    last_max = state.get(stem, {}).get("last_max_ts")

    out_gz = dest / "incremental" / f"{stem}_{today}.csv.gz"
    if out_gz.exists():        # never overwrite an existing day's CSV (e.g. a --force re-run)
        out_gz = dest / "incremental" / f"{stem}_{today}_{dt.datetime.now():%H%M%S}.csv.gz"
    inc = export_incremental_csv(snap, table, out_gz, last_max, dry_run)

    new_max = sstats["ts_max"] if sstats["ts_max"] is not None else last_max
    state[stem] = {"last_max_ts": new_max, "updated": dt.datetime.now().isoformat()}
    save_state(state_path, state)

    prune_snapshots(snap_dir, stem, keep, dry_run)

    gz_mb = (out_gz.stat().st_size / 1e6) if (inc["rows"] and out_gz.exists()) else 0.0
    return (f"{stem}: snapshot {snap.name} (rows={sstats['rows']:,}, "
            f"quick_check={sstats['quick_check']}); incremental rows={inc['rows']:,} "
            f"({gz_mb:.1f} MB gz) ts({inc['ts_min']}..{inc['ts_max']}).")


def snap_con_table(snap: Path) -> str:
    con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    try:
        return detect_table(con)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Once-a-day WISER DB backup (snapshot + incremental CSV).")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--keep-snapshots", type=int, default=2,
                    help="full .sqlite snapshots to retain as restore points "
                         "(default 2 — kept small to avoid piling up duplicated "
                         "full-DB copies). The append-only incremental CSVs are "
                         "always kept and are the complete, non-duplicated archive.")
    ap.add_argument("--also-baseline", action="store_true",
                    help="also snapshot tag_reports.sqlite once (static baseline).")
    ap.add_argument("--baseline-db", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--force", action="store_true",
                    help="re-run even if today's snapshot exists (or overwrite a "
                         "--backfill-day file).")
    ap.add_argument("--dry-run", action="store_true", help="plan only; no writes.")
    ap.add_argument("--backfill-day",
                    help="Backfill ONE past local-day (yyyy-mm-dd): extract its rows "
                         "from the newest snapshot into incremental/<stem>_<day>.csv.gz "
                         "and log it. No snapshot, no state change, no live-DB read.")
    ap.add_argument("--tz-offset-hours", type=int, default=-4,
                    help="Local UTC offset for --backfill-day day boundaries (EDT = -4).")
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"[backup] Database not found: {args.db}")
    assert_safe_dest(args.dest)
    if not args.dry_run:
        args.dest.mkdir(parents=True, exist_ok=True)

    # --- backfill mode: cut one past day from the snapshot, log, and exit ---
    if args.backfill_day:
        line = backfill_day(args.db, args.dest, args.backfill_day,
                            args.tz_offset_hours, args.force, args.dry_run)
        print("  " + line)
        if not args.dry_run:
            log = args.dest / "backup_log.txt"
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")
            print(f"\n  logged -> {log}")
        return

    started = dt.datetime.now()
    print(f"=== WISER daily backup ===\n  source: {args.db}\n  dest:   {args.dest}\n"
          f"  read-once: snapshot first, CSV derived from the snapshot\n")

    lines = [backup_one(args.db, args.dest, keep=args.keep_snapshots,
                        force=args.force, dry_run=args.dry_run)]
    if args.also_baseline and args.baseline_db.exists():
        lines.append(backup_one(args.baseline_db, args.dest, keep=args.keep_snapshots,
                                force=args.force, dry_run=args.dry_run, once_only=True))

    for ln in lines:
        print("  " + ln)

    if not args.dry_run:
        log = args.dest / "backup_log.txt"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{started:%Y-%m-%d %H:%M:%S}] " + " | ".join(lines) + "\n")
        print(f"\n  logged -> {log}")


if __name__ == "__main__":
    main()

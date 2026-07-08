"""
plot_hourly_occupancy.py
========================
Hourly WISER occupancy maps from the *live* tracking SQLite database.

For each completed 1-hour window this writes ONE PNG: a per-tag grid of position
-density heatmaps plus a combined all-animals overlay. Reads are strictly
read-only (``mode=ro`` + ``PRAGMA query_only=ON``) and bounded to one hour at a
time, so the live recorder writing the database is never disturbed.

Safety invariants (do not weaken):
  * Never writes to the source database / its folder. The output path is
    refused if it resolves under the DB's drive folder (e.g. D:\\Wiser).
  * "Completed hour" is derived from the DB's own MAX(timestamp), not the system
    clock. The in-progress hour is never plotted.
  * One bounded timestamp window per query — never a full-table scan.

Usage:
    # Phase 1 — one specific completed hour (verify first):
    python scripts/plot_hourly_occupancy.py --hour 2026-06-29T00 --tz utc

    # Phase 2 — every completed hour not yet plotted (idempotent):
    python scripts/plot_hourly_occupancy.py --backfill

    # Continuous fallback (prefer the scheduled task instead):
    python scripts/plot_hourly_occupancy.py --watch
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.wiser_io import load_sqlite_window, sqlite_time_bounds
from src.plotting import plot_occupancy_grid, plot_hourly_scatter

DEFAULT_DB       = Path(r"D:\Wiser\data\1stcohort_2026.sqlite")
DEFAULT_OUT_DIR  = Path(r"D:\Wiser_plot")          # off the C: drive, never git-tracked
DEFAULT_BIN_IN   = 4.0
EXTENT_PAD_IN    = 12.0       # padding added around observed data when deriving extent
HOUR_MS          = 3_600_000


# ---------------------------------------------------------------------------
# Timezone-aware hour bucketing (timestamps in the DB are Unix ms, UTC)
# ---------------------------------------------------------------------------

def _ms_to_dt(ms: int, tz: str) -> datetime:
    """Unix-ms timestamp -> naive datetime in *tz* (no flooring)."""
    if tz == "utc":
        return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None)
    return datetime.fromtimestamp(ms / 1000)        # local


def _ms_to_hour_dt(ms: int, tz: str) -> datetime:
    """Floor a Unix-ms timestamp to the start of its hour, as a naive dt in *tz*."""
    return _ms_to_dt(ms, tz).replace(minute=0, second=0, microsecond=0)


def _hour_dt_to_ms(dt: datetime, tz: str) -> int:
    """Inverse of :func:`_ms_to_hour_dt`: naive hour-start dt in *tz* -> Unix ms."""
    if tz == "utc":
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return int(dt.timestamp() * 1000)        # naive interpreted as local


def _hour_label(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d_%H")


def _fmt12(dt: datetime) -> str:
    """'7:00 PM' (no leading zero), portable across platforms."""
    return dt.strftime("%I:%M %p").lstrip("0")


def _range_label(start_dt: datetime, end_dt: datetime, tz: str) -> str:
    """Human-readable window for the plot title, e.g.
    '2026-06-28  7:00 PM–8:00 PM (local)'. Repeats the date on the end when the
    window crosses midnight."""
    if end_dt.date() != start_dt.date():
        span = (f"{start_dt.strftime('%Y-%m-%d')} {_fmt12(start_dt)}"
                f"–{end_dt.strftime('%Y-%m-%d')} {_fmt12(end_dt)}")
    else:
        span = f"{start_dt.strftime('%Y-%m-%d')}  {_fmt12(start_dt)}–{_fmt12(end_dt)}"
    return f"{span} ({tz})"


def _interval_label(start_dt: datetime, tz: str) -> str:
    """Title label for a one-hour bucket starting at *start_dt*."""
    return _range_label(start_dt, start_dt + timedelta(hours=1), tz)


def _parse_hour_arg(s: str) -> datetime:
    """Parse --hour ('2026-06-29T00', '2026-06-29 00', '2026-06-29T00:00')."""
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Could not parse --hour '{s}'. Use e.g. 2026-06-29T00")


def _parse_dt(s: str) -> datetime:
    """Parse --from/--to to minute precision (NOT floored to the hour)."""
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Could not parse datetime '{s}'. Use e.g. 2026-06-28T20:00")


# ---------------------------------------------------------------------------
# Safety: never write under the source data tree
# ---------------------------------------------------------------------------

def _assert_safe_output(out_dir: Path, db_path: Path) -> None:
    out_res = out_dir.resolve()
    db_dir = db_path.resolve().parent
    forbidden = [db_dir]
    # Also block the whole D:\Wiser tree regardless of where the DB sits.
    try:
        forbidden.append(Path(db_dir.anchor) / "Wiser")
    except Exception:
        pass
    for bad in forbidden:
        try:
            if out_res == bad or bad in out_res.parents:
                raise SystemExit(
                    f"[occupancy] Refusing to write under the source data tree: "
                    f"{out_res} is inside {bad}. Choose a different --output.")
        except SystemExit:
            raise
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Extent: computed once from a bounded read, then cached & reused
# ---------------------------------------------------------------------------

def _resolve_extent(args, sample_df) -> tuple[float, float, float, float]:
    """Return (xmin, xmax, ymin, ymax). CLI override > cached json > derive+cache."""
    if None not in (args.xmin, args.xmax, args.ymin, args.ymax):
        extent = (args.xmin, args.xmax, args.ymin, args.ymax)
        _write_extent(args.extent_json, extent, source="cli")
        return extent

    if args.extent_json.exists() and not args.refresh_extent:
        data = json.loads(args.extent_json.read_text())
        return (data["xmin"], data["xmax"], data["ymin"], data["ymax"])

    if sample_df is None or sample_df.empty:
        raise SystemExit("[occupancy] No data available to derive plot extent; "
                         "pass --xmin/--xmax/--ymin/--ymax or run on a non-empty hour.")
    p = EXTENT_PAD_IN
    extent = (
        float(sample_df["x"].min()) - p, float(sample_df["x"].max()) + p,
        float(sample_df["y"].min()) - p, float(sample_df["y"].max()) + p,
    )
    _write_extent(args.extent_json, extent, source="derived")
    return extent


def _write_extent(path: Path, extent, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "xmin": extent[0], "xmax": extent[1],
        "ymin": extent[2], "ymax": extent[3],
        "units": "inches", "source": source,
    }, indent=2))
    print(f"  Plot extent ({source}): "
          f"x[{extent[0]:.0f},{extent[1]:.0f}] y[{extent[2]:.0f},{extent[3]:.0f}] in "
          f"-> {path}")


# ---------------------------------------------------------------------------
# Plot one hour
# ---------------------------------------------------------------------------

def _plot_one_hour(hour_dt: datetime, args, extent) -> bool:
    """Load + plot a single hour. Returns True if a PNG was written."""
    start_ms = _hour_dt_to_ms(hour_dt, args.tz)
    end_ms = start_ms + HOUR_MS
    label = _hour_label(hour_dt)
    out_path = args.output / f"{args.style}_{args.tz}_{label}.png"

    if out_path.exists() and not args.force:
        print(f"  [skip] {out_path.name} already exists.")
        return False

    df = load_sqlite_window(args.db, start_ms, end_ms, table=args.table)
    if df is None or df.empty:
        print(f"  [empty] no fixes in {label} ({args.tz}).")
        return False

    if extent is None:
        extent = _resolve_extent(args, df)

    title_label = _interval_label(hour_dt, args.tz)
    print(f"  {label} ({args.tz}): {len(df):,} fixes, "
          f"{df['shortid'].nunique()} tags -> {out_path.name}")
    if args.style == "occupancy":
        plot_occupancy_grid(df, hour_label=title_label,
                            extent=extent, bin_inches=args.bin_inches,
                            save_path=out_path, log_scale=not args.linear)
    else:  # scatter
        plot_hourly_scatter(df, hour_label=title_label,
                            extent=extent, save_path=out_path)
    return True


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _run_single(args) -> None:
    bounds = sqlite_time_bounds(args.db, table=args.table)
    if bounds is not None:
        in_progress = _ms_to_hour_dt(bounds[1], args.tz)
        if args.hour >= in_progress and not args.force:
            raise SystemExit(
                f"[occupancy] {_hour_label(args.hour)} ({args.tz}) is the in-progress "
                f"hour (DB max is in {_hour_label(in_progress)}); it is incomplete. "
                f"Pick an earlier hour or pass --force.")
    extent = _maybe_cached_extent(args)
    _plot_one_hour(args.hour, args, extent)


def _run_backfill(args) -> int:
    bounds = sqlite_time_bounds(args.db, table=args.table)
    if bounds is None:
        raise SystemExit(f"[occupancy] Could not read time bounds from {args.db}.")
    first = _ms_to_hour_dt(bounds[0], args.tz)
    in_progress = _ms_to_hour_dt(bounds[1], args.tz)   # incomplete; do NOT plot

    extent = _maybe_cached_extent(args)
    written = 0
    hour = first
    while hour < in_progress:
        if _plot_one_hour(hour, args, extent):
            written += 1
            if extent is None and args.extent_json.exists():
                # extent was just derived+cached on the first plotted hour; reuse it
                data = json.loads(args.extent_json.read_text())
                extent = (data["xmin"], data["xmax"], data["ymin"], data["ymax"])
        hour += timedelta(hours=1)
    print(f"\n[occupancy] backfill complete: {written} new PNG(s); "
          f"in-progress hour {_hour_label(in_progress)} ({args.tz}) skipped.")
    return written


def _run_range(args) -> None:
    """Plot one arbitrary window from --from to --to (either may be omitted to
    use the DB's min / max timestamp)."""
    bounds = sqlite_time_bounds(args.db, table=args.table)
    if bounds is None:
        raise SystemExit(f"[occupancy] Could not read time bounds from {args.db}.")

    start_ms = _hour_dt_to_ms(args.from_dt, args.tz) if args.from_dt else bounds[0]
    end_ms = _hour_dt_to_ms(args.to_dt, args.tz) if args.to_dt else bounds[1]
    if end_ms <= start_ms:
        raise SystemExit("[occupancy] Empty/inverted range: --to must be after --from.")

    df = load_sqlite_window(args.db, start_ms, end_ms, table=args.table)
    if df is None or df.empty:
        print("  [empty] no fixes in the requested range.")
        return

    extent = _maybe_cached_extent(args)
    if extent is None:
        extent = _resolve_extent(args, df)

    start_dt = _ms_to_dt(start_ms, args.tz)
    end_dt = _ms_to_dt(end_ms, args.tz)
    label = _range_label(start_dt, end_dt, args.tz)
    fn = (f"{args.style}_{args.tz}_"
          f"{start_dt:%Y-%m-%d_%H%M}_to_{end_dt:%Y-%m-%d_%H%M}.png")
    out_path = args.output / fn

    if out_path.exists() and not args.force:
        print(f"  [skip] {out_path.name} already exists.")
        return

    print(f"  {label}: {len(df):,} fixes, {df['shortid'].nunique()} tags -> {out_path.name}")
    if args.style == "occupancy":
        plot_occupancy_grid(df, hour_label=label, extent=extent,
                            bin_inches=args.bin_inches, save_path=out_path,
                            log_scale=not args.linear)
    else:
        plot_hourly_scatter(df, hour_label=label, extent=extent, save_path=out_path)


def _maybe_cached_extent(args):
    """Load a usable extent up front (cli or cached json); else None to derive later."""
    if None not in (args.xmin, args.xmax, args.ymin, args.ymax):
        extent = (args.xmin, args.xmax, args.ymin, args.ymax)
        _write_extent(args.extent_json, extent, source="cli")
        return extent
    if args.extent_json.exists() and not args.refresh_extent:
        data = json.loads(args.extent_json.read_text())
        return (data["xmin"], data["xmax"], data["ymin"], data["ymax"])
    return None


def _run_watch(args) -> None:
    print("[occupancy] watch mode: backfilling once per hour. Ctrl-C to stop.")
    while True:
        _run_backfill(args)
        # Sleep until ~2 min past the next top of the hour.
        now = datetime.now()
        nxt = (now.replace(minute=0, second=0, microsecond=0)
               + timedelta(hours=1, minutes=2))
        time.sleep(max(60, (nxt - now).total_seconds()))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Hourly WISER occupancy maps (live-DB-safe).")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help="WISER SQLite database (read-only).")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_DIR,
                    help="Output folder for occupancy PNGs (must NOT be under the DB tree).")
    ap.add_argument("--table", default=None,
                    help="Table name (default: auto-detect, normally 'Position').")
    ap.add_argument("--style", choices=("scatter", "occupancy"), default="scatter",
                    help="scatter = fast QC, points coloured by time (default); "
                         "occupancy = 2-D density heatmap.")
    ap.add_argument("--bin-inches", type=float, default=DEFAULT_BIN_IN,
                    help="Occupancy bin size in inches (default 4, ~WISER resolution).")
    ap.add_argument("--tz", choices=("local", "utc"), default="local",
                    help="Timezone for hour bucketing/labels (default local).")
    ap.add_argument("--linear", action="store_true",
                    help="Linear colour scale instead of log.")

    # Extent overrides / cache control.
    ap.add_argument("--xmin", type=float, default=None)
    ap.add_argument("--xmax", type=float, default=None)
    ap.add_argument("--ymin", type=float, default=None)
    ap.add_argument("--ymax", type=float, default=None)
    ap.add_argument("--extent-json", type=Path, default=None,
                    help="Cached plot extent (default: <output>/arena_extent.json).")
    ap.add_argument("--refresh-extent", action="store_true",
                    help="Recompute and overwrite the cached extent.")

    # Modes.
    ap.add_argument("--hour", type=_parse_hour_arg, default=None,
                    help="Plot a single hour, e.g. 2026-06-29T00.")
    ap.add_argument("--from", dest="from_dt", type=_parse_dt, default=None,
                    help="Custom range start (default: DB earliest), e.g. 2026-06-28T19:20.")
    ap.add_argument("--to", dest="to_dt", type=_parse_dt, default=None,
                    help="Custom range end (default: DB latest), e.g. 2026-06-28T20:00.")
    ap.add_argument("--backfill", action="store_true",
                    help="Plot every completed hour not yet plotted.")
    ap.add_argument("--watch", action="store_true",
                    help="Loop: backfill once per hour (prefer the scheduled task).")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing PNGs / allow plotting the in-progress hour.")
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"[occupancy] Database not found: {args.db}")
    _assert_safe_output(args.output, args.db)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.extent_json is None:                  # keep the cache next to the PNGs
        args.extent_json = args.output / "arena_extent.json"

    is_range = args.from_dt is not None or args.to_dt is not None
    n_modes = sum(bool(x) for x in (args.hour is not None, is_range,
                                    args.backfill, args.watch))
    if n_modes == 0:
        args.backfill = True          # sensible default
    elif n_modes > 1:
        raise SystemExit("[occupancy] Choose only one of "
                         "--hour / --from/--to / --backfill / --watch.")

    print(f"=== WISER hourly occupancy ===\n  DB:     {args.db}\n"
          f"  Output: {args.output.resolve()}\n  tz:     {args.tz}\n")

    if args.hour is not None:
        _run_single(args)
    elif is_range:
        _run_range(args)
    elif args.watch:
        _run_watch(args)
    else:
        _run_backfill(args)


if __name__ == "__main__":
    main()

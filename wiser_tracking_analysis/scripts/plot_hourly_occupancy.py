"""
plot_hourly_occupancy.py
========================
Hourly WISER occupancy maps — snapshot-first, never queried live.

For each completed 1-hour window this writes ONE PNG: a per-tag grid of position
-density heatmaps plus a combined all-animals overlay.

The live database is touched EXACTLY ONCE per run: a single online-backup
snapshot into the output folder (the same bounded, sub-second pattern as
backup_wiser_daily.py). Every query — time bounds, hour windows, battery watch —
then runs against the snapshot copy. Rationale (operator order, 2026-08-19): in
rollback-journal mode ANY reader's lock can starve the wiserex writer past its
5 s busy_timeout and drop fixes; a 2-min read query cost 150 s of tracking that
day. Copy first, work on the copy.

Safety invariants (do not weaken):
  * The live DB is opened only by the one-shot snapshot (mode=ro backup API);
    no analysis query ever runs against it. --no-snapshot exists ONLY for
    static/archived DB files.
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
from src.plotting import (plot_occupancy_grid, plot_hourly_scatter,
                          load_rat_identities, set_identity_path)
from src.wiser_slack import DEFAULT_SLACK_CONFIG, load_slack_config, send_slack_text

DEFAULT_DB       = Path(r"D:\Wiser\data\1stcohort_mice_2026.sqlite")
DEFAULT_OUT_DIR  = Path(r"D:\Wiser_plot")          # off the C: drive, never git-tracked
DEFAULT_BIN_IN   = 4.0
EXTENT_PAD_IN    = 12.0       # padding added around observed data when deriving extent
HOUR_MS          = 3_600_000
DEFAULT_GAP_MIN  = 15.0       # per-tag no-data gap (minutes) that triggers a Slack warning
DEFAULT_ROI_CFG  = PROJECT_ROOT / "configs" / "wiser_rois.json"
SHELTER_TYPES    = ("refuge", "tunnel")   # covered structures that occlude UWB (NLOS)
DEFAULT_SHELTER_MARGIN_IN = 18.0          # a drop within this of a shelter edge = expected occlusion

# --- Battery watch (DM-only) ------------------------------------------------
DEFAULT_BATTERY_WARN_V        = 2.70   # absolute low-battery warn level (volts); PLACEHOLDER
DEFAULT_BATTERY_BELOW_COHORT_MV = 40.0 # or: this many mV below the cohort median
DEFAULT_BATTERY_WINDOW_H      = 6.0    # recent window used for each tag's voltage stat
DEFAULT_BATTERY_INTERVAL_H    = 6.0    # min hours between battery DB scans (once/hour job)


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
# Snapshot-first: the ONLY touch of the live DB per run
# ---------------------------------------------------------------------------

def _snapshot_live_db(live_db: Path, out_dir: Path) -> Path:
    """Copy *live_db* into *out_dir* with SQLite's online-backup API and return
    the copy's path. Single-shot backup = one bounded SHARED-lock hold roughly
    equal to the file's read time (0.74 s measured for 360 MB by the daily
    backup task) — within the wiserex writer's 5 s busy_timeout. Every other
    query in this script must run against the returned copy, never the live
    file, so a slow or unindexed read can never starve the writer."""
    import sqlite3
    snap = out_dir / "_live_snapshot.sqlite"
    tmp = out_dir / "_live_snapshot.sqlite.tmp"
    t0 = time.perf_counter()
    src = sqlite3.connect(f"file:{Path(live_db).as_posix()}?mode=ro",
                          uri=True, timeout=5.0)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)     # one pass; chunked backup would livelock
        finally:                # restarting whenever the live writer commits
            dst.close()
    finally:
        src.close()
    tmp.replace(snap)
    print(f"  [snapshot] {Path(live_db).name} -> {snap.name} "
          f"({snap.stat().st_size / 1e6:.1f} MB in "
          f"{time.perf_counter() - t0:.2f} s); all reads use the copy.")
    return snap


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
# Per-tag coverage QC -> Slack warning (missing rat / >gap-min no-data hole)
# ---------------------------------------------------------------------------

def _valid_until_ms(iso: str) -> int | None:
    """Parse a rat_identities `valid_until` (ISO, may carry a tz offset) to
    Unix ms. Empty/unparseable -> None (tag has no cutoff)."""
    iso = (iso or "").strip()
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:                       # assume local if bare
        dt = dt.astimezone()
    return int(dt.timestamp() * 1000)


def _tag_label(tag: str, identities: dict) -> str:
    """'Sen (306b/12395)' when known, else 'tag 12395'."""
    info = identities.get(str(tag)) if identities else None
    if not info or not info.get("name"):
        return f"tag {tag}"
    phys = info.get("physical_tag_id", "")
    return f"{info['name']} ({phys}/{tag})" if phys else f"{info['name']} ({tag})"


def _expected_tags(identities: dict, hour_start_ms: int) -> dict:
    """shortid -> cutoff_ms|None for tags expected to transmit during this hour.

    A tag whose `valid_until` is at/*before* the hour start (e.g. a removed or
    deceased animal) is not expected and never triggers a 'missing' warning."""
    out: dict[str, int | None] = {}
    for tag, info in (identities or {}).items():
        cutoff = _valid_until_ms(info.get("valid_until", ""))
        if cutoff is not None and cutoff <= hour_start_ms:
            continue                            # tag retired before this hour
        out[str(tag)] = cutoff
    return out


def _load_roi_cfg(path: Path):
    """Load wiser_rois.json (small); None if absent/unreadable. Kept local so the
    hourly task never imports the heavy analysis module."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def _shelter_near(x, y, roi_cfg, margin_in: float):
    """Name of a covered structure (refuge/tunnel) whose footprint — expanded by
    margin_in — contains (x, y); else None. A drop here is expected UWB occlusion
    (rat resting in/at a shelter), not a lost tag. Position may be None."""
    if x is None or y is None or not roi_cfg:
        return None
    import math
    for roi in roi_cfg.get("rois", []):
        if roi.get("type") not in SHELTER_TYPES:
            continue
        dx, dy = x - roi["x"], y - roi["y"]
        if roi.get("shape") == "rect":
            th = math.radians(roi.get("orientation_deg", 0.0))
            c, s = math.cos(-th), math.sin(-th)
            lx, ly = c * dx - s * dy, s * dx + c * dy
            if (abs(lx) <= roi.get("width_in", 10.0) / 2 + margin_in and
                    abs(ly) <= roi.get("height_in", 10.0) / 2 + margin_in):
                return roi["name"]
        else:                                   # circle
            if math.hypot(dx, dy) <= roi.get("radius_in", 12.0) + margin_in:
                return roi["name"]
    return None


def _coverage_report(df, expected: dict, start_ms: int, end_ms: int,
                     gap_ms: int, roi_cfg=None,
                     shelter_margin_in: float = DEFAULT_SHELTER_MARGIN_IN) -> dict:
    """Per-tag coverage for one hour.

    Returns ``{'missing': [tags], 'gappy': [dict...], 'counts': {tag: n}}`` where
    each *gappy* dict is ``{tag, gap_min, where, shelter}``. A tag is *missing*
    if it has zero fixes in its expected window. A present tag is *gappy* if its
    longest no-data hole — including the lead-in from the window start and the
    run-out to the window end — is >= gap_ms. ``shelter`` is the name of a
    covered structure the tag sat in/at on either side of that hole (expected
    NLOS occlusion), or None if it dropped out in the open. For a tag with a
    mid-hour cutoff the window ends at the cutoff (retirement is not a gap)."""
    import numpy as np
    missing, gappy, counts = [], [], {}
    have_data = df is not None and not df.empty
    for tag, cutoff in expected.items():
        eff_end = min(end_ms, cutoff) if cutoff is not None else end_ms
        if eff_end - start_ms < gap_ms:
            continue                            # window too short to judge
        if have_data:
            sub = df[df["shortid"].astype(str) == tag]
            sub = sub[(sub["ts_raw"] >= start_ms) & (sub["ts_raw"] <= eff_end)]
            sub = sub.sort_values("ts_raw")
            ts = sub["ts_raw"].to_numpy(dtype="int64")
            xs = sub["x"].to_numpy(dtype=float)
            ys = sub["y"].to_numpy(dtype=float)
        else:
            ts = np.empty(0, dtype="int64"); xs = ys = np.empty(0)
        counts[tag] = int(len(ts))
        if len(ts) == 0:
            missing.append(tag)
            continue
        # Candidate holes: lead-in (start->first), run-out (last->end), and the
        # single largest inter-fix gap. Pick the biggest; keep its endpoints.
        cands = [(ts[0] - start_ms, "start of hour", None, None, xs[0], ys[0]),
                 (eff_end - ts[-1], "end of hour", xs[-1], ys[-1], None, None)]
        if len(ts) > 1:
            inner = np.diff(ts)
            i = int(inner.argmax())
            cands.append((int(inner[i]), "mid-hour",
                          xs[i], ys[i], xs[i + 1], ys[i + 1]))
        dur, where, bx, by, ax, ay = max(cands, key=lambda c: c[0])
        if dur >= gap_ms:
            shelter = (_shelter_near(bx, by, roi_cfg, shelter_margin_in)
                       or _shelter_near(ax, ay, roi_cfg, shelter_margin_in))
            gappy.append({"tag": tag, "gap_min": dur / 60000.0,
                          "where": where, "shelter": shelter})
    return {"missing": missing, "gappy": gappy, "counts": counts}


def _format_coverage_alert(rep: dict, hour_label: str, identities: dict,
                           gap_min: float, open_gaps: list, n_shelter: int) -> str:
    """Alert text for the *actionable* problems only: whole-hour-missing tags and
    open-field gaps. Shelter-occlusion gaps are summarised as a one-line FYI."""
    lines = [f":warning: *WISER data gap* — {hour_label}"]
    if rep["missing"]:
        names = ", ".join(_tag_label(t, identities) for t in sorted(rep["missing"]))
        lines.append(f"• No data at all this hour: {names}")
    for g in sorted(open_gaps, key=lambda x: -x["gap_min"]):
        lines.append(f"• {_tag_label(g['tag'], identities)}: "
                     f"{g['gap_min']:.1f} min gap in the open ({g['where']})")
    if n_shelter:
        lines.append(f"_(+{n_shelter} shelter-occlusion gap(s) suppressed — "
                     f"rats resting in a refuge, expected)_")
    lines.append(f"_(threshold {gap_min:g} min; live DB, unverified alignment)_")
    return "\n".join(lines)


def _load_alert_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _check_coverage_and_alert(df, hour_dt: datetime, start_ms: int,
                              end_ms: int, args) -> None:
    """Evaluate per-tag coverage for one plotted hour and Slack-warn once if a
    rat is missing or any tag has a >= gap-min hole. Best-effort; never raises."""
    if args.no_slack:
        return
    identities = load_rat_identities()
    if not identities:
        print("  [coverage] no rat_identities.csv -> coverage QC skipped.")
        return
    expected = _expected_tags(identities, start_ms)
    roi_cfg = None if args.no_shelter_suppress else _load_roi_cfg(args.roi_config)
    rep = _coverage_report(df, expected, start_ms, end_ms,
                           int(args.gap_minutes * 60000), roi_cfg,
                           args.shelter_margin_in)

    # Split gaps: shelter occlusion (expected, suppressed) vs open-field (alert).
    open_gaps = [g for g in rep["gappy"] if not g["shelter"]]
    shelter_gaps = [g for g in rep["gappy"] if g["shelter"]]
    hour_label = _interval_label(hour_dt, args.tz)

    if shelter_gaps:
        detail = ", ".join(f"{_tag_label(g['tag'], identities)} {g['gap_min']:.0f}min"
                           f"@{g['shelter']}" for g in shelter_gaps)
        print(f"  [coverage] {len(shelter_gaps)} shelter-occlusion gap(s) "
              f"suppressed: {detail}")

    if not rep["missing"] and not open_gaps:
        return                                  # nothing actionable to alert

    n_miss = len(rep["missing"])
    print(f"  [coverage] WARN {hour_label}: {n_miss} missing, "
          f"{len(open_gaps)} open-field gap(s).")

    state_path = args.output / "coverage_alert_state.json"
    state = _load_alert_state(state_path)
    key = f"{args.tz}_{_hour_label(hour_dt)}"
    if key in state:
        print("  [coverage] already alerted for this hour; not re-sending.")
        return

    cfg = load_slack_config(args.slack_config)
    msg = _format_coverage_alert(rep, hour_label, identities, args.gap_minutes,
                                 open_gaps, len(shelter_gaps))
    sent = send_slack_text(msg, cfg)
    if sent > 0:
        print(f"  [coverage] Slack warning sent to {sent} destination(s).")
    # Record so we don't re-alert: on delivery, or when Slack is disabled (no
    # token) so a misconfig doesn't reprocess every run. A transient send
    # failure with a valid token is left unrecorded to retry next run.
    if sent > 0 or not cfg.get("token"):
        state[key] = {
            "missing": rep["missing"],
            "open_gaps": [[g["tag"], round(g["gap_min"], 1), g["where"]]
                          for g in open_gaps],
            "shelter_gaps": [[g["tag"], round(g["gap_min"], 1), g["shelter"]]
                             for g in shelter_gaps],
            "sent": sent, "at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            state_path.write_text(json.dumps(state, indent=2))
        except Exception as exc:
            print(f"  [coverage] could not write alert state: {exc}")


# ---------------------------------------------------------------------------
# Battery watch -> DM-only Slack (tag voltage low / diverging from the cohort)
# ---------------------------------------------------------------------------

def _detect_table(con) -> str:
    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                      "AND name='Position'").fetchone()
    if row:
        return row[0]
    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                      "LIMIT 1").fetchone()
    return row[0] if row else "Position"


def _latest_battery(db: Path, table, window_h: float):
    """Read-only: per-tag battery_voltage over the most recent window_h hours.
    Returns ({tag: {'median','min','n'}}, max_ts_ms) or ({}, None)."""
    import sqlite3
    import numpy as np
    uri = f"file:{Path(db).as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        con.execute("PRAGMA query_only=ON")
        tbl = table or _detect_table(con)
        row = con.execute(f'SELECT MAX(timestamp) FROM "{tbl}"').fetchone()
        if not row or row[0] is None:
            return {}, None
        tmax = int(row[0])
        tmin = tmax - int(window_h * 3_600_000)
        cur = con.execute(
            f'SELECT shortid, battery_voltage FROM "{tbl}" '
            f'WHERE timestamp >= ? AND battery_voltage IS NOT NULL', (tmin,))
        buckets: dict[str, list] = {}
        for sid, bv in cur:
            buckets.setdefault(str(sid), []).append(float(bv))
    finally:
        con.close()
    out = {}
    for sid, vals in buckets.items():
        a = np.asarray(vals, float)
        out[sid] = {"median": float(np.median(a)), "min": float(a.min()),
                    "n": int(a.size)}
    return out, tmax


def _check_battery_and_alert(args) -> None:
    """Once per run (rate-limited): DM a heads-up if any expected tag's battery is
    below the absolute warn level or is >= N mV below the cohort median. DM-only.
    Best-effort; never raises."""
    if args.no_slack or args.no_battery_check:
        return
    state_path = args.output / "battery_alert_state.json"
    state = _load_alert_state(state_path)
    now = datetime.now()
    last = state.get("last_check")
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < \
                    args.battery_interval_h * 3600:
                return                          # scanned recently; skip DB read
        except Exception:
            pass

    stats, tmax = _latest_battery(args.db, args.table, args.battery_window_h)
    state["last_check"] = now.isoformat(timespec="seconds")
    if not stats or tmax is None:
        _save_json(state_path, state)
        return

    identities = load_rat_identities()
    expected = set(_expected_tags(identities, tmax).keys()) if identities else set(stats)
    present = {t: s for t, s in stats.items() if t in expected and s["n"] > 0}
    if len(present) < 2:
        _save_json(state_path, state)
        return

    import numpy as np
    cohort_med = float(np.median([s["median"] for s in present.values()]))
    low = []
    for tag, s in present.items():
        below_mv = (cohort_med - s["median"]) * 1000.0
        reasons = []
        if s["median"] <= args.battery_warn_volts:
            reasons.append(f"{s['median']:.3f} V ≤ {args.battery_warn_volts:.2f} V")
        if below_mv >= args.battery_below_cohort_mv:
            reasons.append(f"{below_mv:.0f} mV below cohort")
        if reasons:
            low.append((tag, s, reasons))

    today = now.strftime("%Y-%m-%d")
    if state.get("date") != today:
        state["date"] = today
        state["alerted_tags"] = []
    already = set(state.get("alerted_tags", []))
    fresh = [x for x in low if x[0] not in already]

    if not fresh:
        _save_json(state_path, state)          # nothing new today
        return

    lines = [":battery: *WISER battery watch* — "
             f"cohort median {cohort_med:.3f} V"]
    for tag, s, reasons in sorted(low, key=lambda x: x[1]["median"]):
        flag = "  ← NEW" if tag in {f[0] for f in fresh} else ""
        lines.append(f"• {_tag_label(tag, identities)}: median {s['median']:.3f} V, "
                     f"min {s['min']:.3f} V ({'; '.join(reasons)}){flag}")
    lines.append(f"_(recent {args.battery_window_h:g} h; warn ≤{args.battery_warn_volts:.2f} V "
                 f"or ≥{args.battery_below_cohort_mv:g} mV under cohort; DM only)_")
    msg = "\n".join(lines)

    cfg = load_slack_config(args.slack_config)
    dm_cfg = {"token": cfg.get("token"),
              "channels": cfg.get("battery_channels") or [],
              "source": cfg.get("source")}
    sent = send_slack_text(msg, dm_cfg)
    print(f"  [battery] {len(low)} low tag(s), {len(fresh)} new -> DM sent to "
          f"{sent} destination(s).")
    if sent > 0 or not cfg.get("token"):
        state["alerted_tags"] = sorted(already | {x[0] for x in fresh})
        state["voltages"] = {t: round(s["median"], 3) for t, s in present.items()}
    _save_json(state_path, state)


def _save_json(path: Path, obj) -> None:
    try:
        path.write_text(json.dumps(obj, indent=2))
    except Exception as exc:
        print(f"  [battery] could not write state: {exc}")


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
        # A completed hour with NO fixes at all is itself worth a warning.
        _check_coverage_and_alert(None, hour_dt, start_ms, end_ms, args)
        return False

    # Per-tag coverage QC -> Slack (missing rat / >gap-min hole). Best-effort.
    _check_coverage_and_alert(df, hour_dt, start_ms, end_ms, args)

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
    live_db = args.db                    # re-snapshot each cycle, not once at start
    while True:
        if not args.no_snapshot:
            args.db = _snapshot_live_db(live_db, args.output)
        _run_backfill(args)
        _check_battery_and_alert(args)
        args.db = live_db
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
                    help="WISER SQLite database (snapshotted once, then read-only "
                         "from the copy).")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="Read --db directly instead of snapshot-first. ONLY for "
                         "static/archived DB files — NEVER for the live database.")
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
    ap.add_argument("--identities", type=Path, default=None,
                    help="Identity CSV for THIS cohort (default configs/"
                         "rat_identities.csv). WISER tag ids are reused across "
                         "cohorts — pass configs/mouse_identities.csv for the "
                         "mice DB or every panel is labelled with a rat's name.")

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

    # Coverage QC -> Slack warning (missing rat / per-tag no-data hole).
    ap.add_argument("--slack-config", type=Path, default=DEFAULT_SLACK_CONFIG,
                    help="PowerShell psd1 with SlackBotToken + WiserAlertChannels/"
                         "SlackChannels (default: the recorder QC config on E:).")
    ap.add_argument("--gap-minutes", type=float, default=DEFAULT_GAP_MIN,
                    help="Per-tag no-data gap (minutes) that triggers a Slack "
                         f"warning (default {DEFAULT_GAP_MIN:g}).")
    ap.add_argument("--roi-config", type=Path, default=DEFAULT_ROI_CFG,
                    help="wiser_rois.json — gaps that start/end in or at a "
                         "refuge/tunnel are treated as expected NLOS occlusion "
                         "and suppressed (not alerted).")
    ap.add_argument("--shelter-margin-in", type=float,
                    default=DEFAULT_SHELTER_MARGIN_IN,
                    help="Inches beyond a shelter's edge still counted as "
                         f"'at the shelter' (default {DEFAULT_SHELTER_MARGIN_IN:g}).")
    ap.add_argument("--no-shelter-suppress", action="store_true",
                    help="Alert on ALL gaps, including shelter-occlusion ones.")
    ap.add_argument("--no-slack", action="store_true",
                    help="Disable the coverage Slack warning (plotting/QC still run).")
    ap.add_argument("--test-slack", action="store_true",
                    help="Send a test message to the configured destinations and exit.")

    # Battery watch (DM-only).
    ap.add_argument("--battery-warn-volts", type=float, default=DEFAULT_BATTERY_WARN_V,
                    help="DM a heads-up when a tag's recent median voltage drops to/below "
                         f"this (default {DEFAULT_BATTERY_WARN_V:g} V; placeholder until the "
                         "real cutoff is known).")
    ap.add_argument("--battery-below-cohort-mv", type=float,
                    default=DEFAULT_BATTERY_BELOW_COHORT_MV,
                    help="...or when a tag is this many mV below the cohort median "
                         f"(default {DEFAULT_BATTERY_BELOW_COHORT_MV:g}).")
    ap.add_argument("--battery-window-h", type=float, default=DEFAULT_BATTERY_WINDOW_H,
                    help=f"Recent hours used per tag's voltage stat (default {DEFAULT_BATTERY_WINDOW_H:g}).")
    ap.add_argument("--battery-interval-h", type=float, default=DEFAULT_BATTERY_INTERVAL_H,
                    help=f"Min hours between battery DB scans (default {DEFAULT_BATTERY_INTERVAL_H:g}).")
    ap.add_argument("--no-battery-check", action="store_true",
                    help="Disable the battery watch.")
    ap.add_argument("--battery-now", action="store_true",
                    help="Run the battery check immediately (ignore the scan interval) and exit.")
    args = ap.parse_args()

    if args.identities:
        if not args.identities.exists():
            raise SystemExit(f"[occupancy] Identity CSV not found: {args.identities}")
        set_identity_path(args.identities)
        print(f"[occupancy] identities: {args.identities}")

    if args.battery_now:
        if not args.db.exists():
            raise SystemExit(f"[occupancy] Database not found: {args.db}")
        args.output.mkdir(parents=True, exist_ok=True)
        if not args.no_snapshot:
            args.db = _snapshot_live_db(args.db, args.output)
        args.battery_interval_h = 0.0
        _check_battery_and_alert(args)
        raise SystemExit(0)

    if args.test_slack:
        cfg = load_slack_config(args.slack_config)
        print(f"[occupancy] Slack config: token={'set' if cfg['token'] else 'NONE'}, "
              f"{len(cfg['channels'])} destination(s), source={cfg['source']}")
        n = send_slack_text(":white_check_mark: WISER hourly occupancy — Slack test "
                            "(coverage warnings will post here).", cfg)
        print(f"[occupancy] test message delivered to {n} destination(s).")
        raise SystemExit(0 if n > 0 else 1)

    if not args.db.exists():
        raise SystemExit(f"[occupancy] Database not found: {args.db}")
    _assert_safe_output(args.output, args.db)
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.no_snapshot and not args.watch:   # watch snapshots per cycle
        # From here on args.db IS the copy; the live file is never opened again.
        args.db = _snapshot_live_db(args.db, args.output)
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

    # Battery watch runs once per invocation (rate-limited internally). Watch mode
    # runs its own copy each loop, so skip the extra call there.
    if not args.watch:
        _check_battery_and_alert(args)


if __name__ == "__main__":
    main()

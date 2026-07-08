"""
wiser_io.py — Load and standardize raw WISER UWB tracking files.

Handles CSV, TSV, TXT, and SQLite (.sqlite / .db) files.
Fuzzy-matches column names to a standard schema:
    shortid  — tag identity
    ts_raw   — raw timestamp value (unconverted)
    x, y     — estimated 2D position
    z        — estimated height (optional)
"""

from pathlib import Path
import sqlite3
import pandas as pd
import warnings

# ---------------------------------------------------------------------------
# Column name fuzzy-matching
# ---------------------------------------------------------------------------

# Each key is the canonical output column name.
# Each value is a list of plausible raw column names (lowercase).
COLUMN_ALIASES: dict[str, list[str]] = {
    "shortid": ["shortid", "short_id", "tagid", "tag_id", "tag", "id",
                "name", "label", "beacon", "node"],
    "ts_raw":  ["timestamp", "time", "ts", "datetime", "epoch",
                "unix_time", "unixtime", "unix", "utc", "t"],
    "x":       ["location_x", "loc_x",
                "x", "x_pos", "xpos", "x_position", "pos_x", "posx",
                "xcoord", "x_coord", "easting", "east"],
    "y":       ["location_y", "loc_y",
                "y", "y_pos", "ypos", "y_position", "pos_y", "posy",
                "ycoord", "y_coord", "northing", "north"],
    "z":       ["location_z", "loc_z",
                "z", "z_pos", "zpos", "z_position", "pos_z", "posz",
                "zcoord", "z_coord", "height", "alt", "altitude"],
}


def _match_columns(raw_cols: list[str]) -> dict[str, str]:
    """Return {canonical_name: raw_col_name} for all recognisable columns."""
    lower_map = {c.lower().strip(): c for c in raw_cols}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                mapping[canonical] = lower_map[alias]
                break
    return mapping


def _standardise_df(df: pd.DataFrame, source_name: str) -> pd.DataFrame | None:
    """Apply column matching, rename, and filter to a raw DataFrame."""
    col_map = _match_columns(list(df.columns))

    required = ["shortid", "ts_raw", "x", "y"]
    missing = [c for c in required if c not in col_map]
    if missing:
        warnings.warn(
            f"[wiser_io] {source_name}: could not find columns for {missing}. "
            f"Raw columns are: {list(df.columns)}"
        )
        return None

    rename = {v: k for k, v in col_map.items()}
    df = df.rename(columns=rename)
    keep = [c for c in col_map if c in df.columns]
    df = df[keep].copy()
    df["source_file"] = source_name
    return df


# ---------------------------------------------------------------------------
# CSV / TXT / TSV loader
# ---------------------------------------------------------------------------

def _sniff_separator(path: Path) -> str:
    """Detect the field separator by reading the first non-empty line."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            counts = {sep: line.count(sep) for sep in [",", "\t", ";", "|", " "]}
            best = max(counts, key=counts.get)
            return best if counts[best] > 0 else ","
    return ","


def load_wiser_file(path: Path) -> pd.DataFrame | None:
    """Load a single WISER CSV/TXT/TSV file and return a standardised DataFrame."""
    path = Path(path)
    sep = _sniff_separator(path)

    try:
        df = pd.read_csv(path, sep=sep, encoding="utf-8-sig", engine="python",
                         on_bad_lines="warn")
    except Exception as exc:
        warnings.warn(f"[wiser_io] Cannot read {path.name}: {exc}")
        return None

    if df.empty:
        warnings.warn(f"[wiser_io] {path.name} is empty — skipping.")
        return None

    return _standardise_df(df, path.name)


# ---------------------------------------------------------------------------
# SQLite loader
# ---------------------------------------------------------------------------

def _connect_readonly(path: Path) -> sqlite3.Connection:
    """
    Open a SQLite database strictly read-only.

    Uses the ``mode=ro`` URI plus ``PRAGMA query_only=ON`` so this process can
    never write. This is safe against a *live* database that another process is
    actively writing (e.g. the WISER recorder): ``mode=ro`` still honours the
    writer's locks and WAL. We deliberately do NOT use ``immutable=1`` or
    ``nolock=1`` — those tell SQLite to ignore locking/WAL and would risk torn
    reads from the in-flight writer.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _pick_table(conn: sqlite3.Connection) -> str | None:
    """
    Pick the most likely tag-report table from a SQLite database.

    Preference order:
      1. A table whose name contains 'report' or 'tag'
      2. The largest table by row count
    """
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    if not tables:
        return None

    # Prefer semantically named tables.
    for name in tables:
        if any(kw in name.lower() for kw in ("report", "tag")):
            return name

    # Fall back to the largest table.
    sizes = {}
    for name in tables:
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            sizes[name] = count
        except Exception:
            sizes[name] = 0
    return max(sizes, key=sizes.get)


def load_wiser_sqlite(path: Path) -> pd.DataFrame | None:
    """Load a WISER SQLite database and return a standardised DataFrame."""
    path = Path(path)
    try:
        conn = _connect_readonly(path)
    except Exception as exc:
        warnings.warn(f"[wiser_io] Cannot open {path.name}: {exc}")
        return None

    table = _pick_table(conn)
    if table is None:
        warnings.warn(f"[wiser_io] {path.name}: no tables found.")
        conn.close()
        return None

    print(f"  Reading table '{table}' from {path.name} …")
    try:
        df = pd.read_sql(f'SELECT * FROM "{table}"', conn)
    except Exception as exc:
        warnings.warn(f"[wiser_io] {path.name}/{table}: read failed — {exc}")
        conn.close()
        return None
    finally:
        conn.close()

    if df.empty:
        warnings.warn(f"[wiser_io] {path.name}/{table} is empty — skipping.")
        return None

    return _standardise_df(df, f"{path.name}/{table}")


def load_sqlite_window(path: Path,
                       start_ms: int,
                       end_ms: int,
                       table: str | None = None,
                       ts_col: str = "timestamp") -> pd.DataFrame | None:
    """
    Read one bounded time window ``[start_ms, end_ms)`` from a WISER SQLite DB.

    Opens the database strictly read-only (see :func:`_connect_readonly`) and
    runs a single bounded query — never a full-table scan — so this stays cheap
    and safe even while the database is growing live. ``start_ms`` / ``end_ms``
    are raw timestamp values in the same units as the DB's timestamp column
    (Unix milliseconds for the WISER recorder). Returns a standardised DataFrame
    (``shortid, ts_raw, x, y[, z]``) or ``None`` if nothing is found.
    """
    path = Path(path)
    try:
        conn = _connect_readonly(path)
    except Exception as exc:
        warnings.warn(f"[wiser_io] Cannot open {path.name}: {exc}")
        return None

    try:
        if table is None:
            table = _pick_table(conn)
        if table is None:
            warnings.warn(f"[wiser_io] {path.name}: no tables found.")
            return None
        query = (f'SELECT * FROM "{table}" '
                 f'WHERE "{ts_col}" >= ? AND "{ts_col}" < ?')
        df = pd.read_sql(query, conn, params=(int(start_ms), int(end_ms)))
    except Exception as exc:
        warnings.warn(f"[wiser_io] {path.name}/{table}: windowed read failed — {exc}")
        return None
    finally:
        conn.close()

    if df.empty:
        return None

    return _standardise_df(df, f"{path.name}/{table}")


def sqlite_time_bounds(path: Path,
                       table: str | None = None,
                       ts_col: str = "timestamp") -> tuple[int, int] | None:
    """
    Return ``(min_ts, max_ts)`` of the timestamp column, read strictly read-only.

    A single cheap aggregate query — used to decide which hour is still
    in-progress (from the DB's own max timestamp, never the wall clock) without
    loading any rows.
    """
    path = Path(path)
    try:
        conn = _connect_readonly(path)
    except Exception as exc:
        warnings.warn(f"[wiser_io] Cannot open {path.name}: {exc}")
        return None
    try:
        if table is None:
            table = _pick_table(conn)
        if table is None:
            return None
        row = conn.execute(
            f'SELECT MIN("{ts_col}"), MAX("{ts_col}") FROM "{table}"'
        ).fetchone()
    except Exception as exc:
        warnings.warn(f"[wiser_io] {path.name}: time-bounds query failed — {exc}")
        return None
    finally:
        conn.close()

    if row is None or row[0] is None or row[1] is None:
        return None
    return int(row[0]), int(row[1])


# ---------------------------------------------------------------------------
# Folder loader — aggregates all supported file types
# ---------------------------------------------------------------------------

def load_wiser_folder(folder: Path | str) -> pd.DataFrame:
    """
    Load all WISER tracking files from *folder* and concatenate them.

    Accepts .csv, .txt, .tsv, .sqlite, and .db files.
    """
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Data folder not found: {folder}")

    flat_files = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in {".csv", ".txt", ".tsv"} and p.is_file()
    )
    sqlite_files = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in {".sqlite", ".db"} and p.is_file()
    )

    if not flat_files and not sqlite_files:
        raise FileNotFoundError(
            f"No CSV/TXT/TSV/SQLite files found in {folder}"
        )

    frames: list[pd.DataFrame] = []

    for f in flat_files:
        df = load_wiser_file(f)
        if df is not None:
            frames.append(df)
            print(f"  Loaded {f.name}: {len(df):,} rows")

    for f in sqlite_files:
        df = load_wiser_sqlite(f)
        if df is not None:
            frames.append(df)
            print(f"  Loaded {f.name}: {len(df):,} rows")

    if not frames:
        raise RuntimeError("No valid WISER files could be loaded.")

    combined = pd.concat(frames, ignore_index=True)
    print(f"\nTotal rows loaded: {len(combined):,} from {len(frames)} source(s).")
    return combined

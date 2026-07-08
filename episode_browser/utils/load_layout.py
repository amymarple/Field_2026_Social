"""
load_layout.py — read-only adapters onto EXISTING repo config, for the browser.

The episode browser must not duplicate or fork the project's ground-truth config.
It reads (never writes) the canonical files and adapts them into small plain dicts:

  * field geometry   <- preprocessing/computer_vision/configs/field_layout.json  (cm, origin A0)
  * zone polygons    <- wiser_tracking_analysis/configs/wiser_rois.json           (WISER inches)
  * subject identities <- wiser_tracking_analysis/configs/rat_identities.csv      (shortid -> name)

Every loader is defensive: a missing file returns None / empty and is reported, so
the browser runs on synthetic data alone before any real config exists. `shortid`
is a TAG id, not an animal — name resolution goes through rat_identities.csv only.

Coordinate caveat carried through, never silently reconciled: field_layout is in
CENTIMETRES (origin pole A0); wiser_rois is in WISER INCHES (unverified offset
frame). The two are NOT unit-convertible until the georeference transform is
confirmed — so this module keeps them labeled and separate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd

# episode_browser/ -> repo root is one level up.
REPO_ROOT = Path(__file__).resolve().parents[2]

FIELD_LAYOUT = REPO_ROOT / "preprocessing" / "computer_vision" / "configs" / "field_layout.json"
WISER_ROIS = REPO_ROOT / "wiser_tracking_analysis" / "configs" / "wiser_rois.json"
RAT_IDENTITIES = REPO_ROOT / "wiser_tracking_analysis" / "configs" / "rat_identities.csv"


def _read_json_lenient(path: Path) -> Optional[dict]:
    """Read JSON that may contain // or /* */ comments (some repo configs do)."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(^|\s)//.*$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def load_field_layout(path: Path = FIELD_LAYOUT) -> Optional[dict]:
    """Field geometry in cm (origin A0). Returns the raw dict, or None if absent."""
    return _read_json_lenient(path)


def load_zones(path: Path = WISER_ROIS) -> dict:
    """WISER ROIs as {zone_name: definition} in WISER inches. Empty dict if absent.

    Kept as a flat name->def map so the browser can offer zone LABELS as a view
    without treating them as the segmentation ontology.
    """
    raw = _read_json_lenient(path)
    if not raw:
        return {}
    zones: dict = {}
    for key, val in raw.items():
        if isinstance(val, list):
            for i, item in enumerate(val):
                name = item.get("name") if isinstance(item, dict) else None
                zones[name or f"{key}_{i}"] = item
        elif isinstance(val, dict):
            zones[key] = val
    return zones


def load_subjects(path: Path = RAT_IDENTITIES) -> pd.DataFrame:
    """shortid -> name/status table. Empty (typed) frame if absent.

    shortid is a TAG id. `valid_until` (e.g. Sova removed 2026-06-29 15:00 EDT)
    is preserved so the browser can show identity validity, not silently drop it.
    """
    cols = ["shortid", "name", "status", "valid_until"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype=str)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df


def subject_name_map(path: Path = RAT_IDENTITIES) -> dict[str, str]:
    """{shortid(str) -> name}. Unresolved / unknown ids map to themselves."""
    df = load_subjects(path)
    out = {str(r.shortid): (r.name or str(r.shortid)) for r in df.itertuples()}
    return out


def resolve_subject(shortid: str, name_map: Optional[dict] = None) -> str:
    """Resolve a tag id to a display name; 'unknown' and unmapped ids pass through."""
    if shortid in (None, "", "unknown"):
        return "unknown"
    name_map = name_map if name_map is not None else subject_name_map()
    return name_map.get(str(shortid), str(shortid))


def availability_report() -> dict[str, bool]:
    """Which real config files are present — surfaced in the browser sidebar."""
    return {
        "field_layout": FIELD_LAYOUT.exists(),
        "wiser_rois": WISER_ROIS.exists(),
        "rat_identities": RAT_IDENTITIES.exists(),
    }

"""measurement_context.py — provenance + camera-covariate metadata for shelter CV outputs.

ANNOTATION + PROVENANCE ONLY. Nothing here changes detector output, view-quality, motion, counts, safety,
thresholds, filtering, exclusion, or validity. It makes every CV-derived shelter number interpretable *as a
measurement* (which camera/model, which detector/version + params, which config versions, coordinate frame)
and stratifiable — it never silently changes a result.

Two layers:
  - per-row camera covariates:  annotate_camera(df, channel) -> + camera_model, shelter_id  (field_layout.json)
  - per-run manifest:           build_context(...) + write_manifest(path, ctx) -> JSON sidecar next to the
                                CSVs, plus run_id(ctx) stamped on every row as `mc_run_id`.

Houses (shelters) exist only on CH05/CH06, so `shelter_id` is null elsewhere; the camera axis is general to
all 6 channels so this schema already fits CH01-CH04 outputs later. Mirrors the pure-annotator shape of
glass_regime.py and the provenance pattern of wiser_analysis_utils.write_run_manifest (replicated, not
imported — the subsystems are intentionally independent).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONFIG_DIR = HERE / "configs"
FIELD_LAYOUT = CONFIG_DIR / "field_layout.json"
SCHEMA_VERSION = "measurement_context/1.0"

CAMERA_COLS = ["camera_model", "shelter_id"]        # per-row camera covariates (mc_run_id added separately)

# camera model -> role (derived; single source of the model itself is field_layout.json)
_ROLE = {"Duo3": "paddock_overview_180", "RLC-1212A": "side_wide", "RLC-520A": "shelter_nadir"}

# fields of the context that define the *measurement configuration* (hashed into mc_run_id). Excludes
# generated_utc, command_args, inputs, and file mtimes so identical setups share an id.
_ID_KEYS = ("git_commit", "detector", "sampling", "configs", "zones", "calibration",
            "field_layout_fingerprint", "run")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_layout(path=None) -> dict:
    p = Path(path) if path else FIELD_LAYOUT
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[measurement_context] could not read {p}: {e}")
        return {}


# ============================ per-row camera covariates ============================
def camera_of(channel, layout=None) -> dict:
    """Camera descriptor for a channel from field_layout.json (covariate lookup); {} if unknown.

    role is derived from model (+shelter): RLC-520A on a shelter -> shelter_nadir, Duo3 -> paddock_overview_180,
    RLC-1212A -> side_wide. This never decides anything.
    """
    lay = layout if layout is not None else _load_layout()
    m = (lay.get("camera_mounts") or {}).get(channel)
    if not m:
        return {}
    model, shelter = m.get("model"), m.get("shelter")
    role = _ROLE.get(model)
    if model == "RLC-520A" and not shelter:
        role = "nadir"
    return {"channel": channel, "model": model, "mapping": m.get("mapping"), "role": role,
            "shelter_id": shelter, "pos_cm": m.get("pos_cm"), "height_cm": m.get("height_cm"),
            "aim": m.get("aim")}


def annotate_camera(df, channel, layout=None):
    """Return a COPY of df with per-row camera covariate columns appended (camera_model, shelter_id).
    Existing columns are never touched. `channel` may be a column name in df or a scalar channel string."""
    out = df.copy()
    if len(out) == 0:
        for c in CAMERA_COLS:
            if c not in out.columns:
                out[c] = pd.Series(dtype="object")
        return out
    lay = layout if layout is not None else _load_layout()
    if isinstance(channel, str) and channel in out.columns:
        ch_vals = out[channel]
    else:
        ch_vals = pd.Series([channel] * len(out), index=out.index)
    cams = {ch: camera_of(ch, lay) for ch in pd.unique(ch_vals)}
    out["camera_model"] = [cams.get(ch, {}).get("model") for ch in ch_vals]
    out["shelter_id"] = [cams.get(ch, {}).get("shelter_id") for ch in ch_vals]
    return out


# ============================ per-run provenance manifest ============================
def git_hash() -> str:
    """Current repo commit, or a sentinel. Replicates wiser_analysis_utils._git_hash (not imported)."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return "uncommitted-or-unavailable"


def file_fingerprint(path):
    """Content identity for a file that has no version field: {path, sha256_16, size, mtime_utc}; None if missing."""
    p = Path(path)
    if not p.exists():
        return None
    data = p.read_bytes()
    return {"path": str(p), "sha256_16": hashlib.sha256(data).hexdigest()[:16], "size": len(data),
            "mtime_utc": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}


def _calib_summary(path):
    """Calibration provenance: created + reproj_rmse_cm (already in CHxx_calib.json) + content fingerprint."""
    fp = file_fingerprint(path)
    if fp is None:
        return None
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    return {"path": str(path), "created": d.get("created"), "type": d.get("type"),
            "reproj_rmse_cm": d.get("reproj_rmse_cm"), "sha256_16": fp["sha256_16"]}


def camera_block(channels, layout=None) -> dict:
    lay = layout if layout is not None else _load_layout()
    return {ch: {k: v for k, v in camera_of(ch, lay).items() if k != "channel"} for ch in channels}


def run_id(ctx) -> str:
    """Short stable hash of the measurement configuration (see _ID_KEYS; excludes time/mtimes/args/inputs),
    so identical setups share an id and any detector/config/arg-that-matters change flips it."""
    def _strip_mtime(o):
        if isinstance(o, dict):
            return {k: _strip_mtime(v) for k, v in o.items() if k != "mtime_utc"}
        if isinstance(o, list):
            return [_strip_mtime(v) for v in o]
        return o
    basis = _strip_mtime({k: ctx.get(k) for k in _ID_KEYS})
    blob = json.dumps(basis, sort_keys=True, default=str)
    return "mc_" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def build_context(script, args, channels, view_quality_config=None, field_conditions=None,
                  glass_treatments=None, inputs=None) -> dict:
    """Assemble the per-run measurement_context dict (provenance + params + config fingerprints + cameras).
    Pure: reads config files for fingerprints, captures git + UTC time; changes nothing on disk or in logic."""
    lay = _load_layout()
    fld = lay.get("field_cm", {})
    a = vars(args) if hasattr(args, "__dict__") else dict(args or {})
    weights = a.get("weights")
    configs = {}
    for name, path in (("view_quality_config", view_quality_config),
                       ("field_conditions", field_conditions),
                       ("glass_treatments", glass_treatments)):
        if path:
            configs[name] = file_fingerprint(path)
    ctx = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": script,
        "generated_utc": _utc_now(),
        "git_commit": git_hash(),
        "run": {
            "experiment": "Field_2026_Social",
            "date": a.get("date"),
            "channels": list(channels),
            "timestamp_source": ("Reolink filename-derived local wallclock; OSD ~1 h behind filenames; "
                                 "devices not clock-synced -> cross-modal alignment unverified"),
        },
        "detector": {
            "weights_path": weights,
            "weights_version": (Path(weights).parents[1].name if weights else None),
            "weights_fingerprint": file_fingerprint(weights) if weights else None,
            "conf": a.get("conf"), "imgsz": a.get("imgsz"), "batch": a.get("batch"), "device": a.get("device"),
        },
        "sampling": {k: a.get(k) for k in ("every_sec", "n_burst", "motion_gap", "n",
                                           "judge_frames", "judge_window") if a.get(k) is not None},
        "cameras": camera_block(channels, lay),
        "field_layout_fingerprint": file_fingerprint(FIELD_LAYOUT),
        "zones": {ch: file_fingerprint(CONFIG_DIR / f"{ch}_zones.json") for ch in channels},
        "calibration": {ch: _calib_summary(CONFIG_DIR / f"{ch}_calib.json") for ch in channels},
        "configs": configs,
        "coordinate_frame": {"units": "cm", "origin": "corner pole A0",
                             "x_len_cm": fld.get("x_len_40ft"), "y_width_cm": fld.get("y_width_20ft"),
                             "note": "WISER inch frame is a separate, unverified offset frame"},
        "inputs": inputs,
        "caveats": [
            "Shelter counts are a LOWER BOUND: the wall-edge blind zone hides rats from both human and detector.",
            "view_quality / usable_* are reliability covariates, not exclusion rules.",
            "measurement_context is covariates + provenance only; it never changes any result.",
        ],
        "command_args": a,
    }
    ctx["mc_run_id"] = run_id(ctx)
    return ctx


def write_manifest(path, context):
    """Write the measurement_context manifest as JSON next to the outputs. Returns the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(context, indent=2, default=str), encoding="utf-8")
    return p

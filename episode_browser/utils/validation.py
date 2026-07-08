"""
validation.py — schema + state-model-registry checks. Data-layer ONLY (no UI).

Two jobs:
  1. Validate episodes against episode_schema.yaml (required fields, enum membership,
     types loose enough for a prototype but strict on the invariants).
  2. Enforce the state-model registry invariants (state_models.yaml):
       * every episode's state_model_id resolves to a registered model;
       * `zones` may NOT appear in a model's `features` unless the model declares
         `zone_is_feature: true` — keeping the segmentation blade off human categories.

Returns structured issues (never raises on data problems) so the browser can SHOW
mess rather than crash on it — surviving messy field data is the point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent.parent
SCHEMA_PATH = HERE / "episode_schema.yaml"
REGISTRY_PATH = HERE / "state_models.yaml"


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def summary(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'} — {len(self.errors)} error(s), {len(self.warnings)} warning(s)"


def load_schema(path: str | Path = SCHEMA_PATH) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_registry(path: str | Path = REGISTRY_PATH) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate_registry(registry: dict) -> ValidationReport:
    """Check the state-model registry itself: the zone-as-feature rule lives here."""
    rep = ValidationReport()
    models = (registry or {}).get("state_models") or {}
    if not models:
        rep.error("state_models registry is empty — every episode needs a model to point at.")
    for mid, model in models.items():
        feats = model.get("features") or []
        zone_is_feature = bool(model.get("zone_is_feature", False))
        if any(f in ("zone", "zones") for f in feats) and not zone_is_feature:
            rep.error(
                f"state_model '{mid}' lists a zone feature but does not declare "
                f"zone_is_feature: true — zone must stay a post-hoc label unless explicitly a feature."
            )
        if "is_synthetic" not in model:
            rep.warn(f"state_model '{mid}' has no is_synthetic flag; assuming False.")
    return rep


def validate_episodes(episodes: list[dict], schema: dict, registry: dict) -> ValidationReport:
    """Validate a list of episode dicts. Accumulates issues; does not raise."""
    rep = ValidationReport()
    fields = schema.get("fields", {})
    enums = schema.get("enums", {})
    required = [name for name, spec in fields.items() if spec.get("required")]
    model_ids = set((registry or {}).get("state_models", {}).keys())

    for i, ep in enumerate(episodes):
        tag = ep.get("episode_id", f"<row {i}>")

        # Required fields present.
        for name in required:
            if ep.get(name) is None:
                rep.error(f"{tag}: missing required field '{name}'.")

        # state_model_id must resolve — the 'what cut this?' guarantee.
        smid = ep.get("state_model_id")
        if smid is not None and smid not in model_ids:
            rep.error(f"{tag}: state_model_id '{smid}' is not in the registry.")

        # Enum membership (loose: unknown values warn, they don't crash the browser).
        lvl = ep.get("level")
        if lvl is not None and lvl not in enums.get("level", []):
            rep.error(f"{tag}: level '{lvl}' is not a valid level.")
        for s in ep.get("source_streams") or []:
            if s not in enums.get("source_streams", []):
                rep.warn(f"{tag}: source_stream '{s}' not in the known vocabulary.")

        # Time sanity.
        ts, te = ep.get("t_start"), ep.get("t_end")
        if ts is not None and te is not None and te < ts:
            rep.error(f"{tag}: t_end < t_start.")

        # Confidence range (0..1) — warn, don't fail; real data is messy.
        for cf in ("boundary_confidence", "identity_confidence", "tracking_quality"):
            v = ep.get(cf)
            if v is not None and not (0.0 <= float(v) <= 1.0):
                rep.warn(f"{tag}: {cf}={v} outside [0,1].")

        # lens_scores: absence is first-class. A present-but-null value is the bug.
        ls = ep.get("lens_scores")
        if isinstance(ls, dict):
            for k, v in ls.items():
                if v is None:
                    rep.warn(f"{tag}: lens_score '{k}' is present but null — use ABSENT (omit) instead of 0/null.")

    return rep


def validate_all(episodes: list[dict],
                 schema_path: str | Path = SCHEMA_PATH,
                 registry_path: str | Path = REGISTRY_PATH) -> ValidationReport:
    """Convenience: load schema + registry, validate both registry and episodes."""
    schema = load_schema(schema_path)
    registry = load_registry(registry_path)
    reg_rep = validate_registry(registry)
    ep_rep = validate_episodes(episodes, schema, registry)
    merged = ValidationReport(ok=reg_rep.ok and ep_rep.ok,
                              errors=reg_rep.errors + ep_rep.errors,
                              warnings=reg_rep.warnings + ep_rep.warnings)
    return merged

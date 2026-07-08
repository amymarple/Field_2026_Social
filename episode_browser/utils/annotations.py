"""
annotations.py — append-only writers for human output. Data-layer ONLY.

Two products, both write NEW timestamped files and NEVER overwrite existing data
or the episode store:

  * standard annotations  -> outputs/annotations/annotations_<session>.jsonl
    {episode_id, annotator_id, verdict, added_labels, note, ts, mode}

  * blind-evaluation logs  -> outputs/evaluations/blind_eval_<session>.jsonl
    {ranking_method, annotator_id, episode_id, verdict, revealed_scores, ts}
    The verdict is recorded BEFORE scores are revealed (the UI enforces the order);
    `annotator_id` is logged so downstream enrichment can detect self-agreement
    (annotator == the person the scorer was tuned to).

`session` is a caller-supplied tag (e.g. a run id) so concurrent annotators don't
clash. Records append; a re-run continues the same file.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ANNOT_DIR = HERE / "outputs" / "annotations"
EVAL_DIR = HERE / "outputs" / "evaluations"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, record: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def write_annotation(episode_id: str, annotator_id: str, verdict: str,
                     added_labels: list[str] | None = None, note: str = "",
                     session: str = "default", mode: str = "standard") -> Path:
    """Append one standard annotation. Returns the file path."""
    rec = {
        "episode_id": episode_id,
        "annotator_id": annotator_id or "anonymous",
        "verdict": verdict,
        "added_labels": added_labels or [],
        "note": note,
        "mode": mode,
        "ts": _utc_iso(),
    }
    return _append_jsonl(ANNOT_DIR / f"annotations_{session}.jsonl", rec)


def log_blind_eval(episode_id: str, annotator_id: str, ranking_method: str,
                   verdict: str, revealed_scores: dict | None = None,
                   session: str = "default") -> Path:
    """Append one blind-evaluation record (verdict captured pre-reveal)."""
    rec = {
        "ranking_method": ranking_method,
        "annotator_id": annotator_id or "anonymous",
        "episode_id": episode_id,
        "verdict": verdict,
        "revealed_scores": revealed_scores or {},
        "ts": _utc_iso(),
    }
    return _append_jsonl(EVAL_DIR / f"blind_eval_{session}.jsonl", rec)


def read_log(path: Path) -> list[dict]:
    """Read back a JSONL log (annotations or evals). Empty list if absent."""
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

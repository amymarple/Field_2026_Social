"""
generate_synthetic_episodes.py — fabricate a MESSY synthetic episode repository.

Purpose: give the browser something to survive before real CV/WISER segmentation
exists. It does NOT clean data — it fabricates the field pathologies the browser
must handle (unknown identity, ID swaps, gaps, conflicting sources, low-confidence
boundaries, fogged views, WISER dropout/jitter, thermal ambiguity, field-note
overlaps, and pair/group episodes invisible in any single-animal channel).

State model: EVERY episode is stamped `state_model_id: synthetic_v0`
(is_synthetic: true, see state_models.yaml). Real and synthetic episodes are meant
to coexist in ONE store, told apart by this flag — never by separate files.

Invariants honored:
  * Completeness is the product — episodes tile time; the un-episoded remainder is
    emitted as gap-reason rows (coverage_gaps.jsonl), not silently dropped.
  * The blade is not human categories — cuts here are over the toy state vector
    (x, y, speed, dyad_distance); zones/labels are attached AFTER, as annotations.
  * lens_scores absence is first-class — most episodes carry NO scores; some carry
    a partial subset. Never write 0 to mean "unscored".

Outputs (episode_browser/data/):
  synthetic_episodes.jsonl     always (human-readable, lossless)
  synthetic_episodes.parquet   when pyarrow is available (primary store)
  coverage_gaps.jsonl          gap-reason sidecar for the coverage timeline

Run:  python generate_synthetic_episodes.py            # default ~3 h night, seed 0
      python generate_synthetic_episodes.py --minutes 60 --seed 7
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from utils import episode_io, validation  # noqa: E402

STATE_MODEL_ID = "synthetic_v0"
SCHEMA_VERSION = 1
EDT = timezone(timedelta(hours=-4))

# Real WISER tag ids (shortid) from rat_identities.csv. shortid is a TAG, not a name.
ACTIVE_SUBJECTS = ["12378", "12395", "12407", "12386", "12380"]  # Siesta Sen Dormi Nox Hypnos
# Field-frame-ish zone names (labels only; NOT the segmentation ontology).
ZONE_NAMES = ["shelter_A", "shelter_B", "refuge_1", "refuge_2", "open", "water", "food", "tunnel"]

# Video-bearing episodes point at a tiny generated stand-in clip (data/sample_clip.mp4,
# built on demand by utils.video_preview). Real ingest will point video_path at the
# actual CLOSED Reolink hour + true offset instead.
SAMPLE_CLIP_REL = "data/sample_clip.mp4"
SAMPLE_CLIP_SECONDS = 60

# Lens keys — assigned SPARSELY. Absence stays absent.
LENS_KEYS = ["recurrence", "self_surprise", "joint_surprise", "transition_strength",
             "context_dependence", "downstream_consequence", "expert_priority"]


def to_ms(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def light_phase_for(dt_edt: datetime) -> str:
    h = dt_edt.hour
    if 6 <= h < 20:
        return "day"
    if h in (20, 21) or h in (5,):
        return "dusk" if h >= 20 else "dawn"
    return "night"


class Gen:
    def __init__(self, seed: int, start_edt: datetime, minutes: int):
        self.rng = np.random.default_rng(seed)
        self.start = start_edt
        self.end = start_edt + timedelta(minutes=minutes)
        self.episodes: list[dict] = []
        self.gaps: list[dict] = []
        self._n = 0

    # -- helpers ---------------------------------------------------------------
    def _eid(self, prefix: str) -> str:
        self._n += 1
        return f"synth_{prefix}_{self._n:05d}"

    def _zones(self, primary: str) -> dict:
        """Probabilistic multi-label zone membership; never a single fixed enum."""
        p = round(float(self.rng.uniform(0.55, 0.9)), 2)
        other = self.rng.choice([z for z in ZONE_NAMES if z != primary])
        return {primary: p, str(other): round(1 - p, 2)}

    def _env(self, dt_edt: datetime) -> dict:
        """Environment covariates. Day 3 (2026-06-30) had a ~17:30 storm; nights cool."""
        rain = bool(self.rng.random() < 0.15)
        return {
            "rain": rain,
            "temperature_c": round(float(self.rng.normal(24 if dt_edt.hour < 6 else 29, 2)), 1),
            "humidity": round(float(self.rng.uniform(0.55, 0.95)), 2),
            "light_phase": light_phase_for(dt_edt),
        }

    def _maybe_lens(self) -> dict | None:
        """~35% of episodes get SOME lens scores; of those, only a subset of keys."""
        if self.rng.random() > 0.35:
            return None                      # absence is first-class
        k = int(self.rng.integers(1, 4))
        keys = self.rng.choice(LENS_KEYS, size=k, replace=False)
        return {str(key): round(float(self.rng.uniform(0, 1)), 3) for key in keys}

    def _video_link(self, streams: list[str], dur_s: float) -> dict | None:
        """Point a video-bearing episode at the stand-in clip for a light preview.
        Offset is placed so [offset, offset+span] fits inside the sample clip."""
        if "video" not in streams or self.rng.random() > 0.7:
            return None
        span = float(min(6.0, max(2.0, dur_s)))
        offset = round(float(self.rng.uniform(0, max(0.1, SAMPLE_CLIP_SECONDS - span))), 1)
        return {
            "video_path": SAMPLE_CLIP_REL,
            "video_t_offset_s": offset,
            "preview_span_s": span,
            "synthetic": True,
        }

    def _base(self, level: str, subjects: list[str], t0: int, t1: int,
              streams: list[str], dt_edt: datetime) -> dict:
        link = self._video_link(streams, (t1 - t0) / 1000.0)
        base = {
            "episode_id": self._eid(level),
            "schema_version": SCHEMA_VERSION,
            "state_model_id": STATE_MODEL_ID,
            "level": level,
            "subject_ids": subjects,
            "t_start": t0,
            "t_end": t1,
            "state_vector": {
                "x": round(float(self.rng.uniform(0, 1219.2)), 1),
                "y": round(float(self.rng.uniform(0, 609.6)), 1),
                "speed": round(float(abs(self.rng.normal(2, 3))), 2),
                "dyad_distance": round(float(abs(self.rng.normal(40, 25))), 1),
            },
            "source_streams": streams,
            "boundary_confidence": round(float(self.rng.uniform(0.4, 0.98)), 2),
            "identity_confidence": round(float(self.rng.uniform(0.4, 0.99)), 2),
            "tracking_quality": round(float(self.rng.uniform(0.3, 0.98)), 2),
            "environment_context": self._env(dt_edt),
            "lens_scores": self._maybe_lens(),
        }
        if link:
            base["linked_assets"] = link
        return base

    # -- per-animal timeline (with gaps + pathologies) -------------------------
    def gen_per_animal(self):
        for subj in ACTIVE_SUBJECTS:
            t = self.start
            while t < self.end:
                dur_s = float(self.rng.choice([8, 15, 30, 90, 240, 600],
                                              p=[0.15, 0.2, 0.2, 0.2, 0.15, 0.1]))
                t0 = to_ms(t)
                t1 = to_ms(t + timedelta(seconds=dur_s))

                # Occasionally leave an UN-EPISODED gap instead of an episode.
                if self.rng.random() < 0.18:
                    reason = str(self.rng.choice(["tracking_lost", "occlusion", "no_data"],
                                                 p=[0.4, 0.35, 0.25]))
                    self.gaps.append({"subject_id": subj, "level": "per_animal",
                                      "t_start": t0, "t_end": t1, "reason": reason})
                    t += timedelta(seconds=dur_s)
                    continue

                streams = ["WISER"]
                if self.rng.random() < 0.6:
                    streams.append("video")
                ep = self._base("per_animal", [subj], t0, t1, streams, t)

                # Post-hoc label (attached AFTER the cut; long+slow => rest_like).
                primary = str(self.rng.choice(ZONE_NAMES))
                ep["zones"] = self._zones(primary)
                labels = []
                if dur_s >= 240 and ep["state_vector"]["speed"] < 1.5:
                    labels.append("rest_like")
                if primary.startswith("shelter") and self.rng.random() < 0.5:
                    labels.append("shelter_entry")
                if labels:
                    ep["labels"] = labels

                # --- pathologies ---
                qc = []
                # Unknown identity.
                if self.rng.random() < 0.06:
                    ep["subject_ids"] = ["unknown"]
                    ep["identity_confidence"] = round(float(self.rng.uniform(0.05, 0.3)), 2)
                    qc.append("unknown_identity")
                # ID swap (assign a different subject, low confidence, flag it).
                elif self.rng.random() < 0.05:
                    other = str(self.rng.choice([s for s in ACTIVE_SUBJECTS if s != subj]))
                    ep["subject_ids"] = [other]
                    ep["subject_confidence"] = {other: 0.35, subj: 0.3}
                    qc.append("id_swap")
                # WISER dropout / jitter.
                if self.rng.random() < 0.1:
                    qc.append(str(self.rng.choice(["wiser_dropout", "wiser_jitter"])))
                    ep["tracking_quality"] = round(float(self.rng.uniform(0.15, 0.5)), 2)
                # Fogged / low-visibility span (CH05/06 glass).
                if self.rng.random() < 0.08 and "video" in streams:
                    qc.append("fogged_view")
                    ep["boundary_confidence"] = round(float(self.rng.uniform(0.2, 0.5)), 2)
                    if self.rng.random() < 0.5:
                        (ep.setdefault("labels", [])).append("possible_artifact")
                # Conflicting sources for the same interval.
                if self.rng.random() < 0.05:
                    qc.append("conflicting_sources")
                    ep["notes"] = "WISER places subject in open; video suggests shelter — unresolved."
                # Missing data (drop tracking_quality).
                if self.rng.random() < 0.05:
                    ep["tracking_quality"] = None
                if qc:
                    ep["qc_flags"] = qc

                self.episodes.append(ep)
                t += timedelta(seconds=dur_s)

    # -- pair episodes (social proximity; sometimes an ID-swap dyad) -----------
    def gen_pairs(self, n: int):
        for _ in range(n):
            a, b = self.rng.choice(ACTIVE_SUBJECTS, size=2, replace=False)
            off = float(self.rng.uniform(0, (self.end - self.start).total_seconds() - 120))
            t = self.start + timedelta(seconds=off)
            dur = float(self.rng.choice([20, 60, 180, 300]))
            ep = self._base("pair", [str(a), str(b)], to_ms(t),
                            to_ms(t + timedelta(seconds=dur)),
                            ["WISER", "video"], t)
            ep["state_vector"]["dyad_distance"] = round(float(self.rng.uniform(5, 25)), 1)
            ep["labels"] = ["social_proximity"] + (["following"] if self.rng.random() < 0.4 else [])
            ep["zones"] = self._zones(str(self.rng.choice(ZONE_NAMES)))
            self.episodes.append(ep)

    # -- group episodes (invisible at per-animal level) ------------------------
    def gen_groups(self, n: int):
        """Synchronized group retreat: each animal's OWN episode is unremarkable, but
        the joint event is the phenomenon. This is the 'invisible in any single
        channel' case the browser must surface at the group level."""
        for _ in range(n):
            off = float(self.rng.uniform(0, (self.end - self.start).total_seconds() - 300))
            t = self.start + timedelta(seconds=off)
            dur = float(self.rng.choice([60, 120, 300]))
            ep = self._base("group", list(ACTIVE_SUBJECTS), to_ms(t),
                            to_ms(t + timedelta(seconds=dur)),
                            ["WISER", "video", "thermal"], t)
            ep["labels"] = ["group_convergence"] + (["rain_response"]
                                                    if ep["environment_context"]["rain"] else [])
            ep["zones"] = self._zones("shelter_A")
            ep["notes"] = "Synchronized retreat — unremarkable per-animal, salient jointly."
            ep["lens_scores"] = {"joint_surprise": round(float(self.rng.uniform(0.6, 0.95)), 3)}
            # Thermal ambiguity pathology.
            if self.rng.random() < 0.4:
                ep["qc_flags"] = ["thermal_ambiguous"]
            self.episodes.append(ep)

    # -- environment / field-note episodes (overlap behavioral ones) -----------
    def gen_field_notes(self):
        """Field-note-sourced episodes drawn from FIELD_OBSERVATIONS.md-style events.
        These OVERLAP behavioral episodes in time (a note about rain co-occurs with
        rat episodes) and are level=environment, source=field_note."""
        notes = [
            ("rain onset + thunder; rats wake", ["rain_response"], {"rain": True}),
            ("glass glare over shelter (view degraded)", ["possible_artifact"], {"rain": False}),
            ("morning fog on IR glass", ["possible_artifact"], {"rain": False}),
        ]
        span = (self.end - self.start).total_seconds()
        for text, labels, envflag in notes:
            off = float(self.rng.uniform(0, max(1.0, span - 600)))
            t = self.start + timedelta(seconds=off)
            dur = float(self.rng.choice([300, 600, 900]))
            ep = self._base("environment", ["unknown"], to_ms(t),
                            to_ms(t + timedelta(seconds=dur)), ["field_note"], t)
            ep["labels"] = labels
            ep["notes"] = text
            ep["environment_context"].update(envflag)
            ep["boundary_confidence"] = None      # a human note has no crisp boundary
            self.episodes.append(ep)

    def run(self):
        self.gen_per_animal()
        self.gen_pairs(n=max(4, int((self.end - self.start).total_seconds() // 900)))
        self.gen_groups(n=max(2, int((self.end - self.start).total_seconds() // 2400)))
        self.gen_field_notes()
        return self.episodes, self.gaps


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--minutes", type=int, default=540,
                    help="record span to fabricate (default 9 h: covers the 6/30 ~17:20 storm + night)")
    ap.add_argument("--start", default="2026-06-30T17:00:00",
                    help="local EDT start, ISO (no tz). Default spans the real 6/30 rain event.")
    ap.add_argument("--outdir", default=str(HERE / "data"))
    args = ap.parse_args()

    start_edt = datetime.fromisoformat(args.start).replace(tzinfo=EDT)
    gen = Gen(args.seed, start_edt, args.minutes)
    episodes, gaps = gen.run()

    # Validate BEFORE writing — the store should never contain schema-invalid rows.
    rep = validation.validate_all(episodes)
    print(f"[validate] {rep.summary()}")
    for e in rep.errors[:20]:
        print("  ERROR:", e)
    for w in rep.warnings[:10]:
        print("  warn :", w)
    if not rep.ok:
        print("[abort] refusing to write an invalid store.")
        sys.exit(1)

    df = pd.DataFrame(episodes)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    jsonl_path = episode_io.write_jsonl(df, outdir / "synthetic_episodes.jsonl")
    print(f"[write] {jsonl_path}  ({len(df)} episodes)")

    # Parquet is the primary store, but pyarrow may be absent — degrade cleanly.
    try:
        pq_path = episode_io.write_parquet(df, outdir / "synthetic_episodes.parquet")
        print(f"[write] {pq_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[skip ] parquet not written ({type(exc).__name__}: {exc}). "
              f"Install pyarrow for the columnar store; JSONL is complete.")

    if gaps:
        gaps_path = episode_io.write_jsonl(pd.DataFrame(gaps), outdir / "coverage_gaps.jsonl")
        print(f"[write] {gaps_path}  ({len(gaps)} gap rows)")

    # Quick completeness read-back so the run reports the tiling it produced.
    by_level = df["level"].value_counts().to_dict()
    print(f"[stats] levels: {by_level}")
    print(f"[stats] span: {args.start} +{args.minutes} min  seed={args.seed}")


if __name__ == "__main__":
    main()

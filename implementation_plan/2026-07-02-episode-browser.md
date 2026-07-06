# Implementation plan — Episode Browser (exploratory episode repository + GUI)

**Date:** 2026-07-02
**Scope tier:** Large (new subsystem, new data schema, new public data model + prototype UI)
**Branch base:** `wiser-analysis-clean`
**Status:** implemented + verified in the same session; this plan is the design of record.

## Goal & motivation

Build a researcher-facing **Episode Browser**: inspect, filter, sort, annotate, and
export candidate *behavioral episodes* from the field recordings. An **episode** is a
time-bounded unit of being-in-a-state (delimited by entry/exit transitions), **not** a
human behavior label. The browser *consumes* episodes; it does not produce or correct
tracks (that is the CV pipeline / SLEAP / idtracker.ai / CVAT job).

## Current problem (why now)

There is no substrate that lets a researcher look across modalities at "interesting
stretches" and judge them. The pilot analyses (WISER directions 1–3, CV shelter, audio)
each produce their own tables; nothing ties a time-bounded behavioral unit to its
provenance, its data-quality caveats, and a human verdict. Before real CV segmentation
exists, we need a data model + UI that already **survives field mess** (unknown identity,
ID swaps, gaps, fogged views, WISER dropout, conflicting sources) so the design is not
retrofitted onto clean toy data.

## Design invariants (non-negotiable)

1. **Completeness is the product; scores are UI.** Every segmented episode enters the
   store. `lens_scores` filter/rank only — never gate ingestion.
2. **The blade is not human categories.** Episodes are cut over a low-level **state
   model**; `zones`/`labels` are post-hoc annotations, never used to segment.
3. **The state model is first-class + versioned.** Every episode carries
   `state_model_id` (FK into `state_models.yaml`); the browser can always answer "what
   cut this?" and marks synthetic-cut episodes distinctly. Real + synthetic coexist in
   one store, told apart by the flag — never by separate files. Validation forbids
   `zones` as a model *feature* unless `zone_is_feature: true`.
4. **The substrate tiles; gaps are rendered, not blanked** (coverage view + % tiled).
5. **Blind evaluation exists** so the enrichment showcase is not self-confirming.
6. **Data model fully separated from UI** — logic in `utils/`, `app.py` is view-only.

## Affected / new files

New subsystem under `episode_browser/` (no existing files modified; all existing
analyses preserved):
- `episode_schema.yaml`, `state_models.yaml` — schema + state-model registry.
- `generate_synthetic_episodes.py` — messy synthetic generator (`synthetic_v0`).
- `utils/episode_io.py` (Parquet/JSONL/CSV; derives `duration_s`),
  `utils/validation.py` (schema + registry checks), `utils/coverage.py` (tiling/gaps),
  `utils/query.py` (filter + lens ranking, absence≠0), `utils/annotations.py`
  (append-only writers), `utils/load_layout.py` (read-only repo-config adapters).
- `app.py` — Streamlit UI (table / detail / coverage / timeline / field / summary /
  annotate + blind-eval).
- `selftest.py` — offline data-layer verification. `README.md`, `requirements.txt`,
  `.gitignore`.

## Inputs / outputs

- **Inputs (read-only, optional):** `preprocessing/computer_vision/configs/field_layout.json`
  (cm, origin A0), `wiser_tracking_analysis/configs/wiser_rois.json` (WISER inches),
  `wiser_tracking_analysis/configs/rat_identities.csv` (shortid→name, Sova `valid_until`).
  All degrade gracefully when absent → synthetic-only behavior.
- **Outputs (git-ignored):** `data/synthetic_episodes.{parquet,jsonl}`,
  `data/coverage_gaps.jsonl`; `outputs/annotations/*.jsonl`, `outputs/evaluations/*.jsonl`
  (new, timestamped, append-only, never overwriting).

## Schema (confirmed against the spec)

Fields: `episode_id, schema_version, state_model_id, level(per_animal|pair|group|
environment), subject_ids(list, allows 'unknown'), subject_confidence, t_start, t_end,
state_vector, state_before, state_after, zones(probabilistic), labels(multi), source_streams,
boundary_confidence, identity_confidence, tracking_quality, qc_flags, lens_scores(optional;
absence first-class), environment_context, linked_assets, notes, expert_annotations`.
`duration_s` derived at load. On disk: Parquet primary, JSONL alt, CSV lossy export only.

## Timestamp / coordinate assumptions

- Time is Unix-ms UTC (matches WISER); the UI labels time UTC and assumes **no**
  cross-device sync (video/audio are EDT wall-clock).
- WISER inches vs field cm are **not** unit-convertible until georeference is confirmed;
  the field view keeps them separate and does not overlay zones on the cm frame.

## Addendum — video preview

Added `utils/video_preview.py` and a Detail-tab filmstrip that **subsamples** a few
frames across an episode's span (fast ffmpeg `-ss` seeks, downscaled — no full decode),
located via `linked_assets`. Honors the recorder safety rule (reads only CLOSED `_to_`
files; refuses an open hour). ffmpeg is an external binary (PATH /
`EPISODE_BROWSER_FFMPEG` / Reolink `bin`), optional — without it only the preview is
disabled. Synthetic episodes link to a tiny on-demand stand-in clip
(`data/sample_clip.mp4`) so the path is exercisable before real footage. Verified via the
extended `selftest.py` (frame extraction + open-file refusal) and `AppTest`.

## Non-goals

- Not a CV tracking-correction / frame-by-frame annotation tool.
- No real segmentation model in this pass (`synthetic_v0` is a placeholder; a real
  `kinematic_v1`/proximity model + ingest loader is future work).
- No claim that synthetic episodes reflect real behavior — they exist to stress the UI.

## Verification

- `python selftest.py` — offline data-layer checks (validation incl. bad `state_model_id`
  and zone-as-feature rule; JSONL + Parquet round-trip preserving nested fields;
  `duration_s` derivation; coverage tiling + gap rendering + % tiled; query lens ranking
  with absence≠0; append-only annotation + blind-eval logging) → PASS.
- `python generate_synthetic_episodes.py` — validates before writing; refuses to write an
  invalid store; produces all four levels + gap sidecar.
- `app.py` boots headless and runs clean through Streamlit `AppTest` (7 tabs, metrics
  compute; no exceptions).

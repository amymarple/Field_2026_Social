# Episode Browser

A light, researcher-facing browser for **behavioral episodes** from the
Field_2026_Social pilot: inspect, filter, sort, annotate, and export candidate
episodes. It **consumes** episodes — it does **not** produce or correct tracks
(that is SLEAP / idtracker.ai / CVAT / the CV pipeline's job).

> Status: **prototype on synthetic messy data.** No real CV/WISER segmentation is
> wired in yet. Everything here is designed to survive field mess *before* real
> data forces it.

## What an "episode" is

A time-bounded unit of *being-in-a-state*, delimited by its entry/exit
transitions — **not** a human behavior label. Two invariants the whole thing
stands on:

1. **Completeness is the product; scores are UI.** Every segmented episode enters
   the store. `lens_scores` (surprise, recurrence, …) only *filter/rank* — they
   never gate what enters.
2. **The blade is not human categories.** Episodes are cut by change-points over a
   low-level **state model** (see below). `zones` and `labels` are attached
   *after* the cut, as annotations. Zone is a label, not the atom.

## The state model is first-class

An episode is only meaningful relative to the representation it was segmented over.
That representation is an unresolved scientific choice, so it is explicit, named,
versioned, and swappable in [`state_models.yaml`](state_models.yaml). **Every
episode records `state_model_id`** — the browser can always answer *"what cut this
episode?"* and marks **synthetic-cut** episodes (⚗️) distinctly from real ones.
Real and synthetic episodes live in **one store**, told apart by this field, never
by separate files. Validation forbids `zones` as a model *feature* unless the model
explicitly declares `zone_is_feature: true`.

## Layout

```
episode_browser/
  episode_schema.yaml            # the extensible episode schema (loaded by validation)
  state_models.yaml              # the state-model registry (the atom's provenance)
  generate_synthetic_episodes.py # fabricate a MESSY synthetic store (all 4 levels + pathologies)
  selftest.py                    # offline data-layer check -> PASS/FAIL, exit code
  app.py                         # Streamlit UI — VIEW ONLY, imports everything from utils/
  requirements.txt
  data/                          # generated store (git-ignored): parquet + jsonl + coverage_gaps.jsonl
  outputs/
    annotations/                 # standard annotations (new, timestamped, append-only)
    evaluations/                 # blind-eval logs for enrichment
  utils/
    episode_io.py                # read/write Parquet/JSONL; CSV = lossy export; derives duration_s
    validation.py                # schema + state-model-registry checks (incl. zone-as-feature rule)
    coverage.py                  # tiling / gap computation for the coverage timeline
    query.py                     # filtering + lens ranking (absence != zero)
    annotations.py               # append-only annotation + blind-eval writers
    load_layout.py               # read-only adapters onto existing repo config
```

**Data model is fully separated from the UI.** `app.py` renders; all read/query/
write logic is in `utils/`. Streamlit's full-rerun model strains large tables and
stateful annotation, so the logic is kept out of it — a faster frontend can replace
`app.py` without moving any data code.

**Layout — a three-region dashboard:**
- **left (sidebar):** view nav (Dashboard / Video / Summary / Annotate), a search box
  + quick-pick chips, the filters, and a "current slice" info card;
- **centre:** the **Episodes** table (click a row to inspect) with an Export button,
  and below it the **Timeline / Field map / Coverage-QC** panels;
- **right:** the **Episode Detail** panel (labels, times, confidences, source streams,
  state vector, source-evidence buttons, lens-score bars, notes, and quick verdict
  actions).

A theme (`.streamlit/config.toml`, picked up when you run from `episode_browser/`)
gives the blue accents.

## Quickstart

```bash
pip install -r requirements.txt          # pandas/numpy/pyyaml + pyarrow + streamlit
python generate_synthetic_episodes.py    # writes data/synthetic_episodes.{parquet,jsonl} + coverage_gaps.jsonl
python selftest.py                        # offline data-layer check -> "PASS — data layer healthy"
streamlit run app.py                      # open the browser
```

`pyarrow` is optional: without it the Parquet write is skipped and the app reads the
JSONL store. The generator is deterministic (`--seed`); `--minutes` sets the
fabricated record span (default 3 h).

**Video preview needs ffmpeg** (not a pip dependency). The app finds it on `PATH`, or
via `EPISODE_BROWSER_FFMPEG`, or in `E:\Reolink_record\bin` / `D:\Reolink_record\bin`.
On the analysis PC the `audio` conda env bundles one:

```bash
export EPISODE_BROWSER_FFMPEG="C:/Users/Cornell/anaconda3/envs/audio/Library/bin/ffmpeg.exe"
```

Without ffmpeg everything else still works; only the frame preview is disabled.

## On-disk format

- **Parquet** — primary store (columnar, fast scalar filtering). Nested fields
  (`state_vector`, probabilistic `zones`, `lens_scores`, …) are stored losslessly.
- **JSONL** — human-readable, lossless alternative.
- **CSV** — **lossy export only** (nested fields get JSON-stringified). Never the
  primary store or a re-import path.

`duration_s` is **derived at load** (`t_end − t_start`), never stored, so it cannot
drift from the canonical time source.

## Lightness (don't load everything at once)

- The store loads once (cached).
- The browser **opens on a short initial time window** (60 min — sized to include the
  real 6/30 storm), not the whole record — widen it deliberately.
- The table shows compact scalar summaries; the full nested record is materialized
  only for the **one selected** episode.

## Browser features

- **Search (sidebar)** — free-text search over an in-memory episode *index*, applied
  live as you type, or click a **quick-pick** chip (the rats plus `group`/`rain_response`).
  Case-insensitive, AND over tokens, matching episode id, subjects (tag id **and** resolved
  name), labels, zones, source streams, QC flags, and notes. The result feeds the whole
  dashboard; the sidebar filters then narrow it, and an **active-filter strip** above the
  table summarizes what's loaded / filtered / selected.
- **Table** — sortable/filterable: time, subject, level, label, zone, source
  stream, `state_model_id`, confidence fields, QC flag, environment, and lens score
  ranges. Columns distinguish **Boundary conf.** (a genuine 0–1 confidence bar),
  **Lens rank** (max lens score — a *UI ranking aid, not ground truth*; blank when
  unscored, never 0), and **Track qual** (tracking quality, a plain number — not a bar),
  with a caption saying the bars are not ground truth. **Absent scores filter as absent,
  not as zero.**
- **Detail** — full per-episode record, `state_model_id` prominent (⚗️ if
  synthetic), state-vector, confidence/QC, labels, lens scores, environment, links.
- **Coverage timeline (required)** — per subject × level, episoded vs **un-episoded**
  time with gap reasons (`tracking_lost` / `occlusion` / `no_data`) and a
  **"% record tiled"** metric. Gaps are the substrate failing to exist — rendered,
  never blanked.
- **Timeline** — episodes over time (per-subject lanes, EDT axis), colored by label,
  with a **rain-shaded background band** (blue) drawn from the real weather so storms
  are visible directly against the episodes.
- **Field map** — a large **scatter of real WISER positions** for the window, read-only
  from the daily backup (`D:\Reolink_record\audio_in\Wiser_backup\incremental\*.csv.gz`,
  override `EPISODE_BROWSER_WISER_DIR`). Each **rat is a distinct hue** and **time is a
  lightness gradient of that hue** (light = earlier → dark = later), shown with one compact
  per-rat gradient legend, over the paddock
  **landmarks** (shelter/house boxes, refuges, water, food + boundary) from
  `wiser_rois.json`. Drawn in the **WISER native inch offset frame** — explicitly
  **UNVERIFIED vs the cm field frame**; inches are never converted to cm until the
  georeference transform is confirmed. The **Focus rats** control limits both this scatter
  and the Timeline to the selected rats.
- **Weather** — real Ambient Weather Network (AWN) station data for the window (temp +
  rain-rate, humidity summary), read from `D:\Reolink_record\audio_in\weather_data`
  (override with `EPISODE_BROWSER_WEATHER_DIR`). Aligned on **EDT wall-clock**, which
  is **unverified** across devices — a covariate over time, not a synchronized signal.
  Each episode's Detail shows the nearest weather sample at its start.
- **Dates & days** — times are shown in **EDT** (field-local), with a **Day** column /
  label counted from **Day 1 = 2026-06-28** (release day / epoch).
- **Summary** — per-rat counts, lens-score *presence* coverage, QC frequency.
- **Video** (own tab) — a few **subsampled** frames across the selected episode's
  span (fast ffmpeg seeks, no full decode; frame count + width adjustable, loaded on a
  button press and cached). Has its own episode picker (defaults to the Table
  selection). Locates footage via `linked_assets.video_path`
  + `video_t_offset_s`. Only **closed** recordings are read — an open Reolink hour
  (`..._<start>.mp4`, no `_to_`) is refused. Synthetic episodes point at a tiny
  generated stand-in clip (`data/sample_clip.mp4`, built on demand) so the preview
  works before real footage exists.
- **Annotate** — quick verdicts (⭐ Interesting / ？ Unclear / ⚠ Artifact / ↪ Follow-up)
  live in the Detail panel; the Annotate view adds labels + notes and a **Blind-evaluation
  mode**: rank by a lens, present top-k with **all `lens_scores` hidden**, record the
  verdict, *then* reveal + log `{ranking_method, annotator_id, verdict, episode_id}`. Set
  your **annotator ID once in the sidebar** (shown in the header); writes are blocked with a
  nudge until it's set, so `annotator_id` is always logged and downstream enrichment can
  detect self-agreement. All writes are **append-only, timestamped, never overwriting**.

## Real data comes later

`utils/load_layout.py` already reads the canonical repo configs read-only
(`field_layout.json`, `wiser_rois.json`, `rat_identities.csv`). The real ingest path
is a future `state_model` (e.g. `kinematic_v1`) plus a loader that segments WISER/CV
streams into episodes and stamps them with that model id — appended into the **same**
store alongside the synthetic ones.

### Caveats carried from the repo

- WISER frame is **inches** (unverified offset origin); CV field frame is **cm**
  (origin pole A0). Not unit-convertible until the georeference transform is
  confirmed — the field view keeps them separate.
- Clocks differ across devices (WISER Unix-ms UTC; video/audio EDT wall-clock). The
  browser labels time as UTC and does not assume cross-device sync.
- `shortid` is a **tag id**, not an animal — names resolve via `rat_identities.csv`.

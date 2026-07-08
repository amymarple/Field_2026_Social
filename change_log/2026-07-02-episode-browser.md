# Change log — Episode Browser (exploratory episode repository + prototype GUI)

**Date:** 2026-07-02
**Commit:** uncommitted at time of writing.
**Plan:** [Episode Browser implementation plan](../implementation_plan/2026-07-02-episode-browser.md)
**Scope tier:** Large (new subsystem, new data schema, new prototype UI).

## What changed

New self-contained subsystem `episode_browser/` — a light, researcher-facing browser for
time-bounded **behavioral episodes**. It *consumes* episodes; it does not produce or correct
tracks. No existing files were modified; all prior analyses are preserved.

- **Schema + registry.** `episode_schema.yaml` (full extensible schema; `duration_s` derived
  at load, never stored) and `state_models.yaml` (the state-model registry — every episode
  carries `state_model_id`; `synthetic_v0` is `is_synthetic: true`; validation forbids `zones`
  as a model *feature* unless `zone_is_feature: true`).
- **Data layer (`utils/`, no UI imports):** `episode_io.py` (Parquet primary / JSONL alt /
  CSV lossy-export; nested fields JSON-encoded losslessly; derives `duration_s`),
  `validation.py` (schema + registry checks, incl. unregistered `state_model_id` and the
  zone-as-feature rule), `coverage.py` (per subject×level tiling, gap reasons, % tiled),
  `query.py` (filtering + lens ranking where **absence ≠ 0**), `annotations.py` (append-only,
  timestamped writers for standard annotations + blind-eval logs), `load_layout.py`
  (read-only adapters onto `field_layout.json` / `wiser_rois.json` / `rat_identities.csv`,
  graceful when absent).
- **Synthetic messy generator.** `generate_synthetic_episodes.py` stamps every episode with
  `synthetic_v0` and fabricates field pathologies (unknown identity, ID swaps, un-episoded
  gaps, fogged views, WISER dropout/jitter, conflicting sources, thermal ambiguity) across all
  four levels — including group episodes invisible per-animal and `field_note` episodes that
  overlap behavioral ones. Validates before writing; refuses to write an invalid store.
- **UI.** `app.py` (Streamlit, view-only): Table / Detail / **Coverage (required)** / Timeline
  / Field-zones / Summary / **Annotate (standard + blind-evaluation)**. Opens on a 15-minute
  slice (does not load the whole record); materializes full nested detail only for the selected
  episode; marks synthetic-cut episodes (⚗️).
- **Support.** `selftest.py` (offline data-layer check), `README.md`, `requirements.txt`,
  `.gitignore` (+ `.gitkeep`s). Generated data and human outputs are git-ignored.

## Why

Before real CV/WISER segmentation exists, we need a substrate + UI that already survives field
mess and encodes the project's invariants: completeness is the product (lens scores rank, never
gate), the segmentation blade is a versioned low-level **state model** (not human categories),
gaps are rendered not blanked, and a **blind-evaluation** mode keeps the enrichment showcase
from being self-confirming. Building against synthetic mess now avoids retrofitting the design
onto clean toy data later.

## Verification

Run with anaconda3 base (pandas/numpy/pyyaml/pyarrow/streamlit present):

- `python selftest.py` → **PASS** — 18 checks: schema/registry validation (incl. bad
  `state_model_id` and zone-as-feature rejected), JSONL **and** Parquet round-trip preserving
  nested `state_vector`/`zones`/`lens_scores`, `duration_s` derived (=3.0 s), coverage tiling
  renders an un-episoded interval as a gap with a sane % tiled, lens ranking + range filter with
  absence ≠ 0, append-only annotation + blind-eval logging.
- `python generate_synthetic_episodes.py` → validate PASS; 358 episodes
  (per_animal 339 / pair 12 / group 4 / environment 3); 80 gap rows; Parquet + JSONL written.
- `app.py` boots headless and runs clean through Streamlit `AppTest` — 7 tabs render, header
  metrics compute (38 episodes in the default 15-min window, 62% tiled), no exceptions.

## Addendum — video preview (same day)

Added a **light, subsampled video preview** to the Detail tab.

- **`utils/video_preview.py`** (data-layer): `resolve_video` (episode → clip via
  `linked_assets.video_path` + `video_t_offset_s` + `preview_span_s`), `extract_frames`
  (N evenly-spaced frames via fast ffmpeg `-ss` seeks, one decoded frame each, downscaled
  — never a full decode), `find_ffmpeg` (PATH / `EPISODE_BROWSER_FFMPEG` / Reolink `bin`),
  `is_closed_recording` (refuses an OPEN `..._<start>.mp4` hour; only `_to_` closed files
  are read), and `ensure_sample_clip` (builds a tiny `data/sample_clip.mp4` stand-in via
  ffmpeg `testsrc` on demand).
- **Generator:** video-bearing episodes now carry `linked_assets` pointing at the stand-in
  clip with an in-clip offset, so the preview is exercisable without real footage.
- **UI (`app.py`):** Detail tab shows a filmstrip of subsampled frames (frame count +
  width adjustable), loaded on a button press and cached; warns on open-file links and
  offers to generate the sample clip when missing.
- **Verification:** `selftest.py` extended (now **PASS**, 25 checks) — `resolve_video`
  offset math, open-file refusal, `ensure_sample_clip` creation, and 4 real downscaled PNG
  frames extracted (with `EPISODE_BROWSER_FFMPEG` set to the `audio` env's ffmpeg). Sample
  clip is ~160 KB. `app.py` re-checked clean through `AppTest`.
- **Note:** ffmpeg is an external binary, not a pip dep; without it the preview is disabled
  and every other feature still works.

## Addendum — top search + Video tab (same day)

- **`query.text_search`** — NCBI-style free-text search over an in-memory episode index
  (case-insensitive, AND over tokens; matches episode id, subjects as tag id **and**
  resolved name, labels, zones, source streams, QC flags, notes). Empty query is a no-op.
- **UI:** a search bar + **Run** at the top of the page with **quick-pick chips** (the five
  rats, like NCBI's Human/Mouse/Rat, plus `group`/`pair`/`rain_response`). The search result
  feeds all tabs; the sidebar then narrows it. Uses the canonical keyed-widget +
  `on_click` callback pattern so chips and typing don't fight.
- **Video preview promoted to its own "Video" tab** (8 tabs total) with a self-contained
  episode picker; the Detail tab now points at it.
- **Verification:** `selftest.py` adds `text_search` checks (resolved-name match, zone-label
  match, empty→all, AND semantics); interactive `AppTest` confirms the search box drives
  filtering (28→2 for "Dormi", →0 for a nonsense query) with no exceptions; 8 tabs render.

## Addendum — dashboard layout (same day)

Reworked `app.py` from a tab strip into a **three-region dashboard** matching a supplied
mockup, without moving any logic out of `utils/`:
- **left (sidebar):** view nav (Dashboard / Video / Summary / Annotate), search + quick-pick
  chips, filters, and a "current slice" info card;
- **centre:** an **Episodes** table with **clickable row selection** (`st.dataframe`
  `on_select`, Streamlit 1.45) driving the detail panel, an Export (lossy CSV) button, and
  ProgressColumn confidence/score bars; below it **Timeline / Field-map / Coverage-QC**
  panels (bordered cards);
- **right:** an always-present **Episode Detail** panel — label chips, times/confidence,
  source-evidence buttons (Video jumps to the Video view), lens-score bars (absence shown as
  "absent"), notes, and quick verdict buttons that append annotations.
- Added a header bar + light CSS and a `.streamlit/config.toml` theme (blue accents).
- Fixed a Streamlit 3-level column-nesting limit by rendering the Coverage KPIs as an HTML
  grid instead of nested columns.
- **Verification:** `AppTest` runs clean across all four views (Dashboard/Video/Summary/
  Annotate), detail metrics compute; data-layer `selftest.py` still PASS. Rendered layout
  screenshotted at 1440px — three regions match the mockup.

## Addendum — weather, dates, and trajectory overlays (same day)

- **Real weather.** New `utils/weather.py` loads Ambient Weather Network (AWN) CSV exports
  from `D:\Reolink_record\audio_in\weather_data` (override `EPISODE_BROWSER_WEATHER_DIR`),
  parsing adapted from `audio_analysis/analysis/weather.py` (each subsystem stands alone).
  Added a **Weather** dashboard panel (temp line + rain-rate area + humidity summary over
  the window) and a "Weather @ start" row in the Episode Detail (nearest sample). Alignment
  is on **EDT wall-clock** and explicitly labeled **unverified** across devices (weather-
  station clock ≠ WISER/NVR clock), per the repo convention.
- **Dates + Day-1 = epoch.** Time display switched to field-local **EDT**; added a **Day**
  column/label counted from **Day 1 = 2026-06-28** (release), matching FIELD_OBSERVATIONS.
- **Field-map trajectory overlays.** A **Focus rats** control overlays each selected rat's
  trajectory (its episodes connected in time order) on the field map, and — shared with the
  Timeline — filters the lanes to those rats.
- **Verification:** `AppTest` clean across all views incl. the focus/trajectory path
  (`focus_rats` = Siesta+Nox); live app confirmed real AWN data ("Day 3 · 2026-06-30",
  "temp 25–26 °C · humidity ~89% · 4 samples"); `selftest.py` adds weather checks (missing
  dir → empty typed frame, slice_window, nearest) → PASS.

## Addendum — paddock illustration + timeline rain band (same day)

- **Field map = real paddock.** New cached `field_geometry()` reads `field_layout.json` and
  draws the paddock outline, the 15-pole grid (A0–C4), and both shelter footprints (all cm,
  origin A0), with episode positions + focus-rat trajectories on top. Shelter-distance in the
  Detail now uses the real shelter centres.
- **Timeline rain band.** The real AWN rain series is drawn as a blue background band behind the
  episode lanes (per 5-min sample where rain > 0), so storms show directly against behavior.
- **Synthetic window shifted to span the real storm.** Generator default start → 17:00 EDT / 9 h
  (was 21:00 / 3 h) so the real **6/30 17:20–17:55 shower (peak 10.2 mm/hr)** falls inside the
  record; the browser opens on a 60-min slice that includes it. Store is now 1088 episodes.
- **Verification:** live app (default window) confirmed the paddock + pole grid render, the
  timeline shows "blue band = rain", and the weather panel reads "temp 25–33 °C · mean rain-rate
  3.51 mm/hr · 13 samples" (the storm). `AppTest` clean across all views; `selftest.py` PASS.

## Addendum — viridis-by-rat + real WISER tracks (same day)

- **Field map coloured by rat (viridis, 5 rats)** instead of by level — episode points now use a
  per-rat viridis scale (shared with the trajectory overlays) so the five animals are distinct.
- **Real WISER positions.** New `utils/wiser_tracks.py` reads the read-only daily backup
  (`D:\Reolink_record\audio_in\Wiser_backup\incremental\1stcohort_2026_<date>.csv.gz`, override
  `EPISODE_BROWSER_WISER_DIR`): canonical `shortid/location_x/location_y/timestamp`, loaded only for
  the day-file(s) covering the current window, needed columns only, downsampled per tag. A **Field-map
  Synthetic / Real-WISER toggle** plots the actual rat tracks for the window.
- **Frame safety.** WISER is **inches in the offset frame** — real tracks are drawn in their **native
  inch frame** (with the `wiser_rois.json` boundary), labeled **UNVERIFIED vs field cm**; inches are
  never converted to cm (georeference pending), per CLAUDE.md / field_transform.
- **shortid → animal name** resolves via `rat_identities.csv` (`load_layout.subject_name_map`),
  matching the CLAUDE.md roster (12378 Siesta / 12395 Sen / 12407 Dormi / 12386 Nox / 12380 Hypnos /
  12409 Sova). Fixed a pandas ≥2.2/3.0 regression where `groupby(...).apply()` dropped the grouping
  column (`KeyError: ['shortid'] not in index`); the per-tag downsample is now a vectorised mask.
- **Verification:** loader returns 1521 real positions across all 5 rats for the default window (Sova
  correctly absent post-removal); `AppTest` clean on both field-map sources; live app shows the 5-rat
  viridis legend + both toggle options; `selftest.py` adds WISER checks (missing file → empty,
  candidate-date ordering, window filter + name resolve) → PASS (33 checks).

## Addendum — WISER scatter with time gradient + landmarks (same day)

- **Field map redesigned around real WISER.** Dropped the Synthetic/Real toggle — the field map is
  now a large (**full-width, height 430**) **scatter of real WISER positions**, **coloured by a
  timestamp gradient** (viridis, so temporal flow reads directly) and **shape-coded by rat**.
- **Paddock landmarks.** New `wiser_tracks.load_landmarks()` reads `wiser_rois.json` (WISER inch
  frame — the same frame as the tracks; its house boxes derive from `field_layout.json`'s shelters)
  and the map draws the **shelter/house boxes + tunnel** (rects, labeled), **refuges/water/food**
  (points, labeled), and the boundary. The layout was reflowed: Timeline + Coverage-QC share a row,
  the WISER map spans full width, Weather below.
- **Frame safety unchanged:** native inches, offset frame, **UNVERIFIED vs field cm** — not converted.
- **Verification:** `AppTest` clean across all views in the `cv` env (pandas 3.0.3) with the real
  WISER map loading by default; `selftest.py` PASS (34 checks, adds a `load_landmarks` check); live
  app confirmed time-gradient colour + landmarks + no synthetic toggle.

## Addendum — field-map colour: per-rat hue + time lightness (same day)

- The WISER scatter now encodes **rat = hue** and **time = lightness of that hue** (light early →
  dark late), instead of the previous time-viridis-colour + rat-shape. Colours are computed per
  point (`app.hex_from_hue` via `colorsys`, stable hues from `rat_hue_map`) and drawn with
  `alt.Color(scale=None)`; a single HTML legend row shows each rat's light→dark gradient swatch.
- **Verification:** colours checked (5 distinct hues, each light→dark — Dormi blue / Hypnos orange /
  Nox green / Sen magenta / Siesta purple); live app confirmed the new legend + 5 gradient swatches,
  old time/shape legends gone, UNVERIFIED note retained; `AppTest` clean (cv env); `selftest.py` PASS.

## Addendum — UX pass (workflow clarity, no scientific-logic change)

Implemented the Top-5 from `episode_browser/UX_REVIEW.md`; only `app.py` + README changed (no
data-layer/`utils/` change), preserving every invariant:
- **Table bars no longer read as ground truth.** `score_of` → `lens_rank` (**max lens or NaN, never
  0** — fixes an absence→0 bug); columns are **Boundary conf.** (bar), **Lens rank** (bar, blank when
  unscored), **Track qual** (plain number, not a bar), with a "bars are UI aids, not ground truth"
  caption.
- **Removed the inert header tray** (`ⓘ Help ⚙ Settings 🧑 HC`); the header now shows the session
  annotator ID, and a collapsed **"How to use"** (find → click row → inspect → judge) sits atop the
  dashboard.
- **Active-filter / status strip** above the table: *Showing N of M · day+slice · filters · selected ·
  click a row to inspect* (Q3/Q7 legibility).
- **Unified identity + subject controls:** one **"Your annotator ID"** in the sidebar (was entered
  twice); relabeled "Subject ID" → "Filter table by subject" and "Focus rats" → "Overlay on map &
  timeline". Dropped misleading panel numbering (1·/2·/3·/4·).
- **Data-safety made legible without friction:** append-only one-liner by the verdict actions; **Export
  → "Export CSV"** with a "lossy — not a re-import path" tooltip; verdict/Save/Reveal **block with a
  nudge** until an annotator ID is set (so self-agreement analysis always has a real id).
- **Docs aligned to reality:** README's stale "Run button" / "15-min window" claims corrected (no Run
  button; opens on 60 min).
- **Verification:** `AppTest` clean across all views (cv env); the annotator nudge blocks a write when
  the id is empty and allows it when set; the table exposes Boundary conf./Lens rank/Track qual with
  **Lens rank NaN (never 0)** for unscored episodes; live app confirmed the fake tray is gone, the
  status strip + single annotator input + export relabel render; `selftest.py` PASS (data layer
  untouched).

## Known limitations & next steps

- **Prototype on synthetic data only.** `synthetic_v0` is a placeholder segmentation, not a
  real model; synthetic episodes make no behavioral claim.
- **No real ingest yet.** Next: a real `state_model` (e.g. `kinematic_v1` / proximity-graph)
  plus a loader that segments WISER/CV streams into episodes and appends them into the **same**
  store, told apart from synthetic by `state_model_id`.
- **Frames not reconciled.** WISER inches vs field cm remain separate until the georeference
  transform is confirmed (see `wiser_tracking_analysis` georeferencing); the field view does not
  overlay WISER zones on the cm frame.
- **Streamlit full-rerun** will strain very large stores; the data layer is deliberately UI-free
  so a faster frontend can replace `app.py` without moving logic.

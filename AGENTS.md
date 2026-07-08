# AGENTS.md

Repository-specific workflow instructions for coding agents working on the
Field_2026 codebase and related analysis repositories.

This repository is not only a software project. It is also a real field-data
analysis project involving continuous multimodal recordings from outdoor rat
experiments. Coding agents must therefore protect both code correctness and data
provenance.

## Language Policy

- Write implementation code in English-oriented naming where practical.
- Use English for new function names, class names, variable names, and public
  API names unless compatibility requires otherwise.
- Write implementation-facing documentation in English.
- Write code comments in English.
- Use English for notebook-facing technical guidance, parameter descriptions,
  and developer notes unless the user explicitly asks for another language.
- User-facing summaries may be bilingual or Chinese-first when the user asks,
  but repository code, APIs, schemas, and durable docs should remain English.

## Project Scope

This repository may contain software for:

- WISER/UWB tracking data import, calibration, quality control, and position
  error analysis;
- Reolink/NVR, RTSP, thermal-camera, and other video processing workflows;
- ephys/LFP synchronization and event alignment;
- weather-station and environmental logging;
- manual annotation, identity mapping, and behavioral event tables;
- multimodal integration across tracking, video, thermal, ephys, weather, and
  manually curated metadata;
- downstream modeling, visualization, notebooks, and figure generation.

When in doubt, treat this as a scientific data-analysis repository, not a toy
script repository. Preserve raw data, document assumptions, and make analysis
steps reproducible.

## Change Size Tiers

Use the smallest documentation burden that still protects reproducibility.

### Small Changes

Small changes include typo fixes, formatting, comments, plot-label corrections,
and purely mechanical documentation edits that do not change behavior.

- No new implementation plan is required.
- Update existing documentation if the change alters the meaning of an existing
  feature or instruction.

### Medium Changes

Medium changes include parser behavior, timestamp conversion, exclusion rules,
coordinate transforms, QC metrics, plotting defaults that affect interpretation,
and notebook-facing analysis behavior.

- Create or update an implementation plan in `implementation_plan/` before
  editing source code.
- Update notebooks or notebook-facing docs when needed.
- Create or update a change log entry in `change_log/` after verification.

### Large Changes

Large changes include new data modalities, new alignment methods, new data
schemas, new models, new public APIs, new calibration algorithms, or analysis
pipelines that produce derived datasets or paper-facing figures.

- Create a full implementation plan before changing source code.
- Include expected inputs, outputs, assumptions, non-goals, and verification.
- Update source, tests, notebooks, API-facing docs, and indexes as needed.
- Create a change log after verification.

## Implementation Documentation Workflow

Every non-trivial implementation change must be documented in two steps:

1. Before editing source code, create or update an implementation plan in
   `implementation_plan/`.
2. After implementation and verification, create or update a change log entry in
   `change_log/`.

Small typo fixes, pure formatting, and mechanical documentation-only edits do
not require a new implementation plan, but they should still update existing
documentation if they change the meaning of an implemented feature.

## Field Data Analysis Workflow

For any analysis involving real field data, use this sequence unless there is a
clear reason to do otherwise:

```text
raw data registration -> schema validation -> timestamp normalization -> sync/alignment -> QC report -> derived data generation -> analysis notebook -> figure/report output -> change log
```

This workflow applies to WISER tracking, video, thermal video, ephys/LFP,
weather, manual annotations, and merged multimodal datasets.

Do not skip directly from raw files to figures. First establish what files were
loaded, what time ranges they cover, how timestamps were interpreted, what data
were excluded, and whether the resulting data pass basic QC.

## Raw And Derived Data Rules

- Never modify raw field data in place.
- Keep raw data under `data/raw/` when practical, or record the external raw data
  path in a manifest when files are too large to store in the repository.
- Keep generated or cleaned data under `data/processed/`, `outputs/`, or another
  explicitly documented derived-data location.
- Do not commit large raw videos, large binary ephys files, or bulky generated
  outputs unless the repository is explicitly configured for them.
- Every derived dataset should be reproducible from raw data plus code plus
  metadata.
- Every derived file or analysis output should record, either in a sidecar file,
  notebook metadata, or run manifest:
  - source files and raw data path;
  - script, function, or notebook that generated it;
  - git commit hash when available, or note that the repo was uncommitted;
  - timestamp conversion method;
  - excluded intervals and the reason for exclusion;
  - calibration file or calibration constants used;
  - coordinate system, origin, orientation, and units;
  - animal/tag/camera/device identity mapping used.

## Data Manifest Requirements

When adding support for a new real dataset or test run, prefer a manifest file
such as:

```text
data_manifests/YYYY-MM-DD-short-run.yaml
```

A field-data manifest should include:

- experiment/run name;
- date and absolute time zone;
- animal IDs and tag IDs when available;
- device names and device clocks;
- raw file paths;
- expected sampling rates or frame rates;
- paddock dimensions and coordinate units;
- antenna/camera/sensor layout when relevant;
- known bad intervals, removed intervals, or handling notes;
- whether timestamps come from acquisition computer, device metadata, exported
  filenames, NVR overlays, frame metadata, or manual logs.

For quick pilot data, a lightweight manifest is acceptable, but the timestamp
source and excluded intervals must still be explicit.

## Timestamp And Synchronization Rules

Timestamp handling is a high-risk part of this project. Any code that loads,
converts, aligns, merges, or resamples timestamps must document:

- original timestamp field name;
- original timestamp unit, such as milliseconds, seconds, frames, or samples;
- original clock source, such as WISER computer, NVR, camera, ephys acquisition,
  weather station, or manual logger;
- target time representation;
- time zone, if wall-clock time is used;
- drift correction, offset correction, or sync-pulse method, if any;
- known uncertainty or acceptable alignment error.

Never silently assume that two devices share the same clock. If alignment is not
verified, label the result as unverified alignment rather than synchronized data.

## Quality Control Requirement

Any script or notebook that imports real tracking, video, weather, or ephys data
must be able to produce a QC summary before downstream interpretation.

Minimum QC should include, where applicable:

- number of files loaded;
- time range covered;
- sample count or frame count;
- missing intervals;
- timestamp gaps and duplicate timestamps;
- per-device or per-tag sample counts;
- excluded intervals;
- out-of-bounds coordinates;
- impossible jumps or velocities;
- dropped frames or discontinuous video segments;
- calibration error when ground truth is available;
- warnings about unverified timestamp alignment.

QC outputs should be saved in `outputs/qc/`, `reports/qc/`, or another documented
location when they are used to support later analysis.

## Modality-Specific Requirements

### WISER / UWB Tracking

WISER analyses must document:

- antenna layout and antenna IDs;
- paddock dimensions;
- coordinate origin, orientation, and units;
- tag ID to animal ID mapping;
- tag mounting position when relevant, such as head, jacket, or body;
- sampling rate or requested update rate;
- fixed-point calibration positions;
- excluded intervals, including intervals after a tag or animal was removed;
- position error metric, usually Euclidean error in cm or meters;
- whether errors are reported per tag, per location, per time window, or pooled.

WISER parsing utilities should not assume that short IDs are animal names. They
should use an explicit mapping table or report unresolved short IDs.

### Video / NVR / RTSP

Video analyses must document:

- camera name, NVR channel, stream URL, or export source;
- frame rate and resolution;
- codec and export settings when relevant;
- whether timestamps come from NVR metadata, filenames, frame overlays, RTSP
  acquisition computer time, or another source;
- dropped or missing segments;
- camera position, field of view, and calibration when spatial inference is
  performed;
- whether frames are raw exports, re-encoded clips, manually cut playback files,
  or automated RTSP captures.

Video processing code should avoid duplicating heavy video files unless needed.
Prefer sidecar metadata, frame indexes, and derived tables over unnecessary
large intermediate videos.

### Thermal Cameras

Thermal analyses must document:

- device model and stream source;
- thermal channel versus visible/fusion channel;
- absolute temperature reliability, if known;
- whether analysis uses absolute temperature, relative contrast, or detection
  only;
- calibration assumptions and environmental conditions when relevant.

### Ephys / LFP

Ephys alignment must document:

- acquisition system and recording clock;
- sample rate;
- sync pulse source and detection method;
- alignment target, such as video frames, WISER timestamps, or manual events;
- allowed alignment error;
- channels, regions, and animal IDs used;
- any excluded noisy channels or bad intervals.

Do not claim millisecond-level synchronization unless the sync method and error
estimate support it.

### Weather And Environmental Data

Weather analyses must document:

- device/source name;
- logging interval;
- time zone and clock source;
- measured variables and units;
- missing periods;
- whether data are local exports, cloud downloads, manual logs, or API pulls;
- sensor placement relative to the paddock.

### Manual Annotations And Identity Tables

Manual annotations must document:

- annotator or source;
- annotation schema;
- timestamp basis;
- animal identity confidence;
- ambiguous intervals;
- whether labels are ground truth, weak labels, or exploratory notes.

Identity tables should be versioned when tag names, animal names, colors, camera
views, or marker assignments change.

## Source And Notebook Separation

This repository separates reusable source code from execution-facing notebook
work.

- Keep research, simulation, parsing, QC, synchronization, and model logic in
  the Python package under `src/`.
- Treat notebooks as execution clients of the package rather than as the home of
  core logic.
- When adding exploratory or runnable analysis artifacts, prefer `marimo`
  notebooks under `notebooks/` so the execution side stays reproducible and
  scriptable.
- Structure new notebook work so it imports from `src/` instead of duplicating
  model code, numerical kernels, timestamp conversion, data parsers, or QC logic
  inside notebook cells.
- If a notebook reveals a reusable helper, promote that logic back into `src/`
  and keep the notebook focused on orchestration, parameter selection,
  visualization, and experiment-specific commentary.
- When implementation changes affect how experiments are run, update the
  relevant notebook entrypoints or notebook-facing documentation along with the
  source code.

## Implementation Plan Requirements

Create a dated Markdown file:

```text
implementation_plan/YYYY-MM-DD-short-topic.md
```

The plan should include:

- goal and motivation;
- current problem at the time of planning;
- why this implementation is needed now;
- relevant git base, branch, or worktree state when useful;
- affected modules/files;
- expected input files and output files when data are involved;
- public parameters or API changes;
- timestamp, synchronization, or coordinate-system assumptions when relevant;
- update equations or algorithm details for model changes;
- expected behavior;
- tests, fixture data, QC checks, or notebook checks that will verify the
  change;
- explicit non-goals when scope could otherwise expand.

If the implementation is a continuation of an existing plan, update that plan
instead of creating a duplicate.

The current-problem section should be concrete. Prefer observed failures,
diagnostic results, missing API/notebook support, confusing model behavior,
ambiguous data semantics, or research conclusions that make the implementation
necessary. Avoid only stating the desired feature.

## Change Log Requirements

After the implementation is complete, create or update a dated Markdown file:

```text
change_log/YYYY-MM-DD-short-topic.md
```

The change log should include:

- date and relevant git commit hash, or note that the change is still
  uncommitted;
- what changed in code, notebooks, tests, and documentation;
- why the change was made;
- source data or fixture data used for verification when relevant;
- verification performed, including exact test commands when run;
- QC output or observed behavior;
- known limitations and next steps.

If a change implements a prior plan, link the plan from the change log. If the
implementation changes direction, update the plan or state why the final design
differs.

## Index Maintenance

When adding a new implementation plan, change log, data manifest, or durable QC
report:

- update `implementation_plan/README.md` when plans change;
- update `change_log/README.md` when change logs change;
- update `data_manifests/README.md` if data manifests are indexed there;
- update `reports/README.md` or `outputs/README.md` if durable outputs are
  indexed there;
- keep links relative and valid after moving files.

## Paperflow-To-Implementation Workflow

When the user asks a research or design question and explicitly instructs the
agent to use paperflow, use the repository's paperflow workflow before making
implementation changes.

Required sequence:

```text
paperflow -> discussion -> implementation plan -> implementation -> notebook/API updates -> change log
```

Use this sequence as follows:

1. `paperflow`: create or update `paperflow/<request-slug>/request.md`,
   `run-manifest.yaml`, `run-log.md`, `literature_add_candidates.md`,
   per-paper summaries, and review artifacts as needed.
2. Google Drive connector: when paperflow is used, check the user's Google Drive
   library/Paperpile materials through the Google Drive connector when available,
   especially the canonical `Paperpile/paperpile.bib`, before falling back to
   free web search.
3. `discussion`: capture the research conclusion, design options, rejected
   alternatives, and implementation recommendation in the relevant paperflow
   review/proposal/discussion artifact.
4. `implementation plan`: create or update a dated file in
   `implementation_plan/` before changing source code.
5. `implementation`: make the code change according to the accepted plan.
6. `notebook/API updates`: update notebooks, analysis helpers, public exports,
   or API-facing docs needed to exercise the new behavior.
7. `change log`: after verification, create or update a dated file in
   `change_log/` and link back to both the paperflow discussion and the
   implementation plan.

Do not use paperflow automatically for every implementation. Use it when the
user asks for it, when the question depends on literature-grounded design, or
when the task explicitly starts from a paperflow request.

## Git And Worktree Safety

- Do not revert unrelated user changes.
- Before editing, check `git status --short`.
- If notebooks or generated outputs are already modified and unrelated to the
  task, leave them untouched.
- Prefer source, test, and documentation changes over committing generated logs
  or large output artifacts.
- Do not overwrite raw data, manually curated annotation files, identity maps,
  calibration files, or manifests without an explicit user request.
- When editing generated notebooks or outputs, first determine whether they are
  source-of-truth artifacts or disposable outputs.

## Command Approval Hygiene

To reduce repeated execution-approval prompts in this repository:

- Prefer `rg`, `sed`, and `jq` for code search, text inspection, and notebook or
  JSON metadata checks.
- For `.ipynb` inspection, use `jq` against the notebook JSON directly by
  default.
- Avoid reading JSON or notebook structure through `python - <<'PY'` and similar
  heredoc-style arbitrary Python execution when a standard shell tool or an
  existing script can answer the question.
- Prefer existing approved test and verification entrypoints over ad hoc one-off
  scripts.
- Treat arbitrary script execution as a last resort, and keep any required
  approval request narrowly scoped to the exact command family being used.

## Agent Behavior For Ambiguous Field Data Requests

When the user asks for analysis code but the data semantics are ambiguous, do
not silently guess. Make a best-effort implementation with explicit assumptions
when possible, and surface unresolved assumptions in the plan or notebook.

Common ambiguities to resolve or document include:

- whether timestamps are Unix epoch, device-relative time, computer time, frame
  number, or sample index;
- whether timestamp units are seconds, milliseconds, microseconds, or samples;
- whether `shortid` refers to tag ID, animal ID, or a temporary device name;
- whether coordinates are in meters, centimeters, pixels, or vendor-specific
  units;
- whether an interval should be excluded because a tag was removed, an animal was
  absent, a camera was offline, or calibration was invalid;
- whether a plot is exploratory or paper-facing.

The preferred failure mode is a conservative QC warning, not a confident but
wrong scientific result.

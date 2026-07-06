# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Preprocessing and analysis for **Field_2026_Social** — continuous multimodal recordings from
outdoor rat experiments (field season Jun–Oct 2026) in a **20 × 40 ft paddock**. The repo spans
data *capture* (running 24/7 on a field PC) and offline *analysis*. It is a scientific
data-analysis project, not a toy-script repo: protect raw data and provenance.

`AGENTS.md` is the authoritative workflow contract (implementation plans, change logs, data
manifests, timestamp/QC rules, modality-specific documentation requirements). Read it before any
non-trivial change. Highlights that bite if ignored:
- Never modify raw field data in place; derived data goes under `outputs/`/`data/processed/`.
- Medium/large changes require an `implementation_plan/<date>-topic.md` **before** coding and a
  `change_log/<date>-topic.md` **after** verification.
- Never assume two devices share a clock. If alignment isn't verified, call it "unverified
  alignment," not "synchronized."
- Daily field observations (rat status, weather, equipment changes by date) are logged in
  `FIELD_OBSERVATIONS.md` — consult it for context before interpreting data for a specific date,
  but treat observer interpretations as covariates/hypotheses, not labels or exclusion rules.
- Code/docs/comments in English; user-facing summaries may be Chinese on request.

## Two machines (know which one you're on)
Work happens on two Windows PCs, and several scripts/paths differ between them:
- **Field PC** (RTX 5060 Ti) — runs 24/7 capture: RTSP recording, WISER, and the light CV/audio
  jobs. `conda` via **miniforge** condabin; ffmpeg reused from `E:\Reolink_record\bin`. Capture is
  the priority here and must not be disturbed (see the CV warning below).
- **Analysis PC** (RTX 3060) — offline analysis on **transferred, read-only** copies of the data.
  `conda` via **anaconda3** (`C:\Users\Cornell\anaconda3`); audio uses the env's own ffmpeg and the
  machine-specific `audio_analysis/configs/audio_analysis.analysis_pc.yaml` (repointed to
  `D:\Reolink_record\audio_in`). Do **not** treat "recorder down" alarms as real here — this box
  doesn't record. Recurring audio extraction is meant to run here, not on the field PC.

## Where the original (raw) data lives
Raw inputs are **not in the repo** (too large) — scripts read them from fixed on-disk paths that
differ by machine. Never modify raw in place (`AGENTS.md`); derived data goes to each subsystem's
git-ignored `outputs/`.

| Modality | Field PC (capture = source of truth) | Analysis PC (transferred, read-only) |
|---|---|---|
| **Video** — Reolink, 6 ch, hourly MP4 | `E:\Reolink_record\CHxx\` | `D:\Reolink_record\audio_in\Reolink_record\CHxx\` |
| **Audio** — embedded in those MP4s (mics on **CH01/CH02** only) | `E:\Reolink_record\CHxx\` (same files) | `D:\Reolink_record\audio_in\Reolink_record\CHxx\` |
| **Thermal** — EmpireTech 108/109, thermal+visual | `E:\thermal_record\1xx_*` | `D:\Reolink_record\audio_in\thermal_record\{108,109}_{thermal,visual}\` |
| **WISER** — UWB positions, timestamp **Unix ms UTC** | live DB `D:\Wiser\data\1stcohort_2026.sqlite` (+ `tag_reports.sqlite` fixed baseline); daily backup → `E:\Wiser_backup\` | `D:\Reolink_record\audio_in\Wiser_backup\` — `snapshots\1stcohort_2026_<date>.sqlite` (full DB copies; use the newest), `incremental\1stcohort_2026_<date>.csv.gz` (per-day), `tag_reports_<date>.sqlite` |
| **Weather** — Ambient Weather (AWN) CSV export, `Date` has a −04:00 offset | (exported from AWN cloud) | `D:\Reolink_record\audio_in\weather_data\AWN-*.csv` |

- Reolink hourly files are **closed** when named `..._<start>_to_<end>.mp4`; the open in-progress hour
  is `..._<start>.mp4` — never read that one on the field PC (see the CV warning).
- On the analysis PC the whole transfer lands under `D:\Reolink_record\audio_in\`. The audio pipeline's
  `configs/audio_analysis.analysis_pc.yaml` and the Phase-2 loaders (`audio_analysis/analysis/weather.py`,
  `audio_analysis/analysis/wiser_activity.py`) already point at these paths.
- Clocks differ per device (camera/NVR local wallclock, WISER UTC, AWN local+offset) — treat any
  cross-modality alignment as **unverified** unless a shared event confirms it.

## Five independent subsystems

There is no shared package or build across the repo — each subsystem stands alone with its own
runtime, language, and conventions. Know which one you're in.

### `reolink_record/` — PRIMARY video capture (PowerShell, runs on field PC)
Continuous real-time RTSP recording of 6 NVR channels via `ffmpeg -c copy` (no re-encode, ~0 CPU),
one fragmented MP4 per hour per channel. This is the source of truth for video; it replaces the
deprecated `reolink_export/` GUI automation. Key scripts: `rtsp_record.ps1` (supervisor +
stall watchdog + retention/disk guard), `thermal_record.ps1` (EmpireTech thermal/visual cams),
and `install_*_task_system.ps1` (register SYSTEM scheduled tasks). **Secrets and config live in
`D:\Reolink_record\recorder.config.psd1`, NOT in git.** Recordings land under `E:\Reolink_record`
(CHxx) and `E:\thermal_record` (1xx_*), ~400+ GB/day, ~6-day retention. See `reolink_record/README.md`.

Operational scripts around the recorders (each documents its own safety stance in a top `.SYNOPSIS`):
- **QC:** `recording_health_check.ps1` / `check_recording_continuity.ps1` (daily continuity smoke
  alarm — filename + filesystem metadata by default, ffprobe only with `-DeepCheck`),
  `overexposure_check.ps1` (hourly per-channel exposure/black-frame Slack alerts),
  `disk_space_check.ps1` (Slack alert when the recording drive crosses 50/80/90% full). Recorder
  auto-delete is intentionally **OFF**, so `disk_space_check.ps1` is the safety net that buys
  lead time to back up and free space before capture is at risk. `recording_alive_check.ps1`
  (registered via `install_recording_alive_check_task_system.ps1`) is the stall alarm — it Slack-
  alerts when a channel's newest file stops advancing, catching a wedged recorder between daily QC runs.
- **Data lifecycle:** `copy_day_to_usb.ps1` (hand off ONE day to USB) **then** `delete_day.ps1`
  (remove that day after confirming it was copied). Hard invariant: capture/QC/copy scripts are
  **read-only at the source**; `delete_day.ps1` is the *only* script that removes recordings, takes
  a REQUIRED `-Date`, has no "delete all" mode, and never touches the still-recording file. Preserve
  these guarantees when editing — they are enforced in code, not just documented.

### `reolink_export/` — DEPRECATED backfill only (AutoHotkey v2 + PowerShell)
Coordinate-click automation of the Reolink Client Electron app (Playback → Download). Retained only
for occasionally backfilling days recorded before RTSP capture started. Coordinates in `config.ini`
are calibrated to one specific machine/display. Prefer `reolink_record/` for anything ongoing.

### `wiser_tracking_analysis/` — UWB/WISER tracking analysis (Python)
Imports, QC's, and analyzes WISER UWB tag positions for the pilot study. **Units: inches**
(1.82 in/pixel). Two layers: a small canonical **library** (`src/wiser_io.py` loads CSV/TSV/
SQLite and fuzzy-matches to the canonical schema `shortid, ts_raw, x, y, z`; `time_utils.py`,
`metrics.py`, `plotting.py`) and a large **analysis layer** (`src/wiser_analysis_utils.py`,
~3000 L) adding pilot functionality: a rich read-only loader preserving QC columns
(`anchors_used`/`calculation_error`), speed + validity flags, jitter-floor-aware proximity,
occupancy/ROI, nightly activity, weather merge, provenance.
- **`ANALYSIS_STATUS.md` is the single index of done vs *candidate* vs placeholder** and the path
  to publishable results — read it first. Rows are marked ✅/⚠️/◻️/⛔; each row's status must match
  its `change_log/` entry, and both are updated in the same change. Most spatial/social findings
  are currently **candidate**, not confirmed.
- **The live DB (`D:\Wiser\data\1stcohort_2026.sqlite`) is a running WAL writer.** Every read MUST
  be strictly read-only (`mode=ro`, `PRAGMA query_only=ON`) and never touch the in-progress data —
  enforced in `wiser_io`/`wiser_analysis_utils`, preserve it. Loading both a CSV export and the
  SQLite of one session double-counts rows.
- `scripts/`: `analyze_fixed_position_test.py` (stationary precision, trims the tag-removal tail),
  `analyze_formal_recording.py` (field sessions — still a load/clean stub), `plot_hourly_occupancy.py`,
  `backup_wiser_daily.py`, and the `place_*` config GUIs. `notebooks/wiser_pilot_analysis.ipynb`
  is the QC-first pilot notebook.
- **The pilot analysis is organized into three research directions** (see `ANALYSIS_STATUS.md`), each
  its own driver + change log, all currently **candidate**: **Direction 1 (rain / nightly movement)**
  `analyze_nightly_progression.py` (rain difference-in-differences with bootstrap 95% CI across the 5
  rats + per-night confound covariates); **Direction 2 (route structure)** `analyze_route_structure.py`
  (adopts the surveyed paddock boundary once georeference is `confirmed`, else the provisional ROI
  rectangle); **Direction 3 (daytime sleep/rest site)** `analyze_daytime_sleep_site.py` (per-day primary
  rest site as a low-speed occupancy proxy — daytime rest window 05:00–21:00 — and its within-/across-day
  drift; "sleep" is unvalidated vs ephys, CV shelter cams are the intended cross-check).
  `analyze_nightly_behavior.py` is the older combined driver.
- `configs/`: `fixed_position_ground_truth.csv` (inches; precision floor only), `rat_identities.csv`
  (**`shortid` is a tag ID, not an animal — resolve here**), `wiser_rois.json` (`confirmed=false`
  → refuge/home claims fall back to inferred zones).
- **Rat identification differs by modality** (roster + mapping in `FIELD_OBSERVATIONS.md` and
  `configs/rat_identities.csv`): WISER identifies by **tag** (`shortid`); the **color cameras
  CH01/CH02** identify by **coband color**; the **IR cameras CH03–CH06** are monochrome, so color
  is not recoverable — identify by **coband pattern** (Vertical Line / Open Circle / Filled Circle /
  X / …), never color, on those channels.
- **Georeferencing (the #1 blocker).** The WISER frame is native inches with an *unverified* offset
  origin, so every spatial claim (wall-running, thigmotaxis, route-vs-boundary) risks being a
  coordinate artifact. `src/field_transform.py` (pure-numpy Umeyama similarity + robust outlier
  rejection + affine diagnostic) and `scripts/georeference_wiser.py` fit a WISER-inch →
  physical-field-cm transform from a pole-dwell survey (`configs/wiser_georef_survey.csv`), tying
  WISER to the **CV field frame** (`preprocessing/computer_vision/field_coords.py`; cm, origin pole
  A0). Until a survey passes QC, `configs/wiser_to_field_transform.json` stays `confirmed:false`,
  the helpers (`load_field_transform`/`apply_field_transform`/`verified_boundary_in_wiser`) are
  **no-ops**, and analyses run unchanged in inches. QC gates `confirmed`: scale ≈ 2.54 cm/in,
  inlier residuals near the ~7 in jitter floor, negligible affine shear.
- Raw data in `D:\Wiser\data\`; outputs (CSVs, plots, QC overlays) to `outputs/` (git-ignored).

### `preprocessing/computer_vision/` — Field-PC CV pipeline "Stage 0" (Python, GPU)
Turns Reolink footage into per-animal **(x, y) + ID in a shared field frame (cm)** plus
sleep/activity. No pose/keypoints. Tracking is per-camera, then transformed into one field frame.

> ⚠️ **This is the LIVE field PC.** It is concurrently running **WISER UWB tracking** and
> **RTSP streaming/recording for ~10 channels** — that capture is the priority and must not be
> disturbed (dropped frames can't be re-recorded). So CV work here must stay **light**: throttle
> video decode (few ffmpeg threads, sequential, sample only the frames you need — don't full-decode
> hours), be gentle on the recording drive **E:** (capture writes there; minimize concurrent reads),
> keep GPU batches small, and prefer running heavy jobs (full training, dense occupancy scans) when
> capture load allows. Recorders use `ffmpeg -c copy` (~0 CPU/GPU), so the headroom is real but finite.
>
> 🚫 **NEVER read the file currently being recorded.** CV must process only **closed** hourly
> recordings — the recorder finalizes a file by renaming it to `..._<start>_to_<end>.mp4`, while the
> in-progress hour is `..._<start>.mp4` (no `_to_`). Reading the open file can contend with / corrupt
> the active write and risks the capture. **Filter every scan to `_to_` files and drop the newest
> open file.** (Recording of that hour is not lost — process it after it closes.)
- `field_coords.py` owns the common frame + pixel↔field-cm transforms (homography / poly / PnP) and
  the field layout. **Field axes: x = 40 ft length (0–1219.2 cm), y = 20 ft width (0–609.6 cm),
  origin at corner pole A0.** Output cm aligns with the WISER inch frame for cross-validation.
- Calibration is anchored on the existing 15-pole grid + wall + shelters (no new markers):
  `place_cameras.py` (drag GUI) → `make_layout_map.py` (pole-index map) →
  `extract_clip.py --frame` → `calibration.py --pick` → per-camera `configs/CHxx_calib.json`.
- `intrinsics.py` is a separate **lens-distortion** step for the wide fisheyes (CH03/CH04): a single
  homography/poly fit to ground points overfits there (LOO error ~55–97 cm). Record a checkerboard
  clip → `intrinsics.py --channel CH03 --clip ...` writes `configs/CHxx_intrinsics.json` {model,K,D,...};
  then `calibration.py --refit` undistorts the clicked points before fitting the homography.
- **Detector loop (Stage 1 feasibility, YOLO11/Ultralytics).** The tracker needs a `rat` detector,
  built by a harvest→label→train→validate cycle: `scan_for_rats.py` (default HARVEST mode —
  sparse seek-sampling + frame-diff dedup → diverse frames in `dataset/rat/images`; a separate
  `--occupancy-hz` mode does a dense streamed decode → occupancy CSV, saves no frames) →
  `label_frames.py` (OpenCV box labeler → YOLO txt; empty txt = valid negative) →
  `train_detector.py` (80/20 split held out **by session/video**, fine-tunes `yolo11s.pt` → `runs/detect/<name>/weights/best.pt`, prints val mAP) → feed the weights to `animal_tracking.py --weights ... --classes 0`.
- `animal_tracking.py` (detections → per-camera track CSV) → `../data_merging/merge_cameras.py`
  (merge into common frame) → `sleep_activity.py` (rest/active bouts).
- Canonical track CSV schema: `camera, frame, time_s, track_id, conf, x_img, y_img, x_field_cm, y_field_cm`.
- Channel→model mapping drives the transform: CH01/02 Duo3 180° → poly; CH03/04 RLC-1212A wide
  fisheye → undistort (`intrinsics.py`) + homography; CH05/06 RLC-520A shelter (~nadir) → homography.
- **CH05/CH06 view the rats THROUGH an IR-transmitting window**, so rain/fog/condensation/drips/glare
  land on the glass. `shelter_sleep.py` is zone-aware (`inside_shelter`/`doorway`/`outside_surrounding`,
  drawn via `place_zones.py` → `configs/CHxx_zones.json`) and tags each bin with a per-zone
  `view_quality` (clear/degraded/unusable, from `view_quality.py`). Hard rule: **degraded inside-glass
  never becomes `occupied_high_motion`, unusable → `indeterminate`; weather/glass artifacts must never
  count as rat activity.** Inside motion uses the glass-noise-resistant `robust_inside_motion` (rejects
  rain speckle/glare/AE). States: `empty`/`occupied_low_motion`/`occupied_high_motion`/`indeterminate`.
  `validate_shelter.py` is the ground-truth check: it prompts you for the true inside count + still/
  moving on random closed-footage samples (detector answer hidden), then reports accuracy **stratified
  by `view_quality`** and asserts the safety check that degraded/unusable bins never score `occupied_high_motion`.

### `audio_analysis/` — environmental-audio feature pipeline (Python, field PC)
Lightweight, **resumable** extraction of relative camera-mic level + band-limited soundscape indices
from the Reolink hourly-MP4 audio (CH01/CH02 only — the mics enabled ~2026-06-29 12:00; CH03–06 are
silent). Streams audio from the shared ffmpeg in chunks (no temp WAVs, never loads a whole hour) and
writes one compact timestamped CSV per channel/day to git-ignored `outputs/`; the heavy Phase-2
analysis (WISER/weather merge, spectrograms, stats, figures) happens later on the main analysis
computer from those CSVs. Layout mirrors `wiser_tracking_analysis/`: `src/` (`audio_io.py` ffmpeg
decode + discovery, `time_utils.py`, `features.py` level/spectral/scikit-maad indices, `qc.py`,
`plotting.py`), `scripts/` (`extract_audio_features.py` CLI, `summarize_soundscape.py`,
`selftest_features.py`), `configs/audio_analysis.yaml`. Two hard caveats that shape every number:
- Level columns are **relative camera-mic dBFS, NOT calibrated SPL** (suffix `_dbfs_relative`,
  full-scale ref 1.0) — use as relative covariates over time, never absolute noise levels.
- Audio is **16 kHz mono → ~8 kHz ceiling**; the ecoacoustic indices are band-limited camera-specific
  variants (`bi_2_8k_camera`, `ndsi_1_2k_vs_2_8k_camera`) comparable only *within this dataset*. Rat
  ultrasonic vocalizations (>20 kHz) are physically out of scope for a camera mic.

Uses its own `audio` conda env (see below). Filter valid rows with `valid_audio` (True only for
`qc_flag == ok`); silent/`pre_mic_enable` windows skip the expensive spectral stage by design (NaN
spectral/index values). See `audio_analysis/README.md`.

### Other `preprocessing/` dirs (stubs, no code yet)
`data_merging/merge_cameras.py` is the CV merge step (above). `lfp_recording/` and
`security_camera/` currently hold only `copy_and_storage_protocol.md` — planned modalities for the
multimodal rig (neural LFP, security video), not broken/missing code. Don't scaffold them unprompted.

## Coordinate systems (easy to get wrong)
- WISER tracking: **inches**. CV pipeline: **centimetres**. Field is 609.6 × 1219.2 cm. Convert with
  1 in = 2.54 cm (`IN_TO_CM` in `field_coords.py`) when cross-validating WISER against CV.
- The CV cm frame (origin pole A0) is the *physical* reference. WISER inches are in an unverified
  offset frame — a raw unit conversion does **not** align the two. The proper bridge is the fitted
  georeference transform (`wiser_to_field_transform.json`), which only exists once a pole survey
  passes QC; until then WISER positions cannot be placed in the physical frame.

## Commands

CV pipeline (conda env `cv`, with cu128 PyTorch — the RTX 5060 Ti Blackwell/sm_120 GPU requires it):
```bat
cd preprocessing\computer_vision
conda env create -f environment.yml && conda activate cv
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python verify_gpu.py
python animal_tracking.py --channel CH05 --synthetic --out tracks\CH05_synth.csv   :: Stage-0 self-test, no detector
```
ffmpeg/ffprobe are reused from `E:\Reolink_record\bin` (no install).

WISER analysis (`pip install pandas numpy matplotlib`):
```bash
cd wiser_tracking_analysis
python scripts/selftest_georeference.py                  # offline: transform-fit logic -> PASS (no DB/field data)
python scripts/selftest_daytime_sleep_site.py            # offline: Direction-3 rest-site core -> PASS
python scripts/analyze_fixed_position_test.py            # default run
python scripts/analyze_fixed_position_test.py --data D:\Wiser\data --trim-minutes 5 --no-plots
python scripts/analyze_daytime_sleep_site.py             # Direction 3: daytime rest site + drift
python scripts/georeference_wiser.py                     # fit WISER->field transform (needs a filled survey)
```

Audio analysis (own `audio` conda env). **This depends on which machine you're on** — see the
field-PC vs analysis-PC note below:
```powershell
# Field PC: conda not on PATH -> miniforge condabin; ffmpeg from E:\Reolink_record\bin (config default)
& "C:\Users\Cornell\miniforge3\condabin\conda.bat" env create -f audio_analysis\environment.yml
cd audio_analysis
python scripts\selftest_features.py                       :: offline: synthetic tone/noise/silence/clip -> PASS
python scripts\extract_audio_features.py --channel CH01 --date 2026-06-29 --hours 12-13   :: canonical smoke test
# Analysis PC: env is under anaconda3, and extraction needs the machine-specific config
#   (transferred D:\Reolink_record\audio_in paths + the env's bundled ffmpeg):
python scripts\extract_audio_features.py --config configs\audio_analysis.analysis_pc.yaml --channel CH01 --date 2026-06-29
```
Extraction is resumable (skips files already in the CSV unless `--overwrite`).

Recorder ops (PowerShell, field PC):
```powershell
Get-Process ffmpeg | Measure-Object                     # are all 6 streams up?
Get-Content D:\Reolink_record\logs\recorder.log -Tail 20
Start-ScheduledTask -TaskName 'Reolink RTSP Recorder'
.\recording_health_check.ps1 -SelfTest                  # offline: exercise parser + gap logic, no disk
.\recording_health_check.ps1 -DryRun                    # last-24h continuity report to console only
```

There is no test suite, linter, or CI configured. Verification is per-subsystem: CV via the
`--synthetic`/`--frame` self-tests above, audio via `selftest_features.py` (offline synthetic
signals), WISER via the offline `selftest_georeference.py` / `selftest_daytime_sleep_site.py`
(synthetic, exit-coded PASS/FAIL) plus the analysis scripts on real recordings, recorders via
`recording_health_check.ps1 -SelfTest` (offline logic check), the continuity report, and the
ffmpeg process count.

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
- Code/docs/comments in English; user-facing summaries may be Chinese on request.

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
Imports, QC's, and computes position-error metrics for WISER UWB tag data. **Units: inches**
(1.82 in/pixel). Layout:
- `src/wiser_io.py` — loads CSV/TSV/TXT/SQLite, fuzzy-matches columns to the canonical schema
  `shortid, ts_raw, x, y, z`. Note: loading both a CSV export and the SQLite of the same session
  double-counts rows.
- `src/time_utils.py` (timestamp detection/conversion), `src/metrics.py` (jitter/RMSE/bias),
  `src/plotting.py` (diagnostic plots).
- `scripts/analyze_fixed_position_test.py` — stationary-tag precision test (trims the tag-removal
  tail). `scripts/analyze_formal_recording.py` — field sessions (no trim, no ground truth).
- `configs/fixed_position_ground_truth.csv` — per-tag ground truth (inches).
- Raw data expected in `D:\Wiser\data\`; outputs to `outputs/` (git-ignored).
- `shortid` is a **tag** ID, not an animal name — resolve via an explicit mapping table.

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
python scripts/analyze_fixed_position_test.py            # default run
python scripts/analyze_fixed_position_test.py --data D:\Wiser\data --trim-minutes 5 --no-plots
```

Audio analysis (own `audio` conda env; conda not on PATH — use the miniforge condabin):
```powershell
& "C:\Users\Cornell\miniforge3\condabin\conda.bat" env create -f audio_analysis\environment.yml
cd audio_analysis
python scripts\selftest_features.py                       :: offline: synthetic tone/noise/silence/clip -> PASS
python scripts\extract_audio_features.py --channel CH01 --date 2026-06-29 --hours 12-13   :: canonical smoke test
```
Reuses the same `E:\Reolink_record\bin\ffmpeg.exe`; extraction is resumable (skips files already in the CSV unless `--overwrite`).

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
signals), WISER via the analysis scripts on real recordings, recorders via
`recording_health_check.ps1 -SelfTest` (offline logic check), the continuity report, and the
ffmpeg process count.

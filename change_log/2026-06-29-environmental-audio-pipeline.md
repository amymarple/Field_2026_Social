# Environmental-Audio Feature Pipeline (Phase 1)

## Date

2026-06-29 (verified 2026-06-29). Change is currently uncommitted.

## Plan

Implemented from
[`implementation_plan/2026-06-29-environmental-audio-pipeline.md`](../implementation_plan/2026-06-29-environmental-audio-pipeline.md).

## What Changed

New top-level `audio_analysis/` subsystem (mirrors `wiser_tracking_analysis/`) that extracts
**relative camera-mic level + band-limited soundscape indices** from the CH01/CH02 Reolink MP4
audio into compact, timestamped CSVs for transfer to the main analysis computer.

- `src/audio_io.py` — discover Reolink hourly MP4s; stream audio via
  `ffmpeg -vn -ac 1 -ar 16000 -f f32le -` one window at a time (never loads a whole hour, no
  temp WAVs); `DecodeError` guard.
- `src/time_utils.py` — parse `CHxx_YYYY-MM-DD_HH-MM-SS[_to_HH-MM-SS].mp4` → absolute local
  timestamps (window ts = file start + intra-file offset).
- `src/features.py` — per 60 s window: level (`leq/l10/l50/l90/peak_dbfs_relative` from 1 s
  sub-frames), spectral (band energies 0–1 k / 1–2 k / 2–8 k, centroid, rolloff via Welch),
  and scikit-maad indices (`aci`, `bi_2_8k_camera`, `ndsi_1_2k_vs_2_8k_camera`, `adi`). Any
  index that cannot compute → NaN + note, never raises. Two-stage: the expensive spectral+index
  work is skipped on silent / pre-mic windows.
- `src/qc.py` — flags `ok / silent / pre_mic_enable / clipped / decode_error / timeline_gap /
  partial_window / too_short`; `valid_audio` True only for `ok`.
- `src/plotting.py` — optional, CSV-driven (level-over-time, index series, single-window
  spectrogram). Nothing renders by default.
- `scripts/extract_audio_features.py` — resumable CLI → `outputs/audio_features_<CH>_<date>.csv`
  + `…metadata.json` sidecar; skips already-processed files unless `--overwrite`.
- `scripts/summarize_soundscape.py` — minimal daily summary from existing CSVs (Phase 1).
- `scripts/selftest_features.py` — offline synthetic self-test.
- `configs/audio_analysis.yaml`, `environment.yml`, `requirements.txt`, `README.md`.
- `.gitignore` — ignore `audio_analysis/outputs/` (derived data).
- New conda env `audio` (miniforge): numpy/scipy/pandas/matplotlib/librosa/**scikit-maad**/
  pyyaml + conda-forge `pysoundfile`/`libsndfile`. The package registers the env's `Library\bin`
  on import so the env python works when invoked by full path (not just `conda activate`).

## Why

CH01/CH02 mics were enabled ~2026-06-29 12:00 for ambient/environmental context. We want
relative environmental-audio covariates (level + birdsong/biophony activity) aligned to the
recording timeline, extracted cheaply on the field PC and analysed in depth elsewhere.

## Interpretation guardrails (intentional)

Outputs are **relative camera-mic dBFS, not calibrated SPL** (mics uncalibrated; AAC/gain/AGC
affect amplitude). 16 kHz → ~8 kHz ceiling, so BI/NDSI are camera-specific band-limited variants
named accordingly and comparable only within this dataset. Rat ultrasound is out of scope.

## Verification

Env imports OK (`maad 1.5.2`, `librosa 0.11.0`, `soundfile 0.14.0`). All run with the env's
python by full path.

- `selftest_features.py` → **PASS** (silence→`silent`; 3 kHz tone→2–8 k band dominance + centroid
  ≈ 3 kHz; noise→indices return floats, no crash; full-scale→`clipped`).
- Smoke `--channel CH01 --date 2026-06-29 --hours 12-13`: 60 windows, 41 `ok`, **Leq median
  −48.4 dBFS relative** (matches the live −50 measured when the mic was enabled), indices
  populated; pre-enable minutes flagged `silent`. Silent-window skip cut runtime 41.7 s → 9 s.
- CH02 same hour: 60 windows, 34 `ok`.
- Negative control CH01 pre-noon: 0 `ok` — all `pre_mic_enable`.
- Resume: re-running an already-processed hour reports "0 to process"; a later run appends new
  files to the same channel/date CSV.

Raw MP4s untouched (read-only); all output under git-ignored `audio_analysis/outputs/`.

## Known limitations / follow-ups

- **TODO / DECISION (2026-06-30): run the daily extraction on the separate ANALYSIS computer,
  NOT the field PC.** Safer — avoids adding `E:` read-I/O alongside live capture and sidesteps
  the open-file hazard. So **do not install a field-PC scheduled task** for this. The field PC's
  role is only to record; copy the closed hourly MP4s (or already-copied day folders) to the
  analysis machine and run extraction there.
- **TODO: add an active-file skip.** `find_recording_files` currently includes the in-progress
  `..._<start>.mp4` (no `_to_`). Per the CLAUDE.md open-file rule, filter to closed `_to_` files
  and drop the newest open file so extraction is safe regardless of when/where it runs.
- Extracting the **pre-mic-enable era still decodes** each hour (≈30 s/file) before flagging it
  `pre_mic_enable`; target the mic-on era to avoid the cost. A file-level skip (no decode when a
  file ends before `mic_on_after`) is a possible future optimization.
- `mic_on_after` is set to 2026-06-29 12:00; the physical enable was ~12:18, so 12:00–12:18 is
  caught by the silence floor rather than `pre_mic_enable` (both exclude it correctly).
- Low spectral centroid on `ok` windows reflects real camera-mic low-frequency self-noise/wind;
  the 2–8 kHz band + indices isolate the biophony content.
- Phase 2 (WISER/weather merge, diurnal/all-day figures, event detection) is main-analysis-
  computer work, fed by these CSVs.

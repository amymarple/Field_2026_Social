# Environmental-Audio Feature Pipeline (Phase 1)

This directory records the plan before source changes; see the session plan for full detail.

## Context / Why

CH01 + CH02 (Duo 3) microphones were enabled ~2026-06-29 12:00. Audio is embedded in the hourly
Reolink MP4s as 16 kHz mono AAC (audible-band only — NOT rat ultrasound). We want relative
environmental-audio covariates: (1) relative camera-mic **level** over time, and (2) band-limited
**soundscape / ecoacoustic indices** (birdsong/biophony activity).

The field PC is **not** the analysis computer. Phase 1 is strictly **lightweight, resumable
feature extraction → compact, tidy, timestamped CSVs** that get copied elsewhere for the heavy
work (WISER merge, weather merge, stats, figures).

## Interpretation rule (load-bearing)

Outputs are **relative camera-mic dBFS covariates only** — Reolink mics are not calibrated SPL
meters; AAC / camera gain / possible AGC affect amplitude. Never labelled as SPL. Level columns
carry the `_dbfs_relative` suffix; the camera-specific band-limited indices are named
`bi_2_8k_camera` / `ndsi_1_2k_vs_2_8k_camera` and are only comparable within this dataset.

## What will be added

New top-level `audio_analysis/` mirroring `wiser_tracking_analysis/`:
- `src/`: `audio_io.py` (ffmpeg chunked decode + file discovery), `time_utils.py` (Reolink
  filename → absolute local timestamps), `features.py` (level + spectral + scikit-maad indices),
  `qc.py` (flags), `plotting.py` (optional simple plots).
- `scripts/`: `extract_audio_features.py` (CLI batch → CSV + metadata JSON sidecar, resumable,
  `--lightweight` default), `summarize_soundscape.py` (minimal, from existing CSVs; not required
  for Phase 1).
- `configs/audio_analysis.yaml`, `environment.yml`, `requirements.txt`, `README.md`,
  git-ignored `outputs/`.

Decode: `ffmpeg -i <mp4> -vn -ac 1 -ar 16000 -f f32le -` streamed in chunks (one 60 s window in
memory; no temp WAVs). Env: new conda `audio` via existing miniforge; ffmpeg reused from
`E:\Reolink_record\bin`.

## Verification

Offline self-test (synthetic tone+noise) + three smoke runs (CH01 12–13h non-silent Leq ≈ −50;
CH02 louder; pre-noon CH01 flagged `silent`/`pre_mic_enable`). Raw MP4s read-only; outputs only
under git-ignored `audio_analysis/outputs/`.

## Out of scope (Phase 1)

Rat ultrasound; source localization; species ID; WISER/weather merge; diurnal/publication
figures; all-day spectrograms — all deferred to the main analysis computer / Phase 2.

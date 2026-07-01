# audio_analysis — environmental-audio feature pipeline

Lightweight, resumable extraction of **relative camera-mic level + band-limited soundscape
indices** from the Reolink hourly MP4 audio (CH01 / CH02). Produces compact, timestamped CSVs
on the **field PC**; the heavy analysis (WISER merge, weather merge, stats, figures) happens
later on the **main analysis computer** from those CSVs.

## ⚠️ Read this first — what the numbers are (and are not)

- **Relative camera-mic dBFS, NOT calibrated SPL.** Reolink camera mics are not sound-level
  meters; AAC compression, camera gain, and possible AGC affect amplitude. All level columns
  carry the `_dbfs_relative` suffix (full-scale reference = 1.0). Use them as **relative
  covariates over time**, never as absolute environmental noise levels.
- **~8 kHz usable ceiling.** Audio is 16 kHz mono, so analysis stops at 8 kHz. The ecoacoustic
  indices are **band-limited camera-specific variants** — `bi_2_8k_camera`,
  `ndsi_1_2k_vs_2_8k_camera` — and are only comparable *within this dataset*, not against
  full-band literature.
- **Rat ultrasonic vocalizations are out of scope** (they live above 20 kHz; a camera mic
  cannot capture them — that needs a dedicated ≥250 kHz ultrasonic mic).
- **Clocks.** Timestamps come from the recorder filename wallclock (local time). Within a
  channel, audio↔video share the camera/NVR clock; cross-device alignment to WISER / weather is
  **timestamp-aligned only and unverified** until separately validated.
- **Mics enabled ~2026-06-29 12:00** (CH01, CH02). Earlier audio is silent and is auto-flagged
  `pre_mic_enable` / `silent` — not biologically meaningful.

## Environment

Create the `audio` conda env (uses the existing miniforge; conda is not on PATH):

```powershell
& "C:\Users\Cornell\miniforge3\condabin\conda.bat" env create -f environment.yml
conda activate audio
```

Decoding uses the existing **ffmpeg** at `E:\Reolink_record\bin\ffmpeg.exe` (no install).

Run scripts either with the env activated, or by full path (the package adds the env's DLL
directory so the direct call works too):

```powershell
conda run -n audio python scripts\extract_audio_features.py ...
# or
C:\Users\Cornell\miniforge3\envs\audio\python.exe scripts\extract_audio_features.py ...
```

## Usage

```powershell
# one hour, one channel (the canonical smoke test)
python scripts\extract_audio_features.py --channel CH01 --date 2026-06-29 --hours 12-13

# a whole day, both mic channels (default channels come from the config)
python scripts\extract_audio_features.py --date 2026-06-29

# an explicit time range
python scripts\extract_audio_features.py --channel CH02 --start 2026-06-29T18:00:00 --end 2026-06-29T20:00:00
```

Key flags: `--channel` (repeatable), `--date`, `--hours HH-HH`, `--start/--end`, `--input-root`,
`--output-dir`, `--config`, `--overwrite` (reprocess files already in the CSV), `--dry-run`,
`--max-files`. Extraction is **resumable**: a source file already present in the target CSV is
skipped unless `--overwrite`.

Optional summary / plots from existing CSVs (not required):

```powershell
python scripts\summarize_soundscape.py --channel CH01 --date 2026-06-29 --plots
```

## Output

`outputs/audio_features_<CHxx>_<date>.csv` — one row per analysis window (default 60 s), plus a
`…metadata.json` sidecar (params, frequency bands, library versions, ffmpeg path, mic-on
boundary, silence threshold, git commit, exact command). `outputs/` is **git-ignored** (derived
data; copy to the analysis computer).

Columns: timestamps + provenance (`window_start/end_timestamp`, `channel`, `source_file`,
`file_start_timestamp`, offsets, `window_s`, `sample_rate_hz`, `n_samples`, `audio_duration_s`),
level (`leq_dbfs_relative`, `l10/l50/l90_dbfs_relative`, `peak_dbfs_relative`), spectral
(`band_0_1k_db`, `band_1_2k_db`, `band_2_8k_db`, `centroid_hz`, `rolloff_hz`), indices (`aci`,
`bi_2_8k_camera`, `ndsi_1_2k_vs_2_8k_camera`, `adi`), and QC (`n_silent_subframes`, `clipped`,
`valid_audio`, `qc_flag`, `index_notes`).

`qc_flag` ∈ {`ok`, `silent`, `pre_mic_enable`, `clipped`, `decode_error`, `timeline_gap`,
`partial_window`, `too_short`}. Use `valid_audio` (True only for `ok`) to filter. To keep the
field PC light, the expensive spectral + index stage is **skipped on silent / pre-mic windows**
(those rows have NaN spectral/index values by design).

## Layout & design

```
src/   audio_io.py (ffmpeg chunked decode + file discovery), time_utils.py (filename->timestamp),
       features.py (level + spectral + scikit-maad indices), qc.py (flags), plotting.py (optional)
scripts/  extract_audio_features.py (CLI), summarize_soundscape.py (minimal), selftest_features.py
configs/  audio_analysis.yaml (windows, bands, thresholds, mic_on_after, paths)
```

Audio is streamed from ffmpeg in chunks — a whole hour is never loaded, no temp WAVs are written.

## Verify

```powershell
python scripts\selftest_features.py     # offline: synthetic tone/noise/silence/clipping -> PASS
```

## Phase 2 (main analysis computer, not here)

Diurnal/seasonal soundscape summaries, all-day spectrograms, merging the level/index covariates
with WISER occupancy + AWN weather, event detection, and publication figures — all fed by these
CSVs.

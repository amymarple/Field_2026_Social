# Audio feature extraction — first run on the ANALYSIS PC

## Date

2026-07-01 (first analysis-PC run); overlap-dedup fix + clean re-extraction 2026-07-02. Currently
uncommitted.

## Plan

Implements the 2026-06-30 decision recorded in
[`change_log/2026-06-29-environmental-audio-pipeline.md`](2026-06-29-environmental-audio-pipeline.md)
(run recurring audio extraction on the analysis computer, not the field PC). Session plan:
`C:\Users\Cornell\.claude\plans\check-audio-process-and-keen-simon.md`.

## What changed

Stood up the Phase-1 audio pipeline on **this analysis PC**, ran it against the transferred
Reolink audio, and added an overlap-dedup filter to discovery after the first run surfaced a
duplicate-coverage hazard (copied-back NVR backfill overlapping the RTSP files — see Observations).

- `audio_analysis/src/audio_io.py` — **new** `_dedup_overlapping()`, called by
  `find_recording_files`: drops any file whose time span is fully covered by earlier-kept files
  (nested duplicate), keeping the earliest/longest-reaching file. A contiguous RTSP chain never
  nests, so it is untouched; each dropped file is surfaced via `warnings.warn`. Plan:
  [`implementation_plan/2026-07-01-audio-overlap-dedup.md`](../implementation_plan/2026-07-01-audio-overlap-dedup.md).
- `audio_analysis/scripts/selftest_features.py` — added offline dedup checks (the 6/29 14:00
  nested-backfill scenario drops exactly the two covered segments; a contiguous chain is unchanged).
- `audio_analysis/src/plotting.py` — `plot_level_over_time` now annotates the level plot with a
  glossary caption and clearer legend labels (Leq = energy-averaged loudness; L10 = level exceeded
  10% of the time = loud peaks; L90 = exceeded 90% = quiet background). Label-only; no data change.
- `audio_analysis/src/plotting.py` + `scripts/summarize_soundscape.py` — new
  `plot_bird_vs_ambient()` (wired into `--plots`): diurnal **2–8 kHz "bird-like" band vs 0–1 kHz
  "ambient" band** (5-min median), with a night-floor reference and shaded "biophony likely" spans
  where the 2–8 kHz band exceeds its day floor by `BIOPHONY_MARGIN_DB` (8 dB). Answers "birds vs
  ambient", which the Leq level plot cannot. **Heuristic, not a validated bird detector** (camera
  mic, ≤8 kHz, relative dBFS); rain/wind attribution of the low band stays unverified until the
  Phase-2 AWN-weather merge. On 6/30 it shows a sharp ~04:00 dawn-chorus onset and a midday loudness
  peak that is ambient-dominated (low band), not biophony.
- `audio_analysis/environment.yml` — added `- ffmpeg` (conda-forge) so the `audio` env is
  self-sufficient here (the field PC still uses its own `E:\Reolink_record\bin\ffmpeg.exe`;
  additive, no behavior change there).
- `audio_analysis/configs/audio_analysis.analysis_pc.yaml` — **new** machine-specific config; a
  copy of `audio_analysis.yaml` with only two keys repointed to analysis-PC paths:
  - `input_root: 'D:\Reolink_record\audio_in\Reolink_record'` (transferred hourly MP4s)
  - `ffmpeg: 'C:\Users\Cornell\anaconda3\envs\audio\Library\bin\ffmpeg.exe'` (env ffmpeg)
  The field config was left untouched. A dedicated config was required because
  `extract_audio_features.py` has an `--input-root` flag but no ffmpeg override — ffmpeg comes
  only from `cfg["ffmpeg"]`.
- Created the `audio` conda env from `environment.yml` via `anaconda3\condabin\conda.bat`
  (miniforge is not installed here; only `anaconda3` with envs `dlc`, `phy2`). Env python:
  `C:\Users\Cornell\anaconda3\envs\audio\python.exe`.

## Why

The pipeline had only ever run on the field PC. Per the standing decision, recurring extraction
belongs on the analysis PC (avoids extra `E:` read-I/O beside live capture and the open-file
hazard). This is the first real analysis-PC run and confirms the environment + config work here.

## Environment / provenance

From the output metadata sidecars: `git_commit 22fa8f3576e76adc846c6310ade7c48cf1827b96`;
`numpy 2.4.6, scipy 1.17.1, librosa 0.11.0, maad 1.5.2, soundfile 0.14.0`. (numpy/scipy are newer
than the field-PC env; results reproduced the field baseline regardless — see below.)

## Source data

Transferred, read-only: `D:\Reolink_record\audio_in\Reolink_record\CH01\` and `CH02\`, 2026-06-29.
All closed `_to_` files (no in-progress file, so the "active-file skip" TODO is not exercised).
**Two provenance classes are mixed in the 14:00–16:18 span:** the RTSP recorder's hour-aligned
files (the mic-audio source of truth) **plus** manually copied-back NVR playback exports for the
IP-change gap (odd-length, overlapping segments such as `14-20-52_to_14-41-18`). The NVR exports
carry an AAC 16 kHz audio track but it is **digital silence** (see below).

## Verification

Run with the env python by full path, `--config configs\audio_analysis.analysis_pc.yaml`:

- `selftest_features.py` → **PASS** (silence→`silent`; 3 kHz tone→2–8 k dominance + centroid
  ≈ 3 kHz; noise→indices finite/NaN, no crash; full-scale→`clipped`; **dedup**→two nested backfill
  segments dropped, RTSP chain + gap filler kept, contiguous chain untouched). The maad
  divide-by-zero RuntimeWarnings on the silence/noise cases are expected (NaN indices).
- Smoke `--channel CH01 --date 2026-06-29 --hours 12-13`: **60 windows, 41 `ok`** — exact match to
  the documented field-PC smoke test.
- Final full-day run is **after** the dedup fix (`--overwrite`). Discovery skipped the two nested
  NVR-backfill segments per channel (`14-20-52_to_14-41-18`, `14-41-18_to_14-59-59` on CH01;
  the 14-20-42/14-41-10 pair on CH02), each with a `warnings.warn`.
- Full day CH01: **1363 windows** — `pre_mic_enable` 711, `ok` **517**, `silent` 105,
  `partial_window` 17, `too_short` 11, `clipped` 2 (`timeline_gap` now 0 — the spurious gap the
  overlap created is gone). Leq median **−53.4 dBFS relative** (p10 −58.4, p90 −47.8). Hour-12
  median Leq **−48.4** = the documented baseline. Indices: aci 326.4, bi_2_8k_camera 548.3,
  ndsi 0.517, adi 1.403. Hour 14 back to **61 windows** (was 101 pre-dedup).
- Full day CH02: **1364 windows** — `ok` 511, `silent` 112; Leq median −53.1; indices unchanged.
- Before→after dedup, the `ok`-based Leq/index medians are identical to 3 dp — the science was
  already correct; dedup only removed the ~40 duplicate silent windows/channel (and one spurious
  `timeline_gap`).
- Outputs (git-ignored `audio_analysis/outputs/`): `audio_features_CH0{1,2}_2026-06-29.csv`
  (+ `.metadata.json`), and `plots/CH0{1,2}_2026-06-29_{level,indices}.png` via
  `summarize_soundscape.py --plots`.

Raw MP4s untouched (read-only).

## Observations / known limitations

- **The 6/29 ~15:00–17:45 audio gap is REAL and not recoverable from the NVR backfill.** During the
  NVR IP change the mics were reset/off (see
  [`2026-06-29-nvr-ip-change-recording-gap.md`](2026-06-29-nvr-ip-change-recording-gap.md):
  "CH01/02 mics reset & re-enabled"), and the RTSP recorder was down. Copying the NVR playback
  exports back restores **video** for that window but **not audio**: those files' audio track is
  digital silence — median Leq **−120 dBFS** on every backfill segment, both channels, vs **−51.8
  dBFS** on the real RTSP `14-00-00_to_15-00-04` file. So 15:00 = 0 `ok` (58 `silent`), 16:00 = 0
  `ok` (17 `silent`); the flat stretch in the level plot is genuine absence of camera-mic audio,
  not a pipeline skip.
- **Overlapping backfill duplicate coverage — FIXED by the dedup filter.** The copied NVR segments
  overlapped the real 14:00–15:00 RTSP hourly file, so the first run's hour 14 had ~101 windows
  (≈60 real `ok` + ~39 duplicate `silent`). `_dedup_overlapping` now drops the fully-nested
  segments, restoring hour 14 to ~61 and removing ~40 duplicate silent windows/channel. This was a
  new hazard class beyond the "active-file skip" TODO: *overlapping same-timespan segments from a
  second source*. Note the fix only removes **fully-nested** duplicates: the silent NVR fillers that
  extend into the true 15:00–16:18 gap are **kept** (they extend coverage), remain flagged `silent`,
  and are excluded downstream by `valid_audio` — an honest "audio absent" marker, not a duplicate.
  Even before the fix the `ok`-based science was unaffected (all `ok` rows come from the RTSP files).
- 2026-06-29 (baseline day) and **2026-06-30** are processed. 6/30 is the first *complete* day
  (mics on all 24 h, no gap): 27 files/channel, 98% `ok` windows, no dedup skips (the 09:26–10:00
  segments are a genuine contiguous RTSP rollover chain, not backfill), and a clear diurnal curve —
  quiet at night (~−57 dBFS), rising through dawn, loudest midday–late-afternoon (hour 17 ≈ −41),
  dropping after ~18:00. (2026-06-30 was a wet/rain day per the nightly-behavior change logs — a
  weather covariate for Phase 2.) 2026-07-01 audio is transferred and can be extracted the same way.
- Recurring scheduling on the analysis PC (a Windows task pointing at this config) is **not** set
  up here — this was a manual verification run. The outstanding extraction TODOs (active-file
  skip; file-level pre-mic decode skip) still stand for the recurring case.
- Phase 2 (downstream) is now designed:
  [`implementation_plan/2026-07-01-audio-phase2-staged-analysis.md`](../implementation_plan/2026-07-01-audio-phase2-staged-analysis.md)
  (design only, not built).

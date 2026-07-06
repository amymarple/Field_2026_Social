# Audio Phase 2 — diurnal soundscape + weather panel (first piece)

## Date

2026-07-02. Currently uncommitted.

## Plan

First implemented slice of
[`implementation_plan/2026-07-01-audio-phase2-staged-analysis.md`](../implementation_plan/2026-07-01-audio-phase2-staged-analysis.md)
— starts the `audio_analysis/analysis/` (Phase-2) package and the audio↔weather merge. Grew out of
the "bird vs ambient" interpretation question on the 2026-06-30 level plot.

## What changed

- `audio_analysis/analysis/__init__.py`, `audio_analysis/analysis/weather.py` — **new** Phase-2
  package. `weather.load_awn()` loads Ambient Weather (AWN) CSV exports, parsing the `Date` column
  (ISO 8601 **with −04:00 offset**) to **local-wallclock naive** time so it lines up with the audio
  feature timestamps; renames the useful columns (temp/wind/gust/rain-rate/rain-cumulative/humidity/
  solar/pressure); concats + de-dups multiple exports. `find_awn_files()` globs `AWN-*.csv`.
- `audio_analysis/scripts/plot_soundscape_day.py` — **new** driver: a 3-panel diurnal figure for one
  channel/day — (1) Leq loudness, (2) bird-like 2–8 kHz vs ambient 0–1 kHz bands with the
  "biophony likely" shading, (3) AWN rain-rate + wind. Rain periods are shaded across all panels so
  the "is the loud daytime birds or weather?" question is answerable at a glance. Prints which hours
  logged rain.
- Uses the existing feature CSVs in `outputs/` and the transferred AWN files at
  `D:\Reolink_record\audio_in\weather_data` (both already on this analysis PC).

## Why

The Leq level plot cannot separate birdsong from wind/rain — all three just read as "louder". Merging
the soundscape bands with weather covariates makes the driver of each part of the day explicit.

## Verification / finding (CH01 2026-06-30)

Panel written to `outputs/plots/CH01_2026-06-30_soundscape_panel.png`. Hourly medians (audio) vs
hourly weather:

- **Dawn 04:00–06:00 = birds.** 2–8 kHz band jumps −84 → −65 dB while wind is calm (0.1–0.3 mph) and
  rain = 0. Unambiguous biophony (dawn chorus).
- **Midday 11:00–16:00 loudness = WIND, not rain.** The 0–1 kHz ambient band rises to ~−55 dB
  tracking wind (2–3 mph, gusts 5–7 mph); **rain = 0 mm/hr** the whole window. This **corrects an
  earlier hypothesis** that the midday hump was rain — the weather merge shows it is wind noise.
- **17:00 = a rain shower (the day's loudest peak).** Rain 10.2 mm/hr; both bands spike broadband.
- **Night = calm and quiet** (wind ~0, bands at floor); birds taper by ~21:00.

Daily rain total ≈ 4 mm, confined to the ~17:00 shower.

## Known limitations

- **Cross-device alignment is UNVERIFIED** — the AWN station clock is not tied to the camera/NVR
  clock; this is timestamp alignment only (`data_manifests/2026-06-29-camera-audio.yaml`). The
  wind/rain↔audio correspondence is strong but remains a covariate association, not a synced signal.
- **Biophony shading false-positives under rain.** The heuristic marks 17:00 "biophony likely"
  because rain deposits 2–8 kHz energy. A future refinement: suppress/flag the biophony shading when
  rain > 0 or when the 0–1 kHz band spikes simultaneously (a broadband weather event, not birdsong).
- Levels are relative camera-mic dBFS (not SPL); ≤8 kHz ceiling; AWN wind/rain sensor placement
  relative to the paddock is not documented here.
- `plot_soundscape_day.py` currently plots one channel/day; multi-day and WISER-occupancy merge
  (the rest of the Phase-2 design) are not built yet.

## Env note

pandas in the `audio` env is 3.0.x; `Series.corr()` on a small merged frame crashed the interpreter
(native exit) during ad-hoc analysis — avoided it (the hourly table is sufficient). Not triggered by
the shipped scripts, but worth knowing for future Phase-2 stats work in this env.

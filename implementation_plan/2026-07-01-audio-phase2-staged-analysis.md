# Audio Phase 2 — Staged Downstream Analysis (DESIGN ONLY)

Design for the "main analysis computer" (Phase 2) side of the environmental-audio pipeline.
**No source code is written by this document** — it is the plan that a later implementation change
will follow. Phase 1 (feature extraction → tidy CSVs) is complete and, as of 2026-07-01, runs on
this analysis PC; see [Phase 1 plan](2026-06-29-environmental-audio-pipeline.md) and the change log
`change_log/2026-07-01-audio-extraction-on-analysis-pc.md`.

> **Status (2026-07-02): components 2 + 3 substantially implemented.** The `analysis/` package now has
> `weather.py` (AWN loader) and `wiser_activity.py` (per-local-hour rat activity, read-only, reusing
> the WISER pipeline); `scripts/plot_soundscape_day.py` is now a **multi-day 4-panel** figure (Leq /
> rain-aware bird-vs-ambient / weather / WISER rat activity). See
> `change_log/2026-07-02-audio-soundscape-weather-panel.md` and
> `change_log/2026-07-02-audio-wiser-activity-merge.md`. Still to build: multi-day aggregation tables
> (`aggregate.to_hourly`), all-day spectrograms, and event detection (`events.py`).

## Context / Why

Phase 1 produces per-window relative camera-mic level + band-limited soundscape-index CSVs
(`audio_analysis/outputs/audio_features_CHxx_<date>.csv`). The scientific value comes from the
**downstream** work that Phase 1 deliberately deferred and that currently exists only as prose in
`audio_analysis/README.md`:

1. Diurnal / multi-day soundscape summaries and all-day spectrograms.
2. Merging the audio level/index covariates with **WISER occupancy** and **AWN weather**.
3. Exploratory soundscape **event detection**.

All required inputs are already present on this analysis PC (transferred under
`D:\Reolink_record\audio_in\`), so Phase 2 can be built and validated here without touching the
field PC. This plan fixes the module layout, data contracts, reused utilities, and non-goals so the
implementation stays reproducible and honest about alignment uncertainty.

## Interpretation rules (load-bearing, inherited from Phase 1)

- Level columns are **relative camera-mic dBFS, not calibrated SPL**. Never relabel as SPL. Keep the
  `_dbfs_relative` suffix on every derived level column.
- Analysis ceiling is **8 kHz** (16 kHz mono audio). Indices are camera-specific band-limited
  variants (`bi_2_8k_camera`, `ndsi_1_2k_vs_2_8k_camera`) — comparable only within this dataset.
- Only use rows with `valid_audio == True` (`qc_flag == "ok"`) for interpretation; report the QC
  distribution alongside every summary so dropped/silent/pre-mic windows are visible.
- **Cross-device alignment is UNVERIFIED.** Only within-channel audio↔video shares a clock
  (`data_manifests/2026-06-29-camera-audio.yaml`). Any audio↔WISER↔weather join must be labeled
  "timestamp-aligned, unverified" until a shared physical event is independently confirmed.

## Module layout

New Phase-2 code as a sibling of extraction, keeping the repo's source/notebook separation
(`AGENTS.md`): reusable logic in a package, scripts/notebooks as thin clients.

```
audio_analysis/
  analysis/                 # NEW — Phase 2 (downstream) package
    __init__.py
    aggregate.py            # load + QC-filter + resample Phase-1 CSVs
    soundscape_summary.py   # diurnal/diel curves; all-day spectrogram
    merge.py                # audio <-> WISER occupancy <-> AWN weather (unverified align)
    events.py               # rolling-median + MAD burst/anomaly flags
  scripts/
    run_soundscape_summary.py   # NEW — CLI: CSVs -> summary tables + figures
    run_crossmodal_merge.py     # NEW — CLI: build merged hourly table
  outputs/                  # git-ignored (adds phase2/ subdir for merged tables + figures)
```

Reuse across modules (do **not** duplicate):
- `audio_analysis/src/audio_io.py` — `iter_windows` / `_stream_pcm` for spectrogram re-decode.
- `audio_analysis/src/plotting.py` — `plot_level_over_time`, `plot_index_timeseries`,
  `plot_window_spectrogram`.
- `wiser_tracking_analysis/src/wiser_io.py` — `load_wiser_sqlite`, `load_sqlite_window`,
  `_standardise_df` (for the gz incrementals).
- `wiser_tracking_analysis/src/time_utils.py`, `metrics.py`, `wiser_analysis_utils.py` — WISER
  timestamp conversion + occupancy binning (same approach as the hourly-occupancy pipeline).

## Data sources (present on this PC)

| Modality | Path | Clock / key fields |
|---|---|---|
| Audio features | `audio_analysis/outputs/audio_features_CHxx_<date>.csv` | `window_start_timestamp` = local wallclock (recorder filename time); filter `valid_audio` |
| WISER incrementals | `D:\Reolink_record\audio_in\Wiser_backup\incremental\1stcohort_2026_YYYY-MM-DD.csv.gz` | raw `timestamp` = Unix **ms**; `location_x/y` (**inches**), `shortid`; 6/28–7/01 |
| WISER snapshots | `D:\Reolink_record\audio_in\Wiser_backup\snapshots\1stcohort_2026_YYYY-MM-DD.sqlite` | same schema; use `load_sqlite_window` for bounded reads |
| Weather (AWN) | `D:\Reolink_record\audio_in\weather_data\AWN-*.csv` | `Date` ISO **with -04:00 offset**; temp/wind/gust/rain-rate/humidity/solar/pressure; ~10-min cadence |

Weather is the only source with an explicit tz offset, so it is the least ambiguous clock; document
that audio/WISER are aligned to weather local wallclock (still unverified cross-device).

## Data contracts

1. **Phase-1 CSV → tidy per-window frame** (`aggregate.load_features`): concat per-channel/day CSVs,
   parse `window_start_timestamp` to tz-aware local time, drop rows where `valid_audio != True`,
   carry `channel`.
2. **Per-window → hourly** (`aggregate.to_hourly`): resample level (median Leq, L10/L50/L90) and
   indices (median) to 1 h per channel; also a diel-hour (0–23) grouping across days. Emit window
   counts + QC-flag counts per bin.
3. **Hourly audio ⋈ WISER hourly occupancy ⋈ weather** (`merge.build_merged_hourly`): join on
   `(local_hour)`; WISER occupancy per hour derived with `wiser_analysis_utils`/`metrics`
   (per-tag/pooled active time, occupancy density), weather resampled/interpolated to hourly.
   Output a single merged hourly table + a `merge_manifest` recording each source's clock, offset
   assumptions, and the UNVERIFIED-alignment flag.
4. **Events** (`events.detect`): rolling-median + MAD threshold on `leq_dbfs_relative` and
   `bi_2_8k_camera` over the valid series → boolean burst/anomaly columns + an event table
   (start/end, peak, channel). Exploratory only.

## Verification (when implemented)

- Offline: unit-test `to_hourly` / merge on tiny synthetic frames (no field data needed).
- Real data: run `run_soundscape_summary.py` on CH01/CH02 2026-06-29→07-01; confirm diel curves are
  populated only in the mic-on era, spectrogram matches band energies (2–8 kHz biophony visible),
  and QC counts reconcile with `summarize_soundscape.py`.
- Merge: confirm row counts and hour coverage across the three modalities; spot-check that a
  weather rain interval and the audio level series line up in wallclock (documented as unverified).

## Non-goals

- No implementation this round (design only).
- No calibrated SPL; no analysis above 8 kHz; rat ultrasound out of scope (needs ≥250 kHz mic).
- No claim of verified cross-device synchronization; no source localization or species ID.
- No changes to Phase-1 extraction logic or the field-PC config.

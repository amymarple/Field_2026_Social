# Nightly 9pm–12am movement, 6/28–6/30 — rate-normalized paired habituation vs rain

## Goal

Compare each rat's nocturnal movement across three nights (21:00–24:00 EDT) to separate **novelty
habituation** from **rain**, using rate-normalized paired inference on the 5 rats present all three
nights (Sova removed).

## Approach (units: inches internally → metres for rates; reuse heavily)

Logic in `src/wiser_analysis_utils.py`; driver `scripts/analyze_nightly_progression.py`. Output →
`D:\Wiser_plot\nightly_progression_YYYYMMDD_HHMM\`. Pipeline: `load_wiser_session` →
`convert_timestamps` → `add_speed` → `add_validity_flags` → `apply_tag_cutoffs` → **drop 12409** →
`select_route_window(21, 24)` split by `night`.

- **Primary metric** = `active_distance_m_per_valid_hour` (`window_rate` / `_rate_from_df`): active
  path length above the ~12.5 in/s noise floor ÷ valid tracked time (Σ `dt_s`, gap-capped). Rate-
  normalized so unequal sub-windows compare.
- `nightly_rates` (tag×night), `night_split_rates` (pre/post at a clock split, ±transition buffer),
  `rain_did` (per-rat Δ(rain)−Δ(control)), `cumulative_night_distance`, `load_weather_multi`
  (+`rain_rate_mmhr`). Plots N1–N5.

## Rain facts (weather-confirmed) & design

- 6/30 **afternoon rain 17:20–17:55** (station, peak 10.2 mm/hr) → **wet ground the whole 6/30 night**,
  categorically different from dry 6/28/6/29 → a whole-night covariate **confounded with habituation**.
- 6/30 **in-window rain ~22:30–22:50** (observed; station evening data sparse) → per-rat DiD on the
  **22:30** split, buffers 0 and 20 min (the buffer drops the observed burst).
- **Clean habituation = 6/28 vs 6/29 (both dry, station-confirmed)**. 6/29 evening weather unknown;
  6/30 wet.

## Deliverables

CSVs: `nightly_rates.csv`, `night_split_rates.csv`, `rain_did.csv`, `weather_night_summary.csv`,
`nightly_qc.csv`, `nightly_conclusion.txt`, `run_manifest.json`. Figures: N1 per-rat trajectories ×3
nights; N2 nightly-rate paired lines (habituation); N3 through-the-night cumulative curves (rain band
shaded on the wet night); N4 station rain timeline; N5 per-rat DiD (buffer variants).

## Interpretation guardrails

Exploratory/candidate (3 nights, n=5 paired). Candidate habituation, candidate rain, possible
novelty×rain — no causal claim. 6/30 wet-ground confound stated; 6/29 weather unknown; rates jitter-
inflated (relative/paired only); WISER frame unverified; Sova removed.

## Verification

conda `cv`: `py_compile`; run driver → read-only on `D:\Wiser\data`; 5 rats × 3 nights; rates finite;
6/28 rain-rate ≈ 0 (dry) and 6/30 shows the 17:20 burst; DiD has both buffers; N1–N5 render; no writes
under `D:\Wiser`.

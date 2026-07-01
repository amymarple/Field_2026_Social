# Nightly 9pm–12am movement, 6/28–6/30 — habituation vs rain

## Date

2026-06-30/07-01. Uncommitted at writing.

## Plan

[`implementation_plan/2026-06-30-nightly-progression.md`](../implementation_plan/2026-06-30-nightly-progression.md).
Reuses the pilot data manifest ([`data_manifests/2026-06-29-wiser-pilot.yaml`](../data_manifests/2026-06-29-wiser-pilot.yaml)).

## What changed

- `src/wiser_analysis_utils.py` — new "Nightly progression" section: `load_weather_multi` + weather
  parsing extended to `rain_rate_mmhr`/event/daily rain; `_night_bounds`, `window_rate`/`_rate_from_df`
  (**primary metric `active_distance_m_per_valid_hour`** = active path above the noise floor ÷ valid
  tracked time), `nightly_rates`, `night_split_rates` (pre/post at a clock split, ±transition buffer),
  `rain_did` (per-rat difference-in-differences), `cumulative_night_distance`; plots
  `plot_nightly_trajectories`, `plot_nightly_rate_lines`, `plot_cumulative_night`,
  `plot_rain_timeline`, `plot_rain_did`. Added `LOCAL_OFFSET_STR`.
- `scripts/analyze_nightly_progression.py` — driver: 5-rat paired core (Sova/12409 removed), nights
  6/28–6/30, rates + habituation + DiD (buffers 0/20 at the 22:30 split) + weather; writes CSVs +
  N1–N5 + conclusion + manifest to `D:\Wiser_plot\nightly_progression_*`.

## Why

To see how per-rat nocturnal movement progresses over the first nights and to separate novelty
habituation from a rain effect, rate-normalized (unequal windows) and paired (same 5 rats).

## Source data used for verification

Read **read-only**: `D:\Wiser\data\1stcohort_2026.sqlite` (live), `…\tag_reports.sqlite` (baseline →
moving threshold 12.5 in/s, jitter floor 7.0 in), `D:\weather_data\AWN-…-20260628-20260629.csv` and
`…-20260630-20260701.csv`.

## Verification performed

conda `cv`; `py_compile` + run:
- Paired core = **5 rats × 3 nights** (Sova removed); read-only; DB counts match the probe.
- **Candidate habituation (dry nights)** 6/28→6/29: **229 → 115 m/valid-hr (−50%)**, all 5 rats drop.
  6/30 (wet all night) 115 → **124** (no further drop) → the wet-ground covariate is confounded with
  habituation and did not further suppress night movement.
- **Rain facts (weather):** 6/30 afternoon **17:20–17:55** burst (peak 10.2 mm/hr) — wets the ground;
  in-window **~22:30–22:50** rain is observed (station evening data sparse, recorded 0). 6/28 dry-
  confirmed; 6/29 evening weather unknown.
- **In-window rain DiD** (per-rat, 22:30 split): mean **+17.6** (no buffer) / **+7.5** (20-min buffer)
  m/valid-hr vs controls → **no acute rain suppression** beyond the time-of-night trend (positive =
  6/30 rose slightly more than controls; n=5, exploratory).
- N1–N5 render; N4 shows the 17:20 burst + window + observed band; no writes under `D:\Wiser`.

## QC output

`nightly_rates.csv`, `night_split_rates.csv`, `rain_did.csv`, `weather_night_summary.csv`,
`nightly_qc.csv`, `nightly_conclusion.txt`, `run_manifest.json`, `figures/N1…N5`.

## Known limitations

Exploratory: 3 nights, n=5 paired, one rain event. 6/30 wet-ground is **confounded with habituation**
(cannot separate). 6/29 evening weather unknown. Distances jitter-inflated (relative/paired only).
WISER frame unverified. Recording began 6/28 19:20 EDT, so 6/28 has no *morning* — the night window is
what makes all three days comparable.

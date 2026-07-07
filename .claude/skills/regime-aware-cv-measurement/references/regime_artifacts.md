# Regime artifacts — the concrete repo bridge

This maps the regime-aware discipline in `SKILL.md` onto the **real files, columns, and functions**
in this repo, so an analysis drives the existing pipeline instead of reinventing it. All paths are
relative to the repo root (`Field_2026_Social/`). Verified against the tree on 2026-07-06.

## Regime signal → machine-readable source

| Regime signal | Source (structured) | Format | Key fields |
|---|---|---|---|
| Fog / rain exclusion windows | `data_manifests/field_conditions.yaml` | YAML (append by date) | `date, start, end, type, channels, affects, note` |
| Glass view-quality per time bin | `preprocessing/computer_vision/outputs/CHxx_sleep_<date>.csv` | CSV | `view_quality_inside`, `view_quality_doorway`, `weather_logged`, `usable_for_headline_summary`, `usable_for_coarse_activity`, `state`, `inside_motion_score`, `n_detected_*` (raw evidence) |
| Ground-truth validation (per sample) | `preprocessing/computer_vision/outputs/validation_<date>.csv` | CSV | `view_quality_inside`, `pred_count`, `pred_state`, `gt_count`, `gt_view`, `gt_motion`, `gt_n_moving` |
| Weather (temp / humidity / **dew point** / rain rate / solar) | `AWN-*.csv` via `wiser_tracking_analysis/src/wiser_analysis_utils.py::load_weather` | CSV → tidy DataFrame | `datetime_local`, `temp_c`, `humidity`, `dewpoint_c`, `rain_rate_mmhr`, `solar_wm2`, … |
| Animal validity (Sova removed) | `wiser_tracking_analysis/configs/rat_identities.csv` | CSV | `shortid`, `valid_until` |
| Shelter structure changes (tunnel removal) | `data_manifests/2026-06-29-wiser-pilot.yaml` → `time_varying_structures` | YAML | time-bounded ROI, `valid_until` (UTC) |
| Per-night confounds | generated `night_covariates.csv` (from `analyze_nightly_progression.py`) | CSV | `night`, `wet_ground`, `tunnel_present`, `sova_removed` |
| Glass treatments (tape / lift / anti-fog film) — optical regimes | `data_manifests/glass_treatments.yaml` (narrative in `FIELD_OBSERVATIONS.md`) | YAML | `regimes[] {start,end,regime,layers,uncertain_layers,time_precision,confounded}`, `change_points[] {at,kind,precision}` |

## `field_conditions.yaml` schema

```yaml
conditions:
  - date: 2026-06-30
    start: "03:00"          # observed LOCAL wall clock
    end:   "07:00"          # null = ongoing at time of logging
    type:  fog              # ONLY two types exist: fog | rain
    channels: [CH05]        # or: all
    affects: [detection, motion]
    note:  "foggy"
```

- **Only `fog` and `rain` `type`s** are modeled — these are **transient weather** windows. Glass
  treatments and dew point are **not** here: persistent **optical/equipment regimes** live separately
  in `data_manifests/glass_treatments.yaml` (see below), read by the pure covariate annotator
  `preprocessing/computer_vision/glass_regime.py`.
- Times are **observed local wall-clock**. The Reolink OSD clock runs **~1 h behind the recording
  filenames** on this rig — when mapping a window to hourly files, reconcile against filename
  timestamps.
- Consumed by `view_quality.py::load_conditions` / `in_degraded_window`, which force any matching bin
  to **≥ degraded** and set `weather_logged=true` in the sleep CSV.
- Known missing window: `FIELD_OBSERVATIONS.md` (Day 6) flags that **2026-07-03 04:00–06:00 heavy
  fog is not yet in this file** — add it before trusting that morning's clear-view budget.

## View-quality vocabulary + hard safety rules

- **Tiers** (`preprocessing/computer_vision/view_quality.py`, `TIERS`): `clear`, `degraded`,
  `unusable`. This is the only view-quality vocabulary; there is no fog/rain/glare *type* label
  (deferred to a future "Phase B").
- **Shelter states** (`shelter_sleep.py`, `STATES`): `empty`, `occupied_low_motion` (the rest/sleep
  proxy), `occupied_high_motion`, `indeterminate`.
- **`_fuse` rules** (`shelter_sleep.py::_fuse`) — the safety core:
  - `view == unusable` → `indeterminate` (no occupancy number emitted).
  - present under `view == degraded` → forced `occupied_low_motion`, confidence `low`; **never
    auto-high under degraded glass.**
  - `view == clear` and present → `occupied_high_motion` iff `inside_motion_score ≥ motion_thresh`,
    else `occupied_low_motion`.
  - a `weather_logged` (fog/rain) window upgrades a `clear` bin to `degraded`.
- **`robust_inside_motion`** (`view_quality.py::robust_inside_motion`) is the glass-noise-resistant
  motion score: illumination-normalize each frame → temporal-median background (static drips + a
  still rat vanish here → rest proxy) → keep only pixels *darker* than the median (rat body, not
  bright specular raindrops) → morphological open (kills speckle) → sum rat-sized blob area. This is
  why rain/glare/AE drift do not register as rat motion.

## Sleep-CSV column meaning (`CHxx_sleep_<date>.csv`)

Canonical current schema (15 columns):

```
channel, t, file, view_quality_inside, view_quality_doorway,
n_detected_inside, n_detected_doorway, n_detected_outside_near_shelter,
inside_motion_score, n_inside_estimated, n_inside_confidence, state,
weather_logged, usable_for_headline_summary, usable_for_coarse_activity
```

- `usable_for_headline_summary` = view is **clear** — the only bins allowed into a headline
  occupancy/rest budget.
- `usable_for_coarse_activity` = view not `unusable` (or doorway clear) — coarse activity only,
  caveated.
- `n_detected_*` are **raw detector evidence** and are never overwritten by the fused
  `n_inside_estimated`. Prefer them when auditing.
- **Schema caveat:** files dated **before 2026-06-30** use a legacy 6-column schema
  (`channel,file,t,n_rats,roi_motion,state`) with **no regime columns** — do not mix schemas; treat
  legacy files as regime-blind.

## Validation artifacts (`validate_shelter.py`)

- Writes `outputs/validation_<date>.csv` (columns above): per random closed-footage sample, the
  pipeline's `view_quality_inside`/`pred_state`/`pred_count` vs the human's `gt_view`/`gt_count`/
  `gt_motion`/`gt_n_moving` (detector answer hidden while judging). `gt_view` uses the same 3 tiers
  (`c`=clear, `f`=degraded, `u`=unusable).
- **Prints** (stdout, not saved): a by-`view_quality` 3×3 confusion matrix; degraded/unusable
  detection recall & precision; the **safety count** (samples the pipeline scored
  `occupied_high_motion` while the human saw degraded/unusable — must be **0**); and count MAE /
  presence / still-vs-moving agreement computed **on clear-view samples only**.
- When you re-score with a new detector, the output filename carries the model
  (`validation_<date>_rescored_<model>.csv`) — that is the only place the model version is recorded.

## Optical-regime boundary timeline — CH05/CH06

These glass treatments are recorded as structured, queryable state in
**`data_manifests/glass_treatments.yaml`** (`regimes` = contiguous spans with `layers` /
`uncertain_layers` / `time_precision` / `confounded`; `change_points` = the boundaries; narrative in
`FIELD_OBSERVATIONS.md`). This file is a **covariate, not an exclusion rule**, and **nothing reads it
yet** — join it by hand. Each `change_point` is an **optical-regime boundary**: **do not pool shelter
view-quality across a boundary** — treat each `regime` span as a distinct camera.

| Regime (from glass_treatments.yaml) | Starts (local EDT) | Layers | Note |
|---|---|---|---|
| `bare` | 2026-06-28 19:25 | — | baseline bare, seated glass |
| `tape` | 2026-06-30 ~09:00 (approx) | aluminum_tape | patch over condensation break; not a like-for-like baseline |
| `lift_1cm` | 2026-07-01 16:00 | lift_1cm (tape uncertain) | IR glass lifted ~1 cm for airflow; tape persistence not logged |
| `antifog_film` | 2026-07-02 13:00 | antifog_film | film on **and** ~1 cm lift off — **confounded**; see nuance below |
| `bare_seated_post_film` | 2026-07-03 11:00 → ongoing | — | film removed; **not assumed identical** to the original `bare` (residue/cleaning/repositioning) |

**Important — instrument ≠ weather:** the observer reported the `antifog_film` regime (2026-07-02
13:00 → 07-03 11:00) made the view **worse than bare glass**, not merely ineffective. That span is a
**view-degrading optical regime in its own right**, so degraded view there is partly the *instrument*
(film), not weather alone — a textbook sensor-path confound. Also note fog itself recurs pre-dawn
(e.g. 2026-07-03 ~04:00–06:00, rats "hardly visible") and looks like a **hard optical floor** for
these cameras, not fixed by any surface treatment so far.

## Known gaps you must handle (do not imply these are solved)

- **Model version — provenance module exists, confirm it ran.** There is no `model_version` column in
  the sleep CSV itself; `preprocessing/computer_vision/measurement_context.py` stamps run provenance
  (camera_model, shelter_id, detector/model version + params, config versions, frame) as a JSON sidecar
  + a per-row `mc_run_id`. **Confirm the run you're analyzing actually emitted that** — if it predates
  the module or didn't call it, record the model by hand (current default
  `shelter_sleep.py::DEF_WEIGHTS = runs/detect/rat_feasibility-6/weights/best.pt`, 2026-07-04, val
  mAP50 0.876).
- **Glass regime — annotator exists, weather cross-check still doesn't cover it.**
  `data_manifests/glass_treatments.yaml` (optical regimes + change_points) is read by
  `preprocessing/computer_vision/glass_regime.py` (`annotate()` adds `glass_*` covariate columns; pure,
  never changes results/safety). Confirm your bins carry those columns; if not, join/stratify by regime
  by hand. Either way the `field_conditions.yaml` weather cross-check does **not** cover glass regime,
  and the `antifog_film` span is itself view-degrading (above).
- **The stratified validation summary is printed, not saved** — only the raw per-sample CSV is
  written. Re-derive the by-view_quality breakdown from `validation_<date>.csv` if you need it.
- **`dewpoint_c` is loaded but unused** as a condensation predictor — there is no dew-point→fog model
  yet; use it as a manual risk cue only.
- **`CH06_zones.json` does not exist** — CH06 falls back to the calibration shelter quad as its
  `inside` zone (only `CH05_zones.json` is present).
- **Wall-edge blind zone** — the top-down shelter cams cannot see a band along the wall edge where
  rats hide; a visible "empty"/low count is a **lower bound**, not a true headcount.

## Weather join

`wiser_tracking_analysis/src/wiser_analysis_utils.py`:
- `load_weather(path)` — richest AWN loader; keeps `dewpoint_c`, `humidity`, `rain_rate_mmhr`,
  `temp_c`, `solar_wm2`, `datetime_local`. (The `audio_analysis` and `episode_browser` loaders drop
  dew point.)
- `merge_activity_weather(group_hour, weather)` — floors weather to the UTC hour and left-merges onto
  hourly activity; sets `attrs["alignment"] = "wall-clock UTC, unverified (~5 min)"`. **Weather is an
  unverified covariate over time, never a synchronized signal** — it can act on the sensor path,
  the animal path, or both.

## Output-report template

Fill this for every regime-aware CV result:

```
Result: <what was measured, e.g. CH05 daytime rest occupancy>
Date/time range:     2026-06-30 05:00–21:00 EDT
Camera/channel:      CH05 (through IR-filter glass)
Detector/model:      rat_feasibility-6 (2026-07-04, val mAP50 0.876)   # no model_version column — by hand
Label set:           single-class `rat`; huddle deferred
Regime states incl.: clear <x%> / degraded <y%> / unusable <z%>; fog window 03:00–07:00 (field_conditions.yaml)
Glass regime:        aluminum-tape era (post 06-30 patch, pre 07-01 lift) — distinct optical regime
Count type:          visible_count (LOWER BOUND — wall-edge blind zone)   # or inferred_count / true_count est.
Reliability:         headline OK on clear bins only; degraded bins coarse-activity only; unusable excluded
Known failure modes: fog false-empty; wall-edge blind zone; motion agreement weak under degraded glass
Behavior vs artifact: rest-budget change survives restricting to CLEAR bins → behavioral;
                      if it disappears once fog bins are dropped → measurement artifact
Category:            (1) behavioral / (2) artifact / (3) mixed / (4) invalid–lower-bound-only
```

**Worked example (2026-06-30 fog):** the pre-dawn 03:00–07:00 fog window is logged in
`field_conditions.yaml`, so those bins are forced ≥ `degraded` and are excluded from the headline
budget; a "no motion / empty" reading there is fog-obscured, **not** true stillness (category 2 /
4). Any rest-vs-active claim for that morning must be computed on the clear bins only, on the CH05
aluminum-tape optical regime, with the count reported as a visible lower bound.

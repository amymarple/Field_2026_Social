---
name: regime-aware-cv-measurement
description: >-
  This skill should be used when analyzing long-duration field video or CV-derived behavioral
  measurements from the Field_2026_Social shelter cameras — shelter occupancy, rest/sleep proxy,
  headcount or count, huddle, detector validation, weather-behavior interpretation, comparing days
  or cameras, or deciding what to label next. CH05/CH06 image the shelter interior (where rats rest
  and sleep) through IR-filter glass, so view quality — fog, condensation, rain, glare, anti-fog
  film and other glass treatments — confounds every number. Carry regime context before making any
  behavioral claim. Trigger phrases: "shelter occupancy", "rest/sleep proxy", "sleep site",
  "detector validation", "validate_shelter", "headcount", "huddle", "does weather cause", "compare
  CH05 vs CH06", "compare days", "what should I label next", "occupied_high_motion", "view quality".
version: 0.1.0
---

# Regime-aware Field CV Measurement

## Core principle

**Do not treat CV output as direct behavior.** In this field rig, weather and human interventions
push on two different paths that produce *identical-looking* changes in raw occupancy/motion numbers:

1. **Sensor path** — humidity / dew point / rain / condensation / anti-fog film / IR reflection /
   camera shift → **view quality** changes → detector errors, count bias, motion artifacts.
2. **Animal path** — rain / cold / heat / storm / shelter temperature → **real behavior** changes →
   true occupancy / rest / huddle changes.

A movement drop can be a fogged lens or a sleeping rat; the raw number cannot tell you which. Every
analysis must carry regime context so these two paths stay separable. This matters most for
**CH05/CH06**, which see the rats *through IR-filter glass* on a 24/7 recorder — the inside view is
only as good as the glass at that moment.

**New (2026-07-07): CH07/CH08 are interior in-house cameras** (EmpireTech 4MP pinhole) imaging *directly
inside* `house_1`/`house_2` — so they are **glass-free / fog-immune**. The through-glass view-quality /
`glass_regime` discipline in this skill applies to **CH05/CH06 only**, NOT to CH07/CH08 (whose failure
modes are pinhole / close-range / IR-lighting, TBD). CH07/CH08 are a candidate **glass-free interior
reference** for cross-checking CH05/CH06 through-glass occupancy, but are **not yet calibrated / zoned /
validated** — see `configs/field_layout.json` (`view: interior_in_house`).

The load-bearing safety invariant (already enforced in `shelter_sleep.py`, never weaken it):
**degraded inside-glass never becomes `occupied_high_motion`; `unusable` view → `indeterminate`.**
Weather/glass artifacts must never be counted as rat activity.

## When to invoke

Invoke before any of: shelter occupancy analysis; rest/sleep proxy or sleep-site analysis; detector
validation; count/headcount analysis; huddle analysis; weather↔behavior interpretation; comparing
days or cameras; deciding what to label next.

## Required checks before interpreting

Inspect (or create) machine-readable regime context for the window under analysis. Each signal has a
home in this repo — see `references/regime_artifacts.md` for exact paths, columns, and schemas:

- **rain / storm / wet periods** — `data_manifests/field_conditions.yaml`; AWN weather CSVs.
- **humidity / dew point / condensation risk** — AWN weather via the WISER `load_weather` loader
  (the only one that keeps `dewpoint_c`).
- **observed fog / degraded view** — `field_conditions.yaml` windows *and* the per-bin
  `view_quality_inside` column in the `shelter_sleep.py` output CSV.
- **glass-treatment changes** (anti-fog film, hydrophobic coating, tape, cleaning, glass lift) —
  structured in `data_manifests/glass_treatments.yaml` (`regimes` + `change_points`; narrative in
  `FIELD_OBSERVATIONS.md`). Treat each `change_point` as an optical-regime boundary and each `regime`
  span as a distinct camera. **No code reads this file yet — join it by hand** as a covariate/stratifier.
- **camera-specific issues** — misalignment, glare, IR change, channel swap, FOV change
  (`FIELD_OBSERVATIONS.md`).
- **shelter physical changes** — bedding, hay, tunnel, food/water, door obstruction
  (`data_manifests/2026-06-29-wiser-pilot.yaml` `time_varying_structures`; `FIELD_OBSERVATIONS.md`).
- **human field observations** that explain sensor or animal state — `FIELD_OBSERVATIONS.md` (these
  are covariates/hypotheses, **not** labels or exclusion rules).

## Required distinction — never collapse these

Classify **every** result into exactly one of:

1. **likely behavioral signal**
2. **likely measurement artifact**
3. **mixed / ambiguous**
4. **invalid / lower-bound only**

Do not merge categories to make a cleaner story. A number that is only trustworthy as a floor
(wall-edge blind zone, degraded glass) is category 4, not a headcount.

## Minimal workflow

1. **Locate** existing field notes and condition logs (`FIELD_OBSERVATIONS.md`,
   `field_conditions.yaml`).
2. **Build or update** a regime timeline for the window (weather + view-quality + glass treatments +
   physical changes), keyed on local wall-clock EDT — mind the ~1 h Reolink OSD-vs-filename offset
   and the unverified cross-device clocks.
3. **Join** regime state onto CV validation frames (`validate_shelter.py` output) or shelter time
   bins (`shelter_sleep.py` output) using the existing `view_quality_*` / `weather_logged` columns.
4. **Stratify errors by regime** — never report one pooled accuracy across mixed view quality.
5. **Identify the current failure mode**: detector miss · undercount · false positive · huddle
   compression · wall-edge blind zone · fog/view degradation · motion-threshold failure — route via
   [`references/context_debug_map.yaml`](references/context_debug_map.yaml) (observation/regime → failure
   mode → diagnostic → allowed action → forbidden interpretation).
6. **Recommend targeted labeling** only for the failure modes that remain (see below).
7. **Do not claim weather causes behavior** unless clear-view data supports it (weather can be acting
   on the sensor, not the animal).

## Output requirements

Every analysis reports (use the template in `references/regime_artifacts.md`):

- date / time range
- camera / channel
- detector / model version (there is **no** `model_version` column — record it by hand; the current
  default is `rat_feasibility-6`)
- label set used
- regime states included
- whether the output is a **`visible_count`**, **`inferred_count`**, or **`true_count` estimate**
- reliability flag
- known failure modes
- what evidence separates behavior from measurement artifact

## Forbidden shortcuts

Do not:

- report only aggregate mAP or accuracy;
- treat weather as a nuisance variable to simply regress out (it acts on *both* paths);
- interpret occupancy changes without view-quality / regime context;
- label randomly when failures are regime-specific;
- claim sleep from low motion without validation (low motion under degraded glass is not rest);
- claim a true headcount where wall-edge blind zones make only a **lower bound** possible;
- retrain before checking whether errors are concentrated in specific regimes.

## Preferred labeling strategy

Active labeling **by failure mode and regime** — the goal is labels that attack the *current*
uncertainty, not more labels in general:

- huddle-heavy frames
- wall-edge / doorway frames
- foggy / degraded-view frames
- rain + clear-view frames
- rain + foggy-view frames
- post anti-fog / post glass-treatment frames
- day-night / IR-transition frames
- short clips for motion/rest validation

## Grounding in this repo

The CV pipeline already implements the *mechanics* of this discipline (3-tier `view_quality`,
`shelter_sleep.py` safety rules, `validate_shelter.py` stratified scoring, the `field_conditions.yaml`
weather cross-check). **Read [`references/regime_artifacts.md`](references/regime_artifacts.md)** for
the exact files, columns, schemas, the CH05/CH06 glass-treatment (optical-regime) timeline, the known
data gaps you must handle, and the fillable output-report template. Drive those real artifacts —
don't reinvent them. To **route** an observation / configuration change to the specific failure mode to
test, its diagnostic, the allowed next action, and the forbidden interpretation, use
[`references/context_debug_map.yaml`](references/context_debug_map.yaml) (with its
[`.md`](references/context_debug_map.md) design doc) — the canonical, machine-readable version the
`cv-measurement-auditor` agent executes.

For the current-state architecture + the catalogue of known failure modes (with evidence and the
sensor-artifact / lower-bound / unresolved classification), see `docs/methods/shelter_cv_measurement.md`
and `docs/methods/shelter_failure_modes.md`.

The WISER analog is the **`regime-aware-wiser-tracking`** skill (UWB jitter / dropout / unverified
frame). They cross-validate: **WISER is fog-immune, so it is the reference for shelter occupancy when
the glass is degraded; conversely CV misses huddles under wet glass** — use each to check the other,
never assume they agree.

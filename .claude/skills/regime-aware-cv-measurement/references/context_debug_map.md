# Context → CV-debugging map (design)

Turns a **field observation / configuration change / regime state** into an explicit CV debugging
hypothesis: *what measurement failure to test → the query that tests it → which frames/bins to pull →
the one allowed next action → the forbidden interpretation.* When an observation says "lighting
changed", "glass changed", "weather risk high", or "zone/calibration changed", the pipeline should
already know **which failure mode to test before anyone retrains or interprets results**.

This is a **covariate / debugging map, not code and not an exclusion-rule engine.** It changes no
detector output, view-quality, motion, count, safety, threshold, or filter. It is the decision layer on
top of the machinery in [`regime_artifacts.md`](regime_artifacts.md) and the failure-mode catalogue in
[`docs/methods/shelter_failure_modes.md`](../../../../docs/methods/shelter_failure_modes.md) (FM #s
below). The machine-readable rules live in [`context_debug_map.yaml`](context_debug_map.yaml); this doc
is the reader's guide.

## 1. Schema

Each rule is one row of `rules:` in the YAML with these fields:

| Field | Meaning |
|---|---|
| `id` / `name` | Stable rule id (R1–R9) + short label. |
| `trigger` / `trigger_signal` | The observation/config/regime that fires it, in prose and as a machine-readable column+value condition. |
| `trigger_source` | Where the trigger comes from: `columns`, `files`, and `status: present | partial | missing` (can it fire automatically today?). |
| `failure_mode` | `{ref: FM#, class, one_line}` — the expected measurement failure, **generalized** (no dataset-specific numbers in the one_line). `class` ∈ `sensor_artifact | lower_bound | geometry_config | provenance | unresolved_candidate`. |
| `evidence_example` | *(optional)* a dataset-specific illustration — the numbers (e.g. a recall figure) live here, so the rule generalizes beyond one audit. |
| `diagnostic_query` | The read-only stratification/comparison that **tests** the hypothesis. |
| `selection` | Which frames/bins to pull — for inspection or **targeted labeling by failure mode**. |
| `allowed_next_action` | The smallest permitted step(s). Never "retrain" before stratifying. |
| `forbidden_interpretation` | The wrong story this trigger guards against. |
| `default_classification` | The result bucket a finding defaults to: `behavioral | measurement_artifact | mixed | lower_bound`. |
| `provenance_gap` / `comparability_gap` | *(optional bool, R7)* the mismatch is a provenance/comparability problem, not just ambiguous interpretation. |
| `interpretation_blocker` | *(optional bool, R9)* a catastrophic trigger blocks **all** stratification/interpretation until patched + tested. |

The chain is **context → failure mode → diagnostic → selection → allowed action → forbidden
interpretation**, exactly as requested, with `default_classification` binding each rule to the skill's
four-bucket discipline. Dataset-specific numbers are kept in `evidence_example`, never in a rule name,
so a rule stays valid as the data grows.

## 2. The rules (9)

| id | Trigger (observation/config/regime) | Expected failure mode | Class | Default bucket |
|---|---|---|---|---|
| **R1** | fog-risk high **or** logged fog/rain window | condensation/fog → detector misses; visible "empty"/"no motion" is fog-obscured (FM1+2) | sensor_artifact | measurement_artifact |
| **R2** | glass taped/lifted/anti-fog-film/reseated; `antifog_film` regime | glass instrument degrades view even at **low** fog-risk; coincident changes confounded (FM3) | sensor_artifact | measurement_artifact |
| **R3** | inside head-count reported; WISER-present but CV recall < ½ on **clear** glass | wall-edge band occluded → visible count ≤ true, a lower bound not a headcount (FM4) | lower_bound | lower_bound |
| **R4** | hot high-solar daytime + clear/low-fog + WISER-present + CV-empty | `heat_exterior_refuge_blind_zone_candidate` — target may be in an exterior blind zone; candidate measurement-gap, **not** a proven heat effect (FM5) | unresolved_candidate | mixed |
| **R5** | huddle-like frame (many overlapping detections) | piled rats counted as fewer separable boxes; lower bound (FM6) | lower_bound | lower_bound |
| **R6** | zones re-drawn / camera pose shift / CH06 quad-fallback | detections mis-assigned inside/doorway/outside → biased zone counts | geometry_config | measurement_artifact |
| **R7** | detector `weights_version`/fingerprint differs, or no sidecar | a **provenance/comparability gap** (`provenance_gap`/`comparability_gap`), not a behavior signal | provenance | mixed |
| **R8** | camera model/pose/FOV/IR/channel change | cross-camera / pre-post-move comparison compares two instruments; homography invalid | geometry_config | measurement_artifact |
| **R9** | schema vintage / OSD ~1 h offset / ms-datetime binning / weather lag > tol; **catastrophic bin-count mismatch** | mis-aligned bins → wrong attribution; **hard blocker** — patch + test binning before any stratification | geometry_config | measurement_artifact |

R1–R5 are the sensor/coverage failure modes already catalogued (FM1–6); R6–R9 add the
config/provenance/timing triggers the catalogue treats as caveats but hadn't turned into fire-able
rules. Full `diagnostic_query` / `selection` / `allowed_next_action` / `forbidden_interpretation` for
each are in the YAML.

## 3. Which existing columns/files supply each trigger

| Rule | Supplied by (present today) | Status |
|---|---|---|
| R1 | `weather_logged` (sleep CSV); `field_conditions.yaml` fog/rain windows | **partial** — `fog_risk_level` not yet a sleep column (join by hand) |
| R2 | `glass_regime`, `glass_confounded`, `glass_regime_note`, … (9 covariates in sleep CSV since 2026-07-02); `glass_treatments.yaml` `change_points` | **present** |
| R3 | `n_inside_confidence` (proxy); `cv_recall_gap_flags.csv`, `cv_wiser_reconciliation_strata.csv`, `ALIGNMENT_DIAGNOSIS_*` | **partial** — no explicit wall-edge column |
| R4 | `state`, `view_quality_inside`; AWN `temp_c`/`solar_wm2` (not joined); WISER presence | **missing** — composite not computable in CV output |
| R5 | `n_detected_inside` (density proxy) | **missing** — no huddle flag/class |
| R6 | `measurement_context.json` zones+calibration fingerprints; `mc_run_id` | **partial** — silent pose drift undetectable; no `CH06_zones.json` |
| R7 | `measurement_context.json` `detector.weights_version` + fingerprint | **partial** — no `model_version` CSV column |
| R8 | `camera_model`, `shelter_id`, sidecar camera block + `field_layout.json` fingerprint; `FIELD_OBSERVATIONS.md` | **partial** — physical moves only in narrative |
| R9 | CSV header/vintage; `t`/`file`; `weather_lag_min`; OSD-offset prose caveat | **partial** — offset not machine-readable |

Only **R2** fires fully automatically today. Everything else needs a hand-join or the metadata below.

## 4. Triggers that need new metadata (currently missing)

From `metadata_gaps:` in the YAML — each blocks a rule from firing automatically:

- **`fog_risk_in_sleep_csv`** (R1) — wire `fog_risk.annotate` into `shelter_sleep.py` output (deferred:
  adds a weather dependency to CV capture).
- **`wall_edge_flag`** (R3) — a per-bin `wall_edge_occluded` / coverage-limited flag (today only
  `n_inside_confidence` proxies it).
- **`huddle_flag`** (R5) — a per-bin huddle-like flag; then measure indivisible-pile frequency **before**
  proposing a `huddle` class.
- **`heat_refuge_composite`** (R4) — join AWN `temp_c`/`solar_wm2`; add a **house-temperature sensor** (no
  interior-temp sensor exists); a *soft* candidate flag, never an exclusion. FM5 stays a candidate.
- **`model_version_column`** (R7) — carry `weights_version` as a per-row column (or always confirm the
  sidecar emitted).
- **`zone_change_manifest`** (R6) — draw `CH06_zones.json`; a structured zone/calibration change log
  beyond the sidecar fingerprint.
- **`camera_change_manifest`** (R8) — a `camera_changes.yaml` (pose/FOV/IR/channel-swap `change_points`),
  mirroring `glass_treatments.yaml`.
- **`osd_clock_map`** (R9) — a per-channel clock-offset table so window↔bin mapping is reconcilable
  programmatically rather than by the prose "~1 h behind filenames" note.

These are **suggestions for later metadata**, not work to do now (the task is the map, not new code).

## 5. How an auditor agent uses the map

The [`cv-measurement-auditor`](../../../agents/cv-measurement-auditor.md) already reads this
`references/` directory as its source of truth. The map slots into its existing workflow (steps 4–6)
and turns "stratify by regime" into a **context-driven checklist**:

1. **Load** `context_debug_map.yaml` alongside `regime_artifacts.md`.
2. **Fire rules by presence** — for the run under audit, evaluate each `trigger_signal` against the
   covariate columns actually present (`glass_regime`, `weather_logged`, `view_quality_inside`,
   `state`, sidecar fingerprints, schema vintage). Every rule whose trigger is TRUE becomes a
   hypothesis to test **this run**, instead of a generic pass.
3. **Run the `diagnostic_query`** as the stratification — the map tells the auditor *which* axes to
   stratify on (e.g. R2: degraded-fraction across `glass_regime` **within** low fog-risk; R3: is the
   WISER-present/CV-miss set flat across `view_quality_inside`?). This is read-only and matches the
   auditor's "never one pooled accuracy" rule.
4. **Classify** each finding with the rule's `default_classification`, and attach its
   `forbidden_interpretation` list to the report so the wrong story is explicitly ruled out (e.g. "gap
   persists on clear glass → **not** optical failure; lower-bound only").
5. **Recommend** the rule's `allowed_next_action` as the "smallest next action" — targeted labels for
   the *specific* failing regime, an annotation/fingerprint fix, or a manifest update — and **only**
   escalate to retrain when a rule's diagnostic concentrates the failure in a way retraining fixes
   (R1's misses concentrated in high-fog; never R3/R5, which are coverage limits).
6. **Report `metadata_gaps` as `provenance_gaps`** — any rule that *should* apply but couldn't fire
   because its column is `missing`/`partial` becomes a named provenance gap in `cv_audit_<date>.json`,
   pointing at the metadata to add.

Net effect: the auditor stops asking "is anything wrong?" and starts asking "given *this* run's context
(glass regime X, fog window Y, detector Z, schema vintage V), which of R1–R9 fire, and did their
diagnostics pass?" — the same discipline, now driven by what actually changed in the field.

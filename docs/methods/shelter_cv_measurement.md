# Shelter CV measurement — current architecture

> **Read this first.** This is the concise *current-state* reference for the CH05/CH06 shelter CV
> measurement pipeline. For chronology and provenance, consult the `change_log/` entries (e.g.
> [`change_log/2026-07-01-glass-degradation-zones.md`](../../change_log/2026-07-01-glass-degradation-zones.md));
> for the catalogue of known measurement failure modes see
> [`shelter_failure_modes.md`](shelter_failure_modes.md). The auto-firing
> [`regime-aware-cv-measurement`](../../.claude/skills/regime-aware-cv-measurement/SKILL.md) skill enforces
> the discipline described here.

## Principle

CH05/CH06 view the rats **through an IR-transmitting glass window** on a 24/7 recorder, so the inside
view is only as good as the glass at that moment. Two different paths produce *identical-looking* changes
in the raw occupancy/motion numbers:

- **Sensor path** — humidity / dew point / rain / condensation / anti-fog film / IR reflection / camera
  shift → **view quality** → detector errors, count bias, motion artifacts.
- **Animal path** — real behavior → true occupancy / rest / huddle changes.

The pipeline is built to keep these separable. It is **conservative** (degraded glass never becomes
high-confidence activity), **regime-aware** (every number carries its optical/weather regime), and
**measurement-context-bearing** (every number carries who/what/when produced it). CV visible-inside
occupancy is a **high-precision lower bound**, never a true head-count.

## 1. View-quality-aware conservative fusion

`preprocessing/computer_vision/shelter_sleep.py` + `preprocessing/computer_vision/view_quality.py`

- **View-quality tiers** (per zone, per bin): `clear` / `degraded` / `unusable` (`view_quality.py:45`).
- **Occupancy states**: `empty` / `occupied_low_motion` / `occupied_high_motion` / `indeterminate`
  (`shelter_sleep.py:57-59`).
- **Hard safety rules** (`shelter_sleep.py:134-141`, `_fuse`): **degraded inside-glass never becomes
  `occupied_high_motion`** (it is capped at `occupied_low_motion`); **`unusable` view → `indeterminate`**.
  Weather/glass artifacts must never be counted as rat activity — this invariant is load-bearing.
- **Glass-noise-resistant motion**: `robust_inside_motion` (`view_quality.py:196-224`) illumination-
  normalizes each frame, takes a temporal-median background, keeps only darkening-vs-background blobs,
  morphologically opens away speckle, and keeps rat-sized components. It is built to reject rain speckle /
  glare / auto-exposure — **not** to measure behavioral stillness (see failure mode 7).
- **Zones**: `inside` / `doorway` / `outside` (drawn per camera via `place_zones.py` → `CHxx_zones.json`).
- **Key output columns** (`shelter_sleep.py`): `view_quality_inside`, `view_quality_doorway`,
  `n_detected_inside`, `n_detected_doorway`, `n_detected_outside_near_shelter`, `inside_motion_score`,
  `n_inside_estimated`, `n_inside_confidence`, `state`, `usable_for_headline_summary`,
  `usable_for_coarse_activity`.

## 2. `glass_regime` — optical-instrument covariate

`preprocessing/computer_vision/glass_regime.py` + `data_manifests/glass_treatments.yaml`

CH05/CH06 see through glass whose optical state changes over time (tape, ~1 cm lift, anti-fog film,
reseating). `glass_regime.annotate(df, ts, channel)` appends 6 columns (`GLASS_COLS`,
`glass_regime.py:25-26`): `glass_regime`, `glass_layers`, `glass_uncertain_layers`,
`glass_time_precision`, `glass_confounded`, `glass_regime_note`.

- **Step-function regimes**: `bare` → `tape` → `lift_1cm` → `antifog_film` → `bare_seated_post_film`.
- **`glass_confounded`** is `{value, note}` when coincident changes cannot be separated — e.g. the
  2026-07-02 13:00 transition applied the anti-fog film **and** removed the ~1 cm lift at the same time,
  so effects there are *regime-attributable, not film-attributable*.
- **Pure additive annotator** — never changes detector output, view-quality, motion, counts, safety,
  thresholds, filtering, or validity. Treat each `glass_regime` span as a distinct optical instrument.

## 3. `fog_risk` — weather-derived measurement-risk covariate

`preprocessing/computer_vision/fog_risk.py`

`fog_risk.annotate(df, ts)` appends 6 columns (`FOG_COLS`, `fog_risk.py:38-39`): `fog_risk_level`
(`low`/`medium`/`high`), `fog_risk_reason`, `dewpoint_gap`, `humidity_pct`, `rain_mm_hr`,
`weather_lag_min`. It classifies from AWN weather (`fog_risk.py:31-36` / `classify`):
`dewpoint_gap = air_temp − dew_point` with thresholds 1.5 °C / 3.0 °C, RH 97 % / 92 %, a pre-dawn cue,
and a `WX_TOL_MIN = 30` nearest-sample tolerance (beyond which the bin gets `NaN`).

- It estimates **how likely the glass was to fog/condense from weather** — it is **not** a fog measurement
  and **not** a behavior feature. It exists to *explain / stratify* view-quality degradation and detector
  misses; the **direct** measure of degradation stays the video-derived `view_quality_inside`.
- `fog_risk` and `view_quality` are **correlated by construction** (both track condensation) — a
  stratification asks whether errors *track* the covariate, not that weather *caused* anything.
- Pure additive, like `glass_regime` — never changes thresholds, safety logic, counts, or excludes bins.

## 4. `measurement_context` — sidecars / `mc_run_id`

`preprocessing/computer_vision/measurement_context.py`

Makes every CV-derived shelter number interpretable *as a measurement*.

- **Per-row columns**: `camera_model`, `shelter_id` (`CAMERA_COLS`, `measurement_context.py:34`), plus
  `mc_run_id` — a short **stable hash of the measurement setup** (git commit + detector + config
  fingerprints + camera block + coordinate frame), so a row can always be tied back to its run and rows
  from identical setups share an id.
- **Per-run JSON manifest sidecar** (`shelter_sleep_<date>.measurement_context.json`): schema version,
  `git_commit`, generated-by/at, detector `weights_path` + `weights_version` + `weights_fingerprint`
  (sha256-16 + size + mtime) + conf/imgsz/batch/device, sampling params, per-camera block (model, role,
  mapping, pos/height/aim, shelter_id), content-hash **fingerprints** of `field_layout.json` / zones /
  `view_quality` config / `field_conditions.yaml` / `glass_treatments.yaml` / calibration, the
  `coordinate_frame` (cm, origin corner pole A0; note that the WISER inch frame is a separate *unverified*
  offset), and a `caveats` list.
- **Provenance + covariates only** — nothing here changes detector output, view-quality, motion, counts,
  safety, thresholds, filtering, or validity. Metadata make outputs stratifiable and auditable; they are
  **never exclusion rules**.

## 5. Visible-inside count as a lower bound

CV head-counts systematically **undercount** true shelter occupancy, so `n_inside_estimated` is reported
as a **lower bound** with an explicit `n_inside_confidence`. Two structural causes:

- **Wall-edge blind zone** — CH05/CH06 are near-nadir; a band directly along the interior walls is
  perspective-occluded, and rats rest/hide there. Both human ground-truth and the detector miss wall-edge
  hiders (`measurement_context.py:196`; `CLAUDE.md`).
- **Huddle compression** — piled/overlapping rats are counted as fewer separable boxes (see failure
  mode 6).

Because of this, a visible "empty" or low count under WISER-confirmed presence is a **coverage/definition
lower bound**, not proof of absence.

## 6. CV × WISER reconciliation semantics

`wiser_tracking_analysis/scripts/analyze_sleep_site_cv_crossval.py`

WISER (UWB) is unaffected by fog / rain / condensation / IR glass, so it is the **fog-immune reference**;
CV visible-inside-through-glass is the sensor under test and a **high-precision lower bound**. This is an
**asymmetric** reconciliation, not a symmetric agreement:

- **WISER occupancy** = a smoothed, hysteretic, buffer-tolerant shelter **STATE** (`wiser_shelter_state`)
  — a sustained cluster of positions *near* a shelter, i.e. near-shelter presence.
- **Headline, PER SHELTER (never pooled)**: (1) **CV precision** given WISER presence — when CV says
  occupied, does WISER agree? (2) **CV recall / lower-bound gap** vs WISER presence — how much
  WISER-confirmed occupancy does CV recover?
- **Cohen's κ is demoted to a base-rate-sensitive *alignment diagnostic* only** — with WISER presence
  prevalence near 1.0 (e.g. house_1 ~0.99) κ collapses toward 0 even at high raw agreement (the *kappa
  paradox*). A low κ here is prevalence + definition mismatch, **not** misalignment; the 2026-07-02 lag
  sweep is flat (see
  [`ALIGNMENT_DIAGNOSIS_2026-07-02.md`](../../wiser_tracking_analysis/outputs/audit/ALIGNMENT_DIAGNOSIS_2026-07-02.md)).
- **Reconciliation stratification** — `cv_wiser_reconciliation_strata.csv` reports, among WISER-present
  bins, CV recall + miss count by `shelter` / `camera` / `wiser_validity` / `view_quality_inside` /
  `glass_regime` / `fog_risk_level` (when present) / `n_inside_confidence`.
- **Recall-gap flag** — `cv_recall_gap_flags.csv` marks a (day, shelter) where WISER shows sustained
  near-shelter presence but CV recovers < half (`cv_recall_gap_under_wiser_presence`; thresholds
  `GAP_WISER_PRESENCE = 0.50`, `GAP_MAX_RECALL = 0.50`). It is framed as a **coverage/definition
  lower-bound gap** (wall-edge blind zone, huddle), *not* an "optical failure" — the flag was renamed from
  the earlier `cv_optical_failure_flags.csv` precisely because the CH05 gap persists on **clear** glass.
- **Manifest notes**: `metric_note` (per-shelter precision + recall/lower-bound headline), `kappa_note`
  (κ is a base-rate-sensitive diagnostic, not the headline), `reconciliation_note` (the stratification).

Verified example (2026-07-02): CH05 precision ≈ 1.00 / recall ≈ 0.49 (WISER presence 0.99); CH06
precision ≈ 0.99 / recall ≈ 0.67 (presence 0.75). CV recall is ~0.50–0.60 flat across every stratum
(clear ≈ degraded, low ≈ high fog-risk) → the gap is a coverage/definition limit, not fog.

## See also

- [`shelter_failure_modes.md`](shelter_failure_modes.md) — the catalogue of known failure modes with
  evidence and classification.
- [`regime-aware-cv-measurement`](../../.claude/skills/regime-aware-cv-measurement/SKILL.md) skill and its
  [`references/regime_artifacts.md`](../../.claude/skills/regime-aware-cv-measurement/references/regime_artifacts.md).
- `change_log/` — history/provenance (this doc is the current-state summary; the change logs are the
  chronology).

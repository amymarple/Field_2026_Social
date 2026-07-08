# Change log — Regime-aware Field CV Measurement skill

**Date:** 2026-07-06
**Commit:** uncommitted at time of writing.
**Plan:** none (doc/tooling addition, not a pipeline code change — see `AGENTS.md` change-size rules).

## What changed

Added a repo-local Claude Code skill that encodes the discipline for interpreting CV-derived
behavioral measurements from the shelter cameras. Nothing in the CV pipeline changed; the skill is
documentation/guidance that points at the artifacts that already exist.

- **`.claude/skills/regime-aware-cv-measurement/SKILL.md`** — the decision procedure: the two-path
  (sensor vs animal) model, when to invoke, required regime checks, the four-way result
  classification (behavioral / artifact / mixed / invalid–lower-bound), the minimal workflow,
  output-report requirements, forbidden shortcuts, and the failure-mode/regime labeling strategy.
  Auto-discovered (model-invocable) and also runnable as `/regime-aware-cv-measurement`.
- **`.claude/skills/regime-aware-cv-measurement/references/regime_artifacts.md`** — the repo bridge:
  the regime-signal → source table, the `field_conditions.yaml` schema, the `view_quality` tiers +
  `shelter_sleep._fuse` safety rules, the sleep/validation CSV column meanings, the CH05/CH06
  glass-treatment (optical-regime) timeline structured in `data_manifests/glass_treatments.yaml`, the
  known data gaps (no `model_version` column, `glass_treatments.yaml` not yet read by any code,
  printed-not-saved stratified summary, unused `dewpoint_c`, missing `CH06_zones.json`, wall-edge
  blind zone), and a fillable output-report template with a worked 2026-06-30 fog example.
- **`CLAUDE.md`** — one-line pointer so humans discover the skill.

## Why

CH05/CH06 image the shelter interior (where rats rest/sleep) through IR-filter glass on a 24/7
recorder, so fog/condensation/rain/glass-treatment regime confounds every occupancy, rest, count,
and motion number — the sensor path and the animal path look identical in raw output. The pipeline
already enforces the *mechanics* (3-tier `view_quality`, `shelter_sleep.py` safety rules,
`validate_shelter.py` stratified scoring, the `field_conditions.yaml` cross-check), but there was no
portable rule that made every analysis *carry regime context before making a behavioral claim*. The
skill supplies that and routes future work to the real artifacts instead of reinventing them.

## Design notes

- **Code-free by choice.** The chosen scope was skill-only; no `field_conditions.yaml` extension or
  join script was added. The CH05/CH06 optical-regime timeline is recorded in
  `data_manifests/glass_treatments.yaml` (`regimes` + `change_points`; a covariate, not an exclusion
  rule), and the skill points at it. During this session that file gained a reader —
  `preprocessing/computer_vision/glass_regime.py` (a pure covariate annotator; appeared as concurrent
  work), and `measurement_context.py` began stamping detector/model provenance — so the reference was
  updated to point at both and to hedge "confirm the run emitted the columns" rather than call them
  gaps. Note the `antifog_film` span (2026-07-02 → 07-03) was reported to *degrade* the view vs bare
  glass — an instrument effect rather than weather.
- All file paths, function names (`TIERS`, `DEF_WEIGHTS`, `_fuse`, `robust_inside_motion`,
  `load_weather`, `merge_activity_weather`), and CSV headers referenced in the skill were verified
  against the tree on 2026-07-06. Note the sleep-CSV schema changed at 2026-06-30 (legacy 6-column
  files before that carry no regime columns).

## Verification

- Frontmatter `name` matches the directory; `description` is third-person with explicit trigger
  phrases (auto-fires on occupancy/rest/validation/count/huddle/weather-behavior work).
- Every path, symbol, and column named in `references/regime_artifacts.md` confirmed present
  (`view_quality.py`, `shelter_sleep.py`, `validate_shelter.py`, `wiser_analysis_utils.py`,
  `field_conditions.yaml`, `rat_identities.csv`, the real `outputs/*_sleep_2026-06-30.csv` and
  `outputs/validation_2026-06-30*.csv` headers).
- Glass-treatment timeline dates/times cross-checked against `FIELD_OBSERVATIONS.md` (Days 3–6).

# Change log — Measurement-Context Auditor agents (CV + WISER)

**Date:** 2026-07-06
**Commit:** uncommitted at time of writing.
**Plan:** none (doc/tooling addition, not a pipeline code change — see `AGENTS.md` change-size rules).
**Related skills:** [regime-aware CV skill](2026-07-06-regime-aware-cv-skill.md) ·
[regime-aware WISER skill](2026-07-06-regime-aware-wiser-skill.md)

## What changed

Added two repo-level Claude Code subagents (auto-discovered from `.claude/agents/`). They are the
*dispatchable worker* counterpart to the two `regime-aware-*` skills: where a skill is inline
discipline, an agent is launched to run a fixed audit workflow on a concrete output artifact and return
a verdict, without cluttering the main thread.

- **`.claude/agents/cv-measurement-auditor.md`** — audits CH05/CH06 shelter CV outputs. Reads the
  `CH0x_sleep_<date>.csv` + its per-run `shelter_sleep_<date>.measurement_context.json` sidecar (and
  `validation_<date>.csv` when present), verifies annotation purity + per-row context completeness
  (`glass_*`, `camera_model`, `shelter_id`, `mc_run_id` ↔ manifest), and runs one of two modes:
  **validation mode** (ground-truth labels present → error metrics stratified by regime) or
  **metadata mode** (no labels → regime/occupancy distributions + completeness only, no invented error
  metrics). Stratifies by channel · shelter_id · glass_regime · view_quality · fog/rain window ·
  huddle/wall-edge, asserts the safety invariant, classifies each finding (behavioral / artifact /
  mixed / lower-bound), and recommends the smallest next action. v1 is shelter-focused; CH01–CH04
  whole-field tracking is out of scope unless an artifact is passed.
- **`.claude/agents/wiser-measurement-auditor.md`** — audits WISER analysis-driver run dirs. Prefers an
  explicitly supplied run directory (treats `D:\Wiser_plot` / `wiser_tracking_analysis/outputs` as
  default search roots, degrades gracefully if no `run_manifest.json`). Verifies provenance
  completeness, reports `flag_summary` validity fractions, stratifies by tag · validity flags ·
  night_covariates · georeference status · jitter floor · weather · wet-hay-wall dropout, gates every
  spatial claim on the unverified inch frame, and reuses the `ANALYSIS_STATUS.md` candidate/confirmed/⛔
  vocabulary. Because WISER lacks a CV-style `measurement_context` sidecar + per-row stamp, it treats
  that as a first-class finding and its verdict is often "partially auditable / weaker provenance than
  CV"; its smallest-next-action recommendation is to build that sidecar (a follow-up PR, not done here).
- **Mutual awareness** — each agent names the other and the hand-off condition, citing the existing
  cross-validation bridge `wiser_tracking_analysis/scripts/analyze_sleep_site_cv_crossval.py` (WISER =
  fog-immune reference; CV sees inside / catches huddles). The check runs both ways.
- **`CLAUDE.md`** — brief pointers in the CV and WISER subsystem sections.
- **`change_log/README.md`** — index rows for this entry and the two 2026-07-06 skill entries (which
  were previously missing from the index).

## Why

The mission is to stop "improve the detector/tracker first" reflexes: before touching a model, confirm
each derived number is interpretable *as a measurement* (traceable to timestamp, camera/model, detector
version, config fingerprints, zone/calibration, glass/optical regime, weather/view-quality, run
manifest), and that errors are regime-concentrated. The CV pipeline now emits a full measurement-context
record (`measurement_context.py` + `glass_regime.py`), which makes a dispatchable auditor genuinely
useful; the WISER side is auditable but with weaker provenance, which the auditor surfaces rather than
hides.

## Design notes

- **Advisory + persist (chosen scope).** Agents write **only** two report files (`.md` + `.json`) into
  `outputs/audit/` (CV) or the audited run's `audit/` folder (WISER); they never modify detector/
  tracking logic, thresholds, filters, configs, manifests, raw data, or pipeline outputs. Tools are
  `Read, Grep, Glob, Bash, Write` (Bash for pandas stratification; Write hard-scoped in the prompt to
  the report).
- **Not symmetric.** The CV auditor keys on the standardized `measurement_context/1.0` sidecar + 9
  covariate columns; the WISER auditor keys on `run_manifest.json` + `flag_summary` validity flags +
  `night_covariates.csv`, and explicitly reports the provenance asymmetry.
- **Agents use skills.** Each body instructs the agent to follow its `regime-aware-*` skill and read the
  skill's `references/*_artifacts.md`, so the deep domain detail lives in one place.
- Building a WISER `measurement_context` module was explicitly deferred to its own follow-up PR.

## Verification

- Frontmatter valid: `name` matches filename; `description` is delegation-style with triggers + a
  "do NOT use" clause; `tools: Read, Grep, Glob, Bash, Write`; `model: inherit`. Both appear in the
  Agent tool's available types.
- Referenced artifacts confirmed present: `outputs/shelter_sleep_2026-07-02.measurement_context.json`
  and the 9-column `CH05_sleep_2026-07-02.csv`; `write_run_manifest` / `flag_summary` /
  `add_validity_flags` in `wiser_analysis_utils.py`; `analyze_sleep_site_cv_crossval.py`.
- CV dry run on the 2026-07-02 outputs produces a persisted `outputs/audit/cv_audit_2026-07-02.{md,json}`
  and the 5-item summary, in the correct mode for whether a labeled `validation_2026-07-02.csv` exists.
- WISER dry run resolves an explicit run dir or degrades gracefully when none is found, and records the
  provenance-gap finding.

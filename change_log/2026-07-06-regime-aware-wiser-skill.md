# Change log — Regime-aware WISER / UWB Tracking skill

**Date:** 2026-07-06
**Commit:** uncommitted at time of writing.
**Plan:** none (doc/tooling addition, not a pipeline code change — see `AGENTS.md` change-size rules).
**Sibling:** [change_log/2026-07-06-regime-aware-cv-skill.md](2026-07-06-regime-aware-cv-skill.md)

## What changed

Added a second repo-local Claude Code skill, the WISER analog of `regime-aware-cv-measurement`. It
encodes the discipline for interpreting noisy UWB tag data. No pipeline code changed; the skill points
at the artifacts that already exist in `wiser_tracking_analysis/`.

- **`.claude/skills/regime-aware-wiser-tracking/SKILL.md`** — the decision procedure for WISER work:
  the two-path (sensor vs animal) model in UWB terms, when to invoke, required noise/dropout/frame
  checks, the four-way result classification, the minimal workflow, output-report requirements,
  forbidden shortcuts, and the WISER-specific failure modes. Auto-discovered; also runnable as
  `/regime-aware-wiser-tracking`.
- **`.claude/skills/regime-aware-wiser-tracking/references/wiser_artifacts.md`** — the repo bridge:
  the signal→source table (`add_validity_flags`, `speed_noise_floor`, `proximity_summary`,
  `assign_roi`, `load_weather`/`merge_activity_weather`, `apply_tag_cutoffs`), the jitter-floor and
  sampling numbers (~7 in median / p95 ~15 in; ~3.7–3.9 Hz; keep proximity ≥ 1 m; `DEFAULT_MIN_ANCHORS=4`),
  the georeference-blocker status (`wiser_to_field_transform.json` absent → helpers no-op; `wiser_rois.json`
  confirmed only in the inch frame), the weather⟷UWB coupling, the wet-hay-wall dropout regime, the
  known gaps, and a fillable output-report template with a worked rainy-night example.
- **`CLAUDE.md`** — one-line pointer in the WISER subsystem section.
- **`.claude/skills/regime-aware-cv-measurement/SKILL.md`** — added a reciprocal cross-reference to
  this sibling (they cross-validate: WISER is fog-immune; CV misses huddles under wet glass).

## Why

The user observed that the CV skill does not (and should not) cover WISER: UWB has entirely different
sensor physics. WISER positions are noisy (~4–7 in jitter), the inch frame is an unverified offset
origin, and the signal drops out under weather and near certain shelters — so occupancy, proximity,
sleep-site, and nightly-movement claims can be sensor artifacts just as easily as CV numbers can. The
skill makes any WISER analysis carry noise + dropout + frame context before a behavioral or spatial
claim, and routes work to the real QC helpers instead of trusting raw positions.

## Design notes

- **New field knowledge captured** (not previously in the repo, and not derivable from code): the
  **bottom-right low-rank shelter is a ~1-inch hay-wall refuge, and when the wall gets wet/white the
  UWB signal there can stop**, biasing that rat's occupancy down and "time outside" up in wet weather.
  Recorded in the skill and in auto-memory. Mapping it to a specific `wiser_rois.json` ROI name
  (`house_2` / `refuge_2` / `refuge_4`?) is left as a TODO to confirm with the user / a georeferenced map.
- **Code-free by choice**, mirroring the CV skill. A future option is a structured UWB-dropout log
  (the WISER analog of `field_conditions.yaml`) plus a loader; for now dropout is detected empirically
  via `gap_flag` and cross-checked against weather + the wet-hay-wall periods in `FIELD_OBSERVATIONS.md`.
- All functions/columns/thresholds referenced were verified against `wiser_analysis_utils.py`,
  `ANALYSIS_STATUS.md`, `wiser_rois.json`, and `rat_identities.csv` on 2026-07-06.

## Verification

- Frontmatter `name` matches the directory; `description` is third-person with WISER trigger phrases;
  the skill appears in the available-skills list.
- Every path/symbol named in the reference confirmed present (`add_validity_flags`, `speed_noise_floor`,
  `proximity_summary`, `DEFAULT_MIN_ANCHORS`, `load_field_transform`, `wiser_rois.json`,
  `wiser_to_field_transform.json` confirmed absent).

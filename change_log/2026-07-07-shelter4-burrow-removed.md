# Change log — shelter 4 (`refuge_4`) is a burrow entrance; hole + removal encoded

**Date:** 2026-07-07
**Type:** field-observation-driven provenance / config update (no analysis-code change).
**Source:** field note (`FIELD_OBSERVATIONS.md` Days 9–10).

## What / why

A field observation (2026-07-07 ~13:00) established that the bottom-right small refuge **"shelter 4"
= `refuge_4`** is used as a **burrow ENTRANCE, not a sleep site**: **more than one rat** dug a hole
under it, **nightly, from ~2026-07-03 01:00 EDT**; the hole was discovered ~2026-07-06 13:00; the
refuge was **physically REMOVED ~2026-07-07 13:00 EDT** to stop further digging.

**Scope correction:** this applies to **`refuge_4` ONLY**. The other three small refuges
(`refuge_1`/`refuge_2`/`refuge_3`) are **normal refuges (house area), unaffected**. (An earlier draft
of this update over-generalized to "all four refuges" — corrected here.)

This also **supersedes the earlier "wet hay wall attenuates UWB" hypothesis** for shelter 4: the poor
WISER/UWB signal there is the **burrow** (a tag below the anchor plane / underground), so it is
**structural + weather-INDEPENDENT + persistent within the dig window**, not a wet-only effect.

## Changes

- **`wiser_tracking_analysis/configs/wiser_rois.json`** — `refuge_4` gets
  `valid_until: 2026-07-07T13:00:00-04:00` + a note (burrow entrance; >1 rat; dig ~07-03→removal
  07-07). `assign_roi` already honors `valid_until`, so post-removal fixes there fall back to `open`.
  `refuge_1/2/3` unchanged. `_README` updated.
- **`data_manifests/2026-06-29-wiser-pilot.yaml`** — `time_varying_structures` gains a `refuge_4`
  entry (dig_started_local / removed_local / rats_involved + note).
- **`FIELD_OBSERVATIONS.md`** — Day 10 (2026-07-07) reframed to shelter-4-only + dig-start timing +
  >1 rat; Day 9 records the discovery (co-edited).
- **Memory + `.claude/skills/regime-aware-wiser-tracking/SKILL.md` + `CLAUDE.md`** — the WISER
  dropout field-knowledge note corrected from "wet hay-wall" to "shelter-4 burrow" (weather-independent).

## Impact on existing results

**None for the current WISER analyses.** All WISER data in play (**2026-06-28 → 06-30**) **PREDATES
the burrow** (dig started ~07-03), so shelter-4 reads in that window are **not** burrow-contaminated,
and the Direction-3 house_1↔house_2 finding (which does not involve `refuge_4`) is unaffected.
Forward-looking: any analysis of **07-03 → 07-07** data must treat `refuge_4` occupancy / "time
outside" as a **burrow-dropout lower bound** (weather-independent); after 07-07 13:00 `refuge_4` is
gone (`valid_until`).

## Verification / follow-up

- `wiser_rois.json` re-parses as valid JSON; the `time_varying_structures` block parses as valid YAML;
  all four refuges checked (`refuge_4` has `valid_until`, others do not).
- Open test (turns the field note into a data-confirmed regime): compare `gap_flag`/dropout at
  `refuge_4` on **dry vs wet** days — **burrow** ⇒ elevated on both; **hay** ⇒ wet-only.

---
name: regime-aware-wiser-tracking
description: >-
  This skill should be used when analyzing WISER / UWB tag-tracking data from the Field_2026_Social
  paddock — tag positions, speed/activity, proximity or social distance, occupancy/ROI, nightly
  movement, route/corridor structure, daytime sleep-site, or any cross-day/cross-tag or spatial
  claim. UWB positions are noisy (~4–7 in jitter) and the WISER inch frame is an UNVERIFIED offset
  origin; signal also drops out under weather (rain/wet ground) and near certain shelters (the
  bottom-right low-rank refuge / "shelter 4" loses UWB because of a HOLE/burrow under it — a tag below
  the anchor plane — found 2026-07-07; weather-independent, supersedes the earlier wet-hay hypothesis).
  Carry noise, dropout, and frame context before any behavioral or spatial claim. Trigger phrases: "WISER",
  "UWB", "tag position", "shortid", "proximity", "social distance", "occupancy", "ROI", "nightly
  movement", "route structure", "sleep site", "jitter floor", "georeference", "wall-running",
  "thigmotaxis", "does weather cause", "compare nights", "compare tags".
version: 0.1.0
---

# Regime-aware WISER / UWB Tracking

## Core principle

**Do not treat WISER output as ground-truth position or behavior.** As with the shelter cameras, two
different paths produce identical-looking changes in the raw position/speed/occupancy numbers:

1. **Sensor path** — UWB jitter / multipath / anchor geometry / **signal dropout** (weather-driven
   attenuation; wet materials) / unverified coordinate frame → position noise, false stationarity,
   missing tracks, spatial artifacts.
2. **Animal path** — real movement / rest / proximity / shelter use → true position and behavior
   changes.

A tag that goes still can be a sleeping rat *or* a fix pinned by jitter; a rat that "leaves the
shelter" can be a real move *or* the UWB signal dropping out. The raw number cannot tell you which.
Carry noise + dropout + frame context so the two paths stay separable.

Two load-bearing invariants (enforced in `wiser_analysis_utils.py`; never weaken them):
**never make a spatial/social claim finer than the jitter floor** (~7 in median, p95 ~15 in — keep
proximity thresholds ≥ 1 m), and **a signal gap is not absence** — a dropout means "unknown," not
"the rat left."

## When to invoke

Invoke before any of: proximity / social-distance analysis; occupancy or ROI analysis; nightly
movement (e.g. rain difference-in-differences); route / corridor structure; daytime sleep-site or
rest analysis; speed / activity analysis; any wall-running / thigmotaxis / boundary claim; comparing
nights or tags; any claim that places WISER positions in the physical field.

## Required checks before interpreting

Each signal has a home in the WISER library — see `references/wiser_artifacts.md` for exact functions,
columns, and thresholds:

- **jitter floor & sampling rate** — derive the per-session floor from the stationary baseline
  (`speed_noise_floor`); WISER is ~3.7–3.9 Hz and bursty (~0.28 s). Below the floor, movement and
  jitter are indistinguishable.
- **fix-quality / validity flags** — `anchors_used` (< 4 = low-confidence), `calculation_error`,
  `battery_voltage`; `add_validity_flags` emits `low_anchor_flag` / `gap_flag` / jump flags.
- **signal dropout / gaps** — quantify missing time. Expect dropout under **weather (rain / wet
  ground)** and at the **bottom-right low-rank refuge ("shelter 4", ~`refuge_4`)** — caused by a
  **HOLE / burrow under that shelter** (discovered 2026-07-07; tag goes below the anchor plane), **not**
  the wet hay wall as earlier thought, so it is **weather-INDEPENDENT / persistent** (present on dry
  days too). Dropout there biases occupancy *down* and "time outside" *up* — a sensor artifact, not
  behavior. (See `FIELD_OBSERVATIONS.md` Day 10, 2026-07-07.)
- **weather** — rain / wet / humidity degrade UWB (water attenuates the signal) *and* change behavior;
  it acts on **both** paths (`load_weather`, `merge_activity_weather`; alignment unverified ~5 min).
- **coordinate frame (the #1 spatial blocker)** — positions are **inches in an UNVERIFIED offset
  frame**. `wiser_to_field_transform.json` does not exist yet, so `load_field_transform` /
  `apply_field_transform` are **no-ops** and no position can be placed in the physical field. ROIs in
  `wiser_rois.json` are confirmed *in the inch frame* (membership works), but directional/physical
  claims (wall-running, "northeast corner") are not verified.
- **tag validity cutoffs** — resolve `shortid` → animal via `rat_identities.csv`; apply removals
  (Sova removed 2026-06-29 15:00) with `apply_tag_cutoffs`; a tag is not an animal.

## Required distinction — never collapse these

Classify **every** result into exactly one of:

1. **likely behavioral signal**
2. **likely measurement artifact** (jitter, dropout, multipath, frame)
3. **mixed / ambiguous**
4. **invalid / lower-bound only** (below jitter floor, or heavy dropout)

## Minimal workflow

1. **Load read-only**, preserving QC columns (`anchors_used`, `calculation_error`, `battery_voltage`).
   Never write to the live WAL DB.
2. **Compute the jitter floor** from the stationary baseline; record the sampling rate.
3. **Flag validity & gaps** (`add_validity_flags`); measure the dropout fraction for the window.
4. **Identify the dropout/noise regime** — weather (rain/wet), the shelter-4 burrow/hole (persistent),
   low anchors, battery decline, edge/multipath near walls.
5. **Stratify by validity/regime** — never pool a clean night with a heavy-dropout / rainy one.
6. **Name the current failure mode**: jitter-floor violation · signal dropout / gap · multipath /
   wall artifact · unverified-frame (physical claim) · below-speed-noise-floor stillness ·
   sub-1-m proximity.
7. **Do not claim behavior** the data can't support above the floor and within the (unverified) frame.

## Output requirements

Every analysis reports (template in `references/wiser_artifacts.md`):

- time range
- tag(s): `shortid` **and** resolved animal name
- jitter floor + sampling rate used
- validity / QC filters applied (`anchors_used ≥ N`, gap/jump flags)
- frame status: **inches, unverified offset** vs georeferenced (currently always unverified)
- whether any spatial claim survives the unverified frame
- dropout / gap fraction, and the regime driving it
- reliability flag
- known failure modes
- what evidence separates behavior from measurement artifact

## Forbidden shortcuts

Do not:

- make proximity / social-distance / fine-geometry claims below the jitter floor (keep proximity
  thresholds ≥ 1 m);
- treat a signal gap as "absent" / "left the shelter" — a dropout is *unknown*, not *outside*;
- compare raw inch positions across the unverified frame as if physical, or claim wall-running /
  thigmotaxis without a confirmed georeference;
- regress weather out as a nuisance — it drives dropout *and* behavior;
- claim rest/sleep from low speed below the stationary speed-noise floor without corroboration;
- pool across tag cutoffs, battery death, or the tunnel-removal boundary (2026-06-29 07:00);
- trust raw route length (jitter-inflates it) or sub-floor straightness.

## WISER-specific failure modes & field knowledge

- **Jitter** ~7 in median (18 cm), p95 ~15 in — the fixed-position precision floor, not accuracy.
- **Shelter-4 burrow dropout** — **ONLY** the bottom-right low-rank refuge ("shelter 4" = `refuge_4`;
  `refuge_1/2/3` are normal) loses UWB because of a **HOLE / burrow** under it: **>1 rat dug it nightly
  from ~2026-07-03 01:00 EDT**, hole found ~07-06, refuge **removed 07-07 13:00** (`valid_until` set). A
  tag below the anchor plane / underground stops ranging → `refuge_4` occupancy under-counts and "time
  outside" over-counts. This is a **structural, weather-INDEPENDENT** dropout regime (window ~07-03 →
  07-07), not the rat leaving. **06-28→06-30 data predates it** (uncontaminated). (Supersedes the
  "wet hay-wall" hypothesis. Test: hole ⇒ dropout dry *and* wet; hay ⇒ wet-only.)
- **Weather** — rain / wet ground attenuates UWB and raises dropout/noise (WISER's analog of the CV
  glass problem).
- **Unverified frame** — the #1 blocker for every spatial claim until a pole survey passes QC.

## Grounding & sibling skill

Read [`references/wiser_artifacts.md`](references/wiser_artifacts.md) for the functions, columns,
thresholds, ROI list, georeference status, and the output-report template. The camera analog is the
**`regime-aware-cv-measurement`** skill (glass view-quality). They cross-validate: **WISER is
fog-immune, so it is the reference for shelter occupancy when the CV glass is degraded; conversely CV
misses huddles under wet glass** — use each to check the other, never assume they agree.

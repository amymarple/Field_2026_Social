# WISER artifacts — the concrete repo bridge

Maps the regime-aware discipline in `SKILL.md` onto the **real functions, columns, and thresholds**
in `wiser_tracking_analysis/`. Paths are relative to the repo root. Verified against the tree on
2026-07-06. Units are **inches** throughout (WISER native); 1 in = 2.54 cm.

## Signal → machine-readable source

| Signal | Source (function / file) | Notes |
|---|---|---|
| Fix quality | columns `anchors_used`, `calculation_error`, `battery_voltage`, `reportid` | preserved by the read-only loader; canonicalized in `wiser_analysis_utils.py` |
| Validity flags | `wiser_analysis_utils.add_validity_flags` | emits `low_anchor_flag` (anchors_used < `DEFAULT_MIN_ANCHORS=4`), `gap_flag`, and a jump flag; records `jitter_floor_in` in `df.attrs` |
| Jitter floor (precision) | `scripts/analyze_fixed_position_test.py`; `speed_noise_floor` / `grid_speed_noise_floor` | ~7 in median (18 cm), p95 ~15 in; ~3.7–3.9 Hz. Precision, **not** absolute accuracy |
| Jitter-suppressed speed | `add_speed` (centred rolling-median positions, `smooth_window`) | raw speed is jitter-inflated + bursty; smoothed speed above the noise floor = real locomotion |
| Proximity (with reliability) | `proximity_summary` (`PROXIMITY_THRESHOLDS_IN`) | marks results unreliable near the jitter floor; **keep thresholds ≥ 1 m** (sub-1 m is below the floor) |
| Occupancy / ROI | `assign_roi`, `occupancy_hist`; `configs/wiser_rois.json` | ROI membership uses a jitter buffer (~7 in median, p95 ~15 in) around the small ~36×27 in shelter footprint |
| Georeference (inch→physical) | `src/field_transform.py`; `load_field_transform` / `apply_field_transform` / `verified_boundary_in_wiser`; `configs/wiser_to_field_transform.json` | **transform file absent → all helpers are no-ops**; frame stays unverified inches |
| Weather | `load_weather`, `merge_activity_weather` | keeps `dewpoint_c`, `humidity`, `rain_rate_mmhr`; `attrs["alignment"] = "wall-clock UTC, unverified (~5 min)"` |
| Tag validity | `apply_tag_cutoffs`; `configs/rat_identities.csv` `valid_until` | Sova (`shortid 12409`) removed 2026-06-29 15:00 EDT; `shortid` ≠ animal |
| Per-night confounds | generated `night_covariates.csv` | `night`, `wet_ground`, `tunnel_present`, `sova_removed` |
| Structure changes | `data_manifests/2026-06-29-wiser-pilot.yaml` `time_varying_structures` | tunnel removed 2026-06-29 07:00 EDT (= 11:00 UTC) |

## Jitter floor & sampling — the numbers

- **Jitter floor ~7 in median (18 cm), p95 ~15 in** (from the stationary fixed-position test). The
  user's field estimate of ~4–6 in is the same order — treat **~4–7 in** as the working floor.
- **Sampling ~3.7–3.9 Hz, bursty (~0.28 s between samples).**
- `DEFAULT_MIN_ANCHORS = 4` (below → `low_anchor_flag`); some analyses filter harder (`anchors_used ≥ 6`).
- **Keep proximity / social-distance thresholds ≥ 1 m.** `PROXIMITY_THRESHOLDS_IN` starts at
  0.5 m (19.69 in) but sub-1 m is below the floor and unreliable.
- `follow_radius_in = max(3 × stationary jitter radius, min_r_in)` — close-following radius is scaled
  off the floor, never finer.

## Coordinate frame — the #1 spatial blocker

- Positions are **inches in an UNVERIFIED offset origin frame**. A raw unit conversion to cm does
  **not** align WISER to the physical CV field frame.
- `configs/wiser_to_field_transform.json` **does not exist yet**, so `load_field_transform` returns
  `None` (unless `allow_unconfirmed=True`), `apply_field_transform` passes positions through
  unchanged, and `verified_boundary_in_wiser` yields nothing. **No WISER position can be placed in
  the physical field** until a pole survey passes QC (`scripts/georeference_wiser.py`).
- **`wiser_rois.json` is confirmed *in the inch frame*** (per-ROI `confirmed: true` for houses,
  refuges, water, food, and the tunnel; boundary confirmed too). So ROI membership and inside-vs-open
  work — but that is **not** the same as the frame→physical transform. Directional/physical claims
  (wall-running, "the northeast refuge") remain unverified. *(Note: CLAUDE.md/ANALYSIS_STATUS.md may
  still say `wiser_rois.json confirmed=false` — the file has since been placed; the georeference
  transform is the part that is still missing.)*

## Weather ⟷ WISER (both paths)

Water attenuates UWB, so **rain / wet ground / wet materials raise dropout and noise** — the WISER
analog of fog on the CV glass. Weather also changes behavior. So weather is **never** a nuisance to
regress out: model it as a covariate acting on *both* the sensor and the animal. `merge_activity_weather`
joins hourly weather at "wall-clock UTC, unverified (~5 min)" — an unsynchronized covariate, not an
aligned signal. `night_covariates.csv` carries `wet_ground` per night.

## Signal-dropout regime (field knowledge — not yet in any structured file)

- The **bottom-right low-rank shelter is a ~1-inch-thick hay-wall refuge**. When the hay wall gets
  **wet / white**, the UWB signal for a tag inside can **stop** (water in the wall attenuates it).
- Effect: that rat's fixes disappear → **occupancy under-counts** and **"time outside" over-counts**
  for the low-rank animal, concentrated in wet weather. This is a **sensor dropout**, not the rat
  leaving the shelter — classify it as measurement artifact / lower-bound, never as behavior.
- **Gap:** there is no structured WISER-dropout log yet (no equivalent of `field_conditions.yaml` for
  UWB). Detect dropout empirically (gap fraction per tag per window via `gap_flag`) and cross-check
  against weather and the wet-hay-wall periods in `FIELD_OBSERVATIONS.md`.
- **TODO to confirm:** which named ROI in `wiser_rois.json` is the "bottom-right low-rank" shelter
  (candidates by high-x position: `house_2`, `refuge_2`, `refuge_4`) — confirm with the user / a
  georeferenced map before hard-coding it.

## ROIs present in `wiser_rois.json` (inch frame, confirmed)

`house_1`, `house_2` (rect refuges, ~36×27 in); `refuge_1..4` (circles); `water_1`; `food_2`;
`tunnel_1` (rect, `valid_until` 2026-06-29 07:00 EDT — gone after that). Boundary rect confirmed.
Do not attach compass/physical meaning to these until the georeference transform is confirmed.

## Known gaps you must handle

- **No confirmed georeference** → every physical/directional spatial claim is provisional (inches).
- **No structured UWB-dropout log** → quantify dropout yourself; the wet-hay-wall regime is field
  knowledge only.
- **`shortid` is a tag, not an animal** → always resolve via `rat_identities.csv` and apply cutoffs.
- **Social rank is not in any config** → the "low-rank shelter" is field knowledge, not a data column.
- **Live DB is a WAL writer** → every read must be strictly read-only (`mode=ro`,
  `PRAGMA query_only=ON`); loading both a CSV export and the SQLite of one session double-counts rows.

## Output-report template

```
Result:              <what was measured, e.g. Hypnos nightly time-outside, night 2026-06-30>
Time range:          2026-06-30 21:00 – 07-01 05:00 EDT
Tag(s):              shortid 12380 → Hypnos            # shortid ≠ animal
Jitter floor / rate: ~7 in median (p95 ~15 in); ~3.8 Hz
QC filters:          anchors_used ≥ 4; gap_flag/jump_flag applied
Frame status:        inches, UNVERIFIED offset (no georeference) — no physical placement
Spatial claim survives frame?  N/A (no directional claim) / or: NO until georeferenced
Dropout / gap:       <x%> of window missing; regime = rain + wet hay-wall shelter
Reliability:         proximity ≥1 m only; occupancy is a LOWER BOUND under dropout
Known failure modes: wet-hay-wall dropout under-counts shelter occupancy; sub-floor proximity
Behavior vs artifact: "time outside ↑" disappears once wet-hay-wall dropout windows are excluded
                      → measurement artifact, not a real excursion
Category:            (1) behavioral / (2) artifact / (3) mixed / (4) invalid–lower-bound-only
```

**Worked example (rainy night, low-rank rat):** on a wet night the low-rank rat's fixes in the
bottom-right hay-wall shelter drop out; naive occupancy shows it "spending the night outside." Before
claiming any rain→behavior effect, restrict to windows with acceptable `gap_flag`/anchor quality and
compare wet vs dry nights on **dropout-matched** data — the excursion typically vanishes, marking it a
**sensor dropout artifact** (category 2/4), not an animal-path response to rain.

# Route-structure analysis (9–11 pm pooled, candidate corridor/route use)

## Date

2026-06-29. Change is currently uncommitted.

## Plan

Implemented from
[`implementation_plan/2026-06-29-route-structure-analysis.md`](../implementation_plan/2026-06-29-route-structure-analysis.md).
Reuses the WISER pilot data manifest
([`data_manifests/2026-06-29-wiser-pilot.yaml`](../data_manifests/2026-06-29-wiser-pilot.yaml)).

## What changed

- `wiser_tracking_analysis/src/wiser_analysis_utils.py` — new "Route-structure" section:
  `select_route_window` (pooled local-time trunk + `night` tag), `corridor_mask` (smoothed occupancy
  ≥ percentile), `skeletonize_mask` (numpy **Zhang–Suen** thinning — skimage is not in the env),
  `route_reuse_index` (self-concentration / corridor-adherence / entropy),
  `occupancy_similarity_loo` (leave-one-out cosine+corr), `movement_bouts` + `straightness_summary`
  (straightness = displacement / path length), `per_tag_transitions` (node visits over **named**
  ROIs, skipping open/edge so separated ROIs connect), `edge_usage_similarity` (cross-rat cosine +
  Jaccard + shared-edge table), `route_robustness` (stricter-QC straightness + corridor IoU),
  `baseline_route_compare` (geometry-artifact check vs the stationary baseline), and plots `RS1…RS8`.
  `make_output_dir` gained a `prefix` arg.
- `wiser_tracking_analysis/scripts/analyze_route_structure.py` — thin driver (default 9–11 pm EDT,
  all dates → pooled trunk; `--clock-start/--clock-end/--dates`). Cleaned points only; writes CSVs +
  figures + manifest + verdict to `D:\Wiser_plot\route_structure_*`. Read-only on the source DBs.

## Why

We had occupancy and a temporal leader-follower analysis, but no test of whether rats reuse the same
**spatial** corridors/routes — and, crucially, no check that apparent routes are not WISER
anchor-geometry artifacts. This adds that, QC-gated.

## Source data used for verification

Read **read-only**: `D:\Wiser\data\1stcohort_2026.sqlite` (free-moving, live), `tag_reports.sqlite`
(stationary baseline → jitter floor + moving threshold), `configs/wiser_rois.json`,
`configs/fixed_position_ground_truth.csv`. No new raw data.

## Verification performed

conda env `cv`: `python -m py_compile …`; `python scripts/analyze_route_structure.py`. Observed:
- Window: **296,801 cleaned fixes**, nights 2026-06-28 + 2026-06-29; Sova/12409 present night 1 only
  (deceased, cutoff applied). Jitter floor 7.0 in; moving threshold 12.5 in/s.
- Corridor mask + skeleton extracted (skeleton ⊂ mask). Per-rat `self_concentration` ≈ 0.88,
  `corridor_adherence` ≈ 0.67–0.73 (Sova 0.99 / 0.91 — barely moving near death). LOO occupancy
  similarity ≈ 0.61–0.70 cosine (Sova 0.10 — used different space).
- Bout straightness 0.86–0.95, **but the stationary-jitter baseline median is 0.97 ≥ free → the
  geometry-artifact flag fires**: straightness alone is NOT a trustworthy route cue here.
- Robustness: straightness ≈ 0.91 and corridor-mask IoU 0.92–1.00 across `anchors≥6` / `calc_err≤p50`
  / in-bounds → the occupancy/corridor **structure is robust to QC**, even though straightness is
  confounded.
- Transition graph: all 6 rats share the top route edges (house↔house, house↔refuge), high edge-usage
  cosine → different rats use the same edges.
- **Per-night corridor IoU = 0.27** → corridors are only weakly consistent across the two nights.
- All 8 figures (RS1–RS8) + CSVs + `route_verdict.txt` written.

## Conclusion (candidate)

Rats concentrate in shared high-occupancy corridors (robust to QC) and traverse the same ROI edges,
but (a) bout **straightness is confounded by WISER geometry** (stationary baseline is as straight),
and (b) corridors are only ~27% consistent night-to-night. So: **candidate route structure / shared
corridor use**, not confirmed stable routes. Fine route geometry needs the cm-scale CV pipeline.

## Update (2026-06-30) — straightness jitter null, self route reuse, edge effect

Three follow-ups from field review. New functions in `wiser_analysis_utils.py`
(`jitter_straightness_null`, `straightness_vs_null_summary`, `self_route_reuse`, `_edge_vec_cosine`,
`thigmotaxis_index`, `add_edge_distance`, `edge_band_cell_mask`, `interior_route_summary`) + plots
`RS9–RS11`, wired into the driver (CSVs `straightness_vs_jitter.csv`, `self_route_reuse.csv`,
`thigmotaxis.csv`).

- **Straightness jitter null (RS9) — revises the earlier verdict.** The n≈6 "moving" stationary bouts
  were too few/spread to threshold (a tag relocation reads ~1.0, a jitter wobble ~0.1 → the wide
  baseline box). Replacing it with a **displacement-matched** null (slide random windows over the
  stationary tags, n=600): jitter straightness is spread only at net displacement < ~25 in, and the
  stationary tags never produce a bout beyond ~15 in. **100% of free bouts occur at displacements >
  15 in, where jitter has zero bouts** → the high free straightness (0.65–0.95) is **real locomotion,
  not geometry**. The blunt baseline-median flag (kept as `baseline_median_straightness_flag`) was a
  small-displacement artifact; `geometry_artifact_risk` is now the displacement-matched result (False).
- **Self route reuse / memory (RS10).** Per-rat night-1-vs-night-2 occupancy + own-edge cosine.
  Cross-rat edge similarity (**0.88**) *exceeds* within-rat night-to-night route similarity
  (**0.13–0.77, mean 0.35**) → the shared corridors are **environment-driven, with little evidence of
  individual route memory**; Siesta (0.77) is the most self-consistent. (Caveat: cross-rat is over the
  pooled window, self is across the night gap.)
- **Edge effect / thigmotaxis (RS11).** Per-rat fraction of fixes within 12 in of the confirmed
  boundary is **~0.4%** (median dist-to-wall 40–79 in), and **0% of the corridor mask is perimeter** →
  corridors are **interior**, so wall-running does not confound the route structure here. (The WISER
  boundary is unverified as the physical wall.)

Net: corridors/edges are shared and robust, **straightness is real movement** (not the geometry
artifact the first pass implied), but the reuse is shared-environment-driven (not individual memory)
and only ~27% consistent night-to-night → still **candidate** route structure.

## Update (2026-06-30b) — user-drawn exclude region for the edge effect

The rectangular WISER boundary is not the true physical wall, so the band-based thigmotaxis
under-counts edge use (0.4%). Added a way to mark the real edge region by eye:
- `scripts/place_exclude_region.py` — a matplotlib (TkAgg) GUI that draws polygon(s) over the
  all-rats 9-11 pm scatter (boundary + 50-in grid) and writes `configs/wiser_exclude.json`
  (read-only on data; mirrors `place_wiser_rois.py`'s backend handling).
- `wiser_analysis_utils.py`: `load_exclude_regions`, `points_in_polygons` (matplotlib `Path`),
  `region_cell_mask`, `edge_mask_points`; `thigmotaxis_index` and `interior_route_summary` now take
  `regions=` and use polygon membership when present (else the boundary band, unchanged default).
  The driver loads `--exclude` (default `configs/wiser_exclude.json`) and reports the edge zone used.
- Verified headless: a test left-edge polygon gives per-rat thigmotaxis 0.16-0.24 and a 0.72
  interior-vs-full corridor IoU; save/load round-trips; with no exclude file the driver falls back to
  the 12-in band (unchanged). The GUI needs a display (run on the field PC).

## Known limitations / next steps

- WISER ~7 in jitter and unverified frame cap fine route geometry; straightness path-length is
  jitter-inflated (conservative). Co-located food/house ROIs produce trivial self-edges.
- Stationary baseline has few bouts (n≈6) so its 0.97 straightness is a coarse reference, but the
  caution it raises is real.
- Outputs to `D:\Wiser_plot\route_structure_*` (off C:, off git); nothing written under `D:\Wiser`.

# Route-structure analysis (9–11 pm pooled, candidate corridor/route use)

## Goal

On the **9–11 pm EDT block pooled across both nights** (whole trunk, not hour-binned), determine
whether the rats reuse the same corridors/routes — and whether any apparent route is real or a WISER
anchor-geometry artifact. Spatial-structure complement to the leader-follower (temporal) analysis.
Every route claim is **candidate** and cross-checked against the stationary baseline + jitter floor.

## Approach (units = inches)

Logic in `src/wiser_analysis_utils.py` ("Route-structure" section); thin driver
`scripts/analyze_route_structure.py` → timestamped `D:\Wiser_plot\route_structure_*` (read-only on
source). Cleaned points = `valid==True` after `add_validity_flags` + `apply_tag_cutoffs` (dead Sova
excluded from night 2). Reuses `occupancy_hist`, `_box_blur`, `assign_roi`, `load_rois`,
`observed_extent`, `speed_noise_floor` (moving threshold), `plotting._tag_colors/_save_or_show`.

Steps → functions:
1. `select_route_window(clock_start=21, clock_end=23)` — pooled trunk + `night` tag.
2. group + per-rat `occupancy_hist` (4-in bins, `_box_blur`).
3. `corridor_mask` (smoothed occupancy ≥ p80) + `skeletonize_mask` (numpy **Zhang–Suen**; no skimage).
4. `route_reuse_index` — `self_concentration`, `corridor_adherence`, occupancy entropy.
5. `occupancy_similarity_loo` — each rat vs the summed map of the others (cosine + corr).
6. `movement_bouts` (moving > stationary-speed p99 ≈ 12.5 in/s) + `straightness = disp/path`;
   `straightness_summary`.
7. `per_tag_transitions` — node visits over **named** ROIs, skipping `open`/`edge` (so separated
   ROIs connect); nodes = user ROIs (`wiser_rois.json`), `infer_candidate_zones` fallback.
8. `edge_usage_similarity` — cross-rat edge-weight cosine + Jaccard; shared-edge table.
9. `route_robustness` — straightness + corridor mask under `anchors_used≥6`, `calc_error≤p50`,
   in-bounds; corridor-mask IoU vs base.
10. `baseline_route_compare` — occupancy + straightness on the stationary baseline; fires a
    geometry-artifact flag if stationary straightness ≥ free.

Plots `RS1…RS8`: corridor map (mask+skeleton), per-rat occupancy, route-reuse bars, LOO similarity,
straightness boxplots (+baseline line), shared-edge graph, edge-usage heatmap, baseline comparison.
CSVs: `route_reuse_index`, `occupancy_similarity`, `straightness_summary`, `straightness_robustness`,
`edge_usage_similarity` + `shared_edges`, `baseline_comparison`; `run_manifest.json` + `route_verdict.txt`.

## Interpretation guardrails

Corridors/routes are **candidate**, not confirmed. Compare straightness/corridor scale to the ~7 in
jitter floor and the stationary baseline; if the baseline shows comparable structure, attribute to
WISER anchor geometry, not behavior. WISER frame unverified vs the paddock.

## Verification

`python -m py_compile`; `python scripts/analyze_route_structure.py`. Confirm window counts, all CSVs +
RS1–RS8 non-empty, `0 ≤ self_concentration ≤ 1`, straightness ∈ (0,1], skeleton ⊂ mask, robustness
IoU reported, baseline flag computed. Read-only on `D:\Wiser\data`.

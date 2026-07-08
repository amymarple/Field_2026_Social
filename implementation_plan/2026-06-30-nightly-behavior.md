# Nightly 9pm–12am behavior & social, 6/28–6/30 — home, exploration, cohesion, graph, geometry

## Goal

Extend the 3-night paired (5-rat, Sova removed) nightly framework beyond raw movement to answer:
do rats use home/shelter more, explore out less, change outside movement, stay socially the same
(shared space + move-together), and does their exploration-graph structure and space-use geometry
change across nights?

## Approach (reuse-heavy)

New driver `scripts/analyze_nightly_behavior.py` (same window setup as
`analyze_nightly_progression.py`). Output → `D:\Wiser_plot\nightly_behavior_YYYYMMDD_HHMM\`. New
helpers in `src/wiser_analysis_utils.py`:
- `nightly_roi_use` — per tag×night time fractions by category (home=type `refuge`, resource=water/
  food, tunnel [6/28 only], open) + home↔open transition rate/valid-hr.
- `nightly_movement_by_cat` — active-distance rate + active fraction on **open** fixes only.
- `nightly_social` — cohesion (mean pairwise distance, ≤0.5/1/2 m fractions with jitter-floor
  reliability, clustering) + leave-one-out occupancy similarity (shared space).
- `nightly_graph_structure` — per-night ROI transition network (edges/nodes/density/hub) + night-to-
  night edge-usage cosine/Jaccard.
- `nightly_geometry` — coverage, concentration, dispersion, corridor/skeleton cells.
Reuses `assign_roi`, `per_tag_transitions`, `roi_time_and_transitions`, `resample_common_grid`,
`pairwise_distances`, `proximity_summary`, `clustering_index`, `occupancy_similarity_loo`,
`occupancy_hist`, `corridor_mask`, `skeletonize_mask`, `plot_roi_transition_graph`,
`plot_corridor_map`. New plots: `plot_nightly_paired`, `plot_nightly_timebudget`, `plot_nightly_lines`.

## Deliverables

CSVs: `nightly_roi_use.csv`, `nightly_movement_by_cat.csv`, `nightly_social.csv`,
`nightly_graph_structure.csv`, `nightly_graph_similarity.csv`, `nightly_geometry.csv`,
`nightly_behavior_conclusion.txt`, `run_manifest.json`. Figures: B1 home fraction (paired), B2 time-
budget stacked, B3 home↔open transitions, B4 outside movement, B5 cohesion, B6 shared-space+proximity,
B7 graph size + per-night transition graphs, B8 geometry metrics, B9 per-night corridor maps.

## Interpretation guardrails

Exploratory/candidate (3 nights, n=5 paired). 6/30 wet-ground confounded with habituation; tunnel 6/28
only (home reported without it); ≤1 m proximity reliable (7 in floor ≪ 1 m), finer flagged; distances
jitter-inflated (paired/relative); WISER frame unverified; Sova removed.

## Verification

conda `cv`: `py_compile`; run driver → read-only; 5 rats × 3 nights; home/resource fractions match the
preview; graph edge-cosine 0.50→0.97; each B-figure renders; no writes under `D:\Wiser`.

# Implementation Plans

This directory records non-trivial implementation plans before source changes.

| Date | Plan | Scope |
|---|---|---|
| 2026-06-25 | [Daily Recording Continuity Check](2026-06-25-daily-recording-continuity-check.md) | Daily QC audit for Reolink, thermal, and SmartPSS recording activity. |
| 2026-06-28 | [Hourly WISER Occupancy Maps](2026-06-28-hourly-occupancy-maps.md) | Live-DB-safe hourly per-tag occupancy heatmaps from WISER UWB tracking. |
| 2026-06-29 | [WISER UWB Pilot Analysis Pipeline](2026-06-29-wiser-pilot-analysis.md) | QC-first pilot pipeline: usability, jitter, dropouts/jumps, spatial/social/refuge/acclimation + weather. |
| 2026-06-29 | [Leader-follower / route-following analysis](2026-06-29-leader-follower-analysis.md) | Time-lagged path-reuse following with circular-shift null controls; candidate route-following. |
| 2026-06-29 | [Route-structure analysis](2026-06-29-route-structure-analysis.md) | 9–11 pm pooled corridor/skeleton + route-reuse + straightness with stationary-baseline artifact check. |
| 2026-06-29 | [Recording-Stall Slack Alert](2026-06-29-recording-stall-alert.md) | Near-real-time watchdog: Slack alert if any Reolink/thermal stream stops growing. |
| 2026-06-29 | [Environmental-Audio Feature Pipeline](2026-06-29-environmental-audio-pipeline.md) | Lightweight, resumable extraction of relative camera-mic level + band-limited soundscape indices (CH01/CH02) to tidy CSVs. |
| 2026-06-30 | [Daily WISER backup](2026-06-30-wiser-daily-backup.md) | Once-a-day SQLite snapshot + gzipped incremental CSV to E:; live DB read exactly once per run. |
| 2026-07-01 | [Shelter glass-degradation zones](2026-07-01-glass-degradation-zones.md) | CH05/CH06 IR-glass view: zone-based (inside/doorway/outside) + per-zone view_quality + glass-noise-resistant motion so weather artifacts never count as rat activity (Phase A). |
| 2026-06-30 | [Nightly 9pm–12am movement (habituation vs rain)](2026-06-30-nightly-progression.md) | Rate-normalized paired (5 rats) nocturnal movement across 6/28–6/30; primary metric active-m/valid-hr; rain difference-in-differences. |
| 2026-06-30 | [Nightly 9pm–12am behavior & social](2026-06-30-nightly-behavior.md) | Home/shelter use, exploration transitions, outside movement, cohesion + shared-space, exploration-graph structure, geometry across 6/28–6/30. |
| 2026-07-01 | [Audio Phase 2 — staged downstream analysis](2026-07-01-audio-phase2-staged-analysis.md) | DESIGN ONLY: diurnal/spectrogram summaries + audio↔WISER↔weather merge (unverified alignment) + event detection, fed by Phase-1 CSVs. |
| 2026-07-01 | [Audio extractor overlap-dedup](2026-07-01-audio-overlap-dedup.md) | Drop fully-nested duplicate segments in `find_recording_files` so copied-back NVR backfill overlapping RTSP files can't double-count windows. |
| 2026-07-01 | [WISER frame georeferencing](2026-07-01-wiser-georeferencing.md) | WISER-inch→field-cm similarity transform (Umeyama) from a surveyed pole dwell; robust to ~7 in jitter; tooling + self-test now, confirmed transform awaits field survey. |
| 2026-07-02 | [Direction 3 — daytime sleep-site](2026-07-02-daytime-sleep-site.md) | Per-animal daytime (05:00–21:00) rest-site + within-day drift + across-day stability; low-speed sleep proxy; new utils + driver + self-test. |
| 2026-07-02 | [Sleep-site WISER↔CV cross-validation](2026-07-02-sleep-site-cv-crossval.md) | Agreement of WISER shelter-ROI presence vs CV shelter occupancy (CH05/CH06); lag-scan + mapping test; unverified alignment, occupancy-boolean headline. |
| 2026-07-02 | [Episode Browser](2026-07-02-episode-browser.md) | New `episode_browser/`: versioned state-model registry + episode schema (Parquet/JSONL), messy synthetic generator (4 levels + pathologies), UI-free data layer, and a light Streamlit browser (coverage/gaps + blind-eval). Prototype on synthetic data. |
| 2026-07-03 | [WISER shelter occupancy as a smoothed state](2026-07-03-wiser-shelter-occupancy-state.md) | Replace raw point-wise ROI with a hysteretic, buffer-tolerant shelter STATE + high-confidence episodes; reframe the CV cross-val WISER-as-reference (CV precision + recall lower-bound; κ demoted to a base-rate-warned alignment diagnostic). Interpretation finalized in the 2026-07-06 reconciliation. |
| 2026-07-07 | [Trajectory stereotypy, stabilization & inter-animal correlation](2026-07-07-trajectory-stereotypy.md) | Per-day stabilization of night-window space-use across 06-28→07-05 + shared-road controls (pooled corridor + residual maps, label-permutation, circular-shift/day-shuffle time-coupling). Phase A ships an intermediate report; DTW/Fréchet motifs + follow-lag are Phase B. |
| 2026-07-07 | [Direction 3 — tiered relocation + within-day temperature relocation](2026-07-07-direction3-temperature-relocation.md) | Stage A: tiered across-day relocation labels (stable/marginal/borderline/robust/major-shelter-switch) replacing the "8/10 relocated" cut. Stage B: within-day rest-bout/window-site sequence + relocation events + AWN weather alignment + per-animal-day dropout guard; per-day timeline + convergence figures + report. Candidate / measurement-limited. |

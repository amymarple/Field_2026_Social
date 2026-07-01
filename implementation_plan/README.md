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

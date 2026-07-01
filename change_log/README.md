# Change Log

This directory records completed non-trivial implementation changes and their
verification.

| Date | Change | Scope |
|---|---|---|
| 2026-06-25 | [Daily Recording Continuity Check](2026-06-25-daily-recording-continuity-check.md) | Daily QC audit for 24/7 recording continuity. |
| 2026-06-28 | [Hourly WISER Occupancy / Position Maps](2026-06-28-hourly-occupancy-maps.md) | Live-DB-safe hourly per-tag scatter/occupancy maps from WISER UWB. |
| 2026-06-29 | [WISER UWB Pilot Analysis Pipeline](2026-06-29-wiser-pilot-analysis.md) | QC-first pilot: usability/jitter/dropouts/jumps + spatial/social/refuge/acclimation + weather; notebook + utils module + ROI GUI. |
| 2026-06-29 | [Leader-follower / route-following analysis](2026-06-29-leader-follower-analysis.md) | Time-lagged path-reuse following (not proximity) with circular-shift null controls; candidate route-following. |
| 2026-06-29 | [Route-structure analysis](2026-06-29-route-structure-analysis.md) | 9–11 pm pooled corridor/skeleton, route-reuse, straightness + baseline geometry-artifact check; candidate route structure. |
| 2026-06-29 | [NVR IP change — recording gap and recorder repoint](2026-06-29-nvr-ip-change-recording-gap.md) | NVR IP moved .151→.163 (channel reconfig); ~2h46m CH01–06 gap; config repointed; CH01/02 mics reset & re-enabled. |
| 2026-06-29 | [Recording-stall Slack alert](2026-06-29-recording-stall-alert.md) | Near-real-time watchdog: Slack-alerts (channel + DM) if any Reolink/thermal stream stops growing. Install pending (elevated). |
| 2026-06-29 | [Environmental-audio feature pipeline](2026-06-29-environmental-audio-pipeline.md) | New `audio_analysis/`: lightweight resumable extraction of relative camera-mic level + soundscape indices (CH01/CH02) to tidy CSVs. |
| 2026-06-30 | [Daily WISER backup](2026-06-30-wiser-daily-backup.md) | Once-a-day SQLite snapshot + gzipped incremental CSV to E:; reads the live DB exactly once (snapshot-first). |
| 2026-06-30 | [Thermal cam 109 visual 30→1 fps](2026-06-30-thermal-visual-framerate.md) | Data-volume fix: 109_visual was the rig's largest stream (~84 GB/day at 30 fps); dropped to 1 fps (~13 GB/day, ~71 GB/day saved). Applied live, no recorder restart. |
| 2026-07-01 | [Shelter CV glass-degradation zones](2026-07-01-glass-degradation-zones.md) | CH05/CH06 view through IR glass: zone-based (inside/doorway/outside) + per-zone view_quality (clear/degraded/unusable) + glass-noise-resistant motion so rain/fog/glare never count as rat activity. Phase A. |
| 2026-06-30 | [Nightly 9pm–12am movement (habituation vs rain)](2026-06-30-nightly-progression.md) | Rate-normalized paired (5 rats) nocturnal movement across 6/28–6/30; candidate habituation (−50% night 1→2) + rain DiD; 6/30 wet-ground confound. |
| 2026-06-30 | [Nightly 9pm–12am behavior & social](2026-06-30-nightly-behavior.md) | Home/shelter use, exploration transitions, outside movement, social cohesion + shared-space, exploration-graph structure, space-use geometry across 6/28–6/30 (5 rats). |

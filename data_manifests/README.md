# Data Manifests

Per-run field-data manifests (sources, clocks, units, excluded intervals, coordinate frame,
alignment notes). See `AGENTS.md` → "Data Manifest Requirements".

| Date | Run | Modalities |
|---|---|---|
| 2026-06-29 | [WISER pilot](2026-06-29-wiser-pilot.yaml) | WISER UWB (free-moving + stationary baseline) + AWN weather |
| 2026-06-29 | [Camera audio](2026-06-29-camera-audio.yaml) | CH01/CH02 ambient audio — relative camera-mic dBFS + soundscape indices (not SPL) |
| ongoing | [Field conditions](field_conditions.yaml) | Weather/visibility windows (fog, rain) to exclude/caveat in video + audio analysis |

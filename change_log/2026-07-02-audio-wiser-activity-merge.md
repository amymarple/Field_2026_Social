# Audio Phase 2 — rain-aware biophony + multi-day panel with WISER rat-activity

## Date

2026-07-02. Currently uncommitted.

## Plan

Continues `implementation_plan/2026-07-01-audio-phase2-staged-analysis.md` and the session plan
`C:\Users\Cornell\.claude\plans\check-audio-process-and-keen-simon.md`. Two user-approved follow-ups
to the soundscape+weather panel: (a) make the "biophony likely" shading reject weather; (b) add the
WISER-occupancy merge and multi-day support.

## What changed

**(a) Rain-/broadband-aware biophony heuristic** — `audio_analysis/src/plotting.py`:
- New reusable `biophony_active(bird, ambient, rain=None)` + `BROADBAND_GUARD_DB = 6.0`. A bin is
  biophony-active only when the 2–8 kHz band exceeds its own night floor by `BIOPHONY_MARGIN_DB`
  **and** rises more than the 0–1 kHz ambient band by `BROADBAND_GUARD_DB` (birds lift only 2–8 kHz;
  wind/rain lift both). When a `rain` series is passed, logged-rain bins are additionally suppressed.
- `plot_bird_vs_ambient` now calls it (broadband guard only — no weather in that plot).
- `scripts/selftest_features.py` — 3 offline cases added: birds (bird up, ambient flat) → active;
  broadband (both rise) → rejected; rain>0 → suppressed. **Self-test PASS.**

**(b) WISER rat-activity merge + multi-day panel:**
- New `audio_analysis/analysis/wiser_activity.py` — `hourly_rat_activity(dates)` reuses the
  `wiser_tracking_analysis` pipeline **read-only** (`load_wiser_session` → `convert_timestamps` →
  `add_speed` → `add_validity_flags` → `hourly_activity`), drops tag `12409` (Sova, deceased
  6/29 ~15:00), and returns a per-**local-hour** frame (`ts_local`, `active_distance_m`,
  `active_frac`, `n_fixes`). WISER time is UTC → shifted −4 h to local to line up with audio/weather.
  Auto-picks the newest full-DB snapshot under `D:\Reolink_record\audio_in\Wiser_backup\snapshots\`.
- `scripts/plot_soundscape_day.py` — now accepts `--dates a,b` (multi-day) alongside `--date`,
  concatenates each day's feature CSV, uses the rain-aware shading, and adds a **4th panel: WISER rat
  active-fraction (bars) + active metres/h (line)**. Single-day usage still works; `--no-wiser` skips
  the (slow) WISER load. Output `<ch>_<start>_to_<end>_soundscape_panel.png`.

## Why

The earlier shading false-positived on the 6/30 17:00 rain shower (rain has 2–8 kHz energy). The
broadband guard + rain suppression fix that. Adding WISER answers "do the rats move differently in the
windy/rainy/loud windows?" — the point of the cross-modal panel.

## Verification (CH01, 2026-06-29 + 2026-06-30)

- `selftest_features.py` → **PASS** (incl. the 3 biophony cases).
- Standalone `bird_vs_ambient` (6/30): shading is now patchy — marked at dawn and calm bird lulls,
  **not** during the windy midday peaks or the ~17:30 rain spike (previously one solid block).
- Multi-day 4-panel renders over 6/29→6/30; the 17:00 rain span is suppressed in the biophony shading.
- **WISER activity is populated for both days** (~69k fixes/hr ⇒ 5 rats, Sova dropped), aligned on
  local wallclock. Read-only load (`mode=ro` + `PRAGMA query_only`); nothing written under the backup.

## Finding (candidate; alignment UNVERIFIED)

**Rats and birds run on inverse cycles.** WISER rat active-fraction peaks at night
(≈0.11–0.14 around 21:00–02:00) and troughs during the day (≈0.01–0.02, 11:00–19:00); the soundscape
biophony (birds) is a daytime/dawn phenomenon. During the windy 6/30 midday and the 17:00 rain shower
the rats are already at their daily activity minimum (daytime rest), so no rat-activity response to
that weather is visible here — but this is a 2-day pilot with unverified cross-device clocks, so treat
it as candidate, not a circadian result.

## Known limitations

- **Three device clocks** (camera/NVR, WISER computer, AWN station); alignment is timestamp-only and
  **UNVERIFIED** (−4 h EDT assumed, no DST change in-window). Labeled on every figure.
- WISER "activity" = above-noise-floor movement in **WISER inches** (default 12 in/s threshold), **no
  georeference / spatial claim**. Snapshot coverage ends ~6/30 23:30 local (last ~30 min of 6/30 absent).
- Biophony shading remains a heuristic, not a validated bird detector (camera mic, ≤8 kHz, rel. dBFS).
- pandas 3.0.x in the `audio` env: avoid `Series.corr()` (native crash observed); not used by shipped code.

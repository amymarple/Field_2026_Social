# Shelter CV: Weather/Glass-Degradation-Aware, Zone-Based Occupancy (Stage 3.5, Phase A)

## Date

2026-07-01. Uncommitted; CV-only change (no capture/recorder code touched).

## Summary

CH05/CH06 image the rats **through an IR-transmitting window**, so rain, condensation/fog, water
drips, and sun glare land on the glass between the lens and the animals. The prior `shelter_sleep.py`
drove its state off a raw ROI grayscale `mean-abs-diff`, which those artifacts spike → **false
`occupied-high-motion`** (and fog flattens contrast → false `low-motion`/`empty`). This change makes
the shelter pipeline **view-quality aware** so weather/glass artifacts can never be counted as rat
activity. It is **Phase A** (the conservative core); smart discrimination is deferred (see below).

Key behaviors added:
- **Zones** per camera (`inside_shelter` / `doorway` / `outside_surrounding`), drawn once in a new
  GUI, so the pipeline trusts detection where the view is open-air-clear and falls back inside.
- **Per-zone `view_quality`** in 3 tiers `clear / degraded / unusable` (exposure/glare/dark thresholds
  ported from `reolink_record/overexposure_check.ps1`, plus Laplacian-variance sharpness → fog, and
  contrast).
- **Glass-noise-resistant inside motion** (`robust_inside_motion`): illumination-normalize → temporal
  median background → keep only DARK moving blobs (rat body/stripe) → morphological open → rat-sized
  blob area. Rain (bright speckle), glare/AE (global shift), and static drips all score ~0.
- **Conservative state fusion:** a **degraded** inside-glass bin can never become
  `occupied_high_motion`; an **unusable** bin is `indeterminate`. Raw per-zone YOLO counts are kept as
  evidence, never overwritten by the fused occupancy estimate.
- **Weather-log cross-check:** logged windows in `data_manifests/field_conditions.yaml` force a bin to
  `≥ degraded` (`weather_logged=true`).

## What Changed

New files (`preprocessing/computer_vision/`):
- `view_quality.py` — GPU-free primitives: `view_quality()`, `robust_inside_motion()`, zone loaders,
  a `field_conditions.yaml` loader, and an offline `--selftest`.
- `place_zones.py` — OpenCV editor to draw the 3 zones on `configs/CHxx_reference.png` →
  `configs/CHxx_zones.json` (`inside` pre-loaded from the calibration shelter quad).
- `configs/view_quality.yaml` — tunable thresholds.

Modified:
- `shelter_sleep.py` — zone-/view-aware rewrite of `analyze_channel`: burst sampling, per-zone
  view_quality, robust motion, zone-assigned raw counts, conservative `_fuse()` state, new CSV schema,
  timeline with a hatched degraded-view band + occupancy line, split (headline vs coarse) summary,
  weather cross-check. States renamed to underscore form and `indeterminate` added.
- `validate_shelter.py` — scores the SAME pipeline (zone-inside count + robust motion + view + state);
  report is stratified by view_quality and includes the **safety check** that degraded/unusable
  samples are never scored `occupied_high_motion`.
- `README_cv.md` (Stage 3.5 section) and `CLAUDE.md` (IR-glass one-liner + hard rule).

New per-bin CSV schema: `channel, t, file, view_quality_inside, view_quality_doorway,
n_detected_inside, n_detected_doorway, n_detected_outside_near_shelter, inside_motion_score,
n_inside_estimated, n_inside_confidence, state, weather_logged, usable_for_headline_summary,
usable_for_coarse_activity`.

## Verification

Offline (no field data / GPU / disk), in the `cv` conda env:
- `python view_quality.py --selftest` → **PASS**. Classifies synthetic clear/fog/glare/near-black to
  the right tier; robust motion fires on a moving dark blob (score 10.3) but scores **0.0** for rain
  bright-speckle, a global AE brightness shift, and a stationary rat (rest).
- Fusion/state + weather cross-check logic test → **PASS**. Load-bearing case confirmed:
  `degraded + high motion` stays `occupied_low_motion` (never high); `unusable` → `indeterminate`;
  `in_degraded_window` correctly matches the logged CH05 fog / all-channel rain / open-ended fog
  windows for 2026-06-30 and rejects off-window/off-day/off-channel times.
- Real `data_manifests/field_conditions.yaml` parses via the loader (4 windows: fog/rain/rain/fog).
- All five modules byte-compile; PyYAML confirmed present in the `cv` env.

## Real-run verification (2026-07-01, CH05/CH06 hour 03)

Ran `shelter_sleep.py --date 2026-06-30 --hours 3` (the logged CH05 fog window 03:00–07:00):
- **Crash fixed.** `IMREAD_GRAYSCALE` returned `(H,W,1)` (singleton channel axis) under the full run
  with ultralytics loaded — 3-D, so the `mh,mw = shape` unpack and `cvtColor` both failed. Added
  `read_gray()` to normalize `(H,W)`/`(H,W,1)`/`(H,W,3)` → contiguous 2-D. Used in shelter_sleep +
  validate_shelter.
- **Primary bar holds on real footage:** CH05 → `view_quality_inside=degraded` on all 80 bins,
  `weather_logged=true`, **0 `occupied_high_motion`** (75 empty, 5 coarse low-motion). CH06 (not
  logged) → clear, normal night states (59 empty, 21 low-motion), 0 high-motion.
- **Auto view-quality CALIBRATED on real frames (2026-07-01).** Initially, CH05 hour 3 with the
  weather log disabled rated **clear 100%** + 1 false high-motion — the synthetic-tuned thresholds
  were blind to real fog. Added a `--probe` mode to `view_quality.py` (samples an hour's closed
  frames, prints inside-ROI metric distributions, no detector) and measured fog (hours 4–5) vs
  cleared-morning (hours 9–10) for both cameras. **Fog-on-glass BRIGHTENS the ROI (IR backscatter)**
  with a clean per-channel gap (CH05 clear mean ≤114 vs fog ≥129; CH06 clear ≤106 vs fog ≥115);
  sharpness also drops on CH05. (Two synthetic assumptions were wrong: real Laplacian variance is
  ~1000× higher, and fog *raises* contrast here, not lowers it.) Added a per-channel fog rule
  (`mean ≥ fog_mean AND sharpness ≤ fog_sharpness`) with support for a `channels:` block in
  `view_quality.yaml`; `load_config(path, channel)` and the pipeline now load thresholds per camera.
  Set CH05 `fog_mean 120 / fog_sharpness 30000`, CH06 `110 / 55000`.
  **Verified (weather log OFF):** fog hours 4–5 → 100% degraded on both cams; clear hours 9–10 →
  100% clear on both; the full pipeline on CH05 hour 3 now auto-flags degraded with **0 high-motion**
  (was clear + 1 false high-motion). The weather log remains a backstop for windows the metrics miss.
  Thresholds are calibrated on one fog event — re-`--probe` and adjust if a later fog looks different.

## Pending (field-run, needs the user)

- Draw zones: `python place_zones.py --channel CH05` (and CH06) → `configs/CHxx_zones.json`.
- Dry-run hours spanning a logged window: `python shelter_sleep.py --date 2026-06-30 --hours 3 4 5`
  (fog) and `--hours 17` (rain) → confirm those bins are `degraded`/`unusable`, `weather_logged=true`,
  and NEVER `occupied_high_motion`; clear midday hours still produce normal states.
- `python validate_shelter.py --date 2026-06-30 --n 60` → the SAFETY line should read PASS; fold the
  count/presence/motion figures (clear bins) into the accuracy report and tune `motion_thresh` /
  `view_quality.yaml` if the suggested threshold differs.

## Known Limitations & Follow-ups (Phase B, deferred)

- Phase A uses only the `inside_shelter` region, which `shelter_sleep` loads from the calibration
  shelter quad automatically — so drawing zones is optional. These are top-down views with **2 doors**;
  `place_zones.py` stores each door as a 2-point **gate line** (`doors` in the zones JSON) for the
  deferred Phase-B entry/exit counting (the old doorway/outside polygons were dropped — a doorway is a
  crossing line, not a region, and "outside" was just "not inside").
- `view_quality.yaml` sharpness/contrast thresholds are content-dependent defaults; tune per camera on
  real clear-vs-foggy frames (the printed metrics help).
- Deferred to Phase B: fog/rain/glare **type** classification + dark-stripe/implant-vs-drip channel;
  optical-flow (Farneback) coherence; **directional entry/exit occupancy accounting** via the 2 door
  gate-lines (count a rat track crossing a gate as one in/out to estimate occupancy when the inside
  glass is fogged — needs denser sampling than the Phase-A ~45 s cadence near transitions); optional
  detector fine-tune on degraded-glass frames.

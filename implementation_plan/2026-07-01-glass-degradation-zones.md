# Weather/Glass-Degradation-Aware, Zone-Based Shelter Analysis (Stage 3.5, Phase A)

Records the plan before source changes; see the session plan for full detail
(`~/.claude/plans/partitioned-tinkering-lake.md`, Stage 3.5).

## Context / Why

CH05/CH06 are security cams mounted OUTSIDE the house, imaging the rats **through an
IR-transmitting window**. Rain, condensation/fog, water drips, and sun glare land on the glass,
between the lens and the rats. The current `shelter_sleep.py` drives its state off a raw ROI
grayscale `mean-abs-diff`: those glass artifacts spike it → **false `occupied-high-motion`**, and
fog flattens contrast → **false `low-motion`/`empty`**. Nothing tags a bin with its view condition,
so a rainy bin is indistinguishable downstream from a busy-rats bin.

**Primary goal of Phase A is NOT smarter sleep/activity classification — it is preventing
rain/fog/glass artifacts from being counted as rat activity.** Build the conservative core, prove it
on real hours, THEN add discrimination (Phase B).

## Interpretation rules (load-bearing)

- `occupied_low_motion` = shelter **rest proxy**, NOT EEG sleep (unchanged from Stage 3).
- Interior rat counts are **approximate** (huddles + degraded glass). Raw detector counts are kept
  as evidence columns and are **never** overwritten by the fused occupancy estimate.
- A **degraded** inside-glass bin can never become automatic `occupied_high_motion`.
- Degraded bins are **kept, flagged, and excluded from the headline budget** — never silently dropped.
- Doorway/outside detections are **evidence** for presence-near-shelter, not a hard occupancy count
  (sparse ~45 s sampling misses crossings). Strong entry/exit accounting is Phase B.
- CLOSED files only (`_to_`), never the live file (unchanged capture-safety invariant).

## What will be added / changed

- new `preprocessing/computer_vision/view_quality.py` — GPU-free. Per-zone view_quality in 3 tiers
  `clear / degraded / unusable` (ported exposure/glare/dark thresholds from
  `reolink_record/overexposure_check.ps1` + Laplacian-variance sharpness + contrast). Robust
  inside-motion: illumination-normalize (subtract ROI mean) → temporal median background → foreground
  diff → drop small BRIGHT speckles (rain) → score coherent DARK moving blobs (rat). Tiny
  `field_conditions.yaml` loader (channel+timestamp → logged degraded window). `--selftest` on
  synthetic frames (clear / fog / glare / near-black; dark-blob vs bright-speckle vs global-shift).
- new `preprocessing/computer_vision/place_zones.py` — OpenCV editor to draw `inside_shelter` /
  `doorway` / `outside_surrounding` polygons on `configs/CHxx_reference.png` → `configs/CHxx_zones.json`
  (`inside` pre-loaded from `CHxx_calib.json["image_px"]`, editable). Reuses the `label_frames.py` /
  `place_cameras.py` drag pattern.
- new `configs/view_quality.yaml` — thresholds (sat/dark ratios, sharpness, contrast, motion, blob
  sizes), tunable.
- modify `preprocessing/computer_vision/shelter_sleep.py` — zone- + view-aware rewrite of
  `analyze_channel`: burst sampling, per-zone view_quality, robust dark-motion, zone-assigned raw
  counts, conservative state/fusion, new CSV schema (evidence + confidence + state + usability flags),
  timeline degraded/unusable shading, split summary, light `field_conditions.yaml` cross-check.
- modify `preprocessing/computer_vision/validate_shelter.py` — record per-sample view_quality; report
  accuracy stratified by view_quality; assert rainy/foggy samples are not scored `occupied_high_motion`.
- modify `README_cv.md` (Stage 3.5 section) and `CLAUDE.md` (one line on the IR-glass view + degraded
  handling).

## Per-bin CSV schema

`channel, t, view_quality_inside, view_quality_doorway, n_detected_inside, n_detected_doorway,
n_detected_outside_near_shelter, inside_motion_score, n_inside_estimated, n_inside_confidence, state,
weather_logged, usable_for_headline_summary, usable_for_coarse_activity`

## Verification

- `python view_quality.py --selftest` → synthetic clear/fog/glare/near-black classify to the right
  tier; robust dark-motion fires on a moving dark blob but NOT on bright scattered speckle (rain) or a
  global brightness shift (glare/AE). Offline, no disk/ffmpeg/GPU.
- `place_zones.py` writes valid 3-zone JSON for CH05/CH06; `inside` matches the calib quad.
- **Primary:** dry-run hours spanning a LOGGED window (2026-06-30 fog 03:00–07:00 CH05; rain
  17:25–17:50) → those bins are `degraded`/`unusable`, go `occupied_low_motion`/`indeterminate`,
  are NOT `occupied_high_motion`, carry `weather_logged=true`, excluded from the headline budget;
  clear midday bins still produce normal empty/low/high with raw counts intact.
- `validate_shelter.py` accuracy split by view_quality; rainy/foggy samples not scored high-motion.
- Light on the live-capture PC: closed files only, `ffmpeg -threads 2`, burst of a few frames per
  ~45 s bin, ROI-cropped CPU metrics, batched detector as today.

## Out of scope (Phase A — deferred to Phase B)

fog/rain/glare TYPE classification + dark-stripe/implant-vs-drip channel; Farneback/optical-flow
coherence; strong directional entry/exit occupancy accounting; detector fine-tune on degraded frames;
auto-suggesting unlogged weather windows back into `field_conditions.yaml`.

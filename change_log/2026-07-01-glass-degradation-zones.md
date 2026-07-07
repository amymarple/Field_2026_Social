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

## Update (2026-07-01) — analysis-PC setup + Phase-A verification off the field PC

Ran the pending verification on the **analysis PC** (RTX 3060, Ampere sm_86 — not the field PC's
5060 Ti), against CH05/CH06 footage synced here to `D:\Reolink_record\audio_in\Reolink_record\CHxx\`
(2026-06-29/30, closed `_to_` files). Code changes are uncommitted working-tree edits on top of
merge `22fa8f3`.

**Code changes (portability, backward-compatible):**
- `extract_clip.py`: `REC_ROOT`, `FFMPEG`, and new `FFPROBE` are now env-overridable
  (`REOLINK_REC_ROOT` / `REOLINK_FFMPEG` / `REOLINK_FFPROBE`). Unset → unchanged field-PC defaults
  (`E:\Reolink_record`, bundled ffmpeg). `FFPROBE` defaults next to `FFMPEG`, so one env var locates both.
- `scan_for_rats.py`: `FFPROBE` now reuses `ec.FFPROBE` instead of hardcoding `ec.REC_ROOT/bin`.
- `verify_gpu.py`: docstring/messages generalized — the device name/capability is printed, not asserted,
  so any CUDA GPU passes (was worded for the 5060 Ti / cu128 only).

**Environment (conda env `cv`):** created from `environment.yml` (Python 3.11); ffmpeg/ffprobe come
in as conda deps (`…\envs\cv\Library\bin`). Torch replaced with an **Ampere** wheel:
`torch 2.6.0+cu124` (the docs' cu128 wheel is Blackwell-only). To run here, set:
`REOLINK_REC_ROOT=D:\Reolink_record\audio_in\Reolink_record`,
`REOLINK_FFMPEG=C:\Users\Cornell\.conda\envs\cv\Library\bin\ffmpeg.exe`.

**Verification results:**
- `verify_gpu.py` → PASS (torch 2.6.0+cu124, CUDA available, RTX 3060 sm_86, GPU matmul, YOLO bonus OK).
- `view_quality.py --selftest` and `sleep_activity.py --synthetic` → PASS.
- `view_quality.py --probe` (real frames, no detector) confirms the CH05 fog thresholds separate cleanly
  on this footage — fog 03–05: all 180 bins `degraded` (mean p50≈162, sharpness p50≈21k); clear hr 12/20:
  all `clear` (mean p50≈110/126, sharpness p50≈45k/64k). No re-tuning needed.
- `shelter_sleep.py` **plumbing smoke-test** with the generic `yolo11n` placeholder (CH05 hr3 fog + hr12
  clear) ran end-to-end and produced the 15-column CSV + timeline + heatmap. **Safety invariant confirmed
  directly from the CSV: 0 degraded/unusable bins scored `occupied_high_motion`** (degraded bins:
  29 empty + 1 occupied_low_motion; clear bins may be high-motion). Rat *counts* are meaningless here
  (placeholder COCO model) — see below.

**Clock caveat:** hours are matched to filename timestamps only; per `field_conditions.yaml` the observed
wall-clock differs from the OSD/filename clock — alignment of logged windows to hours is **unverified**
beyond the filename match.

**Real-detector verification — DONE (2026-07-02).** The user synced `runs/` + `dataset/` here, so
`runs/detect/rat_daynight/weights/best.pt` (single class `rat`, 19.3 MB) is now present. Real GPU run
(`shelter_sleep.py --channels CH05 --date 2026-06-30 --hours 3 4 5 12 --device 0 --batch 1`, 320 bins ×45 s):
- inside-view **clear 25% / degraded 75%** — the 3 fog hours (03–05) classified `degraded`, clear midday
  hour 12 classified `clear`. `weather_logged` = 240/320 bins (exactly the fog hours).
- **SAFETY = PASS** on the real per-bin CSV: 0 degraded/unusable bins scored `occupied_high_motion`
  (`degraded`: 199 empty + 42 `occupied_low_motion`, never high-motion; `clear`: 46 empty / 30 low / 3 high).
- Real rat counts are meaningful now: clear bins mean 0.67 / max 4 (nocturnal rats resting in-shelter at
  midday → `occupied_low_motion`); the detector still fires through fog (degraded mean 0.16) but those counts
  stay as EVIDENCE and never drive state. Outputs: `outputs/CH05_sleep_2026-06-30.csv` + timeline + heatmap.

**Still pending (user step — interactive):** `validate_shelter.py --date 2026-06-30 --n 60 --device 0 --batch 1`
needs a human to label ground-truth count/motion, then prints count MAE/bias + motion agreement + the SAFETY line;
fold those figures into the accuracy report and tune `motion_thresh` / `view_quality.yaml` if suggested.

**Env notes:**
- **GPU YOLO — diagnosed & fixed (run detection with `--batch 1` on this PC).** The CUDA
  *illegal memory access* is an asynchronous race in ultralytics/torch batched inference, triggered only by
  **`imgsz>=960` AND `batch>1`** on this `torch 2.6.0+cu124` / RTX 3060 build. Isolation: imgsz=640 batch=8 OK;
  imgsz=960/1280 batch=8 FAIL; imgsz=1280 **batch=1 OK**; imgsz=1280 batch=8 **+`CUDA_LAUNCH_BLOCKING=1` OK**;
  disabling cuDNN does not help and a plain 1280² conv is fine (rules out cuDNN / large-tensor / version-mismatch —
  torch 2.6.0 and torchvision 0.21.0 are a matched cu124 pair). `shelter_sleep`/`validate_shelter` set
  `imgsz=W`(=1280) and loop `predict` in chunks of `--batch`, so **`--batch 1 --device 0` runs fully on GPU**
  (verified end-to-end on CH05 hr3/hr12). No code change (the field PC's cu128/Blackwell build is untouched and can
  keep batching). Optional future robustness: bump to a torch build without the race if batched GPU throughput is
  ever needed here.
- **CPU runs need `KMP_DUPLICATE_LIB_OK=TRUE`** (duplicate OpenMP runtime `libiomp5md`/`libomp` from mixing
  conda-forge + pip wheels in the `cv` env).
- **Pillow — FIXED.** The conda-forge Pillow 12.3.0 had a broken `PIL._imaging` DLL ("cannot run %1", a
  conda-forge/pip binary conflict) that made `import torchvision` (full package) fail. Resolved with
  `pip install --force-reinstall --no-deps pillow` in the `cv` env; `torchvision 0.21.0+cu124` and
  `torchvision.ops.nms` now import cleanly. (Did not block YOLO itself — ultralytics uses OpenCV for IO.)

## Update (2026-07-02) — `validate_shelter.py` usability + fog-detection ground truth

Two changes to the interactive validator, driven by hands-on use:
- **Motion is now judged from a looping CLIP, not a 2-frame blink.** `build_samples` grabs a separate
  human-review clip (`--judge-frames` 12 over `--judge-window` 3.0 s) at the same timestamp and
  `collect_truth` plays it on a loop, so still-vs-moving is actually visible. The pipeline still scores
  its own `n_burst`/`motion_gap` burst unchanged, so the comparison stays honest.
- **Motion is labeled as a COUNT of movers, not a binary.** Instead of aggregating "any mover = moving"
  in your head, enter how many animals move: `m`+digit (or `l`=none, `h`=all). `_set_moving` clamps it to
  the total count and derives the binary `gt_motion` (still = 0 moving) the report compares against the
  pipeline's aggregate motion; `gt_n_moving` is saved to the CSV for future per-animal work. **Keys are
  now case-insensitive** (Caps Lock / Shift no longer silently swallow `l`/`h`/`c`/`f`/`u`/`n`/`q` — the
  reported "key not working").
- **View/fog is now validated too.** New per-sample view label (`c`=clear, `f`=degraded/fog, `u`=unusable);
  the `n` gate needs count+view, and asks for motion only when occupied AND the view is judgeable (empty or
  unusable frames don't demand a still/moving call — that was the main friction). `report()` adds a
  **VIEW/FOG section**: 3-way you-vs-pipeline confusion, degraded/unusable detection recall/precision, and the
  safety-critical **"called clear but you saw fog/degradation"** count (a non-zero value means the fog
  thresholds in `view_quality.yaml` are too lax). `gt_view` is added to `validation_<date>.csv`.
- `--batch` default lowered to **1** here (this tool is interactive/low-volume) so it can't trip the GPU
  batched-inference race by default.

Non-interactive smoke test passed: 12-frame clips captured, GPU scoring at `--batch 1`, and the new
report renders (VIEW/FOG confusion + count/motion/SAFETY). Interactive labeling itself is still a user step.

## Update (2026-07-02) — accuracy-pass results + shelter-cam wall-edge blind zone

First real labeled accuracy pass done by the user: **n=59** samples across CH05+CH06, 2026-06-30
(`outputs/validation_2026-06-30.csv`, git-ignored). Results split by layer:

- **SAFETY = PASS** — 0/14 degraded/unusable samples scored `occupied_high_motion`. The core
  glass-artifact guarantee holds on real labeled data.
- **VIEW/FOG detection = good.** 83% exact 3-way agreement; degraded/unusable **recall 80%,
  precision 86%**. Two rough edges: **3 "called clear but the human saw fog/degradation"** (the unsafe
  direction — CH05/CH06 fog thresholds are slightly lax), and all 5 human-`unusable` frames were only
  called `degraded` (severity undercalled, but still non-clear → still excluded from the clear headline,
  so not unsafe). Worth a small threshold tighten, not a redesign.
- **OCCUPANCY COUNT / MOTION = NOT reliable yet.** On clear bins: count **bias −1.11** (MAE 1.20);
  **26/45 presence false-negatives** (human saw rats, detector returned `empty`); motion agreement 39%
  (`inside_motion_score` ≈ 0 even when the human marked movers). The detector + inside-zone counting
  badly **undercount** rats in the shelter view (cause to investigate: detector recall on the top-down
  through-glass view vs the `inside`-zone polygon clipping detections near the walls). The fog/safety
  layer is trustworthy; **the per-frame count and rest/activity layer is not** — treat shelter
  occupancy/rest numbers as placeholder pending a shelter-view detector fine-tune.

**Shelter-cam wall-edge blind zone (user, 2026-07-02) — a hard optical limit that reframes the counts.**
CH05/CH06 are **top-down**; by perspective, a band **directly along each of the 4 interior walls is not
visible**, and rats rest/hide against the walls there. So:
- The human `gt_count` is **visible-only — a lower bound of true occupancy**; both human and detector
  miss wall-edge hiders, so the count comparison above is "visible vs visible," not vs ground truth.
- A shelter bin scored **`empty` may still hold wall-edge rats.**
- **Phase-B mitigation: infer hidden occupancy from prior movement** — track continuity (a rat that
  entered and hasn't exited is still inside even when occluded) plus the deferred doorway in/out
  gate-line count. Do not treat the visible count as a total headcount.

Net: Phase A's **view-quality / fog / safety design is validated**; the **occupancy-count / rest layer is
not** and needs (a) a shelter-view detector fine-tune (+ inside-zone geometry review) and (b) the
wall-edge prior-movement inference before any rest/occupancy number is publishable. (Recorded as memory
`shelter-cam-wall-edge-blindzone` too.)

### Undercount root-cause diagnosis (2026-07-02)

Re-ran the detector at conf 0.05 on 12 of the 26 clear false-negative frames (human saw rats, detector
inside=0), overlaying raw boxes vs the `inside` zone (overlays in `outputs/diag_undercount/`, git-ignored):

- **8/12 — detector recall: ZERO boxes even at conf 0.05.** The shelter view is genuinely hard: top-down,
  IR grayscale, viewed **through a wire mesh (CH05) / condensation-prone glass (CH06)**, rats camouflaged
  into bedding/shadow. Note `rat_daynight` was already trained on 540 labeled CH05/CH06 frames, so this is
  **under-recall from insufficient/unrepresentative training data** (cf. the README's weak cross-lighting
  generalization, night→day mAP 0.52), not a novel view — more diverse labeled data + retrain is the fix.
- **3/12 — near-noise weak hits** (conf 0.06–0.10 only); lowering the threshold would add false positives,
  not recover these.
- **1/12 — zone clipping:** a real 0.58 detection fell **outside** the `inside` polygon; the **CH06 `inside`
  zone is visibly misaligned** (rotated/shifted off the shelter footprint, bottom edge in the grass).

**Verdict: the undercount is primarily a detector-recall failure on an out-of-distribution view — not the
confidence threshold, and only marginally the zone mask.** Actions: (1) **fine-tune the detector on
shelter-view frames** via the existing `scan_for_rats.py` (harvest) → `label_frames.py` → `train_detector.py`
loop on CH05/CH06 closed footage — the main lever; (2) **redraw CH06's `inside` zone** (`place_zones.py
--channel CH06`). Until (1), shelter occupancy/rest counts stay placeholder; the fog/safety layer is unaffected.

**Toward the fine-tune — `scan_for_rats.py --no-detector` harvest (2026-07-02).** The existing harvest is
detector-gated (drops any frame with no detection ≥ conf_low), which would silently discard exactly the
shelter frames we need. Added a `--no-detector` mode: time-diverse seek-sampling + frame-diff dedup with
**no detection required**, so resting/occluded rats and empty negatives are all kept for hand-labeling
(`tag=unlabeled`, `n_rats=-1` in the manifest). `main()` skips the weights/model load in this mode; default
detector-assisted behavior is unchanged. Launched the first shelter harvest (CH05+CH06, 2026-06-30, diel
hours 22/2/6/10/14/18, every 20 s, cap 200/ch) → **400 new unlabeled frames** into `dataset/rat/images`
(`CH05_*`/`CH06_*`) for the user to label with `label_frames.py`, then fine-tune `rat_daynight` and re-validate.

Root of the under-recall confirmed by dataset audit: the existing **540 labeled frames are only from
2026-06-28 (360) and 06-29 (180)** — **none from 06-30** — with 294 positive / 246 empty / 1178 boxes. So the
detector was validated (06-30, incl. fog/condensation) on a day and conditions it never trained on; 2 days of
data is too little diversity. The 400 new 06-30 diel-cycle frames directly fill that gap → after labeling,
retrain on the combined 940 and re-run `validate_shelter.py` to confirm recall/count improve.

`label_frames.py` usability fixes: (1) **starts on the first unlabeled frame** so you skip the done ones, but
keeps the **full** frame list reachable. `Next` is context-aware — from a NEW (unlabeled) frame it jumps to
the next unlabeled; from an already-labeled frame you're REVIEWING it steps to the adjacent frame (so you can
move forward through labeled frames). `Prev` always steps back one (labeled included) to review/correct;
`--all` starts at frame 1 with `Next` visiting every frame. (An earlier version dropped labeled frames from the list entirely, which hid them from
review — fixed.) (2) Added **mouse-wheel zoom (centered on cursor) + right-drag pan** so faint
fog contours are actually visible while labeling — left-drag stays free for draw/edit (no modifier), keys
`+`/`-`/`0` also zoom/reset. Rendering switched to a clamped viewport crop with a display↔image coordinate
map; verified round-trip/zoom math (0 px round-trip, 0.11 px cursor drift).

**`label_frames.py` re-platformed onto TKINTER (2026-07-04).** Root cause of the persistent "left-drag
pans while drawing": the conda-forge OpenCV is a **Qt6** build (`cv2.getBuildInformation` → `GUI: QT6`), and
the Qt HighGUI `imshow` window has its **own native zoom/pan** running *below* our `setMouseCallback` — no
callback logic could suppress it. Rewrote the GUI on **Tkinter + PIL `ImageTk`** (both already in the `cv`
env — zero new deps): we now own every mouse/key event, so LEFT-drag can only draw. Rendering is unchanged
(OpenCV composes the frame — viewport crop, boxes, banner, button bar — into a numpy image, shown via
`ImageTk.PhotoImage`); `cv2.imshow`/`waitKey`/`setMouseCallback` are gone. RIGHT-drag pan is back (safe now
that we control events) alongside `i`/`j`/`k`/`l`; wheel/`+`/`-` zoom cursor-centered; `0`/`f` fit. All prior
features preserved (draw/edit, skip/huddle status+banner + migration, decision-aware start/Next/Prev, `--all`).
Verified: `py_compile` clean, and an integration smoke test constructs the Tk window, renders a PhotoImage,
and exercises zoom (0.0 px round-trip) + status-toggle + prev/next without `mainloop`.

## Update (2026-07-03) — label convention forensics + skip/defer keys (huddle decision deferred)

Sleeping rats often pile into an indivisible "huddle." Before changing the label schema, audited the
existing labels (read-only). The **540 frames (06-28/29) and the ~28 already done on 06-30 use one
consistent convention**: single class `rat`, one box per rat, **including best-estimate individuals inside
piles** (per-frame box IoU up to ~0.49 — which *survives* the default NMS `iou=0.7`, so pile counting is
not lost). No huddle-as-one-big-box, no skipped piles. Huddle-like frames (≥4 overlapping boxes) are
**~16% of all labeled / ~29% of positive** frames — frequent.

Decision on single-class vs a dedicated `huddle` class is **deferred** until the frequency of *truly
indivisible* piles is known, under the hard rule **do not mix conventions**. To keep labeling while keeping
that decision open, added to `label_frames.py` (additive, default behavior unchanged):
- **`s` = skip/EXCLUDE** → moves the frame to `dataset/rat/excluded/` (fog / rats present but unlabelable).
- **`g` = defer HUDDLE** → moves the frame to `dataset/rat/pending_huddle/` (indivisible pile you can't
  split). No label/box is written for either; a row is appended to `dataset/rat/excluded_manifest.csv`.
- Buckets live outside `images/`, so the "unlabeled-only" queue never re-serves them and `train_detector`
  never sees them; navigation skips moved frames. `s`/`g` are **keyboard-only** (adding them to the button
  bar shifted the nav buttons and broke click muscle-memory for `< Prev`/`Next >`, so they were kept off it);
  the on-screen title + docstring document the keys.

**Revised (2026-07-03) — `s`/`g` mark IN PLACE with a visible banner instead of moving the file.** Key
realization: `train_detector.build_split` only takes images that HAVE a label file (label-less images are
ignored, not treated as negatives), so a skip/huddle frame just needs **no label** — it doesn't need to be
moved out. So `s`/`g` now write a marker to `dataset/rat/status/<stem>.{skip,huddle}` (no label file),
keep the frame in `images/`, and show a **banner** ("SKIPPED"/"HUDDLE") whenever you're on it — so a judged
frame is visually distinct from a not-yet-done one (the reported gap). Pressing the same key again un-marks;
pressing the other switches; drawing a box overrides the marker and labels the frame. "Decided" = labeled OR
marked, so the start index and `Next` skip decided frames; `Prev` still reaches everything. The earlier
move-based buckets were migrated back automatically (idempotent startup step): the 47 already-moved frames
(32 skip + 15 huddle) were restored into `images/` with markers, none carrying a label (train_detector
ignores them). Old `excluded/`, `pending_huddle/`, `excluded_manifest.csv` removed. Verified: `py_compile`
clean; migration + status-toggle + decided-nav + save logic all pass on a temp replica and on the real
dataset (940 images, 47 markers, 0 markers with a stray label).

**Interim convention (documented in the script):** splittable rats (incl. loose groups) → individual boxes
as before; indivisible tight pile → `g` (defer); fog/unlabelable → `s` (exclude); confidently-empty clear
frame → empty negative. Labeled set therefore stays 100% single-class individual — usable now for the
recall retrain. **Decision gate (later):** count `pending_huddle/`; if few → best-guess individuals, stay
single-class; if frequent/important → deliberately migrate to `rat`+`huddle` (batch-relabel that bucket +
re-derive existing pile frames; `train_detector` 2 classes; `shelter_sleep` huddle⇒occupied/rest, count ≥2
unknown). Verified: `py_compile` clean; move/skip mechanics pass on a temp dataset (files routed to the
right buckets, stray label removed, manifest written, navigation skips moved frames).

**`s`/`g` robustness fix (2026-07-04) — reported: pressing `g` on an already-labeled frame showed no
marker.** Root-caused by reproduction: a headless replica (frame pre-labeled from "disk", press `g`)
**passed** — the marker was written, the label deleted, boxes cleared, banner condition true, persisting
across next→prev and toggling off. So the backend is recency-/label-agnostic (the user's "only works on
recently-labeled frames" hypothesis is disproven); the live symptom was **keyboard-focus + no feedback** —
the keypress wasn't reaching the handler and nothing confirmed it, so it read as "nothing happened." Fixes
to `label_frames.py`: (1) key binding moved from `root.bind("<Key>")` → **`root.bind_all("<Key>")`** so
keys fire regardless of which sub-widget holds focus; (2) **Skip / Huddle added to the on-canvas button
bar** (now 9 buttons) — mouse clicks always land, and each button **lights up** when its status is active,
giving a mouse fallback + visible state (this reverses the earlier "keyboard-only" decision, safe now that
the Tkinter bar uses one dynamic `btn_rects` hit-test, so extra buttons no longer misroute `< Prev`/
`Next >`); (3) `s`/`g` now **print the toggle to the console** (`<file>: HUDDLE -> huddle|cleared`) as
ground-truth confirmation. Verified: `py_compile` clean; the huddle-on-labeled-frame replica still PASSES;
a 9-button click-mapping test confirms every button routes to its correct key (no misroute).

**`measurement_context` metadata layer — annotation + provenance only (2026-07-04).** New pure module
`preprocessing/computer_vision/measurement_context.py` makes every shelter number interpretable as a
measurement without changing any result. Per-row (additive): `camera_model` + `shelter_id` from
`configs/field_layout.json` (`annotate_camera`; camera axis is general to all 6 channels, `shelter_id` null
off CH05/CH06 since only those mount a house), plus `mc_run_id` linking each row to its run manifest. Per-run:
an auto **JSON sidecar** `outputs/<script>_<date>.measurement_context.json` (mirrors
`wiser_analysis_utils.write_run_manifest`, replicated not imported) with git commit + generated_utc, detector
weights path/version (`rat_feasibility-6`)/fingerprint + conf/imgsz/batch/device, sampling params, per-camera
block (model/mapping/derived role/pos/shelter), **content-fingerprints** of every config (zones, calib
[+created/reproj_rmse], view_quality, field_conditions, glass_treatments) since none carry version fields,
coordinate frame, inputs, and the "covariate not exclusion" caveats. `mc_run_id` hashes only the
measurement-config fields (excludes time/mtimes/args/inputs) so identical setups share an id and any
detector/config change flips it. Wired into `shelter_sleep.main` and `validate_shelter.report/main`
additively (build context once, annotate rows, write sidecar). **Missing fields recorded for future** (not
built): explicit config version fields (hash is the interim proxy), a detector model-card/registry, the AWN
weather join, and `confirmed` calibration/georef status. Verified offline (no pipeline run): `py_compile`
clean; camera spot-checks (CH05→RLC-520A/left/shelter_nadir, CH06→right, CH01→Duo3/None/paddock_overview_180,
CH03→RLC-1212A/side_wide); additivity on the real `CH05_sleep_2026-06-30.csv` (320 rows) and
`validation_2026-06-30.csv` (59 rows) — same rows, existing columns byte-identical, only
`camera_model,shelter_id,mc_run_id` added; manifest has all sections with a real git commit + fingerprints;
`run_id` stable across identical builds and flips on a conf change; `field_conditions.yaml` loader unchanged.

**Measurement-context audit pass (2026-07-06).** Before adding the AWN weather join, ran a diagnostic audit
(regime-aware-cv-measurement skill) that back-annotates the existing shelter outputs and stratifies errors —
no detector/view/motion/count/safety/threshold change. Report + annotated copies + sidecar in
`outputs/audit/` (`measurement_context_audit_2026-07-06.md`). Findings: (1) the per-row fields populate
correctly on all outputs (`camera_model` RLC-520A, `shelter_id` CH05→left/CH06→right, `glass_regime`
bare/tape, `mc_run_id`); (2) annotation is **additive-only** — every file's row count + existing columns
byte-identical, only the 9 covariate columns added; (3) on the 06-30 validation (current detector
`rat_feasibility-6`) errors **cluster in degraded view** (presence-recall clear 78% vs degraded 29%), and the
apparent `bare` (38%) vs `tape` (77%) gap is a **fog/time-of-day confound, not a treatment effect** — `bare`
is 71% degraded (pre-09:00, contains the 03:00–07:00 pre-dawn fog) vs `tape` 5%; SAFETY invariant intact
(0/14 high-motion under degraded); (4) sidecar `mc_run_id` links cleanly to every row. Key gaps surfaced:
**no CV outputs exist for 2026-07-01…07-03**, so the `lift_1cm`/`antifog_film`/`bare_seated_post_film` regimes
(incl. the reported worse-than-bare film span) can't be audited; 06-29 sleep CSVs are legacy 6-col
(regime-blind); pre-`measurement_context` outputs recorded no detector version. Categories assigned
(artifact / mixed / lower-bound), no weather→behavior claim made. AWN weather join still deferred.

**Weather-lite fog-RISK covariate + 07-01/07-02 processing + audit (2026-07-06).** New pure module
`preprocessing/computer_vision/fog_risk.py` — a MEASUREMENT-CONTEXT covariate (not behavior): from AWN
temp/dew-point/RH/rain it derives `dewpoint_gap`, `fog_risk_level` (low/med/high, documented heuristic
thresholds), `fog_risk_reason`, joined to bins by nearest local-wallclock sample (`weather_lag_min` QC,
30-min tol). Additive-only, like glass_regime/measurement_context; it explains/stratifies view degradation
and never gates a measurement. Independently validated: the weather-only risk flags the *observed* fog
windows (06-30 & 07-03 04:00–06:00 → high, gap ~1.2°C, RH ~93%). Processed the previously-missing
**2026-07-01 + 07-02** shelter outputs (CH05+CH06, `--batch 1`, 5-min sampling) so the `lift_1cm` /
`antifog_film` regimes now have CV outputs (07-03 has no footage on this PC). Audit report + annotated copies:
`outputs/audit/fog_risk_audit_2026-07-06.md`. Findings (measurement audit, no behavioral claim): **degraded
view + detector errors cluster in high fog-risk windows** — 06-30 validation presence-recall 86%→17% (low→
high fog-risk; high-risk bins 90% degraded); CH06 07-02 high-risk 78% degraded. **Two sensor paths separated**
by carrying fog-risk (weather) + glass_regime (instrument): CH05 07-02 degraded view sits in the `antifog_film`
regime at LOW fog-risk (afternoon) → a **non-weather optical-regime artifact** during that regime, distinct
from the weather-condensation pre-dawn path. The `antifog_film` regime **confounds three coincident 07-02 13:00
changes — film applied + ~1 cm lift removed + glass reseated** — so this is **regime-attributable, not
film-attributable** (cannot isolate which change caused it); consistent with, but not proof of, the observer's
"film made view worse" note. CH06 consistently more degraded (zone-quad fallback). Fixed a `merge_asof` dtype
bug in `fog_risk.annotate` (normalize both keys to `datetime64[ns]`). Data-availability caveats recorded (07-01
AWN gap → fog-risk NaN; no 07-03 footage; `CH06_zones.json` missing; heuristic thresholds). No AWN
weather-behavior join; no threshold tuning; no safety/view logic change; no exclusion rules. **Recommended next
step:** transfer + process the 2026-07-03 footage — it captures the `bare_seated_post_film` regime (does view
recover once `antifog_film` ends? → begins to disambiguate the confounded regime artifact) AND the 07-03
04:00–06:00 pre-dawn high-fog-risk window (weather-only risk already flags it high; full AWN coverage there).

**Labeling protocol formalized into a standalone doc (2026-07-04).** The labeling rules (settled over many
hands-on rounds) were scattered across the `label_frames.py` docstring, `README_cv.md`, and these change
logs. Consolidated them into a single human reference, **`preprocessing/computer_vision/LABELING_PROTOCOL.md`**,
and pointed to it from the `label_frames.py` docstring and the `README_cv.md` Stage-1 labeling step. It codifies:
single-class `rat` / one box per separable individual / no huddle-class this batch; the four actions with the
**current** in-place `status/<stem>.{skip,huddle}` markers (not the old `pending_huddle/`/`excluded/` buckets)
+ buttons; the all-or-nothing "never partially label" core rule; box the visible extent (through mesh/glass);
zone-blind detector (inside/outside not encoded in the label); and a quick decision table. One judgment call
was resolved (user): **empty-vs-skip = "trust the pixels"** — a fully-hidden wall-edge rat (0 visible pixels)
makes a frame a valid **visual negative** even though it's not a **biological** empty (downstream
`shelter_sleep.py` recovers the true headcount); **skip** only when a *visible* rat-like region can't be boxed
(fog/glare/ambiguous blob). Docs-only; no code behavior changed.

**Semi-transparent box edges + hide/show toggle (2026-07-04).** User: an existing solid box outline was
obscuring their judgment of a *neighbouring* rat's boundary. `label_frames.py` now draws box edges (incl.
the in-progress box + edit corner handles) onto an overlay of the image region and alpha-blends it
(`BOX_ALPHA = 0.5`) so edges are 50% opacity — the banner/buttons/text stay fully opaque. Added a **`b`**
key to hide/show all boxes (a non-destructive "peek at raw pixels"); the top hint shows `(HIDDEN)` while
off. Verified: `py_compile` clean; a pixel-level render test confirms a green edge over a gray background
composites to the expected 0.5 blend (`[30,158,30]` RGB vs solid `[0,255,0]`), `b` returns the pixel to
background, and toggling back restores the box. Also noted in `LABELING_PROTOCOL.md`.

**Detector fine-tune ran + `train_detector.py` post-train val crash fixed (2026-07-04).** The
harvest→label→train loop paid off: fine-tuning `yolo11s` on the expanded labeled set reached
**mAP50 0.876 / mAP50-95 0.503 (P 0.857, R 0.820)** on the **session-held-out** validation videos
(225 val images / 384 rat instances / 64 empty), a large jump from the earlier night→day 0.52. Weights:
`runs/detect/rat_feasibility-6/weights/best.pt`. Caveat: this is the *detector's* box mAP on held-out
frames — **not** the shelter occupancy/count accuracy; the real cross-check is a fresh `validate_shelter.py`
pass on closed footage (still to do). Environment note: training now runs in a **new `yolo` conda env
(torch 2.11.0+cu128)**, separate from `cv` (torch 2.6.0+cu124).

The run **crashed at the very end** in the redundant standalone `model.val()` (train_detector.py:95) with a
cuDNN `GET was unable to find an engine to execute this computation` → `CUDA illegal memory access` — the
same RTX 3060 batched-inference failure we hit before (`batched imgsz>=960` crashes), now in the cu128 env.
Training itself and its built-in final validation had already succeeded, so the metrics were never in doubt.
Fix: **removed the second `model.val()`** and report metrics from the object `model.train()` returns
(`self.trainer.validator.metrics` after final_eval on best.pt — the 0.876), via a new `_final_val_metrics()`
helper with a `results.csv` last-row fallback. Also fixed a latent path bug: `best.pt` and `results.csv`
paths now come from `model.trainer.save_dir`/`.best` (real run dir) instead of `--name`, which broke once
ultralytics auto-incremented the dir to `rat_feasibility-6`. Verified: `py_compile` clean; unit test of
`_final_val_metrics` passes on both the `results.box` path and a synthetic + the **real**
`rat_feasibility-6/results.csv` (space-padded headers parse). No GPU re-run needed.

**Promoted the fine-tune to the shelter default + validation set-up (2026-07-04).** Pointed
`shelter_sleep.DEF_WEIGHTS` (the single default detector, inherited by `validate_shelter.py` via
`ss.DEF_WEIGHTS`) at `runs/detect/rat_feasibility-6/weights/best.pt`; the old `rat_daynight/best.pt` is
left on disk (not deleted), just no longer the default. Non-destructive, one-line change (+ a comment
flagging that a numbered run dir is volatile — re-point/promote on the next retrain). De-risked the
validation pass without touching the interactive labeler: (1) the yolo-env-trained weights (torch 2.11+cu128)
**load and GPU-infer in the `cv` env at `--batch 1`** — 6 local frames scored 0/0/2/4/3/3 rats, no crash;
(2) ran the **non-interactive slice of `validate_shelter`** (build_samples → score_pipeline) on real closed
CH05/CH06 footage for 2026-06-30 with the new weights: footage decode, inside-zone counting, view_quality,
robust motion, and state fusion all run. Early read (unlabeled): the retrained detector now returns counts
on **clear** views (CH05 3 and 2 rats) where the old one badly under-detected, and **degraded** views still
fall back to `empty` (never `occupied_high_motion` — safety rule holds). Footage present here: CH05/CH06 for
2026-06-29..07-02. Remaining step is the human accuracy pass:
`python validate_shelter.py --date 2026-06-30 --n 60` (defaults to the new weights, `--batch 1`), then
compare its report to the pre-retrain 06-30 numbers (count bias −1.11, 26/45 presence FNs). Added a
convenience launcher **`run_validate.ps1`** (analysis-PC) that sets `REOLINK_REC_ROOT`/`REOLINK_FFMPEG`/
`KMP_DUPLICATE_LIB_OK` (only if unset), cd's to the CV folder, and runs validate_shelter in the `cv`
env forwarding all args — `.\run_validate.ps1 --date 2026-06-30 --n 60` from any directory.

**Re-scored the existing 06-30 labels with the new detector — no re-labeling (2026-07-04).** Key insight
the user raised: the human ground truth (`gt_count/gt_view/gt_motion`) is **detector-independent** — only
the pipeline's prediction changes — so the 59 already-labeled 06-30 samples (`outputs/validation_2026-06-30.csv`)
can be **re-scored** with the new weights instead of collecting fresh labels. This is also a *better*
comparison: paired, same frames, same truth, old vs new. Reconstructed each sample from its (channel, file, t),
re-decoded the burst, re-ran `score_pipeline` with `rat_feasibility-6`, compared to the stored gt. Reproduction
check: recomputed `view_quality` matched the old run **59/59** (view is detector-independent → confirms the
same frames were re-decoded). **Paired result (OLD `rat_daynight` → NEW `rat_feasibility-6`):**
- **Presence recall 26% → 70%** (missed 32/43 → 13/43): 19 frames where the old detector returned 0 but the
  human saw rats are now detected.
- **Clear-view count bias −1.11 → −0.13** (the systematic undercount is essentially gone); **MAE 1.20 → 0.76**.
- **SAFETY unchanged (0 → 0 fog-as-high-motion)** — as expected; the detector doesn't touch the safety layer.

So the retrain moved the **count/presence layer from "not reliable" toward usable**, quantified on a fair
random 06-30 sample. Remaining, detector-independent, still-open (not fixed by the retrain): **MOTION** still/
moving agreement is weak (42% at thresh 0.30; grid-search suggests ~0.00 → 58% — the `robust_inside_motion`
metric/threshold needs work); the **fog threshold** is slightly lax (3 "called clear but you saw degradation");
and big **huddles still undercount** (gt=4 → new 2-3) — the deferred huddle-class decision. Also the wall-edge
blind zone still caps true headcount. Saved `outputs/validation_2026-06-30_rescored_rat_feasibility-6.csv`.
(Full independent multi-date validation is still worthwhile later; this paired re-score answered "did the
retrain help" without redoing the labeling.)

**Encoded the shelter IR-glass optical-regime timeline as machine-readable data (2026-07-04).** The user
flagged that glass interventions (anti-fog film, lift, tape) change the *measurement instrument*, so they
must be carried as covariates — and that weather has a double role (weather→glass→view degrades the
measurement AND weather→behavior changes the signal). Smallest durable first step (chosen over a full
weather join / pipeline wiring): a new **`data_manifests/glass_treatments.yaml`** — a running, ordered,
step-function timeline of the CH05/CH06 glass optical regime (bare → tape → lift_1cm → antifog_film →
bare_seated_post_film) distilled from `FIELD_OBSERVATIONS.md` Days 1–6. **Data-only; nothing reads it yet**
(no loader, no `shelter_sleep` behavior change). Header states the principle: it records instrument STATE,
is a **covariate not an exclusion rule**, and doesn't decide validity by itself. Honest about uncertainty:
tape start `time_precision: approx` (morning, exact time not logged); the 07-01 lift span carries
`uncertain_layers: [aluminum_tape]` (tape persistence not logged — neither asserted nor denied); 07-02 is
`confounded: {value: true, note: film+lift changed together, effects inseparable}`; the post-film regime is
`bare_seated_post_film` (not assumed identical to baseline — residue/scratches/repositioning). `change_points`
are framed as boundaries to **stratify/annotate by regime before pooling**, not cut lines. Distinct from
`field_conditions.yaml` (transient weather windows, consumed by the pipeline); pointers added from
`data_manifests/README.md`, `FIELD_OBSERVATIONS.md` Cross-references, and a `field_conditions.yaml` header
comment. Verified: YAML parses; 5 regimes contiguous/time-ordered/last-ongoing; `regime-at-T` spot-checks
resolve (e.g. 07-03 09:00 → antifog_film, 07-03 12:00 → bare_seated_post_film); `view_quality.load_conditions`
output on `field_conditions.yaml` unchanged (comment-only edit) and the new file invisible to it. **Deferred
(noted, not done):** the still-missing 07-03 04:00–06:00 pre-dawn fog window in `field_conditions.yaml` (adding
it *would* change pipeline output → left for a follow-up); and the AWN weather covariate join
(`audio_analysis/analysis/weather.py:load_awn`).

**Anti-fog film efficacy correction (2026-07-04, user note).** The film not only failed — the observer
reports it **made the CH05/CH06 view WORSE than the bare glass** that night (worse *with* the film on than
without). Logged in `FIELD_OBSERVATIONS.md` Day 5/6 (observed events + a new data-interpretation flag) and
echoed in the `glass_treatments.yaml` `antifog_film` note as an **attributed observer report, not a measured
effect** (keeps the state-file principle). Implication: the film-on window (07-02 13:00 → 07-03 11:00) is the
**worst** view regime — a view-degrading *instrument* covariate, not neutral — so its degradation is partly
the film, not weather alone; treat it accordingly when stratifying shelter occupancy by regime.

**Wired the glass regime in as annotation-only covariate metadata (2026-07-04).** New pure module
`preprocessing/computer_vision/glass_regime.py`: `load_regimes()`, `regime_at(ts, channel=None)` (returns the
active segment dict or None — None for out-of-timeline or a non-shelter channel), and `annotate(df, ts,
channel)` which returns a COPY of `df` with six covariate columns appended — `glass_regime, glass_layers,
glass_uncertain_layers, glass_time_precision, glass_confounded, glass_regime_note`. Wired into both shelter
outputs additively, each right after its DataFrame is finalized: `shelter_sleep.analyze_channel` (per-bin
CSV, `ts="t"` absolute) and `validate_shelter.report` (validation CSV, absolute ts = `scan.clip_start(file)`
+ `t`). **Strictly annotation-only** — nothing downstream reads these columns; detector/view-quality/motion/
count/state/safety/thresholds/filtering and `field_conditions.yaml`/weather behavior are all unchanged (no
AWN join, no 07-03 fog window, no threshold edits — deferred as before). README schema updated. Verified
offline (no pipeline run): `py_compile` clean; `regime_at` spot-checks (2026-06-29 12:00→bare, 07-01 17:00→
lift_1cm, 07-03 09:00→antifog_film, 07-03 12:00→bare_seated_post_film) + channel-gating (CH01→None) +
out-of-range→None; and **additivity on the real existing outputs** — `CH05_sleep_2026-06-30.csv` (320 rows
unchanged, existing columns byte-identical, +6 glass cols, regimes bare 240 / tape 80 split at 06-30 09:00),
`CH05_sleep_2026-06-29.csv` (1700 bins all `bare`), and `validation_2026-06-30.csv` (59 rows unchanged, +6
cols, bare/tape); `view_quality.load_conditions` on `field_conditions.yaml` still returns its 4 windows.

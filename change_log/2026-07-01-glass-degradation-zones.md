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

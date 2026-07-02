# Field-PC Computer-Vision Pipeline (Stage 0)

A light, GPU-accelerated pipeline to turn Reolink footage into **per-animal
position (x, y) + ID in a common field frame**, plus **sleep/activity**. No
pose/keypoints. Tracking is per camera, then transformed into one shared field
coordinate frame (cm).

**Field = 20 × 40 ft = 609.6 × 1219.2 cm.** Coordinates are output in cm and align
with the WISER UWB frame (WISER is inches; 1 in = 2.54 cm) for cross-validation.

Stage 0 = the plumbing, validated with synthetic/manual data (no rat detector
needed yet). Detector training comes later.

## Hardware (this PC)

RTX 5060 Ti 16 GB (Blackwell, sm_120) · Ultra 7 265K (20c) · 64 GB RAM · 19 TB free
on E:. Recorders use `ffmpeg -c copy` (no GPU, ~0 CPU), so CV does not disturb
recording.

## One-time setup

Miniforge is installed. From a **Miniforge Prompt**:

```bat
cd /d C:\Users\Cornell\Documents\GitHub\Field_2026_Social\preprocessing\computer_vision
mamba env create -f environment.yml      :: or conda env create -f environment.yml
conda activate cv
:: Blackwell GPU REQUIRES the cu128 PyTorch build (default wheel lacks sm_120):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

`ffmpeg`/`ffprobe` are reused from `E:\Reolink_record\bin` (no install needed).

## Files

| File | Role |
|---|---|
| `verify_gpu.py` | Confirm CUDA/PyTorch/Blackwell works |
| `extract_clip.py` | Pull a light clip / reference still from a channel (NVDEC, downscaled) |
| `field_coords.py` | Common-frame def + pixel↔field-cm transforms (homography / poly / **PnP**), layout/landmark lookup |
| `calibration.py` | Fit & save a per-camera calibration from the pole grid + wall-tops + shelters; grid-overlay + RMSE |
| `make_layout_map.py` | Render `configs/field_layout_map.png` — the **pole-index reference map** |
| `place_cameras.py` | **GUI to position cameras AND shelters** on the field; writes them back into field_layout.json |
| `animal_tracking.py` | Detections → canonical per-camera tracks CSV (synthetic/manual/YOLO) |
| `../data_merging/merge_cameras.py` | Merge per-camera tracks into the common frame |
| `sleep_activity.py` | Speed + rest/active bouts from a field-cm trajectory |
| `configs/field_layout.json` | **Field geometry**: 15-pole grid, wall height, shelters, camera mounts. Edit to match the site. |
| `configs/camera_specs.json` | Reolink model specs (Duo3 / RLC-1212A / RLC-520A) |
| `configs/field_layout_map.png` | generated pole-index map (regenerate after editing the layout) |
| `configs/` | per-camera `CHxx_calib.json` (saved calibration), reference stills, overlays |

**Field axes**: **x = 40 ft length** (0–1219.2 cm), **y = 20 ft width** (0–609.6 cm),
origin at corner pole A0. 15 poles on a 10 ft grid — 3 rows **A/B/C** (y = 0 / 304.8 /
609.6) × 5 columns **0–4** (x = 0…1219.2); row **B** is the length-wise centreline.
Wall = 38.5 in (97.79 cm).

Channels are the recording folder names **CH01–CH06** (= your CH1–CH6):
- CH01/CH02 = Duo3 180° panoramas → **poly** (click ≥6 poles).
- CH03/CH04 = RLC-1212A side cams that see few/collinear ground poles → **PnP** using the
  wall: click each visible pole at its **base** *and* where it **crosses the wall top**.
- CH05/CH06 = RLC-520A shelter cams (~nadir) → **homography** from the shelter's 4 corners.
(`homography` with only 3 points auto-falls back to `affine`; `--type` overrides the model.)

Canonical per-camera track CSV schema:
`camera, frame, time_s, track_id, conf, x_img, y_img, x_field_cm, y_field_cm`

## Calibrating a camera (gets real field coordinates)

Calibration is anchored on the **existing pole grid + wall + shelters** — no new
markers. You click them; their field-cm come from `configs/field_layout.json`.

0. **Know your poles.** Open `configs/field_layout_map.png` (regenerate with
   `python make_layout_map.py`) — it labels every pole (L0–L4, M0–M4, R0–R4) so you
   click the right one. Verify the grid/shelters match the site; fix
   `configs/field_layout.json` if not.
1. **Position the cameras and shelters** (one time): `python place_cameras.py` — a **drag
   GUI**. The active item has a yellow ring: **drag its box to move** (cameras snap to the
   nearest pole) and **drag its handle to rotate** (camera = aim, shelter = long-axis).
   Click any other marker to edit it. Bottom buttons **[< Prev] [Next >] [Snap] [SAVE & QUIT]**
   (or keys p/n/s/q). It writes positions + orientations to the layout; then re-run
   `python make_layout_map.py` to refresh the map.
2. **Grab a reference still** per camera: `python extract_clip.py --channel CH03 --frame`
3. **Pick landmarks**: `python calibration.py --channel CH03 --pick`. It lists the
   layout landmarks; for each, the banner says exactly what to click. Press `s` to skip
   any not in view, `u` to undo. By model:
   - **CH03/CH04 (PnP)**: for each visible pole you'll be asked for its **base** (on the
     ground) *and* `<pole>_top` (where it **crosses the wall top**). 3 poles × base+top
     = 6 points is enough even when the ground poles are collinear.
   - **CH01/CH02 (poly)**: click ≥6 pole **bases**.
   - **CH05/CH06 (homography)**: click the **shelter's 4 corners** — the prompt names
     each by field direction (e.g. "toward +x,+y"); match it against the corner labels
     on `field_layout_map.png`.
   On finish it saves `configs/CH03_calib.json`, prints reprojection **RMSE (cm)**, and
   writes `CH03_grid_overlay.png` — confirm the green 10-ft grid lands on the real poles.
   Repeat for all six.

(Non-interactive fit for testing: `--points file.json` with `{image_px, world_cm}`,
`{image_px, field_cm}`, or `{landmarks, image_px}` resolved via the layout.)

## Lens distortion correction (CH03/CH04 fisheyes)

The wide CH03/CH04 lenses bow straight lines; a homography/poly fit to a few points
overfits (looks good on the clicked points, ~50–100 cm error elsewhere). Fix = model
the distortion from a **checkerboard**, undistort, then re-fit a clean homography.

1. **Capture a board clip** per camera: wave a flat checkerboard slowly through the
   frame for ~60–90 s at varied tilts (±30–45°), covering **all of the frame incl. the
   corners/edges**, near + far, stop-and-go (no motion blur). The board's field location
   does **not** matter. Extract that window from the recording (native res).
2. **Solve intrinsics**: `python intrinsics.py --channel CH03 --clip <board_clip>` —
   auto-detects the pattern, tries pinhole + fisheye models, keeps the one with the lower
   *held-out* reprojection error, and writes `configs/CH03_intrinsics.json` plus
   `CH03_undistort_preview.png` (top = raw, bottom = undistorted; the wall should look
   **straighter** — the key sanity check).
3. **Re-fit from your existing clicks** (no re-clicking):
   `python calibration.py --channel CH03 --refit` — it undistorts the saved points and
   fits a homography, sets `"undistort": true`, and refreshes the grid overlay.

Once `CHxx_intrinsics.json` exists, `field_coords.to_field` undistorts pixels
automatically, so `animal_tracking.py` needs no changes. (`--no-undistort` ignores it.)

## Stage 1: rat detector (feasibility-first)

Prove rats are reliably detectable with a tiny label+train loop, then scale. YOLO11
is the detector. Start on the nadir shelter cams (CH05/CH06) where rats are largest.

1. **Sample frames** from a window with rats (grayscale IR is fine):
   ```bat
   python extract_clip.py --channel CH05 --frames 60 --start 00:30:00 --window 1740 ^
       --src E:\Reolink_record\CH05\CH05_2026-06-28_19-00-01_to_20-00-00.mp4 ^
       --out-dir dataset\rat\images
   ```
2. **Label** them (drag a box around each rat):
   ```bat
   python label_frames.py
   ```
   drag = box · `d` delete last · `c` clear · `n`/SPACE next · `p` prev · `q` save+quit.
   Resumable; frames with no rats save an empty label (a valid negative). ~50–150 boxes
   is enough for a feasibility read.
3. **Train** a light fine-tune and read the val mAP:
   ```bat
   python train_detector.py            :: yolo11s, 80 epochs, imgsz 1280, 80/20 split
   ```
   Prints `val mAP50 / mAP50-95 / precision / recall` and writes
   `runs/detect/rat_feasibility/weights/best.pt`.
4. **Eyeball** `runs/detect/rat_feasibility/` val plots, or run on a held-out clip:
   `python train_detector.py --predict-clip scratch\CH05_clip.mp4` (annotated video).
5. **Use it** in the full pipeline (detector → field cm → merge → sleep):
   ```bat
   python animal_tracking.py --channel CH05 --clip <clip> --classes 0 ^
       --weights runs\detect\rat_feasibility\weights\best.pt --out tracks\CH05_rats.csv
   ```

`dataset/` and `runs/` are git-ignored (data + weights stay local).

**`scan_for_rats.py` — two decoupled modes (keep their outputs separate):**

*Labeling harvest* (default) — diverse frames to label, **not** an occupancy estimate. Sparse
seek-sampling (~5 s, decodes only sampled frames) + frame-difference **dedup** (drops resting
near-duplicates). Copies frames → `dataset/rat/images/` and writes
`outputs/<CH>_harvest_manifest.csv`:
```bat
python scan_for_rats.py --channel CH05 --date 2026-06-28 --hours 19 20
```
Then `label_frames.py` the new frames and `train_detector.py` again. Tune `--dedup-thresh`
(lower = keep more). For variety, also hand-pick rats at the **entrance / half-out / on grass**
and in **day + night**, or the detector stays a "rat-in-box" specialist.

*Occupancy* (`--occupancy-hz`) — shelter-occupancy time series, **saves no frames**. One streamed
full decode at ~N Hz (the small shelter FOV means brief visits → sample densely). Counts only →
`outputs/<CH>_occupancy.csv` (timestamp, n_rats, max/mean conf) + plot:
```bat
python scan_for_rats.py --channel CH05 --date 2026-06-28 --hours 19 20 --occupancy-hz 1
```

Inference runs on the GPU in both modes; the bottleneck is CPU video decode (this ffmpeg's NVDEC
is broken on the Blackwell GPU), which is why harvest only decodes the frames it samples.

## Shelter occupancy + rest proxy (CH05/CH06) — `shelter_sleep.py`

Shelter-level **occupancy + a rest PROXY** for the shelter cams — NOT individual tracking, NOT EEG
sleep. Per time-bin it reports one of four states:
`empty` / `occupied_low_motion` (rest proxy) / `occupied_high_motion` / `indeterminate`.

**These cams see the rats THROUGH an IR-transmitting window**, so rain, condensation/fog, water
drips, and sun glare land on the glass between lens and animals. The pipeline is built so those
artifacts are **never counted as rat activity**: each bin carries a per-zone `view_quality`
(`clear` / `degraded` / `unusable`), a **degraded** inside-glass bin can never become automatic
`occupied_high_motion`, and an **unusable** inside-glass bin is `indeterminate`. Inside motion uses a
glass-noise-resistant signal (`view_quality.robust_inside_motion`: illumination-normalize → temporal
median → keep only dark moving blobs → reject rain speckle / glare / AE hunting).

**Zones — Phase A needs only `inside_shelter`, and it's auto-loaded from your calibration.**
`shelter_sleep.py` uses the calibration shelter quad (`CHxx_calib.json`) as the inside region, so
**you don't have to run `place_zones.py`** — open it only to *reshape* the inside region. These are
top-down views with **2 doors**, so `place_zones.py` also lets you mark each door as a 2-point
**gate line** (`doors` in the zones JSON); those are OPTIONAL and only feed the deferred Phase-B
entry/exit occupancy counting (a track crossing a gate = one in/out event).

```bat
python view_quality.py --selftest                           :: offline logic check (no disk/GPU)
python shelter_sleep.py --date 2026-06-30                    :: CH05+CH06, all CLOSED hours
python shelter_sleep.py --date 2026-06-30 --hours 3 4 5      :: a few hours (dry run / tuning)
:: OPTIONAL: reshape the inside region (else the calibration quad is used automatically)
python extract_clip.py --channel CH05 --frame               :: reference still (if not present)
python place_zones.py --channel CH05                        :: draw/adjust the inside polygon
```
- **Capture-safe:** reads only closed `_to_` recordings, never the file being written (see CLAUDE.md);
  throttled ffmpeg (`--ffmpeg-threads 2`), small GPU batches, sparse ~45 s sampling with a short
  motion burst (`--n-burst`, `--motion-gap`).
- **Weather cross-check:** logged windows in `data_manifests/field_conditions.yaml` force a bin to
  `≥ degraded` (`weather_logged=true`) as a belt-and-suspenders on top of the auto-detection.
- Thresholds live in `configs/view_quality.yaml` (tunable per camera). `configs/CHxx_zones.json`
  holds the zone polygons.
- Per-bin CSV columns: `view_quality_inside, view_quality_doorway, n_detected_inside,
  n_detected_doorway, n_detected_outside_near_shelter, inside_motion_score, n_inside_estimated,
  n_inside_confidence, state, weather_logged, usable_for_headline_summary, usable_for_coarse_activity`.
- Outputs to `outputs/`: `<CH>_sleep_<date>.csv`, `sleep_timeline_<date>.png` (state shading +
  hatched degraded-view band + occupancy line), `<CH>_rest_heatmap_<date>.png` (approximate, **clear**
  bins only), and a **split summary** — headline budget from clear bins only, a separate coarse-
  activity track, and % of day clear/degraded/unusable.

Validate against ground truth (stratified by view_quality, with the safety check that rain/fog
samples are never scored high-motion): `python validate_shelter.py --date 2026-06-30 --n 60`.

Detector = `runs/detect/rat_daynight/weights/best.pt`. mAP reported honestly: within-day random
split 0.95 (leaky), night→day held-out 0.52, day+night held-out 0.94 — good for shelter detection
on the sessions seen so far, not a blanket "robust." Per-individual sleep needs identity (Stage 2).

> **Scope note (Phase A):** the goal here is preventing weather/glass artifacts from faking activity.
> Deferred (Phase B): fog/rain/glare *type* classification, dark-stripe-vs-drip channel, optical-flow
> coherence, and strong directional entry/exit occupancy accounting.

## Daily flow once you have animal footage

```bat
python extract_clip.py --channel CH05 --seconds 8
python animal_tracking.py --channel CH05 --clip scratch\CH05_clip_8s.mp4 --out tracks\CH05.csv
python ..\data_merging\merge_cameras.py --inputs tracks\CH05.csv tracks\CH03.csv --out tracks\merged.csv --absolute-time
python sleep_activity.py --input tracks\merged.csv --out-prefix tracks\session1
```

Swap a trained rat detector into `animal_tracking.py` (`--weights rat.pt`) when
ready; everything downstream is unchanged.

## Stage 0 acceptance check (synthetic, no detector)

```bat
python verify_gpu.py
python extract_clip.py --channel CH05 --seconds 8
python field_coords.py --channel CH05 --px 480 400          :: after calibration
python animal_tracking.py --channel CH05 --synthetic --out tracks\CH05_synth.csv
python ..\data_merging\merge_cameras.py --inputs tracks\CH05_synth.csv --out tracks\merged.csv
python sleep_activity.py --synthetic --out-prefix tracks\selftest
```

# WISER Tracking Analysis

Python analysis module for UWB (Ultra-Wideband) tracking data produced by the
WISER system — a wireless mesh of UWB antennas and tags that synchronise to
estimate tag positions in real time.

**Coordinate units: inches** (system configured at 1.82 inches/pixel).

> **Status:** see [ANALYSIS_STATUS.md](ANALYSIS_STATUS.md) for what's done vs candidate vs
> placeholder across the pipeline, and the prioritized next steps to make the candidate
> findings publishable.

---

## Fixed-position test results — 2026-06-22

Recording: `test20260622.csv` / `tag_reports.sqlite`  
Paddock: 20 × 40 ft  
Setup: 6 tags placed at two fixed clusters in the paddock interior, never moved.  
Analysis: last 10 minutes excluded (tag removal period).

### Tag identity (decimal ↔ hex)

| Shortid (data) | WISER display | Cluster |
|----------------|---------------|---------|
| 12378 | 305a | Right |
| 12380 | 305c | Right |
| 12395 | 306b | Right |
| 12386 | 3062 | Left |
| 12407 | 3077 | Left |
| 12409 | 3079 | Left |

Cluster separation: **~192 inches (~16 ft)** confirmed by field measurement.

### Sampling frequency

All tags sampled at approximately **~3.7–3.9 Hz** (~0.26 s per fix).

| Tag | Hz |
|-----|----|
| 12378 (305a) | 3.7 |
| 12380 (305c) | 3.6 |
| 12386 (3062) | 3.9 |
| 12395 (306b) | 3.7 |
| 12407 (3077) | 3.9 |
| 12409 (3079) | 3.9 |

### Precision / jitter (inches)

Ground truth = long-run median position per tag (tags were stationary).

| Tag | Cluster | std X | std Y | RMSE | p50 | p75 | p90 | p95 |
|-----|---------|-------|-------|------|-----|-----|-----|-----|
| 12378 (305a) | Right | 4.3 | 6.5 | 7.9 | 3.0 | 5.2 | 10.9 | 15.3 |
| 12380 (305c) | Right | 4.4 | 6.3 | 7.9 | 3.4 | 5.9 | 12.0 | 15.7 |
| 12395 (306b) | Right | 4.5 | 5.5 | 7.2 | 3.2 | 5.0 | 10.0 | 15.6 |
| 12386 (3062) | Left  | 4.0 | 5.0 | 6.4 | 3.9 | 5.9 | 9.5  | 12.5 |
| 12407 (3077) | Left  | 4.3 | 5.2 | 6.8 | 3.3 | 5.9 | 10.9 | 13.8 |
| 12409 (3079) | Left  | 4.1 | 5.5 | 6.9 | 4.1 | 6.3 | 9.8  | 13.4 |

**Summary:** median fix-to-fix precision is **3–4 inches**; 90th percentile tail extends to **9–12 inches**. Left cluster tags are slightly more consistent (RMSE ~6.5–6.9 in) than right cluster tags (RMSE ~7.2–7.9 in).

### Systematic bias (inches)

Bias is near zero for all tags because ground truth = median estimated position. These values reflect mean vs median offset only and are not meaningful as absolute accuracy estimates without an independently surveyed ground truth.

| Tag | Bias X | Bias Y | Bias magnitude |
|-----|--------|--------|----------------|
| 12378 | −0.27 | −1.50 | 1.5 in |
| 12380 | −0.11 | −1.54 | 1.5 in |
| 12386 | +0.06 | −0.32 | 0.3 in |
| 12395 | −0.53 | −0.86 | 1.0 in |
| 12407 | +0.22 | −0.88 | 0.9 in |
| 12409 | +0.22 | −0.65 | 0.7 in |

---

## Project layout

```
wiser_tracking_analysis/
├── src/
│   ├── wiser_io.py        # load & standardise raw WISER files (CSV, TSV, SQLite)
│   ├── time_utils.py      # timestamp detection & conversion
│   ├── metrics.py         # jitter / error calculations
│   └── plotting.py        # diagnostic plots
├── configs/
│   └── fixed_position_ground_truth.csv   # tag ground-truth positions (inches)
├── scripts/
│   ├── analyze_fixed_position_test.py    # fixed-position test analysis
│   └── analyze_formal_recording.py       # placeholder for future field sessions
├── outputs/                              # CSVs, plots (git-ignored)
└── README.md
```

---

## Where to put raw WISER data

Place all raw WISER output files (`.csv`, `.txt`, `.tsv`, or `.sqlite`) inside:

```
D:\Wiser\data\
```

The loader accepts mixed formats, auto-detects field separators, and reads
both CSV and SQLite exports. Column names are fuzzy-matched so minor variations
(e.g. `tagid`, `tag_id`, `location_x`) are handled automatically.

**Note:** if both a CSV export and a SQLite file covering the same session are
present, the loader will load both and double the row count. Keep only one
format per session, or deduplicate after loading.

---

## How to edit ground-truth fixed positions

Open `configs/fixed_position_ground_truth.csv`.  
Coordinates are in **inches**, matching the WISER system output.

- `shortid` must match the decimal tag ID in the WISER data.
- `true_x` and `true_y` are required.
- `true_z` is optional; leave blank for 2D-only analysis.

If all `true_x`/`true_y` values are blank, the analysis runs in
**jitter-only mode** — precision metrics are computed but no absolute error
is reported.

---

## Running the fixed-position analysis

```bash
cd Field_2026_Social\wiser_tracking_analysis

# Basic run
python scripts/analyze_fixed_position_test.py

# Override data folder
python scripts/analyze_fixed_position_test.py --data D:\Wiser\data

# Trim only 5 minutes instead of 10
python scripts/analyze_fixed_position_test.py --trim-minutes 5

# Skip plot generation
python scripts/analyze_fixed_position_test.py --no-plots
```

Outputs written to `outputs/`:

| File | Contents |
|------|----------|
| `fixed_position_cleaned.csv` | Per-frame data with datetime, elapsed_s, jitter_r, error_r |
| `fixed_position_summary.csv` | Per-tag summary statistics |
| `plots/01_position_scatter.png` | All estimated positions, coloured by tag |
| `plots/02_position_clouds.png` | Per-tag position clouds centred on median |
| `plots/03_timeseries.png` | X and Y over time for all tags |
| `plots/04_error_timeseries.png` | Error distance over time (GT mode only) |
| `plots/05_jitter_histograms.png` | Radial jitter distribution per tag |

---

## Understanding error vs jitter

| Metric | Measures | Requires ground truth? |
|--------|----------|----------------------|
| **Jitter / precision** | Consistency of repeated estimates for a stationary tag | No |
| **Absolute error / accuracy** | Distance from known true position | Yes |

- **Jitter** is the radial distance of each frame from the tag's own median position.
- **RMSE** is the standard accuracy metric used in UWB literature.
- **Bias vector** (`bias_x`, `bias_y`) shows any systematic offset from ground truth.

---

## Using the same reader for formal field data

Once real field recordings are available, use `analyze_formal_recording.py`:

```bash
python scripts/analyze_formal_recording.py --data D:\Wiser\field_data\session1
```

The formal script does **not** trim the end of the recording and does not
expect ground-truth positions — it outputs cleaned trajectory data and
basic data-quality checks.

---

## Georeferencing (WISER frame → physical paddock)

The WISER frame is native **inches with an offset origin**, unverified against the physical
paddock — so wall/thigmotaxis/route-vs-boundary claims risk being coordinate artifacts. The
georeference tooling fits a similarity transform (rotation + uniform scale + translation) from a
short field survey, tying WISER inches to the CV pipeline's surveyed field frame (**cm, origin at
pole A0**; `preprocessing/computer_vision/field_coords.py`).

- `src/field_transform.py` — the fit core (Umeyama similarity, affine diagnostic, robust
  outlier rejection, apply/invert, config I/O). Verify offline: `python scripts/selftest_georeference.py`.
- `scripts/georeference_wiser.py` — reads the pole survey, extracts each dwell's
  validity-filtered **median** WISER position (read-only from the DB), fits the transform, and
  writes `configs/wiser_to_field_transform.json` + a validation overlay.

**WISER is noisy** (~7 in median jitter, worse at edges/corners). The transform fixes only the
*frame*, never per-fix noise; fit residuals bottom out at the ~7 in floor, not zero. So: dwell each
pole **~3–5 min** and use **≥6 well-distributed poles** (mix interior + edge) so the median
averages jitter down and no single edge read dominates.

```powershell
# 1. Fill in the dwell windows (or manual WISER x,y) in the survey template:
#    wiser_tracking_analysis\configs\wiser_georef_survey.csv
# 2. Fit (writes configs\wiser_to_field_transform.json + outputs\georef_validation.png):
python scripts\georeference_wiser.py
```

QC gates `confirmed`: scale must land near 2.54 cm/in, inlier residuals near the jitter floor,
and the affine shear negligible. Until a survey passes QC, the config stays `confirmed: false`
and **all analyses run unchanged in inches** (the transform is a no-op). Once confirmed, drivers
adopt the surveyed boundary via `wiser_analysis_utils.verified_boundary_in_wiser()` and can add
`x_field_cm,y_field_cm` via `apply_field_transform()`. See
[ANALYSIS_STATUS.md](ANALYSIS_STATUS.md) and the
[implementation plan](../implementation_plan/2026-07-01-wiser-georeferencing.md).

## Dependencies

```
pip install pandas numpy matplotlib
```

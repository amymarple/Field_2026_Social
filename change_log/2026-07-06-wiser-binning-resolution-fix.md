# 2026-07-06 — WISER shelter-binning made datetime-resolution-safe (pandas `[ms]` bug)

## What changed & why
The 2026-07-02 CV × WISER cross-val (`analyze_sleep_site_cv_crossval.py`) crashed with
`KeyError: 'stratum'` because `wiser_shelter_state` reported **`WISER daytime bins/shelter: 1;
episodes: 0`** — it collapsed a full ~16 h daytime window (1,132,208 valid fixes) into **one** bin.

Root cause (diagnosed in `outputs/audit/GEOMETRY_DIAGNOSIS_2026-07-02.md`): **not** a coordinate-frame
/ ROI mismatch and **not** rat absence — a **pandas datetime-resolution bug**. Under pandas ≥ 2.0 the
SQLite loader yields `datetime64[ms]` (this env: pandas 3.0.3), and three binning sites did
`datetime.astype("int64") // (bin_s * 1_000_000_000)` **assuming nanoseconds**. On `[ms]` data
`astype("int64")` returns *milliseconds*, so the `// 60e9` under-divided by 10⁶ and bucketed the whole
window into a single bin. That one bin had `frac_near ≈ 0.35 < near_frac 0.5` → `state=0` for every
rat/shelter → 0 episodes → empty `picks` → crash. **`state=0` was a code artifact, not absence.**

## Files touched
- **`src/wiser_analysis_utils.py`**
  - New helper `_bin_utc_ns(dt, bin_s)`: floors naive-UTC datetimes to `bin_s`-second bins via the
    unit-aware `pd.to_datetime(dt).dt.floor(f"{bin_s}s")`, then returns **int64 nanoseconds since the
    Unix epoch** (`.astype("datetime64[ns]").astype("int64")`). `bin_utc` stays int64-ns so the
    downstream contract is unchanged (the `np.arange` grid step, `bin_utc` joins, and
    `start_utc`/`end_utc` arithmetic all still operate in ns). Resolution-agnostic: `ns`/`us`/`ms`
    inputs bin identically.
  - Routed all three sites through it:
    - `wiser_shelter_presence` (was line 3406–3407)
    - `wiser_shelter_state` (was line 3501–3502; kept the local `binns` ns step used by the grid)
    - `_cv_bins` (was line 3619–3621; the CV lag is now applied as a `pd.to_timedelta(lag_s, "s")`
      shift **before** flooring — this also fixes a latent mixed-unit bug where the old code added an
      ns lag onto an `[ms]` epoch integer).
  - These are the pattern already used correctly elsewhere in the file (`.dt.floor("h")` at the
    hourly-occupancy / weather-merge sites), now applied to the sub-hour shelter binning.
- **`scripts/selftest_cv_crossval.py`**
  - New **resolution-invariance regression**: the same synthetic rat-in-shelter window and CV frame
    expressed as `datetime64[ns]`, `[us]`, and `[ms]` must yield an identical signature (grid bins,
    occupied/hc bins, episode count + duration, raw presence bins, `_cv_bins` bin_utc + occupancy) and
    must NOT degenerate to one bin. The prior selftest never caught the bug because its synthetic
    timestamps were always `[ns]`.
- **`scripts/analyze_sleep_site_cv_crossval.py`**
  - Added a `--no-plots` flag (default off — field-PC behavior unchanged). The three diagnostic
    figures are now gated behind it so the CSVs, verdict, and run manifest still get written on the
    analysis PC, where the headless matplotlib/MKL stack aborts **natively** at the first `savefig`
    (exit 127, uncatchable by a Python `try/except`). This is an analysis-PC robustness change, not a
    plotting fix — the figures are diagnostics only.

## Scope / non-goals (unchanged behavior guarantees)
- **No threshold, hysteresis, ROI, buffer, jitter-floor, or view-quality logic changed.** For
  `[ns]`-dtype inputs (older pandas) the new code returns byte-identical bins to the old code, so this
  is a pure correctness fix for non-`[ns]` resolutions, not a re-tuning.
- No detector, no CV output, no measurement-context covariate touched. No behavior claim is made here.
- WISER reads remain strictly read-only (snapshot copy; `load_wiser_session` `mode=ro`).

## Verification
- **`python scripts/selftest_cv_crossval.py` → `SELFTEST: PASS` (exit 0).** New line:
  `[resolution-invariance] ns==us==ms: 46 grid bins, 46 cv bins, 1 episode(s), 40 presence bins
  (datetime64[ms] no longer collapses to 1): ok`. All prior checks (kappa, `_rect_membership`,
  hysteresis episodes, presence, detection metrics, best-lag mapping) still pass.
- **Rerun of the 07-02 cross-val** on the `1stcohort_2026_2026-07-03.sqlite` snapshot — see the
  "Rerun" section appended below once the job completes. Sanity criteria (from the diagnosis): a 16 h /
  60 s window should yield ≈ 960 daytime bins (not 1); WISER shelter occupancy should no longer be
  all-zero; the run should complete through per-stratum (`fog_risk_level` × `glass_regime`) output
  instead of crashing on empty `picks`.

## Rerun (07-02, after patch) — sanity checks MET; interpretation deferred
Command (analysis PC, `cv` env):
`analyze_sleep_site_cv_crossval.py --db …\1stcohort_2026_2026-07-03.sqlite --dates 2026-07-02 --no-plots`
(env: `KMP_DUPLICATE_LIB_OK=TRUE` — required, else the matplotlib/MKL import aborts natively at 127).

Sanity checks from the diagnosis — all pass:
- **`WISER daytime bins/shelter: 960`** (was **1**) — the 16 h / 60 s window no longer collapses.
- **`episodes: 42 (19 high-confidence)`** (was **0**) — occupancy is no longer all-zero; the run
  completes through mapping selection, per-day/stratum detection, and the verdict instead of crashing
  on empty `picks`.
- HC anchors: house_1 = 12 hc episodes (Σ 159,000 s, mean spread 10.3 in); house_2 = 7 (Σ 76,140 s,
  9.3 in). Raw→smoothed recovered false exits: house_1 +2 bins, house_2 +25 (smoothing works).

Detection metrics produced (measurement diagnostics — **NOT yet a behavior claim**):
| shelter/cam | stratum | n | WISER hc_frac | CV recall_hc | precision | CV view mode |
|---|---|---|---|---|---|---|
| house_1 / CH05 | coarse | 192 | 0.99 | **0.49** | 1.00 | clear |
| house_2 / CH06 | coarse | 191 | 0.75 | **0.67** | 0.99 | clear |

**Why interpretation is still deferred (regime-aware discipline):**
- **Alignment is UNVERIFIED** — chosen mapping A, best-fit lag **0 s**, **joint κ = 0.20** (low). The
  script itself states κ is a lag/mapping *alignment diagnostic*, not the headline; recall/precision are
  conditioned on this unverified WISER-UTC↔CV-NVR-wallclock alignment.
- CV precision ≈ 1.0 with recall_hc ≈ 0.49 (CH05) means CV **undercounts** confirmed occupancy — but the
  dominant CV `view_quality` here is **clear**, so this gap is *not* obviously the fog/glass sensor path;
  it is at least as consistent with the **wall-edge blind zone** (CH05/CH06 counts are a documented lower
  bound) or the unverified clock alignment. The `likely_cv_optical_failure=True` flag on CH05 is a
  recall-threshold heuristic and here is a **misnomer** (glass is clear) — treat it as "CV undercount,
  cause unresolved," category 4 (lower-bound), not a proven optical failure.
- WISER house_1 occ_frac ≈ 0.99 all day is a buffer-tolerant *state* near a shelter that sits on the
  point-cloud centroid; with ~7 in jitter it is a plausible rest signal but is a WISER measurement
  diagnostic, not confirmation of the sleep-site claim.

→ The binning defect is fixed and the pipeline now yields interpretable-shaped output. **Any CV-vs-WISER
occupancy conclusion still requires resolving the clock alignment first** (the low joint κ / 0 s lag), so
no behavior interpretation is made here.

## Follow-ups
- Resolve the CV↔WISER **clock alignment** (joint κ = 0.20, lag 0 s) before interpreting recall/precision.
- Same bug affected **06-29/06-30** too (their earlier κ=0.66 was produced on an older pandas where
  `datetime` was `[ns]`); those pairings are worth re-confirming on the current env.
- The CH05 "clear-glass" recall gap is worth separating into wall-edge-blind-zone vs alignment vs
  detector-recall — targeted, not by retraining.

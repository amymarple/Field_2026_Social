# WISER Analysis — Status Tracker

Living status of the WISER UWB tracking analysis: what is done, what is still a
**candidate** finding, what is a placeholder, and — the main point — **what it takes to
promote the candidate findings to confirmed / publishable results**.

> **Keep this current.** Every row's status must match the verdict in that analysis's
> [`change_log/`](../change_log/) entry. When a candidate is promoted, a placeholder is
> implemented, or a blocker is resolved, update the row here in the same change. This file is
> the single index; the change logs remain the authoritative record of each result.

## Legend

| Mark | Meaning |
|---|---|
| ✅ | done / validated |
| ⚠️ | candidate — exploratory, interpret only with the stated caveat |
| ◻️ | placeholder / stub — not yet implemented |
| ⛔ | blocker — gates scientific interpretation until resolved |

## Data & config inventory

| Item | Path | Status | Caveat that matters |
|---|---|---|---|
| Live tracking DB | `D:\Wiser\data\1stcohort_2026.sqlite` | ✅ live | WAL writer — reads must stay strictly read-only (`mode=ro`, `PRAGMA query_only=ON`) |
| Stationary baseline | `D:\Wiser\data\tag_reports.sqlite` | ✅ validated | Fixed-position test; source of the jitter floor |
| Weather (AWN) | `D:\weather_data\AWN-*.csv` | ⚠️ partial | 6/29 evening sparse; aligned wall-clock UTC only, **±5 min unverified** |
| Rat identities | `configs/rat_identities.csv` | ✅ complete | Sova (12409) removed ≈2026-06-29 → excluded from night-2 analyses |
| Fixed-position ground truth | `configs/fixed_position_ground_truth.csv` | ✅ validated | Inches; used only for the precision floor |
| ROI definitions | `configs/wiser_rois.json` | ◻️ placeholder | `confirmed=false` → refuge/home/resource claims fall back to inferred zones |
| Exclude regions | `configs/wiser_exclude.json` | ◻️ optional | Absent → 12-in boundary band fallback for thigmotaxis |
| Georeference transform | `configs/wiser_to_field_transform.json` | ◻️ awaiting survey | Tooling ready + self-tested; written `confirmed:true` once a pole survey passes QC (see next steps P0) |

## Pipeline stages

Ordered per the `AGENTS.md` field-data workflow.

| Stage | Component(s) | Status |
|---|---|---|
| Raw registration | `src/wiser_io.py` (CSV/TSV/SQLite, fuzzy columns) | ✅ |
| Schema validation | `wiser_io.py` → canonical `shortid, ts_raw, x, y, z` | ✅ |
| Timestamp normalization | `src/time_utils.py` (Unix-ms/s, ISO auto-detect) | ✅ |
| Sync / alignment | weather↔WISER wall-clock UTC merge (`wiser_analysis_utils.load_weather*`) | ⚠️ unverified ±5 min |
| QC | jitter floor, validity flags (`metrics.py`, `add_validity_flags`) | ✅ |
| Derived data | `wiser_analysis_utils.py` (~3000 L: speed, ROI, social, route, follow, nightly) | ✅ built |
| Analysis notebook | `notebooks/wiser_pilot_analysis.ipynb` (§A–J) | ✅ |
| Figures / reports | `src/plotting.py` + per-script figure sets | ✅ |
| Change log | seven entries in [`change_log/`](../change_log/) | ✅ |

## Analysis inventory

| Analysis | Driver | Status | Key finding | Gating caveat | Records |
|---|---|---|---|---|---|
| Fixed-position precision | `scripts/analyze_fixed_position_test.py` | ✅ | Jitter floor ~7 in (18 cm); ~3.7–3.9 Hz | Precision only; not absolute accuracy | [plan](../implementation_plan/2026-06-28-hourly-occupancy-maps.md) · README |
| Hourly occupancy maps | `scripts/plot_hourly_occupancy.py` | ✅ | Live-DB-safe per-hour scatter/heatmaps | In-progress hour never plotted | [plan](../implementation_plan/2026-06-28-hourly-occupancy-maps.md) · [log](../change_log/2026-06-28-hourly-occupancy-maps.md) |
| Daily backup | `scripts/backup_wiser_daily.py` | ✅ | Snapshot + gz incremental to E: | One live-DB read/run (~0.74 s) | [plan](../implementation_plan/2026-06-30-wiser-daily-backup.md) · [log](../change_log/2026-06-30-wiser-daily-backup.md) |
| Pilot analysis (QC-first) | `notebooks/wiser_pilot_analysis.ipynb` | ✅ | ~1.03M fixes, 96.7% valid; §A–J | Exploratory sections labeled in-notebook | [plan](../implementation_plan/2026-06-29-wiser-pilot-analysis.md) · [log](../change_log/2026-06-29-wiser-pilot-analysis.md) |
| Route structure | `scripts/analyze_route_structure.py` | ⚠️ | Corridors robust to QC; straightness real vs displacement-matched null | Night-to-night IoU only ~27%; **WISER frame unverified** | [plan](../implementation_plan/2026-06-29-route-structure-analysis.md) · [log](../change_log/2026-06-29-route-structure-analysis.md) |
| Leader-follower / route-following | `wiser_analysis_utils` + notebook §J | ⚠️ | 22/30 pairs above circular-shift null | Weak short-lag/asymmetry; single <24 h session → candidate, not confirmed social following | [plan](../implementation_plan/2026-06-29-leader-follower-analysis.md) · [log](../change_log/2026-06-29-leader-follower-analysis.md) |
| Nightly movement (habituation vs rain) | `scripts/analyze_nightly_progression.py` | ⚠️ | −50% active distance 6/28→6/29 (both dry) | 6/30 wet-ground **confounded** with habituation; n=5, 3 nights | [plan](../implementation_plan/2026-06-30-nightly-progression.md) · [log](../change_log/2026-06-30-nightly-progression.md) |
| Nightly behavior & social | `scripts/analyze_nightly_behavior.py` | ⚠️ | Home↑, outside↓, exploration graph simplifies/stabilizes | n=5 paired, 3 nights; tunnel present 6/28 only; sub-1 m proximity below jitter floor | [plan](../implementation_plan/2026-06-30-nightly-behavior.md) · [log](../change_log/2026-06-30-nightly-behavior.md) |
| Daytime sleep-site (Direction 3) | `scripts/analyze_daytime_sleep_site.py` | ⚠️ | Per-animal daytime (05:00–21:00) rest site; within-day drift + across-day stability | Sleep = low-speed proxy (not ephys-validated); site precision gated by ~7 in jitter; frame unverified | [plan](../implementation_plan/2026-07-02-daytime-sleep-site.md) · [log](../change_log/2026-07-02-daytime-sleep-site.md) |
| Sleep-site WISER↔CV cross-val (Direction 3) | `scripts/analyze_sleep_site_cv_crossval.py` | ⚠️ | Shelter-occupancy agreement vs CV (CH05/CH06). **Binning bug fixed + alignment checked 2026-07-06**; 07-02 rerun yields 960 bins / 42 episodes (19 hc). Read **CV precision≈1.0 / recall≈0.49–0.64 (lower bound)** per-shelter — **not** the joint κ | Only the 2 shelters; **alignment adequate** (±1 h fine sweep flat, best lag ~0 s): low joint κ=0.20 is a base-rate (kappa-paradox) + definition mismatch, **not** misalignment and **not** biological disagreement; CH05 recall gap is on *clear* glass ⇒ wall-edge blind-zone lower bound, not optical failure; older 6/29–6/30 κ (0.66 / 0.68–0.82) predate the binning fix (`[ns]` pandas) → re-confirm (see `outputs/audit/ALIGNMENT_DIAGNOSIS_2026-07-02.md`) | [plan](../implementation_plan/2026-07-02-sleep-site-cv-crossval.md) · [log](../change_log/2026-07-02-sleep-site-cv-crossval.md) · [binning fix](../change_log/2026-07-06-wiser-binning-resolution-fix.md) |
| Formal-session analysis | `scripts/analyze_formal_recording.py` | ◻️ | Loads + cleans only | No smoothing / gap detection / session QC yet | — |

## Cross-cutting blockers

| ⛔ Blocker | Impact | Resolve by |
|---|---|---|
| WISER frame not georeferenced to the 20×40 ft paddock (tooling ready; awaiting survey) | Every spatial claim (wall-running, thigmotaxis, route-vs-boundary) may be a coordinate artifact | Run the pole-dwell survey → `scripts/georeference_wiser.py` fits the WISER-inch→field-cm transform. See [georeferencing plan](../implementation_plan/2026-07-01-wiser-georeferencing.md) |
| `wiser_rois.json` unconfirmed (`confirmed=false`) | Refuge/home/resource behavioral claims rest on inferred, not real, locations | Run `place_wiser_rois.py`, set `confirmed=true` |
| Weather↔WISER alignment wall-clock only (±5 min) | Weakens any weather-correlated activity claim | Independent clock check / sync verification |
| Sub-1 m proximity below the ~7 in jitter floor | Fine-grained social-distance claims unreliable | Keep proximity thresholds ≥1 m |

## Three research directions — status & path to publishable

The WISER analysis is organized around three directions. Cross-cutting prerequisites (the
georeference survey and ROI confirmation) gate the spatial directions (D2, D3); both are tooled
and awaiting field input.

### Direction 1 — Rain-influenced behavior  ⚠️ candidate
- **Driver:** `analyze_nightly_progression.py`. Candidate habituation −50% (6/28→6/29, both dry);
  in-window rain DiD ≈ 0, now reported with a **bootstrap 95% CI across rats** + a per-night
  **covariate table** (`night_covariates.csv`: wet-ground / tunnel / Sova).
- **Blocker:** wet-ground (6/30) confounded with habituation; n=5, 3 nights, one rain event.
- **Next:** more **dry-night** baselines / a 2nd cohort to separate rain from habituation; the new
  CIs + covariates make the current uncertainty explicit.

### Direction 2 — Social-influenced trace (route following)  ⚠️ candidate
- **Drivers:** leader-follower (utils + notebook §J) + `analyze_route_structure.py`. Above-null
  path reuse (22/30 pairs); corridors robust but night-to-night IoU ~27%; cross-rat > within-rat
  similarity (environment-driven, not memory).
- **Blocker:** single <24 h session; **WISER frame unverified**; ~7 in jitter caps fine geometry.
- **Next:** run the **georeference survey** — the route driver now adopts the surveyed boundary
  (`verified_boundary_in_wiser`) and adds `x_field_cm` automatically once a transform is confirmed;
  then multi-night/cohort replication and following asymmetry/dominance **stability**.

### Direction 3 — Sleep-location change (05:00–21:00)  ⚠️ candidate *(new 2026-07-02)*
- **Driver:** `analyze_daytime_sleep_site.py`. Per-animal primary rest site, within-day drift, and
  across-day stability. [plan](../implementation_plan/2026-07-02-daytime-sleep-site.md) ·
  [log](../change_log/2026-07-02-daytime-sleep-site.md)
- **Blocker:** sleep = low-speed proxy (not ephys/CV-validated); site precision gated by ~7 in
  jitter; frame unverified; ROI names provisional.
- **Next:** validate the sleep proxy against **CV shelter (CH05/CH06)** occupancy; more rest days;
  georeference + ROI confirmation to name sites and report shifts in cm.

### Cross-cutting prerequisites (unblock D2 & D3)
- **Georeference the WISER frame** — tooling built & self-tested (2026-07-01); awaiting the
  ≥6-pole dwell survey (`configs/wiser_georef_survey.csv` → `scripts/georeference_wiser.py`).
  [plan](../implementation_plan/2026-07-01-wiser-georeferencing.md)
- **Confirm the ROIs** — run `place_wiser_rois.py`, set `confirmed=true` (names sleep sites +
  refuge/home claims).
- Weather↔WISER alignment (±5 min) and the ≥1 m proximity floor remain as noted above; implement
  the `analyze_formal_recording.py` stub (gap detection / smoothing / session QC) for real sessions.

## Deferred (not on the publishability path)

Cross-modal integration, tracked in their own change logs, not required to publish the WISER
behavioral findings: environmental-audio Phase-2 (WISER/weather/audio merge, diurnal figures) —
see [`change_log/2026-06-29-environmental-audio-pipeline.md`](../change_log/2026-06-29-environmental-audio-pipeline.md);
and WISER×CV shelter-occupancy integration — see the CV shelter change log.

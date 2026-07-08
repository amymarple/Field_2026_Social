# Change log — Direction 3: tiered relocation (Stage A) + within-day temperature relocation (Stage B)

**Date:** 2026-07-07
**Commit:** uncommitted at time of writing.
**Plan:** [implementation_plan/2026-07-07-direction3-temperature-relocation.md](../implementation_plan/2026-07-07-direction3-temperature-relocation.md)
**Tracker:** [wiser_tracking_analysis/ANALYSIS_STATUS.md](../wiser_tracking_analysis/ANALYSIS_STATUS.md)
**Skill:** `.claude/skills/regime-aware-wiser-tracking` (weather acts on BOTH the animal path and the
UWB dropout path; a gap ≠ absence; jitter floor ~7 in; inch frame UNVERIFIED).

## Why

The prior Direction-3 run headlined "8/10 animal-day pairs relocated (>3× jitter ~21 in)", which
overstated it — several shifts were 22–28 in, i.e. jitter-scale. And it only measured across-day
primary-site change, not whether rest-site choice follows a **within-day** (temperature-linked)
pattern.

## What changed

### Stage A — tiered across-day relocation (no more "8/10 relocated")
- **`src/wiser_analysis_utils.py`**: `nearest_shelter`, `relocation_tier` (stable <30 · marginal
  30–75 · borderline 75–100 · robust 100–180 · major_shelter_switch ≥180 in **or** a house_1↔house_2
  identity switch), `classify_across_day` (enriches the stability table).
- **`scripts/analyze_daytime_sleep_site.py`**: `rest_site_stability.csv` gains
  `nearest_shelter_prev/nearest_shelter/shelter_switch/relocation_tier`; S2 figure recoloured by
  tier with 30/100/180 reference lines; verdict rewritten to the cautious claim.

### Stage B — within-day rest-site relocation & temperature
- **`src/wiser_analysis_utils.py`**: `day_window`, `zone_class`, `rest_bouts` (gap-aware low-speed
  rest bouts + dominant zone + dist-to-shelter + `dropout_frac`), `within_day_sequence` (per
  (night, shortid, window) dominant rest **site** with centroid + nearest shelter), `relocation_events`
  (between-window centroid shift ≥100 in / shelter-identity / zone change; jitter-scale excluded).
- **`scripts/analyze_daytime_rest_temperature.py`** (new driver): rest bouts, window sequence,
  relocation events, per-window rest-zone entropy + shelter-sharing (convergence proxy), AWN weather
  aligned to bouts/windows, **per-animal-day dropout guard**, per-day timelines + convergence figures,
  and a version-controlled report.
- **`scripts/selftest_rest_temperature.py`** (new): tiers, `nearest_shelter`,
  `within_day_sequence`+`relocation_events` (switcher → 1 shelter_switch, stable → 0), `rest_bouts`.

## Findings (3 days, 5 tags, exploratory / candidate)

**Stage A (across-day):** rest-site fidelity is **heterogeneous** — **12386 and 12407** do a
`major_shelter_switch` (house_1↔house_2, ~185–212 in) on both day-pairs; **12378, 12380, 12395** are
`stable` (all shifts < 30 in). The prior "8/10 relocated" was 4 jitter-scale pairs mislabeled; the
tightened count is **4 major switches (2 animals), 6 stable**.

**Stage B (within-day):**
- **Regular within-day sequence on the hot dry day (6/29, ~30 °C):** cool early morning (~17 °C) →
  rats OUT (refuge/open, 0 in shelter); late morning (~26 °C) → **all 5 converge to house_1**; at the
  **12:00–15:00 heat peak (~29–30 °C) rest sites DISPERSE** — 12386 & 12407 relocate to **house_2**
  and stay there through the afternoon. This house_1→house_2 midday move is a **candidate
  temperature-linked relocation** (NOT proof; the inch frame is unverified so house_2 is *not*
  verified cooler; "prefer above metal/in shade, house may be too hot" is an observer hypothesis).
- **Wet/hot day (6/30, ~34 °C + rain ~17:30):** rats START in house_1 (warm 22 °C morning, unlike the
  cool 6/29 morning), then **all leave shelters (open field) at the midday heat peak**; afternoon is
  the most dispersed window (rain confound).
- **Dropout guard:** 6/29 and 6/30 daytime dropout ≈ **0.0** (full WISER coverage) — so the wet-day
  reads are **real, not a UWB dropout artifact** (6/28 is 0.90 dropout = evening-only partial day, not
  a sensor problem).
- **Convergence vs dispersal:** peak *shelter* convergence is **late-morning**, not midday — the heat
  peak is associated with **dispersal** (to house_2 on 6/29, to open on 6/30), not shelter-seeking.

**Interpretation:** candidate / **measurement-limited**. WISER supports site-level within-day
movement and cross-shelter switching by 2 animals with hot-hour timing; thermal vs social vs
individual-habit cannot be separated without shelter temperature or more days. Language kept to
"temperature-linked". CV corroborates only visible shelter-resident periods (lower bound;
[2026-07-06 reconciliation](2026-07-06-cv-wiser-reconciliation-reframe.md)).

## Verification

- `python scripts/selftest_rest_temperature.py` → **PASS**; `scripts/selftest_cv_crossval.py` still
  **PASS** (regression). Real run on the snapshot `1stcohort_2026_2026-07-01.sqlite` + AWN weather:
  15 rest bouts, 17 within-day relocation events; figures T1 (timeline) / T2 (convergence)
  spot-checked. Read-only on the DB + weather CSVs.
- **Outputs:** data (CSVs + figures) → `D:\Wiser_plot\direction3_temperature_relocation_<ts>\`;
  version-controlled report → `wiser_tracking_analysis/outputs/direction3_temperature_relocation/
  direction3_temperature_relocation_report.md`.

## Known limitations / next steps
- 3 days only; temperature is an **outside-air proxy** (no shelter thermistor) and a covariate on both
  the animal and the UWB paths. Georeference confirmation would let sites be placed physically and
  test a real shade/cool-side hypothesis. A shelter-temperature logger or ephys would move "sleep" and
  "microclimate preference" from proxy to validated.

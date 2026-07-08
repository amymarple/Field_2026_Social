# Post-film window: separating condensation dynamics from rat detectability (weather-controlled)

## Context
The observer's post-film read is **mixed, not monotone**: the glass still fogs (maybe earlier / clears
later), yet rats sometimes look *more* visible through it. So the `antifog_film → bare_seated_post_film`
change may move **two different variables in opposite directions**, and "fraction of bins flagged
`degraded`" (task #2's metric) captures only one of them. Task #2 already showed pre-dawn native
degraded fraction does **not** support "post-film worse" (bare 06-30 = 0.83 > post-film 07-04 = 0.73)
and that fog tracks *which night* far more than *which regime* (the two bare nights alone span
0%→83%). This plan separates the variables and controls for weather.

**Decompose the sensor path into two measurable sub-variables (both weather-driven):**
- **C — condensation dynamics**: does the glass fog earlier, stay fogged longer, clear later?
  Measured by the **native** `view_quality_inside` *timeline* over the night.
- **D — rat detectability under fog**: given the glass is fogged, can we still see/count rats?
  A *different* variable — rat contrast/countability, not glass state.

`view_quality_inside` (degraded fraction) is essentially a **C** measure — an instrument-state flag —
**not** a biological-usability verdict. The observer's claim is C may worsen while D improves, i.e.
`degraded` **overstates** the problem post-film. This plan tests C, D, and the C≠D gap, weather-controlled.

## Hard constraints
Measurement context only. **Native** view quality (`--conditions ''`, weather-force OFF) wherever a
severity/timing number is computed — never the weather-forced `degraded` label. No detector/threshold/
safety/logic change; no retrain. **No causal "post-film changed the glass" claim unless weather-matched
comparisons support it** — and with the current handful of nights they likely won't, so default to
"measurement context, not proof." Keep the `glass_treatments.yaml` coating-damage flag **tentative**.

## Data & repo tools
- **Native view quality / detection**: `shelter_sleep.py --conditions '' --hours ...` over **full
  nights** (not just pre-dawn) → per-bin `view_quality_inside` (native), `n_detected_inside`,
  `inside_motion_score`, `n_inside_estimated`, `state`. Non-destructive rerun harness already proven
  (`scratchpad/predawn_compare.sh`) — extend the window.
- **Weather**: `preprocessing/computer_vision/fog_risk.py::load_weather()` → `air_temp_c`, `dew_point_c`,
  `humidity_pct`, `rain_mm_hr` (local ts). **Dew-point depression = air_temp − dew_point** is the
  physical condensation driver (fog when it →0). (WISER `load_weather` also keeps `dewpoint_c`.)
- **Fog-immune presence reference (KEY for D)**: WISER `wiser_shelter_state` / the
  `analyze_sleep_site_cv_crossval.py` machinery — WISER near-shelter presence is unaffected by glass fog,
  so it answers "was a rat there?" independent of view quality. Read-only snapshot; carry the inch-frame /
  jitter / dropout caveats (regime-aware-wiser-tracking).
- **Human GT (gold standard for D & C≠D)**: `validate_shelter.py` (interactive) — the observer labels
  true count + view (c/f/u) on sampled clips, incl. **degraded** clips.
- **Regime**: `glass_treatments.yaml` (bare vs post-film), joined via `glass_regime.py`.

## The four questions → concrete methods

### Q1. Does post-film change fog onset / duration / clearing time? (variable C)
- **Native full-night profiles**: `--conditions ''` reruns spanning each night (≈18:00→10:00 next day)
  for the comparison nights, → a per-bin native `view_quality_inside` timeline.
- Per-night **C-metrics**: `fog_onset` (first sustained degraded run, e.g. ≥3 consecutive bins),
  `fog_duration_h` (total degraded hours overnight), `fog_clear_time` (last degraded→clear transition),
  `peak_degraded_frac` (worst 2 h). CH05 only for severity (CH06 zones unusable, task #4).
- Compare bare vs post-film nights; **overlay weather** (Q2) on the same axis.
- Output: per-night fog timelines + a C-metric table. **Descriptive**; n small.

### Q2. Conditional on similar humidity / dew point / rain, is post-film worse? (C, weather-controlled)
- Join AWN per night → overnight summaries: min `air_temp_c`, min **dew-point depression**, mean RH,
  rain total + timing.
- **(a) Case-based matching (primary, honest at low n)**: find bare↔post-film night pairs with similar
  overnight dew-point-depression + rain; compare C-metrics within each matched pair.
- **(b) Bin-level model (secondary, flag underpowered)**: pool all native bins;
  `native_degraded ~ dewpoint_depression + rain + air_temp + regime`; inspect the `regime` term as the
  weather-controlled post-film effect. Bins within a night are correlated → cluster by night / treat as
  descriptive. Report CI; expect it to straddle 0 with current n.
- **State plainly** if matched nights are too few → no causal claim.

### Q3. Under degraded view, are rats more detectable/countable post-film than bare? (variable D)
Two complementary measures, both restricted to **native-degraded** bins:
- **(a) WISER-referenced (no new labeling) — CONSERVATIVE**: WISER is a high-confidence *presence*
  reference, **NOT full-visibility ground truth**. A CV miss in a WISER-near-shelter bin may be fog, but
  it may equally be the wall-edge blind zone, doorway occlusion, zone mismatch, or the rat being inside
  but **not visible through the camera view**. So restrict Q3a to bins where WISER presence is **STABLE
  and maps with HIGH CONFIDENCE to the camera-visible shelter/glass region**: use `wiser_shelter_state`
  **high-confidence** episodes (sustained, low spread) in the ROI **core** (not the buffer edge / doorway),
  never single jittered fixes. Among those native-degraded bins, compute CV detection rate
  (`n_detected_inside>0`) + count, **post-film vs bare**. Report strictly as **WISER-referenced
  detectability, NOT human-GT detectability** — a CV miss here is *consistent with* fog, not proven fog
  (residual confounds: occlusion, inside-but-not-visible, zone mismatch, and the **unverified inch→cm
  frame** which limits how precisely WISER maps to the camera-visible region). Gold-standard D is Q3b.
- **(b) Human GT (gold standard)**: `validate_shelter.py` sampled to **degraded** bins in both regimes;
  observer labels true count + countable? Compare **detector recall + count-MAE + human countability**
  within degraded bins, post-film vs bare. Stratify by weather where n allows.

### Q4. Is `degraded-view` a reliable proxy for biological usability? (the C≠D gap)
- From Q3's GT (and/or WISER): cross-tab **auto `view_quality` (clear/degraded/unusable) × human-countable
  (yes/no) × detector-correct**.
- Metric: **P(rat still countable | view = degraded)**, per regime. High ⇒ `degraded` **overstates** the
  problem (it's an instrument flag, not a usability verdict). If **P(countable|degraded) is higher
  post-film than bare**, that quantitatively confirms the observer's "more visible under fog post-film."
- Reframes `view_quality` explicitly as a **condensation/instrument** flag, distinct from
  **biological usability** — and measures the gap rather than assuming they're equal.

## Confound & power (state up front in every output)
Recent frequent rain ⇒ **weather dominates fog and is confounded with regime**; with ~2 bare + ~2
post-film nights, a causal "post-film changed the glass" claim is **not supportable**. This plan is
built to (1) separate C from D so a *future* weather-matched dataset can answer it, and (2) accumulate a
weather-tagged fog profile per new night. All results framed as **measurement context, not proof**;
`glass_treatments.yaml` stays tentative regardless of outcome.

## Execution (phased)
- **Phase A — automated, runnable now** (no user input): (1) native **full-night** profiles for nights
  06-29→07-04 (primary) **plus 07-05/07-06 if transferred, labeled "additional post-film/rainy" — not
  independent causal evidence**; native view (`--conditions ''`), night hours only, non-destructive;
  (2) weather join + per-night C-metric/weather table (Q1, Q2); (3) conservative WISER-referenced
  detectability under native-degraded bins (Q3a). → a report that **separates**: (1) fog
  onset/duration/clearing, (2) weather-matched condensation comparison, (3) WISER-referenced CV
  detectability under native-degraded bins, (4) where human degraded-bin labels are still needed.
- **Phase B — interactive, needs you**: `validate_shelter.py` targeted at **degraded** post-film + bare
  bins (I set up the command; you label). → GT for Q3b/Q4 (the first detectability GT under fog).
- **Phase C — synthesis**: combine into a measurement-context report answering Q1–Q4 with explicit
  "not causal / weather-confounded" verdicts, plus a `cv-measurement-auditor` pass. Update
  `docs/methods/shelter_failure_modes.md` (item 5/#3) and the `glass_treatments.yaml` note with the
  C-vs-D framing.

## Verification / outputs
- Non-destructive: full-day/-night outputs backed up + restored (as in task #2); reruns are
  `--conditions ''` and never overwrite the canonical weather-forced outputs.
- Deliverables: per-night native fog-profile + weather table; WISER-referenced detectability-under-fog
  table; a degraded-bin GT protocol + (once labeled) the C≠D cross-tab; one synthesis report.
- Every number tagged native-vs-forced, regime, weather covariates, camera, and CV `visible_count`
  (lower bound) vs WISER presence — per the skill's output requirements.

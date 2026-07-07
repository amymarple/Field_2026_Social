# Shelter CV — known measurement failure modes

> **Read this first** (with [`shelter_cv_measurement.md`](shelter_cv_measurement.md)). This is the
> current-state catalogue of known ways the CH05/CH06 shelter-CV numbers can be wrong *as a measurement*.
> It mirrors the [`regime-aware-cv-measurement`](../../.claude/skills/regime-aware-cv-measurement/SKILL.md)
> skill. Evidence is cited to `change_log/` and `outputs/audit/` reports — those remain the provenance;
> this is the summary.

## How to classify a result

Every shelter-CV result must be placed in exactly one of four buckets (from the skill):

1. **likely behavioral signal**
2. **likely measurement artifact** (sensor path)
3. **mixed / ambiguous**
4. **invalid / lower-bound only**

The failure modes below are the concrete sensor-path / coverage-limit reasons a result lands in bucket 2,
3, or 4. Do **not** merge categories to make a cleaner story; a number that is only trustworthy as a floor
(wall-edge, degraded glass) is **lower-bound**, not a head-count.

## Summary

| # | Failure mode | Class | One-line |
|---|---|---|---|
| 1 | Degraded-view optical artifact | Sensor artifact | Fog/glare/rain on glass → detector errors; capped below `occupied_high_motion` |
| 2 | High fog-risk condensation path | Sensor artifact + lower-bound | Pre-dawn condensation; presence-recall 86 % → 17 % (low → high fog-risk) |
| 3 | `antifog_film` / optical-regime artifact | Sensor artifact (regime-attributable) | Film regime degrades view at *low* fog-risk; film + lift-removal + reseating confounded |
| 4 | Wall-edge structural blind zone | Lower-bound | Nadir cams miss rats along interior walls; counts are a floor |
| 5 | Heat exterior refuge in structural blind zone | **Unresolved / candidate** | Hot daytime → target in exterior wall-adjacent refuge outside the visible volume (hypothesis, not coded) |
| 6 | Huddle undercount | Lower-bound | Piled rats counted as fewer boxes (gt = 4 → detected 2–3) |
| 7 | Weak motion threshold / rest proxy | Sensor artifact + unresolved | `robust_inside_motion` threshold unvalidated; low motion ≠ rest |

---

### 1. Degraded-view optical artifact — *sensor artifact*

**Definition.** Rain, condensation/fog, water drips, or sun glare on the IR glass between lens and animals
degrade the optical path, causing detector misses, count bias, and motion-score artifacts.

**Evidence.** The mechanism and safety response are enforced in code: a `degraded` inside view is capped
at `occupied_low_motion` and never scores `occupied_high_motion`; `unusable` → `indeterminate`
(`shelter_sleep.py:134-141`; `regime-aware-cv-measurement/SKILL.md`). Field-observed instances include
"13:00 glare over the house" (`FIELD_OBSERVATIONS.md`). Provenance:
`change_log/2026-07-01-glass-degradation-zones.md`.

**Classification.** Sensor artifact. The pipeline already contains it via the hard safety rule; a degraded
bin's motion/high-activity reading is not trustworthy.

### 2. High fog-risk condensation path — *sensor artifact + lower-bound*

**Definition.** Pre-dawn radiative cooling drives high humidity → condensation film on the interior glass;
rats become hardly visible even when present.

**Evidence.** On the 2026-06-30 validation (current detector), presence-recall **collapses with rising
fog-risk: 86 % (low) → 77 % (medium) → 17 % (high)**, with high-risk bins **~90 % degraded view** and 5
misses (`preprocessing/computer_vision/outputs/audit/fog_risk_audit_2026-07-06.md`). The weather-only
`fog_risk` flags the *observed* fog windows independently (06-30 & 07-03 ~04:00–06:00 read high, gap
~1.2 °C, RH ~93 %). Field notes: "~04:00–06:00 EDT heavy fog on the CH05/CH06 shelter IR glass, rats
hardly visible" (`FIELD_OBSERVATIONS.md`).

**Classification.** Sensor artifact (weather-driven) and a lower-bound: in these windows a visible
"empty" / "no motion" is **fog-obscured, not true absence/stillness**.

### 3. `antifog_film` / optical-regime artifact — *sensor artifact (regime-attributable)*

**Definition.** The anti-fog film applied 2026-07-02 ~13:00 is itself a view-degrading instrument: it
degrades the view at **low** fog-risk (a non-weather optical artifact), and the observer reported the view
was **worse with the film than with bare glass**.

**Evidence.** CH05 on 2026-07-02, degraded bins by regime: `lift_1cm` (00:00–13:00) = 1 degraded;
`antifog_film` (13:00–24:00) = **34 degraded**, in the afternoon / low-condensation-risk hours
(`fog_risk_audit_2026-07-06.md`). The 13:00 change applied the film **and** removed the ~1 cm lift **and**
reseated the glass together, so effects are **regime-attributable, not film-attributable** — the audit
cannot isolate which change caused the degradation (`glass_treatments.yaml` `confounded: {value: true…}`;
`change_log/2026-07-01-glass-degradation-zones.md`; `FIELD_OBSERVATIONS.md`).

**Classification.** Sensor artifact, carried as the `glass_regime` covariate (instrument state), separable
from the weather-condensation path but not decomposable into which coincident change caused it.

### 4. Wall-edge structural blind zone — *lower-bound*

**Definition.** CH05/CH06 are near-nadir views of a 3-D shelter; a band directly along each interior wall
is perspective-occluded, and rats rest/hide against the walls there. Visible counts are a floor.

**Evidence.** "By perspective, a band directly along each of the 4 interior walls is not visible… the
human `gt_count` is visible-only — a lower bound of true occupancy; both human and detector miss wall-edge
hiders" (`change_log/2026-07-01-glass-degradation-zones.md`). In the CV×WISER reconciliation, CV recall is
**~0.50–0.60 flat across every stratum** — clear ≈ degraded view, low ≈ high fog-risk — so the miss does
**not** concentrate in degraded/foggy bins; it is a coverage limit, not fog
(`change_log/2026-07-06-cv-wiser-reconciliation-reframe.md`;
`wiser_tracking_analysis/outputs/audit/ALIGNMENT_DIAGNOSIS_2026-07-02.md`).

**Classification.** Lower-bound — a structural optical-geometry limit (visible count ≤ true occupancy),
not a detector failure.

### 5. Heat exterior refuge in structural blind zone — *unresolved / candidate (not implemented)*

**Definition (candidate hypothesis).** During hot, high-solar daytime the IR-glass shelter interior can
warm (greenhouse-like). The target may then stay **outside** the visible shelter interior — pressed
against the exterior wall, doorway edge, or a shaded wall-adjacent region. WISER still reports near-shelter
presence while CH05/CH06 visible-inside CV reads empty on **clear** glass. This is a coverage/definition
mismatch plus environmental-use context — **not** a CV detector false negative, **not** true absence,
**not** fog/glass optical failure, and **not** proof the target was inside.

**Evidence (qualitative only).** Field notes record hot days (Day 2 ~30 °C, Day 3 ~34 °C, Day 4 ~36 °C),
an explicit "the house may be too hot (behavioral thermoregulation)" hypothesis, an "afternoon hot period
… thermal-refuge failure" note, and a shelter-depth "thermal–risk tradeoff" (`FIELD_OBSERVATIONS.md`).
Some wall-adjacent exterior regions are **structurally unobservable** from CH05/CH06; CH01/CH02 give only
partial, opportunistic overview (at least one wall-adjacent side is not reliably visible), so manual
inspection cannot fully resolve this class.

**Status.** **Unresolved candidate — report-semantics only; no code gate is implemented.** A soft trigger
was specified (hot high-solar daytime + high ambient temperature + clear/low-fog + WISER-present +
CV-empty; ambient temperature as a provisional proxy since no house-temperature sensor exists) but is
**not** an exclusion rule and **not** wired into the pipeline. Treat a WISER-present / CV-empty bin under
these conditions as **unknown / lower-bound**, not "CV failed to detect a visible target."

### 6. Huddle undercount — *lower-bound*

**Definition.** Sleeping rats huddle (pile tightly); overlapping/occluded individuals are underestimated
because the detector counts separable boxes.

**Evidence.** Huddle-like frames (≥ 4 overlapping boxes) are **~16 % of all labeled / ~29 % of positive**
frames; after detector fine-tuning big huddles **still undercount** (gt = 4 → detected 2–3, vs the old
detector's gt = 4 → 0) (`change_log/2026-07-01-glass-degradation-zones.md`). A dedicated `huddle` class is
deferred until the frequency of *truly indivisible* piles is known.

**Classification.** Lower-bound — a measurement-definition limit (true count ≥ detected box count), not a
sensor artifact.

### 7. Weak motion threshold / rest proxy — *sensor artifact + unresolved*

**Definition.** `robust_inside_motion` is a glass-noise-resistant motion score, not a validated rest/sleep
detector. Low motion under degraded glass is not rest, and the still/moving threshold is unvalidated.

**Evidence.** Detector-vs-human motion agreement is weak — ~39 % on clear bins, ~42 % at threshold 0.30
(grid-search suggests ~0.00 → ~58 %), i.e. the metric/threshold "needs work"
(`change_log/2026-07-01-glass-degradation-zones.md`). The skill's forbidden shortcut: "claim sleep from
low motion without validation (low motion under degraded glass is not rest)"
(`regime-aware-cv-measurement/SKILL.md`). There is no ephys/behavior-video validation of the
low-motion → rest mapping.

**Classification.** Sensor artifact (optical-noise-reduction conflated with behavior) **and** unresolved
(threshold not validated). `occupied_low_motion` is an operational placeholder, not a behavioral claim.

# WISER tracking — data QC log

Running record of quality-control observations for the WISER UWB tracking data
(`D:\Wiser\data\1stcohort_2026.sqlite`). One dated entry per issue, **newest first**.
Each entry states what was seen, the evidence, the root cause, what (if anything) was
changed, and the impact on downstream analysis. This complements the automated hourly
coverage alert (see [README_occupancy.md](README_occupancy.md)) and the per-analysis
[change logs](../change_log/).

Units are **inches** in the WISER native (offset-origin) frame — alignment to the
physical paddock is **unverified**. `shortid` is a tag, resolved to an animal via
[`configs/rat_identities.csv`](configs/rat_identities.csv).

| Severity | Meaning |
|---|---|
| ⛔ blocking | data unusable / capture at risk until fixed |
| ⚠️ caveat | usable, but a known bias/limitation to account for in analysis |
| ℹ️ info | expected/benign, logged for provenance |

---

## 2026-07-09 — ⚠️ Hypnos (305c / 12380) WISER tracking DISCONTINUED — hypothalamic implant dropped

**What.** The hypothalamic implant on **Hypnos** (shortid `12380`, physical tag `305c`) detached
("dropped") at **2026-07-09 03:35:41 −04:00**. **All WISER data for this animal at/after that
timestamp is INVALID** and must be excluded from analysis.

**Action — removes it from the warning.** Set `valid_until = 2026-07-09T03:35:41-04:00` for 12380 in
[`configs/rat_identities.csv`](configs/rat_identities.csv). The hourly coverage Slack alert
(`scripts/plot_hourly_occupancy.py`) auto-excludes any tag past its `valid_until`, so Hypnos is
**dropped from the expected roster** — no more "missing tag" / gap warnings for it, and a mid-hour
cutoff is clamped so the discontinuation is not mis-reported as a trailing gap. No code change or
task reinstall needed (the exclusion is data-driven from the roster).

**Impact.** Tracked cohort is **4 animals** from 2026-07-09 03:35:41 onward — Siesta (12378),
Sen (12395), Dormi (12407), Nox (12386) — after Sova (12409, retired 2026-06-29) and now Hypnos.
Behavioral/social analyses covering this period must run on 4 animals and **discard any Hypnos
(12380) fixes with timestamp ≥ 2026-07-09T03:35:41−04:00**. Hypnos data *before* that time remains
valid.

---

## 2026-07-10 — ⚠️ Tag batteries aging cohort-wide; 306b/Sen lowest → DM battery watch added

**Trigger.** Field question: is 306b (Sen) battery low? The DB logs `battery_voltage` per fix.

**Finding (read-only, `battery_voltage` by tag/day, EDT).** All five live tags are draining **in
parallel**, ~2.95 → ~2.85 V over 12 days, and the drop is **accelerating** (Sen ~4 mV/day early
→ ~14 mV/day by 7/04–7/10 — the discharge-curve knee). This is normal cohort aging, **not** a single
failing tag. Recent 6 h medians (to 2026-07-10 00:42 EDT):

| Tag | median V | min dip | vs cohort |
|---|---|---|---|
| **Sen / 306b** | **2.842** | **2.759** | −27 mV (lowest) |
| Nox / 3062 | 2.869 | 2.826 | ~0 |
| Dormi / 3077 | 2.869 | 2.842 | ~0 |
| Siesta / 305a | 2.869 | 2.842 | ~0 |
| Hypnos / 305c | (in burrow — no recent fixes) | — | 2nd-lowest historically (dip 2.745 on 7/09) |

Cohort median 2.869 V. **Sen (306b) is the front-runner for a swap; Hypnos (305c) next.**

**Not the cause of the tracking gaps.** Link quality is healthy for all tags (`anchors_used` ≈ 8.4,
same for Sen) — a dying tag would drop out *everywhere*, evenly. The earlier Sen dropouts were
refuge/burrow NLOS (entries below), not battery.

**Action.** Added a **DM-only battery watch** to the hourly occupancy job (`plot_hourly_occupancy.py`,
run once per invocation, DB scan rate-limited to every 6 h): DMs Hongyu when any expected tag's recent
median voltage is **≤ 2.70 V** *or* **≥ 40 mV below the cohort median** (once/day/tag, deduped).
Sends only to the user DM (`WiserBatteryChannels`, else the DM/user-id subset of the config) — never
the team channel. At the current levels **nothing fires** (Sen 2.842 > 2.70, −27 mV < 40). Verified
with a DM wiring test 2026-07-10.

**Caveat.** Thresholds (2.70 V / 40 mV) are **placeholders** — the manufacturer's low-battery cutoff
and fresh voltage are unknown, so "low" is currently relative (Sen vs. pack) + the accelerating slope.

**Follow-up.** Get the tag low-battery cutoff / fresh voltage to set a real threshold and extrapolate a
swap-by date; plan swap/charge starting **306b (Sen)** then **305c (Hypnos)**; watch the daily *min*
voltage — dropouts appearing in the **open** (not just shelters) would be the true end-of-life signal.

---

## 2026-07-05 — ℹ️ Hypothesis (SUPERSEDED for refuge_4): shelter wall material may degrade WISER signal when wet

> **Status — SUPERSEDED as the refuge_4 explanation (2026-07-05).** The refuge_4 signal loss is
> confirmed to be a **dug burrow/tunnel underneath the shelter** (animals go below grade), *not* the
> wall material — see the refuge_4 entry below. Retained here as a **general materials caveat** for
> future wet, enclosed above-ground shelters, where the wet-composite RF concern could still apply.

**Field note / interpretation (HC).** The shelter/wall material under consideration is a composite
of **timothy hay, tapioca starch, glycerin, sodium alginate, propionic acid, salt, rosemary, paper,
and jute**, formed into a **~1 inch thick enclosed wall with a single small opening**, which **may
become damp/wet** in the field. Concern: does this attenuate or bias WISER/UWB localization for an
animal inside?

**RF assessment.**
- **Dry → low risk.** Hay/paper/jute/starch are cellulosic — low-permittivity, low-loss dielectrics.
  A 1" dry wall costs a fraction of a dB at UWB (microwave) frequencies. Acceptable if kept dry and
  loose.
- **Wet → the real risk.** Liquid water is strongly lossy at UWB frequencies, so what matters is how
  much water the wall holds and whether it is conductive:
  - **Salt is likely the dominant factor.** Dissolved NaCl turns retained moisture into an **ionic
    conductor**; ionic-conduction loss (strongest at the lower UWB band) adds to water's dielectric
    loss and also raises reflection. "Salt + moisture" ≫ the other ingredients.
  - **Glycerin, sodium alginate, tapioca** are hygroscopic / gel-forming — they *retain* water (and
    glycerol is itself lossy at GHz), so this composite stays wet longer than plain hay that dries.
  - **Propionic acid, rosemary** — RF-negligible.
- **Mechanisms, in order of concern.**
  1. **Attenuation → dropouts.** A wet, mildly conductive enclosed wall drops the tag below the
     detection threshold at enough anchors → no position solve (matches the refuge_4 gaps below).
  2. **Range/position bias (subtle, easy to miss).** A high-permittivity wet slab slows propagation
     and attenuates the true first path, so UWB time-of-flight reads **biased long** / locks onto a
     later multipath → a *systematic position bias*, not just noise. This can distort shelter-edge
     occupancy even when fixes are NOT fully lost.
  3. **Multipath + jitter.** Reflection off the wet wall increases multipath → higher range variance
     → position jitter.

**Link to evidence & caveat.** This is consistent with the refuge_4 dropouts (next entry) and the
rain/wet-ground timing from 6/30 on. **But it is not yet isolated:** a solid shelter wall plus the
rat's own body already cause NLOS on their own, so wet material is a plausible **aggravator**, not a
proven independent cause.

**Verdict.** Acceptable **only if kept dry and loose**. An **enclosed, damp** 1" wall around the
animal/tag is the risk case and could degrade shelter-occupancy accuracy (both dropouts and bias).

**How to test (before trusting shelter occupancy on wet days).**
- Correlate per-refuge dropout rate/duration with rain events (weather CSVs) — dry vs. post-rain.
- Controlled: park a **stationary reference tag** inside a shelter, measure fix rate / jitter /
  apparent range dry vs. saturated; and a bench test through a dry vs. water-saturated sample at
  fixed anchor distance.
- Check whether post-gap positions show a **consistent directional range bias** (biased away from
  the occluded anchors) — the signature of mechanism (2).

**Impact if confirmed.** Shelter/home dwell time under-counted (dropouts) *and* shelter-boundary
positions biased on wet days — nightly-behaviour home-use and shelter-edge metrics would need a
wet-day flag / down-weighting.

---

## 2026-07-05 — ⚠️ Midday per-tag data gaps = shelter (refuge_4) NLOS occlusion

**Symptom.** The hourly coverage alert fired midday 2026-07-05: Nox (12386) 22.0 min,
Dormi (12407) 15.4 min, plus shorter Sen (12395) / Siesta (12378) holes. An earlier
quiet cluster occurred 2026-07-01 (Hypnos + Sen, ~13:37–17:06 EDT).

**Diagnosis (read-only trace of the live DB).**
- **Not a system fault, not tag batteries.** In every gap the *other four* tags kept
  reporting at full ~4.4 Hz; all tags log a steady ~338k fixes/day (6/30–7/04); and it
  was *different* tags on different days (7/01 Hypnos+Sen; 7/05 Nox+Dormi). A
  recorder/DB/anchor/reboot fault would silence all tags together — it never did.
- **Location-pinned NLOS.** Every 7/05 gap sat at **refuge_4 (~723,636 in)**, with
  displacement across the gap of 1–13 in (below the ~7 in jitter floor → the animal was
  motionless). 7/01 gaps clustered at ~(485,600). While Nox sat silent in refuge_4, Sen
  was in the *same spot* and also dropping — yet Siesta/Hypnos/Dormi huddled together
  elsewhere (~500,730) reported flawlessly (~5,200 fixes each, sd ~4 in). So it is the
  **shelter location**, not huddling per se: a rat resting in/at a covered refuge loses
  line-of-sight to enough UWB anchors and reappears when it moves (often with a
  re-acquisition jump, e.g. Sen's 227-in jumps on 7/01).
- Timing (midday) matches daytime rest for these nocturnal animals.

**Root cause — CONFIRMED 2026-07-05 (field observation, HC).** The animals **dug a burrow /
tunnel underneath refuge_4 and hide inside it, below grade.** The signal loss is therefore
**subterranean occlusion**: a rat underground is behind soil (a severe UWB attenuator,
especially when wet) and loses line-of-sight to *all* above-ground anchors at once — hence
the clean, *complete* multi-minute dropouts pinned exactly at refuge_4, with the animal
motionless and reappearing only on emergence. This is NOT the shelter wall material (see the
superseded material-hypothesis entry above) and NOT poor anchor geometry — it is a physical
dead zone below the shelter. refuge_4 (and any other burrowed refuge) is effectively an
**invisible zone** to WISER while occupied.

**Action.** No data fix required (behavioural/physical, not a pipeline bug). Tuned the
hourly coverage alert to flag real problems only: threshold **5 → 15 min** and added
**shelter-occlusion suppression** (gaps starting/ending within 18 in of a `refuge`/`tunnel`
ROI are logged as an FYI, not alerted). Whole-hour-missing tags and open-field gaps ≥15 min
still alert. See [change_log/2026-07-04-wiser-hourly-coverage-slack.md](../change_log/2026-07-04-wiser-hourly-coverage-slack.md).

**Impact on analysis.** Shelter/home **dwell time is under-counted** during deep refuge
rest (no fixes while occluded), so the nightly-behaviour home-use fractions are
**conservative** — true sheltering is somewhat higher than measured. Positions immediately
after a gap are noisy (re-acquisition) but are already bounded by the speed cap.

**Follow-up.** An added/moved anchor will **not** recover fixes — the animals are underground,
so no above-ground geometry helps. Instead: (a) treat refuge_4 (and any burrowed refuge) as a
known **WISER blind zone** and infer "in burrow" from the disappearance-at-refuge_4 signature
rather than from fixes; (b) map which refuges have burrows (periodic check — they may dig more);
(c) cross-check occupancy with the shelter cameras / thermal where possible, though a burrow
under the shelter may be blind to those too.

# Field Observations Log

Narrative field log for the **Field_2026_Social** pilot, mirroring the Notion notebook
**"1 - Pilot Study → Daily Observations"** (parent: `Rat_field_social_sleep_2026`). It captures
day-by-day field context — rat behavior, sleep, weather, equipment changes, and analysis ideas —
that is otherwise scattered across structured files or recorded nowhere in the repo.

This is a **provenance / context layer, not a source of truth.** Machine-readable cutoffs
(animal validity, weather-exclusion windows, recording gaps) live in the structured files listed
under [Cross-references](#cross-references); this file narrates and points at them.

**Clock caveat.** Times below are as **observed by the user (local wall-clock, EDT / UTC−4)**. The
Reolink on-screen (OSD) clock runs **~1 h behind the recording filenames** on this rig — when
mapping a window to specific hourly files, reconcile against the filename timestamps (see
`reolink_record` notes and `data_manifests/field_conditions.yaml`). WISER timestamps are Unix-ms
UTC. Do not assume two devices share a clock.

## How to use this log

Use this file **before** interpreting WISER, CV, audio, or LFP data for a specific date. Treat it
as field context: it can explain anomalies, suggest covariates, and generate hypotheses. Do
**not** treat observer interpretations as labels or exclusion rules. For exact animal-validity
cutoffs, weather windows, equipment gaps, and analysis exclusions, use the structured files listed
in [Cross-references](#cross-references).

**Each day separates three things — keep the boundary sharp:**

- **Observed field events** — facts the observer saw (what happened, when).
- **Data interpretation flags** — data-quality implications (what to caveat/exclude/watch for).
- **Scientific ideas / hypotheses** — theory; *not* ground truth, *not* labels.
- **Analysis hooks** — concrete, executable analysis directions.

> ⚠️ **Circularity warning.** An agent that reads "rain → rats hid" before analyzing movement may
> *explain away* the data instead of measuring it. Observations are covariates and context, never
> pre-assigned outcomes. **Field event ≠ data artifact ≠ scientific interpretation.**

## Animal roster

Source of truth for identity + validity: `wiser_tracking_analysis/configs/rat_identities.csv`.
`shortid` is the **WISER decimal tag ID**, distinct from the **physical hex tag** on the coband —
`shortid` is a *tag*, not an animal name.

**How each modality identifies a rat:**
- **WISER (UWB tracking):** by tag — the decimal `shortid` (its physical tag is the hex ID).
- **Color cameras CH01/CH02:** by **coband color** (Blue / Green / Red / Yellow / Black …) — color
  is only recoverable on these two color-capable channels.
- **IR cameras CH03–CH06:** by **coband pattern** (Vertical Line / Open Circle / N/A / Filled
  Circle / X …) — these are infrared/monochrome, so color is *not* distinguishable; the ink pattern
  is the only visual ID. Use the Pattern column, never the Coband color, when identifying rats on
  CH03–CH06.

| shortid (dec) | hex tag | Name | Coband | Pattern | Ink | Initial wt | Status |
|---|---|---|---|---|---|---|---|
| 12378 | 305a | Siesta | Blue | Vertical Line | Silver | 345 g | active |
| 12395 | 306b | Sen | Green | Open Circle | Copper | 360 g | active |
| 12407 | 3077 | Dormi | Red | N/A | N/A | 376 g | active |
| 12386 | 3062 | Nox | Yellow | Filled Circle | Purple | 357 g | active |
| 12380 | 305c | Hypnos | Black | X | Silver | 368 g | active |
| ~~12409~~ | ~~3079~~ | ~~Sova~~ | ~~White~~ | ~~Triangle w/ Line~~ | ~~Pink~~ | 296 g | **REMOVED 2026-06-29 15:00 EDT** |

- **Sova (12409)** — mouth injury / breathing issues (superglue-sealed nose?); removed
  **2026-06-29 15:00 EDT**. `valid_until` set in `rat_identities.csv`; excluded from night-2+
  analyses (see `wiser_tracking_analysis/ANALYSIS_STATUS.md`).
- Timeline: implant surgery **2026-06-18**, tags fitted **2026-06-26**, **released into paddock
  2026-06-28 19:25 EDT**. Paddock is 20 × 40 ft.

## Daily observations

### Day 1 — 2026-06-28 · obs HC · Equipment ✅ OK · sunny/warm ~22–23 °C at release

- **Observed field events:** Released 19:25. 19:00–20:00 rats hiding together in one spot;
  20:30–21:00 begin to explore; hopping seen even in the grass-free corridor; following behavior
  noted.
- **Data interpretation flags:** Reolink Client interfered with video saving → **deleted from the
  field PC** this day; Reolink App reduced bandwidth → streaming kept low/minimal (early Day-1
  video may be affected). Release at 19:25 — pre-release fixes are not free-behavior.
- **Scientific ideas / hypotheses:** "social safety" — a region explored by one rat may be treated
  as safe by others; socially-seeded preplay of others' trajectories; following as a
  risk-reduction strategy. (Hopping in a grass-free corridor argues against a purely
  substrate-driven explanation.)
- **Analysis hooks:** test whether regions first visited by one rat show increased *later*
  visitation by others; quantify following / leader-follower events against a shuffled null.

### Day 2 — 2026-06-29 · obs HC · Equipment ✅ OK · sunny/hot ~30 °C

- **Observed field events:** 11:48 pile together to sleep, prefer above metal / in shade; 13:00
  strong glare/reflection over the house; 16:54 new sleep postures, sleeping near the entrance by
  the IR doorway; 21:00 waking, more paddock exploration + following. Sleep: longer true sleep and
  varied postures (esp. Hypnos); Hypnos the only one in the high-value box for hours; REM-like
  twitching visible from the overhead 520A (CH05/06) cameras.
- **Data interpretation flags:** **13:00 glare over the house** → CH05/06 glass-glare window,
  expect view-quality degradation (cross-ref `field_conditions.yaml` and the glass-degradation
  zones). Channel # is **not** hardcoded in the Reolink software — toggle videos with arrows only,
  don't enter display mode (mislabeled-channel risk). "Above metal / in shade" preference confounds
  any temperature reading from shelter occupancy. **Sova removed this day at 15:00 EDT** — exclude
  after the cutoff (see roster / `rat_identities.csv`).
- **Scientific ideas / hypotheses:** "leader" rats may sleep more, or in new postures, from higher
  cognitive demand during exploration; the house may be too hot (behavioral thermoregulation).
- **Analysis hooks:** compare 03:00–05:00 low-temperature movement/ripple structure vs warm-hour
  sleep (`I2_hourly_by_clock.png`); relate posture diversity to prior-day exploration; test per-rat
  high-value-box dwell (Hypnos).

### Day 3 — 2026-06-30 · obs MA, HC, CS · Equipment ✅ OK · sunny/humid high 34 °C; thunderstorm/rain ~17:30

- **Observed field events:** 15:00 sleeping in home boxes, Sen digging (mesh barrier held); 17:30
  rain + thunder, rats wake, Sen bolts low-value → main shelter, unsettles the others, brief
  minimal fighting; 18:30 rain stops, mixed sleep/awake, some exploring outside.
- **Data interpretation flags:** weather event **inside** the behavioral window (rain ~17:30, plus
  the fog/rain windows enumerated in `field_conditions.yaml`) → expect video-visibility degradation
  and false occupied-high-motion from drops. Morning IR-light heat condensation fogged the shelter
  glass → **patched with aluminum tape**, changing the IR/light/heat condition vs the prior day
  (not a like-for-like baseline). A movement drop here **may reflect weather, not habituation** — do
  not attribute causally.
- **Scientific ideas / hypotheses:** rain may transiently raise arousal, then drive shelter
  aggregation; increased fighting may reflect territory / hierarchy formation; sleep position may
  carry social-rank information; digging = nesting/burrowing toward colder soil.
- **Analysis hooks:** compare shelter occupancy before / during / after rain; test whether the
  fighting-event rate co-varies with storm onset; model weather as an explicit covariate before any
  habituation claim.

### Day 4 — 2026-07-01 · obs — · Equipment ✅ OK · sunny/humid high 36 °C; thunderstorm/rain ~19:45

- **Observed field events:** 12:48 all concentrated in a single house, ~no motion,
  quiescent/possibly sleeping (fog obscured the view); 14:35 two rats hide under the water tower;
  16:00 IR glass lifted ~1 cm to reduce fog; rain 19:45; fog 21:00; nesting on the door entries on
  **CH05 but not CH06**.
- **Data interpretation flags:** heavy fog + rain windows → CH05/06 view degradation; the quiescent
  "no motion" may be **fog-obscured rather than true stillness**. **IR glass physically lifted
  ~1 cm at 16:00** — a rig change mid-recording (view/geometry differs before vs after). The CH05 vs
  CH06 nesting asymmetry may be a per-shelter difference, not a rat-behavior difference.
- **Scientific ideas / hypotheses:** the quiescent / consolidated-rest state is a target for LFP;
  sleep-location differences between shelters may be meaningful.
- **Analysis hooks:** segment CH05/06 before vs after the 16:00 glass lift; quantify quiescent bouts
  only within clear-view windows; contrast CH05 vs CH06 nesting/occupancy per shelter.

### Day 5 — 2026-07-02 · obs HC · Equipment ⚠️ anti-fog film added + 07-01 glass lift removed (CH05/06)

- **Observed field events:** _Behavior:_ 10:17 four rats group together outside (five remain after
  Sova); rats run very fast across the field, sometimes pausing mid-run to pick something off the
  ground before continuing; they snack on insects, not only eating at the house; sometimes stop by
  the rocks in the middle. 20:26 fighting among 3 rats near the middle rocks — **Sen won against the
  other two.** _Rig:_ ~**13:00 EDT** two coincident changes on the CH05/CH06 shelter IR glass: an
  **anti-fog film applied**, and the **~1 cm glass lift from 07-01 removed** (glass returned to
  seated). Efficacy assessed next morning — see 07-03: **both did not work**; worse, per the observer the
  **anti-fog film actually made the field of view WORSE** that night — not merely ineffective (the view was
  worse *with* the film on than with the bare glass; still fogs, rats hardly visible).
- **Data interpretation flags:** **rig change at ~13:00 07-02** — CH05/06 view/optics/geometry
  differ before vs after; not a like-for-like baseline vs prior days (add to the fog-mitigation
  series: 06-30 aluminum tape → 07-01 ~1 cm lift → **07-02 ~13:00 film on + lift off**). The two
  changes are confounded (applied together), so their individual effects can't be separated. Weather
  not logged this day.
- **Scientific ideas / hypotheses:** Sen winning fights may indicate a dominance/hierarchy position;
  mid-run foraging on insects — glucose/energy state may modulate movement pattern; do rats defecate
  inside vs outside the home (latrine behavior)?; bird sound may influence sleep — testable in lab by
  playing bird sounds during the day.
- **Analysis hooks:** segment CH05/06 before vs after ~13:00 07-02 as distinct optical regimes (do
  not pool shelter view-quality across the change); relate running speed / theta sequences to the
  fast field runs; detect foraging pauses (stop → pick up → resume) as run interruptions; score
  fighting win/loss toward a dominance ranking (Sen as candidate top); design a bird-sound playback →
  sleep experiment as a lab follow-up.

### Day 6 — 2026-07-03 · obs — · Equipment ⚠️ anti-fog film removed ~11:00 (CH05/06) · pre-dawn fog

- **Observed field events:** _Behavior:_ 06:00 the sleeping group **split into a 3 + 2
  configuration** (all five remaining rats accounted for). _Fog / rig:_ ~**04:00–06:00 EDT** heavy
  fog on the CH05/CH06 shelter IR glass, rats **hardly visible** — confirming the **07-02 ~13:00
  changes (anti-fog film on + ~1 cm lift off) did not work** — in fact the **anti-fog film made the field
  of view WORSE** that night (worse *with* the film on than with the bare glass, not merely ineffective).
  ~**11:00 EDT** the **anti-fog film was removed** (reverted, since it not only failed to help but
  **worsened the view**; the glass is now bare and seated — lift already off since 07-02). No mitigation to date (06-30 tape, 07-01 lift, 07-02 film) has cleared the pre-dawn shelter
  view.
- **Data interpretation flags:** 2026-07-03 ~04:00–06:00 is a **CH05/06 fog / view-degradation
  window** — treat the inside view as **degraded/unusable** there; "empty" or "no motion" in this
  window is **fog-obscured, not true absence/stillness** (consistent with the 06-30 accuracy pass:
  the fog/safety layer holds but the count/motion layer fails under fog, and the wall-edge blind
  zone). Not yet in `data_manifests/field_conditions.yaml`; add this window there so the shelter
  pipeline's weather cross-check forces these bins ≥ degraded. Also: the **anti-fog film regime (07-02
  13:00 → 07-03 11:00, `data_manifests/glass_treatments.yaml`) had a WORSE view than the adjacent bare/lift
  regimes** — the film is a **view-degrading covariate**, so shelter occupancy/detection in that window is
  even less reliable, and part of that degradation is the *instrument* (film), not weather alone.
- **Scientific ideas / hypotheses:** pre-dawn IR-glass fogging looks like a **recurring hard optical
  floor** for CH05/CH06, not fixable by the surface treatments tried so far (condensation likely
  driven by the IR-light heat + overnight temp/humidity, forming on the inner glass). Separately,
  sleep-partner grouping (the 3 + 2 split) may predict nighttime co-movement — do rats that sleep
  together also move together at night?
- **Analysis hooks:** restrict CH05/06 occupancy/rest on 2026-07-03 to clear-view windows and
  exclude the ~04:00–06:00 fogged window; treat **~11:00 07-03 (film off) as an optical-regime
  boundary** — don't pool shelter view-quality across it; once the fine-tuned detector is ready,
  check whether it recovers any rats in fogged frames or whether fog is a hard optical floor; test
  whether sleep-cluster membership (the 3-vs-2 split) predicts nighttime proximity / co-movement.

### Day 7 — 2026-07-04 · obs — · Equipment/optics ⚠️ post-film glass fogging (anti-fog coating likely damaged) · July 4th fireworks disturbance

- **Observed field events:** during the July 4th fireworks, **increased group-level movement** —
  more following behavior and some repeated route-like movement that *superficially* resembled
  patrolling. Following behavior **increased after** the disturbance. _Fog \ rig:_ **evening rain**,
  then fog on the CH05/CH06 shelter IR glass from ~**21:50 EDT (07-03)** through ~**09:30 EDT (07-04)** —
  a long (~11.5 h) overnight view-degradation window, much wider than the pre-dawn 04:00–06:00 ones. The
  observer's read: **removing the anti-fog film (07-03 ~11:00) appears to have DAMAGED the glass's original
  anti-fog coating**, so the now-bare post-film glass fogs **worse than the pre-intervention bare glass** —
  film removal was **not** a clean revert to baseline.
- **Data interpretation flags:** fireworks are an **external acoustic/light disturbance** — a
  movement spike this evening is **disturbance-driven, not spontaneous social behavior**; expect
  elevated broadband level on the CH01/CH02 mics (`audio_analysis`) over the fireworks window. The
  "route-like / patrolling" appearance is **superficial** — do not label it territorial patrol.
  Exact timing not logged. **Separately (fog/optics):** treat **07-03 ~21:50 → 07-04 ~09:30** as a
  CH05/CH06 fog / view-degradation window (inside view **degraded/unusable**; "empty"/"no motion" there is
  **fog-obscured, not true absence/stillness**) — add to `data_manifests/field_conditions.yaml`. And
  **`bare_seated_post_film` (since 07-03 11:00) is NOT a return to the `bare` baseline**: the original
  anti-fog coating appears **damaged** by the film removal, so it is a **distinct, worse** optical regime —
  do **not** use post-07-03 shelter view-quality as a clean "recovery" test of the `antifog_film` regime,
  and do not pool it with the pre-tape `bare` regime (tentative — observer's "looks like"; confirm by
  comparing post-film vs original-bare fog severity).
- **Scientific ideas / hypotheses:** this did *not* look like a simple fear/escape response.
  Fireworks may function less like a *localized predator threat* and more like a **diffuse,
  habitat-level disturbance** — logically similar to thunder, earthquake-like vibration, or shelter
  failure (e.g. leaking rain). For a threat with **no fixed source**, freezing or fleeing to a fixed
  location does not solve the problem, so the adaptive response shifts from *individual escape* to
  **collective reassessment**: increased social following and coordinated scanning to re-evaluate
  environmental safety and shelter reliability. Increased following = **threat-induced social
  coupling** (individuals using each other as information sources under uncertainty). Tentative
  label: **post-firework coordinated scanning / uncertainty-driven following — not confirmed
  territorial patrol, and not simple escape.**
- **Analysis hooks:** align CH01/CH02 audio features over the fireworks window with WISER movement;
  quantify following / leader-follower rate before vs during vs after the disturbance against a
  shuffled null; test whether the apparent "routes" are genuinely repeated trajectories or just
  arousal-driven perimeter movement.

## Standing hypotheses (cross-cutting)

Themes that span multiple days — qualitative hypotheses / covariates, **not** findings. CV
tracking is not yet available, so the shelter/sleep items below come from **direct manual
observation** (early July 2026) and are targets for later quantitative validation.

### Shelter use as dynamic fission–fusion, not fixed territory

Rats do **not** appear to treat the two houses as fixed individual territories. Group composition
across the two shelters shifts by day and time (e.g. 2-vs-3, 1-vs-4; sometimes all together),
rather than each rat consistently owning one house. Though built symmetrically, the houses likely
become **functionally asymmetric** through use — odor, humidity, heat, bedding condition,
disturbance history, social occupancy. The relevant decision may be less "which house does each
rat prefer" and more "**with whom, how deeply, and under what thermal/safety conditions does each
rat rest**." Best framed as **daily fission–fusion sheltering under time-varying thermal-risk
constraints**, not competition for house ownership.

### Shelter function changes across the day

The same house serves different roles by time of day, so a given split can mean different things:

| Time | Likely meaning of a split / shelter choice |
|---|---|
| 05:00–08:00 (pre-dawn) | interior still cool → true sleep shelter; split ≈ social affinity / sleep-group formation |
| midday | heat / crowding avoidance |
| afternoon hot period | thermal-refuge failure / entrance compromise (inside safe but hot, humid, crowded) |
| night | houses act as refuge / activity checkpoint / temporary regrouping, less as sleep chambers |
| post-disturbance | safety reassessment / group sensor coupling |

### Thermal–risk tradeoff and within-group position

Shelter *depth* may reflect a **thermal–risk tradeoff**: deep inside = safer but hotter, more
crowded, low-information; the entrance/mouth = cooler and better for environmental sampling but
more exposed. Splitting across houses may reduce overheating/crowding while preserving social
contact and shelter access. As the group looks increasingly social rather than competitive,
**competition may move from house ownership to within-house position** — edge-vs-center placement,
huddle access, entry order, and consistent sleep partners (all to be quantified once CV is up).

### Vigilance externalized during rest

Rats may **not suspend vigilance during rest**; instead vigilance may be **externalized into
shelter geometry, body posture, group proximity, and rapid arousal transitions** — sleep as a
*reconfiguration* of monitoring, not a withdrawal of it (safety held structurally: where they
sleep, how they lie, who they lie near, how fast they can wake). Posture is likely non-random:
curled / huddled / deep-shelter rest ≈ a protected maintenance mode, while stretched / entrance /
head-exposed rest ≈ a more thermally comfortable or vigilance-ready state. Connects the
sleep-posture / entrance-proximity / pile-together observations (Days 2–4).

### Disturbance → social sensor coupling

For **diffuse, non-localizable** disturbances (fireworks, thunder, vibration, shelter/rain
failure), fleeing to a fixed location does not solve the problem, so the group may shift from
individual rest into a **socially coupled information-sampling state**: coordinated following /
scanning, using each other as environmental sensors — one rat waking or scanning may cascade
arousal and movement to nearby resting rats. See **Day 7 (07-04 fireworks)** for the triggering
observation.

### Later validation targets (once CV / WISER support it)

1. **Partner loyalty vs house loyalty** — do rats co-sleep with specific partners more consistently than they occupy specific houses?
2. **Time-of-day shelter function** — compare pre-dawn / hot-day / evening / night / post-disturbance shelter use separately.
3. **Thermal–risk tradeoff** — does heat predict shallower shelter depth, smaller huddles, more splitting, or more stretched posture?
4. **Entry-order cascade** — does final group composition depend on which rat enters a house first?
5. **Disturbance response** — do fireworks / noise produce coordinated following, group scanning, or social arousal cascades (conspecific movement predicting wake/move transitions *beyond* the external stimulus)?
6. **Posture-conditioned rest** — do posture and shelter depth predict bout duration, wake latency, and post-wake behavior?

## Cross-references

Structured provenance this log summarizes — go here for exact, machine-readable cutoffs:

- `wiser_tracking_analysis/configs/rat_identities.csv` — animal ↔ tag mapping, Sova `valid_until`.
- `data_manifests/field_conditions.yaml` — machine-readable weather / fog / rain exclusion windows.
- `data_manifests/glass_treatments.yaml` — machine-readable CH05/CH06 shelter IR-glass optical-regime
  timeline (the Day 3–6 tape / lift / anti-fog-film interventions as queryable state; a covariate, not an
  exclusion rule).
- `data_manifests/2026-06-29-wiser-pilot.yaml` — tunnel removal (07:00 EDT 2026-06-29), Sova cutoff,
  time-varying structures.
- `change_log/2026-07-01-audio-extraction-on-analysis-pc.md` — 2026-06-29 NVR IP-change audio gap
  (~15:00–17:45, audio not recoverable) and CH01/CH02 mic-enable (~12:00).
- `wiser_tracking_analysis/ANALYSIS_STATUS.md` — WISER analysis status, candidate findings, caveats.
- Notion source: **"1 - Pilot Study → Daily Observations"** (`Rat_field_social_sleep_2026`).

_Maintenance: add a new `### Day N — YYYY-MM-DD` section per date using the four fixed subsections._

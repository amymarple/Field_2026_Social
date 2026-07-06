# Episode Browser — UX Review

_Date: 2026-07-03 · Scope: `app.py` (Streamlit) · Method: source inspection_

> This began as a **review-only** artifact. **Update (2026-07-03): the Top 5 changes below have been
> implemented** in `app.py` (relabels + reorganization only — no scientific-logic change); see the
> UX-pass addendum in `change_log/2026-07-02-episode-browser.md`. The findings are kept here as the
> rationale of record.

Goal: can a real user complete the core workflow — _find an interesting episode → inspect it →
record a verdict_ — with minimal confusion? Scientific logic is out of scope; this is about
legibility and flow. The review holds the app to its own invariants: **lens scores are UI ranking
aids, never ground truth or gates**; **gaps/coverage are rendered, never blanked**; provenance
(`state_model_id`, ⚗️ synthetic marker) and the per-field confidences stay visible; human writes are
**append-only, never overwrite**; cross-device alignment stays labeled **unverified**. Every
recommendation relabels or reorganizes — none removes metadata, coerces an absent score to zero, or
weakens data safety.

---

## 1. Primary user task

Triage and judge candidate episodes: narrow to a slice/subject → scan the **Episodes** table →
select one → inspect its metadata, evidence, and quality caveats → record a verdict (and optionally
export). The browser _consumes_ episodes; it never edits tracks.

## 2. Most visually important thing on the first screen

The central **Episodes table** with its two **progress-bar columns, "Confidence" and "Score"**
([app.py:509-521](app.py#L509-L521)), plus the header badge "Prototype — synthetic episode demo".

_Problem:_ the two most eye-catching elements are precisely the ones that must **not** read as ground
truth. A filled progress bar reads as a quality/goodness meter, directly at odds with the "scores are
UI, not truth" invariant.

## 3. What the user should do next

Narrow via the sidebar search/chips or the time slider, then **click a table row**. But the only
on-screen cue is a small caption under the table ("click a row to inspect",
[app.py:523](app.py#L523)). Every control that drives step one lives in the sidebar; there is **no
primary call-to-action or "start here"** orienting a first-timer.

## 4. Confusing buttons / filters / labels / panels

- **Inert header tray** "ⓘ Help  ⚙ Settings  🧑 HC" ([app.py:300](app.py#L300)) — looks like
  controls, does nothing. An affordance lie.
- **"Score" column** ([app.py:510](app.py#L510), `score_of`) — conflates _max lens score_ with a
  _`tracking_quality` fallback_; two identical-looking bars can mean different things, and the bar
  itself implies "truth/goodness." Highest-priority confusion.
- **"Confidence" column** = `boundary_confidence` specifically, but the schema has four confidences
  (subject / boundary / identity / tracking). The generic label is ambiguous.
- **Sidebar chips** mix kinds: rat names + `group` (a level) + `rain_response` (a label) sit in one
  row ([app.py:327](app.py#L327)) with no grouping. Clicking one silently _replaces_ the search box;
  there is no Run/Clear (the README describes a "Run" button that does not exist).
- **Two subject controls**: sidebar "Subject ID" filter ([app.py:338](app.py#L338)) vs. center
  "Focus rats" ([app.py:531](app.py#L531)) — one filters the table, one overlays map/timeline.
  Overlapping, easy to conflate.
- **"Level"** (per_animal / pair / group / environment) and **"Min confidence"** are unexplained
  jargon.
- **Numbered panels** "1 · Timeline / 2 · Coverage-QC / 3 · Field map / 4 · Weather" — numbering
  implies a sequence that isn't one.
- **Verdict icons** ⭐ / ？ / ⚠ / ↪ ([app.py:465-468](app.py#L465-L468)) are terse.

## 5. Empty / loading / error / missing-data states

_Mostly a strength — preserve it._

- No store → exact remediation command ([app.py:280](app.py#L280)); no matches → clear info
  ([app.py:497](app.py#L497)); WISER/weather empty states name the env var to set; an OPEN (still
  recording) file read is refused with a warning. **Coverage gaps are shown as a metric, never
  blanked**; absent lens scores render literally as "absent" ([app.py:453](app.py#L453)). These
  honor the "gaps are the substrate failing to exist" invariant and must stay.
- _Weakness:_ loading is largely silent (cached, no spinners), so a new user can't always tell
  "loading" from "missing." The Detail empty state ("Select an episode") is passive.

## 6. Are dangerous actions protected?

Verdict buttons, "Save annotation", and "Reveal & log" write files **instantly, no confirmation**
([app.py:469-472](app.py#L469-L472), [app.py:793-796](app.py#L793-L796)). The protection is
**architectural**: append-only, timestamped, never-overwrite — a genuinely strong data-safety design
that must be preserved. But the safety is **invisible** at the moment of action; a toast shows only a
filename, and an empty/mistyped `annotator_id` still writes. Export is **lossy CSV** but labeled just
"⬇ Export" — no signal it isn't a re-import path. No delete/overwrite exists (good).

Net: actions are _safe by construction_ but not _legibly_ protected. The fix is to make the safety
legible — **not** to add modals that would slow the workflow.

## 7. Can a new user tell what's loaded / filtered / selected?

Partly.

- _Loaded:_ the "synthetic demo" badge signals provenance, but nothing states the store size or its
  date span; the info card describes only the current slice.
- _Filtered:_ the sidebar info card ([app.py:376-380](app.py#L376-L380)) and the "Showing N of M
  episodes" caption are good — but there is no summary of **which filters are active**; that state is
  scattered across ~10 widgets.
- _Selected:_ `sel_id` drives the detail panel and is echoed there — reasonable, but there is no
  selected-episode breadcrumb outside the detail panel.

## 8. Fewer steps for the core workflow?

Yes. Two subject controls duplicate effort; `annotator_id` is re-entered in both the detail panel
([app.py:462](app.py#L462)) and the Annotate tab ([app.py:781](app.py#L781)) instead of being set
once per session; the search box + chips + filters overlap and all require discovering the sidebar
first.

## 9. Too dashboard-like / cluttered?

It leans mini-dashboard: a header with a fake icon tray, a long ungrouped sidebar filter stack, and
four numbered analytical panels competing with the primary table→detail loop. Defensible for a triage
tool, but tightenable by grouping filters, demoting the secondary panels, and removing inert chrome.
Do **not** over-minimize — the metadata density is the point.

## 10. Top 5 changes (no change to scientific logic)

1. **Stop the table bars from reading as ground truth.** Rename "Score" → **"Lens rank"** and mark
   the `tracking_quality` fallback distinctly (e.g. a "tracking-qual." tag or muted style, not an
   identical bar); rename "Confidence" → **"Boundary conf."** to match the schema field. Add a table
   caption: _"Bars are UI ranking aids, not ground truth."_ Pure relabel — no scoring logic changes.
2. **Kill the affordance lie in the header.** Remove the inert "ⓘ Help ⚙ Settings 🧑 HC" tray; put a
   real one-line **"How to use"** expander (find → inspect → judge) and show the session
   `annotator_id` where "🧑" was.
3. **Add a one-line orientation + active-filter strip above the table.** _"Showing N of M · {slice} ·
   filters: {level, subject, …} · click a row to inspect"_ — makes loaded/filtered/selected legible
   in one place (Q7) and states the next action (Q3) without a tutorial.
4. **Unify subject controls and annotator identity.** Relabel "Subject ID" → _"Filter table by
   subject"_ and "Focus rats" → _"Overlay on map/timeline"_ (or merge), and set `annotator_id`
   **once** in the sidebar so it is not re-entered in two places (Q8).
5. **Make data-safety legible without friction.** Keep the instant append-only writes, but add a
   persistent one-liner near the verdict actions (_"Saved as a new append-only record — nothing
   overwritten"_), label Export as _"Export (lossy CSV — not re-importable)"_, and nudge when
   `annotator_id` is empty before a write (Q6).

_Minor consistency fixes worth noting:_ the README claims a search "Run" button and a 15-min initial
window; the code has no Run button and opens on 60 min ([app.py:41](app.py#L41)). Align the docs (or
the labels) so they match reality.

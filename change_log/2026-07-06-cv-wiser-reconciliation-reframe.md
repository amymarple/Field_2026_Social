# 2026-07-06 — CV×WISER cross-val reframed as asymmetric measurement reconciliation

## Why
The alignment diagnosis ([ALIGNMENT_DIAGNOSIS_2026-07-02.md](../wiser_tracking_analysis/outputs/audit/ALIGNMENT_DIAGNOSIS_2026-07-02.md))
showed the low joint **κ = 0.20 is a base-rate (kappa-paradox) + measurement-definition mismatch, not
clock misalignment** (the ±1 h fine lag sweep is flat). So κ is the wrong headline, and the script's
hardcoded "wet/degraded-glass optical failure" verdict was misleading — on 2026-07-02 the CH05 recall
gap sits on **clear** glass. This reframes the report around asymmetric measurement semantics.

## What changed (`scripts/analyze_sleep_site_cv_crossval.py` — reporting only)
- **Headline is now per-shelter, never pooled**: (1) **CV precision** given WISER near-shelter presence,
  (2) **CV recall / lower-bound gap** relative to WISER presence. CV visible-inside-through-glass is
  stated as a **lower bound** on WISER near-shelter occupancy (huddle + wall-edge blind zone).
- **New cross-modal reconciliation** (`cv_wiser_reconciliation_strata.csv`): among WISER-present bins,
  CV recall + miss count stratified by **view_quality, glass_regime, fog_risk_level, camera/shelter,
  n_inside_confidence (wall-edge/huddle proxy), and WISER validity**. New covariate-preserving
  `_cv_bins_cov` helper; a per-bin WISER validity summary (`low_anchor_flag`) joins in. `fog_risk_level`
  is added by an **optional, soft** import of the CV `fog_risk` annotator (measurement context only —
  not a weather→behavior join, not a filter); it degrades cleanly if weather/module is unavailable.
- **κ demoted to a base-rate-warned diagnostic** everywhere (docstring, console print, verdict,
  manifest `kappa_note`): "ALIGNMENT DIAGNOSTIC ONLY … base-rate sensitive … not the headline."
- **"Optical failure" removed/softened**: flag `likely_cv_optical_failure` → `cv_recall_gap_under_wiser_presence`
  with a `gap_interpretation` column; file `cv_optical_failure_flags.csv` → `cv_recall_gap_flags.csv`.
  Verdict now says: *"CV visible-inside is a lower-bound relative to WISER near-shelter occupancy; the
  gap is consistent with coverage/definition limits such as the wall-edge blind zone, not necessarily
  fog (on 2026-07-02 the CH05 gap occurs on clear glass)."*

## Scope / non-goals
- **Reporting/framing only.** No detector, WISER filter, threshold, view-quality/safety, or binning
  change. The `GAP_*` flag thresholds are **numerically unchanged** (0.50/0.50) — only the label/meaning.
  The κ-based lag/mapping *selection* is unchanged (κ is a legitimate internal alignment diagnostic);
  only its role as the **headline** is removed.
- **No behavior claim.** The verdict ends "No behavior claim is made here."

## Verification — 07-02 rerun (`--no-plots`, `KMP_DUPLICATE_LIB_OK=TRUE`)
- fog_risk enrichment succeeded; reconciliation covered **7 strata axes**.
- **Per-shelter headline**: house_1/CH05 precision **1.00** / recall(lower-bound) **0.49** (WISER
  presence 0.99, n=192); house_2/CH06 precision **0.99** / recall **0.67** (presence 0.75, n=191).
- **Reconciliation confirms the framing** — CV recall is ~0.50–0.60 **across every stratum**:
  view_quality clear **0.56** ≈ degraded **0.50**; fog_risk low **0.55** ≈ high **0.58**;
  n_inside_confidence high **0.56** ≈ low **0.50**; glass lift_1cm 0.60 ≈ antifog_film 0.50. The
  CV-miss/WISER-present bins do **not** concentrate in degraded/foggy/low-validity strata → the gap is
  a coverage/definition limit (wall-edge blind zone), **not** fog.
- Outputs written: `crossval_verdict.txt`, `cv_wiser_reconciliation_strata.csv`,
  `cv_recall_gap_flags.csv`, `run_manifest.json` (with `kappa_note` + `reconciliation_note`).

## Follow-ups
- `fog_risk_level` in the reconciliation depends on the optional annotator + AWN weather; to make it a
  first-class column, wire `fog_risk` into `shelter_sleep.py` output (deferred — that adds a runtime
  weather dependency to CV capture, out of scope here).
- Wall-edge / huddle are only proxied by `n_inside_confidence`; an explicit per-bin wall-edge/huddle
  flag in the CV output would sharpen the coverage-limit attribution.

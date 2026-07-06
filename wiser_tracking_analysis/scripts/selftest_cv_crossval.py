"""
selftest_cv_crossval.py — offline check of the WISER shelter-state / CV cross-val core.

No DB, no CV files. Verifies:
  * cohen_kappa on hand cases (identical -> 1, independent -> ~0);
  * _rect_membership: a buffer-ring point is in_buffer but not in_core; a far point neither;
  * wiser_shelter_state: a rat sitting in a shelter with jitter that repeatedly crosses the
    ROI edge but never sustains a departure yields ONE high-confidence episode (no false
    exits); a brief sub-enter near-blip never opens a state; a sustained walk-away closes it;
  * wiser_shelter_presence counts distinct rats inside a shelter rect per bin;
  * cv_detection_metrics recall/precision/specificity match hand-built confusion counts;
  * best_lag_agreement recovers a KNOWN clock lag with high kappa for the matching camera
    and stays low for an unrelated one (so the mapping test picks correctly).

Run:  python scripts/selftest_cv_crossval.py     (-> PASS/FAIL, exit code)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import wiser_analysis_utils as w   # noqa: E402

BIN_S = 60
BINNS = BIN_S * 1_000_000_000


def _stay(rng, sid, base, minutes, cx, cy, jitter_in, hz=1.0, night="2026-06-29"):
    """~`hz` fixes/s for `minutes` min around (cx, cy) with gaussian jitter."""
    n = int(minutes * 60 * hz)
    rows = []
    for i in range(n):
        rows.append({"shortid": sid, "night": night,
                     "x": cx + rng.normal(0, jitter_in),
                     "y": cy + rng.normal(0, jitter_in),
                     "datetime": base + pd.Timedelta(seconds=i / hz)})
    return rows


def main() -> int:
    rng = np.random.default_rng(11)
    ok = True

    # --- cohen_kappa sanity ---
    a = np.array([1, 1, 0, 0, 1, 0], bool)
    if abs(w.cohen_kappa(a, a) - 1.0) > 1e-9:
        print("  FAIL kappa(identical) != 1"); ok = False
    big = rng.integers(0, 2, 4000).astype(bool)
    if abs(w.cohen_kappa(big, rng.integers(0, 2, 4000).astype(bool))) > 0.1:
        print("  FAIL kappa(independent) not ~0"); ok = False
    else:
        print("[cohen_kappa] identical=1, independent~0: ok")

    # --- _rect_membership: core vs buffer ring vs far ---
    roi = {"x": 100.0, "y": 100.0, "width_in": 40.0, "height_in": 40.0, "orientation_deg": 0.0}
    xs = np.array([100.0, 130.0, 200.0])   # centre / in 18in buffer ring / far
    ys = np.array([100.0, 100.0, 100.0])
    core, buf = w._rect_membership(xs, ys, roi, buffer_in=18.0)
    if not (core[0] and buf[0] and (not core[1]) and buf[1] and (not core[2]) and (not buf[2])):
        print(f"  FAIL _rect_membership core={core} buf={buf}"); ok = False
    else:
        print("[_rect_membership] centre in core; ring in buffer only; far in neither: ok")

    # --- wiser_shelter_state hysteresis / episodes ---
    base = pd.Timestamp("2026-06-29 09:00:00")
    roi_cfg = {"rois": [{"name": "house_1", "shape": "rect", "x": 100.0, "y": 100.0,
                         "width_in": 40.0, "height_in": 40.0, "orientation_deg": 0.0}]}
    rows = []
    # rat A: 40 min inside with 15 in jitter (fixes cross the +/-20 in ROI edge but stay
    # in the +/-38 in buffer), then walks far away for 6 min.
    rows += _stay(rng, "A", base, 40, 100, 100, jitter_in=15.0, hz=1.0)
    rows += _stay(rng, "A", base + pd.Timedelta(minutes=40), 6, 600, 600, jitter_in=5.0, hz=1.0)
    # rat B: far the whole time except a single 1-bin near blip (< enter_s) -> never enters.
    rows += _stay(rng, "B", base, 20, 600, 600, jitter_in=5.0, hz=1.0)
    rows.append({"shortid": "B", "night": "2026-06-29", "x": 100.0, "y": 100.0,
                 "datetime": base + pd.Timedelta(minutes=10)})   # lone near sample
    win = pd.DataFrame(rows)

    grid, epi = w.wiser_shelter_state(win, roi_cfg, ["house_1"], bin_s=BIN_S,
                                      buffer_in=18.0, enter_s=120, exit_s=120,
                                      hc_min_s=1200, hc_max_spread_in=24.0)
    a_epi = epi[epi["shortid"] == "A"]
    b_epi = epi[epi["shortid"] == "B"]
    if len(a_epi) != 1:
        print(f"  FAIL rat A should have exactly 1 episode (no false exits), got {len(a_epi)}"); ok = False
    elif not bool(a_epi.iloc[0]["high_confidence"]):
        print(f"  FAIL rat A episode should be high-confidence: {a_epi.iloc[0].to_dict()}"); ok = False
    elif not (a_epi.iloc[0]["duration_s"] >= 1800):
        print(f"  FAIL rat A episode too short: {a_epi.iloc[0]['duration_s']}s"); ok = False
    else:
        print(f"[wiser_shelter_state] rat A: 1 high-conf episode {a_epi.iloc[0]['duration_s']}s, "
              f"spread={a_epi.iloc[0]['spread_in']:.1f} in (no false exits): ok")
    if len(b_epi) != 0:
        print(f"  FAIL rat B blip should not open a state, got {len(b_epi)} episodes"); ok = False
    else:
        print("[wiser_shelter_state] rat B: sub-enter blip opens no episode: ok")

    occ = w.shelter_occupancy_bins(grid)
    if occ.empty or not occ["hc_occupied"].any():
        print("  FAIL shelter_occupancy_bins: expected some hc_occupied bins"); ok = False
    else:
        print(f"[shelter_occupancy_bins] {int(occ['hc_occupied'].sum())} hc-occupied bins: ok")

    # --- wiser_shelter_presence (raw diagnostic) ---
    pres = w.wiser_shelter_presence(win, roi_cfg, ["house_1"], bin_s=BIN_S)
    if pres.empty or int(pres["n_rats"].max()) < 1:
        print("  FAIL wiser_shelter_presence produced no in-shelter bins"); ok = False
    else:
        print("[wiser_shelter_presence] raw point-wise counts produced: ok")

    # --- cv_detection_metrics: hand-built confusion ---
    ref = pd.DataFrame({
        "bin_utc": np.arange(10) * BINNS,
        "occupied": [True] * 6 + [False] * 4,
        "hc_occupied": [True] * 4 + [False] * 6})
    cvb = pd.DataFrame({
        "bin_utc": np.arange(10) * BINNS,
        "cv_occupied": [True, True, False, False, True, False, False, False, True, False],
        "cv_n_inside": [1] * 10})
    rec = w.cv_detection_metrics(ref, cvb, ref_col="hc_occupied")   # recall vs hc
    prc = w.cv_detection_metrics(ref, cvb, ref_col="occupied")     # precision/spec vs occ
    if abs(rec["recall"] - 0.5) > 1e-9:
        print(f"  FAIL cv recall {rec['recall']} != 0.5"); ok = False
    elif abs(prc["precision"] - 0.75) > 1e-9 or abs(prc["specificity"] - 0.75) > 1e-9:
        print(f"  FAIL cv precision/specificity {prc['precision']}/{prc['specificity']} != 0.75"); ok = False
    else:
        print("[cv_detection_metrics] recall=0.50 (vs hc), precision=spec=0.75 (vs occ): ok")

    # --- best_lag_agreement: known lag + mapping (unchanged) ---
    N = 300
    bins = np.array([int(base.value) + i * BINNS for i in range(N)])
    truth = (rng.random(N) < 0.35)
    wiser = pd.DataFrame({"bin_utc": bins, "occupied": truth})
    LAG = 120
    def cv_from(series):
        t = pd.to_datetime(bins - LAG * 1_000_000_000)
        return pd.DataFrame({"t_utc": t, "occupied": series,
                             "n_inside_estimated": series.astype(int),
                             "usable_for_headline_summary": True})
    noisy = truth.copy()
    flip = rng.random(N) < 0.08
    noisy[flip] = ~noisy[flip]
    cv_match = cv_from(noisy)
    cv_other = cv_from(rng.random(N) < 0.35)
    grid_l = range(-300, 301, 30)
    best_m, _ = w.best_lag_agreement(wiser, cv_match, lag_grid_s=grid_l, bin_s=BIN_S)
    best_o, _ = w.best_lag_agreement(wiser, cv_other, lag_grid_s=grid_l, bin_s=BIN_S)
    print(f"[best_lag] match: lag={best_m['lag_s']} kappa={best_m['kappa']:.2f} "
          f"n={best_m['n_bins']} | other: lag={best_o['lag_s']} kappa={best_o['kappa']:.2f}")
    if best_m["lag_s"] != LAG:
        print(f"  FAIL recovered lag {best_m['lag_s']} != {LAG}"); ok = False
    if not (best_m["kappa"] > 0.7):
        print("  FAIL matching-camera kappa too low"); ok = False
    if not (best_m["kappa"] > best_o["kappa"] + 0.3):
        print("  FAIL mapping test: correct camera not clearly better"); ok = False

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

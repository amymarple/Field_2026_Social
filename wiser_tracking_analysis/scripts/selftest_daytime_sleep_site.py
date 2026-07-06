"""
selftest_daytime_sleep_site.py — offline check of the Direction-3 rest-site core.

No DB, no field data. Builds synthetic resting fixes for two animals over two
rest days with ~7 in jitter and confirms:

  * rest_mask flags low-speed fixes only;
  * daytime_primary_site recovers each animal-day's true site (within the jitter);
  * rest_site_stability reports ~0 shift for a STABLE animal and a large shift for
    one that RELOCATED between days (with occ_cosine high vs low respectively);
  * intraday_site_drift detects a within-day move (morning vs afternoon).

Run:  python scripts/selftest_daytime_sleep_site.py   (-> PASS/FAIL, exit code)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import wiser_analysis_utils as w   # noqa: E402

EXTENT = (0.0, 800.0, 0.0, 900.0)          # inches, shared across all groups
JIT = 7.0 / 1.4826                          # per-axis sigma -> ~7 in radial median


def _cloud(cx, cy, n, rng, hours):
    return pd.DataFrame({
        "x": cx + rng.normal(0, JIT, n),
        "y": cy + rng.normal(0, JIT, n),
        "clock_hour": rng.choice(hours, n),
        "resting": True,
    })


def main() -> int:
    rng = np.random.default_rng(42)
    ok = True

    # --- rest_mask ---
    tiny = pd.DataFrame({"speed_inps_smooth": [1.0, 5.0, 20.0, np.nan]})
    rm = w.rest_mask(tiny, moving_thr_inps=10.0)["resting"].tolist()
    if rm != [True, True, False, False]:
        print(f"  FAIL rest_mask: {rm}"); ok = False
    else:
        print("[rest_mask] low-speed flagged, NaN/high excluded: ok")

    # --- synthetic two-animal, two-day rest fixes ---
    A_site = (200.0, 700.0)               # tag A: stable both days
    B_d1, B_d2 = (600.0, 200.0), (200.0, 700.0)   # tag B: relocates day1->day2
    frames = []
    # A day1: morning at A_site, afternoon MOVED to (500,500) -> intraday drift
    a1 = pd.concat([_cloud(*A_site, 120, rng, [6, 7, 8, 9, 10]),
                    _cloud(*A_site, 60, rng, [12, 13, 14]),
                    _cloud(500.0, 500.0, 120, rng, [16, 17, 18, 19, 20])])
    a1["night"] = "2026-07-01"; a1["shortid"] = "A"
    a2 = _cloud(*A_site, 240, rng, [6, 8, 12, 16, 19]); a2["night"] = "2026-07-02"; a2["shortid"] = "A"
    b1 = _cloud(*B_d1, 240, rng, [6, 9, 13, 17, 20]); b1["night"] = "2026-07-01"; b1["shortid"] = "B"
    b2 = _cloud(*B_d2, 240, rng, [6, 9, 13, 17, 20]); b2["night"] = "2026-07-02"; b2["shortid"] = "B"
    win = pd.concat([a1, a2, b1, b2], ignore_index=True)

    # --- primary site per animal-day ---
    sites, hists = w.daytime_primary_site(win, extent=EXTENT, min_fixes=30)
    def _site(night, sid):
        r = sites[(sites.night == night) & (sites.shortid == sid)].iloc[0]
        return r.site_x, r.site_y
    a2x, a2y = _site("2026-07-02", "A")
    if np.hypot(a2x - A_site[0], a2y - A_site[1]) > 24:
        print(f"  FAIL primary site A/day2 = ({a2x:.0f},{a2y:.0f}) vs {A_site}"); ok = False
    else:
        print(f"[primary_site] A day2 = ({a2x:.0f},{a2y:.0f}) ~ {A_site}: ok")

    # --- across-day stability ---
    stab = w.rest_site_stability(sites, occ_hists=hists)
    sA = float(stab[stab.shortid == "A"]["site_shift_in"].iloc[0])
    sB = float(stab[stab.shortid == "B"]["site_shift_in"].iloc[0])
    cosA = float(stab[stab.shortid == "A"]["occ_cosine"].iloc[0])
    cosB = float(stab[stab.shortid == "B"]["occ_cosine"].iloc[0])
    print(f"[stability] A shift={sA:.0f} in (cos={cosA:.2f})  B shift={sB:.0f} in (cos={cosB:.2f})")
    if not (sA < 24):
        print("  FAIL: stable animal A should have ~0 shift"); ok = False
    if not (sB > 200):
        print("  FAIL: relocated animal B should have a large shift"); ok = False
    if not (cosA > cosB):
        print("  FAIL: stable animal should have higher day-to-day occupancy cosine"); ok = False

    # --- within-day drift (A day1: morning A_site -> afternoon (500,500)) ---
    drift = w.intraday_site_drift(win, extent=EXTENT, min_fixes=20)
    a1d = drift[(drift.night == "2026-07-01") & (drift.shortid == "A")].sort_values("block")
    aft = a1d[a1d.block == "15-21"]["shift_from_prev_in"].iloc[0]
    print(f"[intraday] A day1 afternoon shift_from_prev = {aft:.0f} in")
    if not (aft > 150):
        print("  FAIL: within-day move not detected"); ok = False

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

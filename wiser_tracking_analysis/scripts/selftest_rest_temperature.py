"""
selftest_rest_temperature.py — offline check of the Direction-3 Stage-A/B core.

No DB, no weather. Verifies:
  * relocation_tier: the tier bins + shelter-identity escalation;
  * nearest_shelter: a site next to house_1 resolves to house_1;
  * within_day_sequence + relocation_events: a rat resting at house_1 in the morning and
    house_2 at midday produces ONE shelter_switch event; a rat stable at house_1 all day
    produces none (jitter-scale wiggles are not events);
  * rest_bouts: a sustained low-speed stay yields >=1 bout with a shelter zone and a
    finite dropout fraction.

Run:  python scripts/selftest_rest_temperature.py     (-> PASS/FAIL, exit code)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import wiser_analysis_utils as w   # noqa: E402

# house coords from wiser_rois.json (WISER inch frame)
ROI_CFG = {"rois": [
    {"name": "house_1", "shape": "rect", "x": 411.5, "y": 718.6,
     "width_in": 36.4, "height_in": 26.6, "orientation_deg": 90.0},
    {"name": "house_2", "shape": "rect", "x": 613.6, "y": 717.3,
     "width_in": 36.4, "height_in": 26.6, "orientation_deg": 90.0}]}


def _rest_fixes(rng, sid, local_hour, cx, cy, n=40, jitter=6.0, night="2026-06-29"):
    """n resting fixes near (cx,cy) at a given LOCAL hour (UTC = local+4)."""
    base = pd.Timestamp(night) + pd.Timedelta(hours=local_hour + 4)   # naive UTC
    return [{"shortid": sid, "night": night, "clock_hour": local_hour, "resting": True,
             "x": cx + rng.normal(0, jitter), "y": cy + rng.normal(0, jitter),
             "datetime": base + pd.Timedelta(seconds=i * 20)} for i in range(n)]


def main() -> int:
    rng = np.random.default_rng(3)
    ok = True

    # --- relocation_tier ---
    cases = [(10, False, "stable"), (50, False, "marginal"), (85, False, "borderline"),
             (140, False, "robust_relocation"), (200, False, "major_shelter_switch"),
             (120, True, "major_shelter_switch"), (40, True, "marginal")]
    bad = [(s, sw, exp, w.relocation_tier(s, sw)) for s, sw, exp in cases
           if w.relocation_tier(s, sw) != exp]
    if bad:
        print(f"  FAIL relocation_tier: {bad}"); ok = False
    else:
        print("[relocation_tier] bins + identity escalation: ok")

    # --- nearest_shelter ---
    sites = pd.DataFrame({"night": ["2026-06-29"], "shortid": ["A"],
                          "site_x": [415.0], "site_y": [720.0]})
    ns = w.nearest_shelter(sites, ROI_CFG)
    if ns.iloc[0]["nearest_shelter"] != "house_1":
        print(f"  FAIL nearest_shelter -> {ns.iloc[0]['nearest_shelter']}"); ok = False
    else:
        print("[nearest_shelter] site by house_1 -> house_1: ok")

    # --- within_day_sequence + relocation_events ---
    rows = []
    # SW switcher: house_1 (early_morning) -> house_2 (midday)
    rows += _rest_fixes(rng, "SW", 6, 411.5, 718.6)
    rows += _rest_fixes(rng, "SW", 13, 613.6, 717.3)
    # ST stable: house_1 both windows (small jitter only)
    rows += _rest_fixes(rng, "ST", 6, 411.5, 718.6)
    rows += _rest_fixes(rng, "ST", 13, 411.5, 718.6)
    win = pd.DataFrame(rows)
    win = w.assign_roi(win, ROI_CFG)
    seq = w.within_day_sequence(win, ROI_CFG)
    ev = w.relocation_events(seq, order_col="window_order", min_shift_in=100.0)
    sw_ev = ev[ev["shortid"] == "SW"] if not ev.empty else ev
    st_ev = ev[ev["shortid"] == "ST"] if not ev.empty else ev
    if len(sw_ev) != 1 or sw_ev.iloc[0]["kind"] != "shelter_switch":
        print(f"  FAIL SW should have 1 shelter_switch event, got {sw_ev.to_dict('records') if len(sw_ev) else 'none'}"); ok = False
    elif len(st_ev) != 0:
        print(f"  FAIL ST (stable) should have 0 events, got {len(st_ev)}"); ok = False
    else:
        print("[within_day_sequence+relocation_events] switcher=1 shelter_switch, stable=0: ok")

    # --- rest_bouts ---
    bouts = w.rest_bouts(win, roi_cfg=ROI_CFG, bin_s=60, min_bout_s=120)
    if bouts.empty or not (bouts["zone_class"] == "shelter").any():
        print(f"  FAIL rest_bouts: expected >=1 shelter bout, got {len(bouts)}"); ok = False
    elif not bouts["dropout_frac"].between(0, 1).all():
        print(f"  FAIL rest_bouts dropout_frac out of [0,1]: {bouts['dropout_frac'].tolist()}"); ok = False
    else:
        print(f"[rest_bouts] {len(bouts)} bout(s), shelter zone + valid dropout: ok")

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

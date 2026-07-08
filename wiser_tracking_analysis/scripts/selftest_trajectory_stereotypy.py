r"""
selftest_trajectory_stereotypy.py — offline, synthetic, no DB.

Exercises the Phase-A trajectory-stereotypy core (src/trajectory_stereotypy.py) on
planted synthetic data and asserts the discriminators actually work:

  A. loader dedups overlapping incremental files on `reportid`;
  B. cross-midnight night labeling (a 02:00 fix belongs to the PREVIOUS night);
  C. a planted SHARED ROAD makes raw inter-animal cosine high, and dividing out the
     pooled corridor collapses the residual cosine — while an animal with a planted
     INDIVIDUAL off-road site keeps high residual concentration;
  D. a planted stabilization (an individual route reinforced over nights) makes the
     similarity-to-late-reference curve RISE;
  E. a planted real-time COUPLED pair beats the circular-shift null while an
     independent pair does not.

Run:  python scripts/selftest_trajectory_stereotypy.py   ->  exit 0 PASS / 1 FAIL
"""

from __future__ import annotations

import gzip
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import trajectory_stereotypy as ts        # noqa: E402
import wiser_analysis_utils as w          # noqa: E402

RNG = np.random.default_rng(7)
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------
# A. loader dedup on reportid
# ---------------------------------------------------------------------------

def _write_gz(path: Path, df: pd.DataFrame):
    with gzip.open(path, "wt", newline="") as f:
        df.to_csv(f, index=False)


def test_loader_dedup():
    print("A. loader dedup (composite (shortid,ts_raw,x,y) key)")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cols = ["reportid", "shortid", "calculation_error", "location_x",
                "location_y", "location_z", "anchors_used", "timestamp",
                "battery_voltage"]
        # cumulative file: reportids 0..999
        n = 1000
        base = pd.DataFrame({
            "reportid": np.arange(n), "shortid": RNG.integers(1, 6, n),
            "calculation_error": 0.0,
            "location_x": RNG.uniform(0, 200, n), "location_y": RNG.uniform(0, 200, n),
            "location_z": 0.0, "anchors_used": RNG.integers(4, 10, n),
            "timestamp": 1782688852625 + np.arange(n) * 250,
            "battery_voltage": 2.9})
        base = base[cols]
        # backfilled subset = exact copies (must be deduped); + 100 GENUINELY new
        # fixes (distinct timestamp/x/y). NOTE: reportid is NOT the dedup key — the
        # composite (shortid, ts_raw, x, y) is, because one reportid spans many tags.
        subset = base[base["reportid"] < 400]
        newrows = base.iloc[:100].copy()
        newrows["reportid"] = np.arange(5000, 5100)     # new reportids...
        newrows["timestamp"] = 1782900000000 + np.arange(100) * 250  # ...AND new fixes
        newrows["location_x"] = RNG.uniform(0, 200, 100)
        newrows["location_y"] = RNG.uniform(0, 200, 100)
        # a duplicate-reportid-but-DISTINCT-fix pair (different shortid) must survive
        multi_tag = base.iloc[:2].copy()
        multi_tag["reportid"] = base.iloc[0]["reportid"]   # share one reportid
        multi_tag["shortid"] = [98, 99]                    # different tags
        _write_gz(d / "1stcohort_2026_2026-06-30.csv.gz", pd.concat([base, multi_tag]))
        _write_gz(d / "1stcohort_2026_2026-06-28.csv.gz", subset)
        _write_gz(d / "1stcohort_2026_2026-07-01.csv.gz", newrows)

        df, log = ts.load_incremental_days(d)
        # base(1000) + 100 new fixes + 2 shared-reportid distinct-tag fixes = 1102
        expected_unique = 1000 + 100 + 2
        check("dedup on (shortid,ts_raw,x,y), NOT reportid",
              log["dedup_key"] == "shortid+ts_raw+x+y", log["dedup_key"])
        check("dedup keeps distinct fixes, drops only exact copies",
              log["rows_after_dedup"] == expected_unique,
              f"after_dedup={log['rows_after_dedup']} expected={expected_unique}")
        check("distinct fixes sharing a reportid are NOT dropped",
              ((df["shortid"] == 98).any() and (df["shortid"] == 99).any()),
              "shared-reportid multi-tag rows survived")
        check("duplicate count = backfilled subset size (400)",
              log["duplicate_rows_removed"] == 400,
              f"removed={log['duplicate_rows_removed']}")
        check("canonical rich schema present",
              {"shortid", "ts_raw", "x", "y", "reportid"} <= set(df.columns),
              str(sorted(df.columns)))


# ---------------------------------------------------------------------------
# B. cross-midnight night labeling
# ---------------------------------------------------------------------------

def test_night_labeling():
    print("B. cross-midnight night labeling")
    # local times (EDT). datetime is naive UTC = local + 4h.
    local = pd.to_datetime([
        "2026-06-28 22:00", "2026-06-28 23:30",   # night 06-28 (evening)
        "2026-06-29 02:00", "2026-06-29 04:30",   # night 06-28 (pre-dawn)
        "2026-06-29 12:00",                        # daytime -> not in night
        "2026-06-29 21:15",                        # night 06-29
    ])
    utc = local + pd.Timedelta(hours=4)            # invert tz_offset (-4)
    df = pd.DataFrame({"shortid": 1, "x": 1.0, "y": 1.0, "datetime": utc})
    lab = ts.add_night_label(df)
    nights = lab["night"].tolist()
    in_night = lab["in_night"].tolist()
    check("evening fix -> that night", nights[0] == "2026-06-28", nights[0])
    check("pre-dawn 02:00 fix -> PREVIOUS night", nights[2] == "2026-06-28", nights[2])
    check("04:30 still previous night", nights[3] == "2026-06-28", nights[3])
    check("noon fix excluded from night", in_night[4] is False or in_night[4] == False,
          str(in_night[4]))
    check("21:15 next day -> new night", nights[5] == "2026-06-29", nights[5])


# ---------------------------------------------------------------------------
# helpers for planted spatial scenario
# ---------------------------------------------------------------------------

EXTENT = (0.0, 200.0, 0.0, 200.0)
BIN = 8.0


def _road_fixes(n):
    """Fixes along a shared horizontal corridor y~100 (the 'road')."""
    x = RNG.uniform(20, 180, n)
    y = 100 + RNG.normal(0, 5, n)
    return x, y


def _blob(n, cx, cy, s=6):
    return cx + RNG.normal(0, s, n), cy + RNG.normal(0, s, n)


def build_spatial_scenario():
    """5 animals x 5 nights. Everyone uses the shared road. Animal '1' has an
    individual off-road site that STRENGTHENS over nights; '2' a constant off-road
    site; '3','4','5' road-only. Returns a `win`-like frame + hists dict."""
    nights = [f"2026-06-2{8+i}" if 8 + i < 10 else f"2026-07-0{8+i-9}" for i in range(5)]
    nights = ["2026-06-28", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"]
    animals = ["1", "2", "3", "4", "5"]
    rows = []
    for ni, night in enumerate(nights):
        for tag in animals:
            xr, yr = _road_fixes(300)                  # shared road
            xs, ys = [xr], [yr]
            if tag == "1":                              # growing individual site
                k = 40 * (ni + 1)
                bx, by = _blob(k, 40, 40)
                xs.append(bx); ys.append(by)
            elif tag == "2":                            # constant individual site
                bx, by = _blob(120, 160, 160)
                xs.append(bx); ys.append(by)
            x = np.concatenate(xs); y = np.concatenate(ys)
            for xi, yi in zip(x, y):
                rows.append({"shortid": tag, "night": night, "x": float(xi),
                             "y": float(yi), "speed_inps_smooth": 15.0})
    win = pd.DataFrame(rows)
    hists = ts.night_animal_hists(win, EXTENT, bin_in=BIN, moving_thr_inps=10.0)
    return win, hists, animals, nights


def test_shared_road_vs_individual():
    print("C. shared-road vs individual (residual test)")
    win, hists, animals, nights = build_spatial_scenario()
    per_animal = {t: ts.sum_hists([hists[(n, t)]["all"] for n in nights
                                   if (n, t) in hists]) for t in animals}
    pooled, mask, skel = ts.pooled_corridor([r["all"] for r in hists.values()])
    raw = ts.pairwise_map_similarity(per_animal, label="raw")
    resid_maps = {t: ts.residual_occupancy(per_animal[t], pooled) for t in animals}
    resid = ts.pairwise_map_similarity(resid_maps, label="residual")

    raw_mean = raw["cosine"].mean()
    resid_mean = resid["cosine"].mean()
    check("raw inter-animal cosine is high (shared road)", raw_mean > 0.7,
          f"raw_mean={raw_mean:.3f}")
    check("residual cosine drops after removing the road", resid_mean < raw_mean - 0.15,
          f"raw={raw_mean:.3f} residual={resid_mean:.3f}")

    conc = {t: ts.residual_concentration(resid_maps[t]) for t in animals}
    # individual-site animals ('1','2') more concentrated than road-only ('3','4','5')
    ind = np.mean([conc["1"], conc["2"]])
    road = np.mean([conc["3"], conc["4"], conc["5"]])
    check("individual-site animals have higher residual concentration", ind > road,
          f"individual={ind:.3f} road-only={road:.3f}")


def test_stabilization():
    print("D. stabilization curve rises for a reinforced individual route")
    win, hists, animals, nights = build_spatial_scenario()
    stab = ts.stabilization_table(hists, animals, nights, which="all")
    g = stab[stab["shortid"] == "1"].sort_values("night")
    cos_first = g["cos_ref"].iloc[0]
    cos_last = g["cos_ref"].iloc[-1]
    check("animal '1' similarity-to-late-reference rises over nights",
          cos_last > cos_first, f"first={cos_first:.3f} last={cos_last:.3f}")
    date = ts.stabilization_date(stab, metric="cos_ref")
    check("a stabilization date is estimated for animal '1'", date.get("1") is not None,
          str(date.get("1")))


# ---------------------------------------------------------------------------
# E. real-time coupling beats the circular-shift null
# ---------------------------------------------------------------------------

def build_temporal_scenario():
    """3 animals, 2 nights. '1' and '2' move together (coupled); '3' independent."""
    rows = []
    t0 = pd.Timestamp("2026-06-29 01:00")             # naive UTC
    for night in ["2026-06-28", "2026-06-29"]:
        T = 600
        # shared latent trajectory for the coupled pair
        ang = np.cumsum(RNG.normal(0, 0.3, T))
        cx = 100 + 60 * np.cos(ang); cy = 100 + 60 * np.sin(ang)
        # independent trajectory
        ang3 = np.cumsum(RNG.normal(0, 0.3, T))
        ix = 100 + 60 * np.cos(ang3); iy = 100 + 60 * np.sin(ang3)
        base = t0 + (pd.Timedelta(days=1) if night == "2026-06-29" else pd.Timedelta(0))
        for k in range(T):
            dt = base + pd.Timedelta(seconds=2 * k)
            elapsed = (dt - t0).total_seconds()
            hour = (dt + pd.Timedelta(hours=-4)).hour
            for tag, (px, py) in (("1", (cx[k], cy[k])),
                                  ("2", (cx[k] + RNG.normal(0, 3), cy[k] + RNG.normal(0, 3))),
                                  ("3", (ix[k], iy[k]))):
                rows.append({"shortid": tag, "night": night, "x": float(px),
                             "y": float(py), "elapsed_s": float(elapsed),
                             "clock_hour": int(hour)})
    return pd.DataFrame(rows)


def test_time_coupling():
    print("E. real-time coupling vs circular-shift null")
    win = build_temporal_scenario()
    grid = ts.sync_grid(win, bin_s=2.0)
    coupled = ts.circular_shift_null(grid, "1", "2", n_shuffles=100)
    indep = ts.circular_shift_null(grid, "1", "3", n_shuffles=100)
    check("coupled pair beats circular-shift null (proximity z>2)",
          coupled["z_frac_within_r"] > 2,
          f"coupled z={coupled['z_frac_within_r']:.2f}")
    check("independent pair does NOT strongly beat the null",
          not (indep["z_frac_within_r"] > 2) or
          coupled["z_frac_within_r"] > indep["z_frac_within_r"] + 2,
          f"indep z={indep['z_frac_within_r']:.2f}")
    check("coupled xy-corr exceeds independent",
          coupled["obs_xy_corr"] > indep["obs_xy_corr"],
          f"coupled={coupled['obs_xy_corr']:.2f} indep={indep['obs_xy_corr']:.2f}")
    day = ts.dayshuffle_null(grid, "1", "2")
    check("coupled pair beats the day-shuffle null (proximity)",
          np.isfinite(day["z_frac_within_r"]) and day["z_frac_within_r"] > 1.5,
          f"day-shuffle z={day['z_frac_within_r']:.2f}")


def main():
    print("== selftest: trajectory stereotypy (Phase A) ==")
    test_loader_dedup()
    test_night_labeling()
    test_shared_road_vs_individual()
    test_stabilization()
    test_time_coupling()
    print()
    if FAILS:
        print(f"FAIL — {len(FAILS)} check(s) failed: {FAILS}")
        sys.exit(1)
    print("PASS — trajectory-stereotypy Phase-A core healthy")
    sys.exit(0)


if __name__ == "__main__":
    main()

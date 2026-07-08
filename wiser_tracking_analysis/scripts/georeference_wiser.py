r"""
georeference_wiser.py — fit the WISER-inch -> field-cm transform from a pole survey.

Consumes a short field survey (a tag dwelt, stationary, at several surveyed poles;
see configs/wiser_georef_survey.csv) and fits a robust 2-D similarity that ties the
WISER native-inch frame to the physical paddock frame the CV pipeline uses (cm,
origin at pole A0 — preprocessing/computer_vision/field_coords.py).

Pipeline per survey row:
  label,shortid,start_local,end_local[,wiser_x,wiser_y]
  -> physical cm  via field_coords.resolve_landmark(label)         (target)
  -> WISER inches via the validity-filtered MEDIAN of that dwell    (source)
     read read-only from the DB over [start_local,end_local) (local EDT),
     OR taken directly from manual wiser_x/wiser_y if provided.
Then robust_fit_similarity (drops gross/edge-biased points), writes
configs/wiser_to_field_transform.json (+ QC), and a validation overlay PNG.

WISER is noisy (~7 in jitter, worse at edges): residuals bottom out at the jitter
floor, not zero. QC is judged against that floor, and `confirmed` is only set true
when scale ≈ 2.54 cm/in, residuals sit near the floor, and the affine shear is
negligible. Until a survey is run, no transform exists and all analyses stay in
inches (the config ships `confirmed: false`).

Examples:
  python scripts/georeference_wiser.py                       # default survey + DB
  python scripts/georeference_wiser.py --survey my.csv --db D:\Wiser\data\1stcohort_2026.sqlite
  python scripts/georeference_wiser.py --no-confirm          # fit but leave confirmed:false
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent           # wiser_tracking_analysis/
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(REPO_ROOT / "preprocessing" / "computer_vision"))

from src import field_transform as ft            # noqa: E402
from src import wiser_analysis_utils as wau       # noqa: E402
from src import time_utils                        # noqa: E402
from src.wiser_io import load_sqlite_window       # noqa: E402
import field_coords                               # noqa: E402  (CV field frame + poles)

DEFAULT_DB = Path(r"D:\Wiser\data\1stcohort_2026.sqlite")
DEFAULT_SURVEY = PROJECT_ROOT / "configs" / "wiser_georef_survey.csv"
DEFAULT_LAYOUT = REPO_ROOT / "preprocessing" / "computer_vision" / "configs" / "field_layout.json"
DEFAULT_OUT = PROJECT_ROOT / "configs" / "wiser_to_field_transform.json"
DEFAULT_OVERLAY = PROJECT_ROOT / "outputs" / "georef_validation.png"

IN_TO_CM = 2.54
LOCAL_TZ = timezone(timedelta(hours=wau.LOCAL_TZ_OFFSET_HOURS))   # EDT (UTC-4)


def _local_to_utc_ms(s: str) -> int:
    """Local-EDT ISO string -> Unix ms UTC (matches the DB's Unix-ms UTC clock)."""
    t = pd.Timestamp(s)
    if t.tz is None:
        t = t.tz_localize(LOCAL_TZ)
    return int(t.tz_convert("UTC").value // 1_000_000)


def _dwell_median(db: Path, shortid, start_local: str, end_local: str,
                  jitter_floor_in: float, min_fixes: int) -> dict | None:
    """Validity-filtered median WISER (x,y) of one stationary dwell + its scatter."""
    start_ms, end_ms = _local_to_utc_ms(start_local), _local_to_utc_ms(end_local)
    df = load_sqlite_window(db, start_ms, end_ms)
    if df is None or df.empty:
        print(f"    [warn] no fixes in window for '{shortid}'"); return None
    df = df[df["shortid"].astype(str) == str(shortid)].copy()
    if df.empty:
        print(f"    [warn] no fixes for tag {shortid} in window"); return None

    df = time_utils.convert_timestamps(df)
    df = wau.add_speed(df)
    df = wau.add_validity_flags(df, jitter_floor_in=jitter_floor_in)
    valid = df[df["valid"]].dropna(subset=["x", "y"])
    if len(valid) < min_fixes:
        print(f"    [warn] only {len(valid)} valid fixes (< {min_fixes}) for {shortid}")
        if valid.empty:
            return None

    mx, my = float(valid["x"].median()), float(valid["y"].median())
    r = np.hypot(valid["x"] - mx, valid["y"] - my)
    return {"wiser_x": mx, "wiser_y": my, "n_fixes": int(len(valid)),
            "scatter_median_in": float(r.median()), "scatter_p95_in": float(r.quantile(0.95)),
            "cloud_x": valid["x"].to_numpy(), "cloud_y": valid["y"].to_numpy()}


def _read_survey(path: Path) -> pd.DataFrame:
    sv = pd.read_csv(path, comment="#", skipinitialspace=True)
    sv.columns = [c.strip().lower() for c in sv.columns]
    if "label" not in sv.columns or "shortid" not in sv.columns:
        raise SystemExit(f"[survey] {path} must have at least 'label' and 'shortid' columns")
    sv = sv[sv["label"].notna() & (sv["label"].astype(str).str.strip() != "")]
    return sv.reset_index(drop=True)


def build_correspondences(sv: pd.DataFrame, db: Path, layout: dict,
                          jitter_floor_in: float, min_fixes: int) -> list[dict]:
    pts = []
    for _, row in sv.iterrows():
        label = str(row["label"]).strip()
        try:
            fx, fy, *_ = field_coords.resolve_landmark(label, layout)
        except KeyError as e:
            print(f"  [skip] {e}"); continue

        has_manual = ("wiser_x" in sv.columns and "wiser_y" in sv.columns
                      and pd.notna(row.get("wiser_x")) and pd.notna(row.get("wiser_y")))
        if has_manual:
            d = {"wiser_x": float(row["wiser_x"]), "wiser_y": float(row["wiser_y"]),
                 "n_fixes": None, "scatter_median_in": None, "scatter_p95_in": None,
                 "cloud_x": None, "cloud_y": None}
            print(f"  {label:>4}: manual WISER ({d['wiser_x']:.1f},{d['wiser_y']:.1f}) in")
        else:
            if not Path(db).exists():
                print(f"  [skip] {label}: no manual xy and DB not found ({db})"); continue
            d = _dwell_median(db, row["shortid"], row["start_local"], row["end_local"],
                              jitter_floor_in, min_fixes)
            if d is None:
                continue
            print(f"  {label:>4}: WISER median ({d['wiser_x']:.1f},{d['wiser_y']:.1f}) in "
                  f"from {d['n_fixes']} fixes, scatter p50/p95={d['scatter_median_in']:.1f}/"
                  f"{d['scatter_p95_in']:.1f} in")
        d.update({"label": label, "shortid": str(row["shortid"]),
                  "field_x_cm": float(fx), "field_y_cm": float(fy)})
        pts.append(d)
    return pts


def _overlay(pts, fit, layout, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    M = fit["matrix"]
    inl = fit["inlier_mask"]
    fig, ax = plt.subplots(figsize=(11, 6))
    # paddock + poles
    ax.add_patch(plt.Rectangle((0, 0), field_coords.FIELD_X_CM, field_coords.FIELD_Y_CM,
                               fill=False, ec="0.4", lw=1.5))
    for name, (px, py) in layout.get("poles", {}).items():
        ax.plot(px, py, "+", color="0.5", ms=8)
        ax.annotate(name, (px, py), color="0.5", fontsize=7, xytext=(2, 2),
                    textcoords="offset points")
    for i, p in enumerate(pts):
        if p["cloud_x"] is not None:                       # transformed dwell cloud
            cloud = ft.apply_transform(M, np.column_stack([p["cloud_x"], p["cloud_y"]]))
            ax.plot(cloud[:, 0], cloud[:, 1], ".", color="tab:blue", ms=1, alpha=0.15)
        tp = ft.apply_transform(M, [[p["wiser_x"], p["wiser_y"]]])[0]
        good = inl[i]
        ax.plot([p["field_x_cm"], tp[0]], [p["field_y_cm"], tp[1]],
                "-", color="0.7", lw=0.8, zorder=1)
        ax.plot(p["field_x_cm"], p["field_y_cm"], "o", color="tab:green", ms=7, zorder=3)
        ax.plot(tp[0], tp[1], "x", color=("tab:red" if good else "black"),
                ms=9, mew=2, zorder=3)
    ax.plot([], [], "o", color="tab:green", label="surveyed pole (truth)")
    ax.plot([], [], "x", color="tab:red", label="WISER median -> field")
    ax.plot([], [], "x", color="black", label="dropped (outlier)")
    ax.set_aspect("equal"); ax.set_xlabel("field x cm (40 ft)"); ax.set_ylabel("field y cm (20 ft)")
    ax.set_title(f"WISER georeference — scale {fit['scale']:.3f} cm/in, "
                 f"rmse {fit['rmse']:.1f} cm, n={fit['n_used']}/{len(pts)}")
    ax.legend(loc="upper right", fontsize=8); ax.margins(0.05)
    fig.tight_layout(); fig.savefig(out_path, dpi=130); plt.close(fig)
    print(f"  overlay -> {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit the WISER-inch -> field-cm georeference transform.")
    ap.add_argument("--survey", type=Path, default=DEFAULT_SURVEY)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    ap.add_argument("--jitter-floor-in", type=float, default=7.0,
                    help="jitter floor (in); sets the residual acceptance floor")
    ap.add_argument("--min-fixes", type=int, default=30,
                    help="warn if a dwell has fewer valid fixes")
    ap.add_argument("--no-confirm", action="store_true",
                    help="fit and write, but force confirmed:false regardless of QC")
    args = ap.parse_args()

    if not args.survey.exists():
        raise SystemExit(f"[survey] not found: {args.survey}  (fill in the template first)")
    layout = field_coords.load_layout(args.layout)
    sv = _read_survey(args.survey)
    print(f"Survey rows: {len(sv)}  (DB: {args.db})")

    pts = build_correspondences(sv, args.db, layout, args.jitter_floor_in, args.min_fixes)
    if len(pts) < 3:
        raise SystemExit(f"[fit] need >=3 usable correspondences, got {len(pts)}. "
                         "Add more surveyed poles or manual wiser_x/wiser_y.")

    src = np.array([[p["wiser_x"], p["wiser_y"]] for p in pts], float)
    dst = np.array([[p["field_x_cm"], p["field_y_cm"]] for p in pts], float)
    floor_cm = args.jitter_floor_in * IN_TO_CM
    fit = ft.robust_fit_similarity(src, dst, k_mad=3.0, floor=floor_cm)
    aff = ft.fit_affine(src, dst)

    res = np.asarray(fit["residuals"])
    inl = np.asarray(fit["inlier_mask"])
    max_inlier_res = float(res[inl].max()) if inl.any() else float("nan")

    # ---- QC gates (respecting the noise floor) ----
    checks = {
        "scale_near_2.54": abs(fit["scale"] - IN_TO_CM) <= 0.10 * IN_TO_CM,   # within 10%
        "residuals_near_floor": max_inlier_res <= 2.0 * floor_cm,
        "enough_points": fit["n_used"] >= 4,
        "affine_shear_small": abs(aff["shear_deg"]) <= 5.0 and aff["anisotropy"] <= 0.10,
    }
    passed = all(checks.values())
    confirmed = passed and not args.no_confirm

    print(f"\nFIT: scale={fit['scale']:.4f} cm/in (target {IN_TO_CM})  "
          f"rot={fit['rotation_deg']:.2f} deg  rmse={fit['rmse']:.1f} cm  "
          f"n_used={fit['n_used']}/{len(pts)}  dropped={fit['dropped']}")
    print(f"     max inlier residual={max_inlier_res:.1f} cm (floor {floor_cm:.1f})  "
          f"affine shear={aff['shear_deg']:.2f} deg anisotropy={aff['anisotropy']:.3f}")
    for name, good in checks.items():
        print(f"     [{'ok ' if good else 'FAIL'}] {name}")
    print(f"  -> confirmed = {confirmed}"
          f"{'' if not args.no_confirm else ' (forced false via --no-confirm)'}")

    payload = {
        "_README": ("WISER native-inch -> physical field-cm transform (origin pole A0; "
                    "CV field_coords frame). Fit by scripts/georeference_wiser.py from a pole "
                    "survey. Similarity (rot+uniform scale+translation). Apply to WISER (x,y) "
                    "in inches to get field cm; invert for the reverse."),
        "source_frame": "WISER native inches (offset origin)",
        "target_frame": "physical paddock cm, origin at pole A0 (field_coords)",
        "units_source": "inches", "units_target": "cm",
        "model": "similarity",
        "matrix": fit["matrix"],
        "scale_cm_per_in": fit["scale"], "expected_scale_cm_per_in": IN_TO_CM,
        "rotation_deg": fit["rotation_deg"], "translation_cm": fit["translation"],
        "rmse_cm": fit["rmse"], "max_inlier_residual_cm": max_inlier_res,
        "jitter_floor_in": args.jitter_floor_in, "residual_floor_cm": floor_cm,
        "confirmed": confirmed, "qc_passed": passed, "qc_checks": checks,
        "affine_diagnostic": {"shear_deg": aff["shear_deg"], "anisotropy": aff["anisotropy"],
                              "rmse_cm": aff["rmse"]},
        "n_correspondences": len(pts), "n_used": fit["n_used"], "dropped_idx": fit["dropped"],
        "correspondences": [
            {"label": p["label"], "shortid": p["shortid"],
             "wiser_x_in": p["wiser_x"], "wiser_y_in": p["wiser_y"],
             "field_x_cm": p["field_x_cm"], "field_y_cm": p["field_y_cm"],
             "n_fixes": p["n_fixes"], "scatter_median_in": p["scatter_median_in"],
             "scatter_p95_in": p["scatter_p95_in"],
             "residual_cm": float(res[i]), "inlier": bool(inl[i])}
            for i, p in enumerate(pts)],
        "source_db": str(args.db), "layout": str(args.layout),
        "generator": "wiser_tracking_analysis/scripts/georeference_wiser.py",
        "created_local": datetime.now(LOCAL_TZ).isoformat(),
    }
    ft.save_transform(args.out, payload)
    print(f"  transform -> {args.out}")
    _overlay(pts, fit, layout, args.overlay)

    if not passed:
        print("\n[!] QC did not fully pass — inspect the overlay and residuals; "
              "re-survey the flagged pole(s) or add points before trusting this frame.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

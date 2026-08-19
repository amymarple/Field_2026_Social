"""
place_exclude_region.py — draw exclude / edge polygon(s) for the route-structure
edge-effect analysis, on the real all-rats position scatter.

The WISER boundary rectangle is not the true physical wall, so the rectangular
"edge band" under-counts wall-running. This GUI lets you draw the real edge /
exclude region(s) by eye over the pooled 9-11 pm position cloud. Points inside any
polygon are treated as wall/edge: `analyze_route_structure.py` then computes
thigmotaxis and the interior-only corridor against YOUR region instead of the
boundary band. Writes `configs/wiser_exclude.json`. Data access is READ-ONLY.

Usage (machine with a display):
    conda activate cv
    cd wiser_tracking_analysis
    python scripts/place_exclude_region.py            # 9-11 pm pooled, all dates

Controls:
    left-click    add a vertex to the current polygon
    n             finish the current polygon, start a new one
    u             undo the last vertex
    c             close/keep the current polygon (>= 3 vertices)
    d             delete: clears the current polygon, else removes the last saved one
    s             save to configs/wiser_exclude.json
    q / Enter     save and quit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import wiser_analysis_utils as w        # noqa: E402
import time_utils                       # noqa: E402

DEFAULT_DB = Path(r"D:\Wiser\data\1stcohort_2026.sqlite")
DEFAULT_ROIS = PROJECT_ROOT / "configs" / "wiser_rois.json"
DEFAULT_OUT = PROJECT_ROOT / "configs" / "wiser_exclude.json"


def build_window(db, rois_path, clock_start, clock_end):
    """Read-only cleaned 9-11 pm pooled window + the WISER boundary rect."""
    df = w.load_wiser_session(db)
    df = time_utils.convert_timestamps(df)
    df = w.add_speed(df)
    roi_cfg = w.load_rois(rois_path)
    boundary = roi_cfg.get("boundary") if roi_cfg else None
    df = w.add_validity_flags(df, boundary=boundary)
    df = w.apply_tag_cutoffs(df)
    win = w.select_route_window(df, clock_start=clock_start, clock_end=clock_end)
    if win.empty:
        raise SystemExit("[exclude] No cleaned fixes in the requested window.")
    brect = (tuple(boundary["rect"]) if boundary and boundary.get("rect") else None)
    return win, brect


def load_existing(path):
    if Path(path).exists():
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            return [np.asarray(p, float) for p in d.get("polygons", [])
                    if len(p) >= 3]
        except Exception:
            pass
    return []


def save_polys(path, polys):
    keep = [p for p in polys if len(p) >= 3]
    payload = {
        "_README": ("Exclude / edge regions (WISER inch frame) for the "
                    "route-structure edge-effect analysis. A fix inside any "
                    "polygon is treated as wall/edge."),
        "units": "inches",
        "frame": "WISER native (offset origin; UNVERIFIED vs paddock)",
        "polygons": [[[float(x), float(y)] for x, y in poly] for poly in keep],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return len(keep)


class ExcludeEditor:                                   # pragma: no cover (GUI)
    def __init__(self, win, brect, out_path, plt):
        self.win, self.brect, self.out, self.plt = win, brect, out_path, plt
        self.polys = load_existing(out_path)
        self.current = []
        self.fig, self.ax = plt.subplots(figsize=(12, 8))
        self._draw_background()
        self._artists = []
        self.fig.canvas.mpl_connect("button_press_event", self.on_click)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self._redraw()

    def _draw_background(self):
        from matplotlib.ticker import MultipleLocator
        tags = sorted(self.win["shortid"].unique())
        colors = w.plotting._tag_colors(tags)
        for t in tags:
            g = self.win[self.win["shortid"] == t]
            if len(g) > 50000:                          # subsample for responsiveness
                g = g.iloc[::len(g) // 50000 + 1]
            self.ax.scatter(g["x"], g["y"], s=2, alpha=0.15, color=colors[t],
                            linewidths=0)
        if self.brect is not None:
            bx0, bx1, by0, by1 = self.brect
            self.ax.plot([bx0, bx1, bx1, bx0, bx0], [by0, by0, by1, by1, by0],
                         "k--", lw=1.2, alpha=0.7)
        self.ax.set_aspect("equal", "box")
        self.ax.set_xlabel("X (in)")
        self.ax.set_ylabel("Y (in)")
        self.ax.xaxis.set_major_locator(MultipleLocator(50))
        self.ax.yaxis.set_major_locator(MultipleLocator(50))
        self.ax.grid(True, linestyle="--", alpha=0.4)

    def _redraw(self):
        for a in self._artists:
            a.remove()
        self._artists = []
        for poly in self.polys:
            if len(poly) >= 2:
                pp = np.vstack([poly, poly[0]])
                self._artists.append(self.ax.plot(pp[:, 0], pp[:, 1], "-",
                                                  color="red", lw=1.6, alpha=0.85)[0])
                self._artists.append(self.ax.fill(poly[:, 0], poly[:, 1],
                                                  color="red", alpha=0.12)[0])
        if self.current:
            cur = np.asarray(self.current)
            self._artists.append(self.ax.plot(cur[:, 0], cur[:, 1], "-o",
                                              color="orange", lw=1.6, ms=4)[0])
        n = len([p for p in self.polys if len(p) >= 3])
        self.ax.set_title(
            f"Draw EXCLUDE polygons (wall/edge)  |  saved polys={n}  "
            f"current verts={len(self.current)}\n"
            "click=add vertex   n=new   u=undo   c=close   d=delete   s=save   q=save+quit")
        self.fig.canvas.draw_idle()

    def on_click(self, e):
        if e.inaxes is self.ax and e.button == 1 and e.xdata is not None:
            self.current.append((e.xdata, e.ydata))
            self._redraw()

    def on_key(self, e):
        k = (e.key or "").lower()
        if k in ("n", "c"):
            if len(self.current) >= 3:
                self.polys.append(np.asarray(self.current))
            self.current = []
        elif k == "u" and self.current:
            self.current.pop()
        elif k == "d":
            if self.current:
                self.current = []
            elif self.polys:
                self.polys.pop()
        elif k in ("s", "q", "enter"):
            if len(self.current) >= 3:
                self.polys.append(np.asarray(self.current))
                self.current = []
            n = save_polys(self.out, self.polys)
            print(f"Saved {n} exclude polygon(s) -> {self.out}")
            if k in ("q", "enter"):
                self.plt.close(self.fig)
                return
        self._redraw()


def main():
    ap = argparse.ArgumentParser(description="Draw WISER edge/exclude polygons.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--clock-start", type=int, default=21)
    ap.add_argument("--clock-end", type=int, default=23)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"[exclude] Database not found: {args.db}")

    win, brect = build_window(args.db, args.rois, args.clock_start, args.clock_end)
    print(f"[exclude] {len(win):,} cleaned fixes in the window; "
          f"draw the edge region(s), then 's' to save -> {args.out}")

    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    ExcludeEditor(win, brect, args.out, plt)
    plt.show()


if __name__ == "__main__":
    main()

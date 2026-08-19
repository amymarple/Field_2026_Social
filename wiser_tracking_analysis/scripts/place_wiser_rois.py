"""
place_wiser_rois.py — Drag-to-edit WISER ROIs on a real occupancy background.

This is a SEPARATE GUI from the computer-vision camera-merging tool
(`preprocessing/computer_vision/place_cameras.py`). It marks, in the WISER native
inch frame, the paddock boundary and the landmark ROIs by dragging handles over a
background built from the real tag occupancy. It reads/writes
`wiser_tracking_analysis/configs/wiser_rois.json`.

ROI shapes:
- **rect** (the 2 big rectangular houses / shelters): size defaults to
  24.63 x 18 in (62.55 x 45.72 cm) from `preprocessing/computer_vision/configs/
  field_layout.json` (`shelters`); position/orientation set here.
- **circle** (smaller refuges, water, food): center + radius.

Why the WISER frame (not the physical paddock): the WISER coordinate frame has an
offset origin and is NOT verified against the 40x20 ft paddock, so editing
directly on the data's own occupancy sidesteps the unverified transform. (Only
the house SIZE is taken from field_layout; the position is placed here.)

Everything is draggable and re-editable. On launch the existing `wiser_rois.json`
is loaded so you fine-tune existing markers. Data access is strictly READ-ONLY.

Usage (run on a machine with a display):
    conda activate cv
    cd wiser_tracking_analysis
    python scripts/place_wiser_rois.py

Controls:
    drag a corner handle    resize the boundary (opposite corner stays put)
    drag the boundary box    move the whole boundary
    drag an ROI             move it
    scroll over an ROI      grow / shrink (circle: radius; rect: both sides)
    , / .                   rotate the selected rectangular ROI (-/+ 15 deg)
    [ / ]                   shrink / grow the selected ROI
    click empty space       drop the next not-yet-placed ROI there
    Tab                     cycle selection;  arrow keys nudge (Shift = x10)
    d / Delete              remove the selected ROI (returns it to the pending list)
    s                       save;   q / Enter   save and quit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import wiser_analysis_utils as w   # noqa: E402

DEFAULT_DB = Path(r"D:\Wiser\data\1stcohort_2026.sqlite")
DEFAULT_OUT = PROJECT_ROOT / "configs" / "wiser_rois.json"

# Big-house footprint from field_layout.json shelters: 62.55 x 45.72 cm.
HOUSE_W_IN = round(62.55 * w.CM_TO_IN, 2)   # 24.63 in
HOUSE_H_IN = round(45.72 * w.CM_TO_IN, 2)   # 18.0 in


def default_roi_slots() -> list[dict]:
    """Landmark slots: 2 big rectangular houses + 4 small circular refuges + 2 water + 2 food."""
    slots = [
        {"name": "house_1", "type": "refuge", "shape": "rect",
         "width_in": HOUSE_W_IN, "height_in": HOUSE_H_IN, "orientation_deg": 90.0},
        {"name": "house_2", "type": "refuge", "shape": "rect",
         "width_in": HOUSE_W_IN, "height_in": HOUSE_H_IN, "orientation_deg": 90.0},
    ]
    for i in range(1, 5):                       # 4 small refuges
        slots.append({"name": f"refuge_{i}", "type": "refuge", "shape": "circle",
                      "radius_in": 10.0})
    for i in range(1, 3):
        slots.append({"name": f"water_{i}", "type": "water", "shape": "circle",
                      "radius_in": 8.0})
    for i in range(1, 3):
        slots.append({"name": f"food_{i}", "type": "food", "shape": "circle",
                      "radius_in": 8.0})
    # Time-varying tunnel: a RECTANGULAR tube, present overnight, removed 07:00
    # local (EDT) 2026-06-29. valid_until is written in LOCAL time with offset
    # (reads as 7am); the analysis converts it to UTC to compare against the WISER
    # timestamps. Default size is a placeholder — resize ([ ]/scroll) and rotate
    # (, .) it onto the overnight high-occupancy cluster.
    slots.append({"name": "tunnel_1", "type": "tunnel", "shape": "rect",
                  "width_in": 24.0, "height_in": 6.0, "orientation_deg": 0.0,
                  "valid_until": "2026-06-29T07:00:00-04:00"})
    return slots


def build_background(db: Path, bin_in: float = 4.0):
    """Read-only: load the session and return (occupancy H, extent, n) for display."""
    df = w.load_wiser_session(db)
    if df is None or df.empty:
        raise SystemExit(f"No data loaded from {db}")
    extent = w.observed_extent(df, pad_in=12.0)
    H, xe, ye = w.occupancy_hist(df, extent, bin_in=bin_in)
    return H, extent, len(df)


def _roi_from_dict(r: dict) -> dict:
    """Normalise a stored/slot ROI dict, filling shape-specific defaults."""
    shape = r.get("shape", "circle")
    out = {"name": r["name"], "type": r.get("type", "refuge"), "shape": shape}
    if "x" in r and "y" in r:
        out["x"], out["y"] = float(r["x"]), float(r["y"])
    if shape == "rect":
        out["width_in"] = float(r.get("width_in", HOUSE_W_IN))
        out["height_in"] = float(r.get("height_in", HOUSE_H_IN))
        out["orientation_deg"] = float(r.get("orientation_deg", 0.0))
    else:
        out["radius_in"] = float(r.get("radius_in", 12.0))
    for k in ("valid_from", "valid_until"):       # time-varying ROIs (e.g. tunnel)
        if r.get(k):
            out[k] = r[k]
    return out


def load_initial_state(out_path: Path, extent):
    """
    Boundary + placed ROIs from an existing wiser_rois.json. Any default slot not
    in that file (e.g. a newly-added tunnel) is **auto-placed at the centre** so it
    is immediately visible and draggable — not hidden in a "pending" state.
    """
    placed: list[dict] = []
    boundary = list(extent)
    existing = None
    if Path(out_path).exists():
        try:
            existing = json.loads(Path(out_path).read_text(encoding="utf-8"))
        except Exception:
            existing = None
    if existing:
        rect = existing.get("boundary", {}).get("rect")
        if rect and len(rect) == 4:
            boundary = [float(v) for v in rect]
        for r in existing.get("rois", []):
            if "x" in r and "y" in r:
                placed.append(_roi_from_dict(r))

    have = {r["name"] for r in placed}
    cx = (boundary[0] + boundary[1]) / 2
    cy = (boundary[2] + boundary[3]) / 2
    for j, s in enumerate(default_roi_slots()):
        if s["name"] in have:
            continue
        roi = _roi_from_dict(s)
        roi["x"] = cx + (j % 3 - 1) * 30.0     # small stagger so multiples don't stack
        roi["y"] = cy + (j // 3) * 30.0
        placed.append(roi)
    return boundary, placed, []                # nothing pending — all visible


def save_rois(path: Path, boundary_rect, placed: list[dict],
              confirmed: bool = True) -> Path:
    """Write the ROI JSON (boundary + placed ROIs, circle or rect), all confirmed."""
    path = Path(path)
    xs = sorted([boundary_rect[0], boundary_rect[1]])
    ys = sorted([boundary_rect[2], boundary_rect[3]])

    def out(r):
        base = {"name": r["name"], "type": r["type"],
                "shape": r.get("shape", "circle"),
                "x": float(r["x"]), "y": float(r["y"]), "confirmed": confirmed}
        if base["shape"] == "rect":
            base["width_in"] = float(r["width_in"])
            base["height_in"] = float(r["height_in"])
            base["orientation_deg"] = float(r.get("orientation_deg", 0.0))
        else:
            base["radius_in"] = float(r["radius_in"])
        for k in ("valid_from", "valid_until"):
            if r.get(k):
                base[k] = r[k]
        return base

    cfg = {
        "_README": "WISER ROIs in the WISER native inch frame (offset origin; "
                   "UNVERIFIED vs physical paddock). Written by place_wiser_rois.py. "
                   "Rect house size from field_layout.json shelters.",
        "units": "inches",
        "frame": "WISER native (offset origin)",
        "boundary": {"rect": [xs[0], xs[1], ys[0], ys[1]], "confirmed": confirmed},
        "rois": [out(r) for r in placed if "x" in r],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"Saved {len(cfg['rois'])} ROI(s) + boundary -> {path}")
    return path


# ---------------------------------------------------------------------------
# Geometry helpers (shared by editor + hit testing)
# ---------------------------------------------------------------------------

def rect_corners(r: dict):
    """Four (x, y) corners of a rotated rectangular ROI."""
    th = np.radians(r.get("orientation_deg", 0.0))
    hw, hh = r["width_in"] / 2, r["height_in"] / 2
    c, s = np.cos(th), np.sin(th)
    return [(r["x"] + c * px - s * py, r["y"] + s * px + c * py)
            for px, py in [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]]


def point_in_rect(px: float, py: float, r: dict) -> bool:
    th = np.radians(r.get("orientation_deg", 0.0))
    dx, dy = px - r["x"], py - r["y"]
    c, s = np.cos(-th), np.sin(-th)
    lx, ly = c * dx - s * dy, s * dx + c * dy
    return abs(lx) <= r["width_in"] / 2 and abs(ly) <= r["height_in"] / 2


# ---------------------------------------------------------------------------
# Interactive draggable editor
# ---------------------------------------------------------------------------

class RoiEditor:                                        # pragma: no cover
    """Drag-to-edit boundary + circle/rect ROIs over an occupancy background."""

    TYPE_COLOR = {"refuge": "lime", "water": "deepskyblue",
                  "food": "orange", "tunnel": "violet"}

    def __init__(self, H, extent, boundary, placed, pending, out_path):
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import LogNorm
        self.plt = plt
        self.Polygon = plt.Polygon

        self.extent = extent
        self.boundary = list(boundary)
        self.placed = [dict(r) for r in placed]
        self.pending = [dict(s) for s in pending]
        self.out_path = out_path
        self.sel = None
        self.drag = None
        span = max(extent[1] - extent[0], extent[3] - extent[2])
        self.hit = span * 0.02

        self.fig, self.ax = plt.subplots(figsize=(12, 8.5))
        masked = np.ma.masked_where(H <= 0, H)
        cmap = plt.get_cmap("magma").copy()
        cmap.set_bad("white")
        self.ax.imshow(masked.T, origin="lower",
                       extent=(extent[0], extent[1], extent[2], extent[3]),
                       aspect="equal", cmap=cmap,
                       norm=LogNorm(vmin=1, vmax=max(H.max(), 1)))
        self.ax.set_xlabel("X (in)")
        self.ax.set_ylabel("Y (in)")
        self.artists = []
        for ev, fn in [("button_press_event", self.on_press),
                       ("motion_notify_event", self.on_motion),
                       ("button_release_event", self.on_release),
                       ("scroll_event", self.on_scroll),
                       ("key_press_event", self.on_key)]:
            self.fig.canvas.mpl_connect(ev, fn)
        self.redraw()

    def corners(self):
        xmin, xmax, ymin, ymax = self.boundary
        return [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]

    def _sel_desc(self):
        s = self.sel
        if s and s[0] == "roi":
            r = self.placed[s[1]]
            if r.get("shape") == "rect":
                return (f"{r['name']} (rect {r['width_in']:.0f}x{r['height_in']:.0f} in, "
                        f"{r.get('orientation_deg', 0):.0f}deg)")
            return f"{r['name']} (r={r['radius_in']:.0f} in)"
        if s and s[0] in ("corner", "box"):
            return "boundary"
        return "none"

    def title(self):
        tip = (f"click empty -> drop '{self.pending[0]['name']}'  |  "
               if self.pending else "all ROIs placed  |  ")
        return (f"{tip}selected: {self._sel_desc()}   "
                f"[drag | scroll/[ ]=size | , .=rotate | Tab | arrows | d | s save | q quit]")

    def redraw(self):
        plt = self.plt
        for a in self.artists:
            a.remove()
        self.artists.clear()
        xmin, xmax, ymin, ymax = self.boundary
        self.artists.append(self.ax.add_patch(plt.Rectangle(
            (xmin, ymin), xmax - xmin, ymax - ymin, fill=False,
            edgecolor="cyan", linewidth=1.6)))
        for (cx, cy) in self.corners():
            self.artists.append(self.ax.add_patch(plt.Circle(
                (cx, cy), self.hit, color="cyan", alpha=0.8)))
        for i, r in enumerate(self.placed):
            col = self.TYPE_COLOR.get(r["type"], "lime")
            lw = 3.0 if (self.sel == ("roi", i)) else 1.6
            if r.get("shape") == "rect":
                self.artists.append(self.ax.add_patch(self.Polygon(
                    rect_corners(r), closed=True, fill=False,
                    edgecolor=col, linewidth=lw)))
            else:
                self.artists.append(self.ax.add_patch(plt.Circle(
                    (r["x"], r["y"]), r["radius_in"], fill=False,
                    edgecolor=col, linewidth=lw)))
            self.artists.append(self.ax.add_patch(plt.Circle(
                (r["x"], r["y"]), self.hit * 0.6, color=col)))
            self.artists.append(self.ax.annotate(
                r["name"], (r["x"], r["y"]), color=col, fontsize=8,
                ha="center", va="bottom"))
        self.ax.set_title(self.title(), fontsize=9)
        self.ax.set_xlim(self.extent[0], self.extent[1])
        self.ax.set_ylim(self.extent[2], self.extent[3])
        self.fig.canvas.draw_idle()

    def pick(self, x, y):
        for i, r in enumerate(self.placed):
            if r.get("shape") == "rect":
                if point_in_rect(x, y, r) or np.hypot(x - r["x"], y - r["y"]) <= self.hit:
                    return ("roi", i)
            elif np.hypot(x - r["x"], y - r["y"]) <= max(self.hit, r["radius_in"]):
                return ("roi", i)
        for i, (cx, cy) in enumerate(self.corners()):
            if np.hypot(x - cx, y - cy) <= self.hit * 1.5:
                return ("corner", i)
        xmin, xmax, ymin, ymax = self.boundary
        if xmin <= x <= xmax and ymin <= y <= ymax:
            return ("box", None)
        return None

    def on_press(self, e):
        if e.inaxes != self.ax or e.xdata is None:
            return
        hit = self.pick(e.xdata, e.ydata)
        if hit is None and self.pending:
            slot = self.pending.pop(0)
            slot["x"], slot["y"] = e.xdata, e.ydata
            self.placed.append(slot)
            self.sel = ("roi", len(self.placed) - 1)
        else:
            self.sel = hit
            self.drag = (e.xdata, e.ydata)
        self.redraw()

    def on_motion(self, e):
        if self.drag is None or self.sel is None or e.inaxes != self.ax or e.xdata is None:
            return
        dx, dy = e.xdata - self.drag[0], e.ydata - self.drag[1]
        kind, idx = self.sel
        if kind == "roi":
            self.placed[idx]["x"] += dx
            self.placed[idx]["y"] += dy
        elif kind == "box":
            self.boundary = [self.boundary[0] + dx, self.boundary[1] + dx,
                             self.boundary[2] + dy, self.boundary[3] + dy]
        elif kind == "corner":
            xmin, xmax, ymin, ymax = self.boundary
            if idx in (0, 3):
                xmin = e.xdata
            else:
                xmax = e.xdata
            if idx in (0, 1):
                ymin = e.ydata
            else:
                ymax = e.ydata
            self.boundary = [xmin, xmax, ymin, ymax]
        self.drag = (e.xdata, e.ydata)
        self.redraw()

    def on_release(self, e):
        self.drag = None

    def _resize_sel(self, factor=None, step=None):
        r = self.placed[self.sel[1]]
        if r.get("shape") == "rect":
            f = factor if factor is not None else (1.05 if step and step > 0 else 0.95)
            r["width_in"] = max(2.0, r["width_in"] * f)
            r["height_in"] = max(2.0, r["height_in"] * f)
        else:
            r["radius_in"] = max(1.0, r["radius_in"] + (step if step else 0))

    def on_scroll(self, e):
        if self.sel and self.sel[0] == "roi":
            up = e.button == "up"
            self._resize_sel(factor=(1.05 if up else 0.95), step=(1.0 if up else -1.0))
            self.redraw()

    def on_key(self, e):
        if e.key == "tab" and self.placed:
            i = self.sel[1] + 1 if (self.sel and self.sel[0] == "roi") else 0
            self.sel = ("roi", i % len(self.placed))
        elif e.key in ("d", "delete") and self.sel and self.sel[0] == "roi":
            r = self.placed.pop(self.sel[1])
            r.pop("x", None); r.pop("y", None)
            self.pending.insert(0, r)
            self.sel = None
        elif e.key in ("[", "]") and self.sel and self.sel[0] == "roi":
            self._resize_sel(factor=(1.05 if e.key == "]" else 0.95),
                             step=(1.0 if e.key == "]" else -1.0))
        elif e.key in (",", ".") and self.sel and self.sel[0] == "roi":
            r = self.placed[self.sel[1]]
            if r.get("shape") == "rect":
                r["orientation_deg"] = (r.get("orientation_deg", 0.0)
                                        + (15 if e.key == "." else -15)) % 360
        elif e.key in ("left", "right", "up", "down", "shift+left", "shift+right",
                       "shift+up", "shift+down") and self.sel and self.sel[0] == "roi":
            step = 10.0 if e.key.startswith("shift+") else 1.0
            k = e.key.split("+")[-1]
            r = self.placed[self.sel[1]]
            r["x"] += step * (1 if k == "right" else -1 if k == "left" else 0)
            r["y"] += step * (1 if k == "up" else -1 if k == "down" else 0)
        elif e.key in ("s", "q", "enter"):
            save_rois(self.out_path, tuple(self.boundary), self.placed, confirmed=True)
            if e.key in ("q", "enter"):
                self.plt.close(self.fig)
                return
        self.redraw()

    def show(self):
        print("Drag boundary/ROIs; scroll or [ ] to resize; , . to rotate rects; "
              "s save, q quit.")
        self.plt.show()


def run_gui(db: Path, out_path: Path, bin_in: float = 4.0) -> None:   # pragma: no cover
    H, extent, n = build_background(db, bin_in)
    boundary, placed, pending = load_initial_state(out_path, extent)
    print(f"Loaded {n:,} fixes; {len(placed)} existing ROI(s), "
          f"{len(pending)} pending. Editing {out_path}")
    RoiEditor(H, extent, boundary, placed, pending, out_path).show()


def main():
    ap = argparse.ArgumentParser(description="Drag-edit WISER ROIs on an occupancy background.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--bin-inches", type=float, default=4.0)
    args = ap.parse_args()
    run_gui(args.db, args.out, args.bin_inches)


if __name__ == "__main__":
    main()

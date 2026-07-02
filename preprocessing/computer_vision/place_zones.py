"""
Draw the shelter zones on a top-down shelter cam's reference still (CH05/CH06).

The cams look straight DOWN on the shelter through the IR window, and the shelter has TWO doors.
So the useful primitives are:
  inside_shelter  - a POLYGON of the shelter footprint (seen through the glass; where rats rest)
  door1, door2    - a short LINE (2 clicks) across each door opening = an entry/exit GATE

Phase A (now) uses ONLY `inside_shelter`, and shelter_sleep.py already loads your calibration
shelter quad as `inside` automatically -- so you only need this tool to (a) reshape the inside
region, or (b) mark the two door gates for the DEFERRED Phase-B entry/exit occupancy counting
(a rat track crossing a gate line = one in/out event, used to estimate occupancy when the inside
glass is fogged).

    python place_zones.py --channel CH05

Controls (also on the button bar):
  click              add a vertex to the ACTIVE item (inside polygon, or a 2-point door line)
  [In][D1][D2] / 1 2 3   select inside / door1 / door2
  u / right-click    undo last vertex      c  clear active item
  s                  save -> configs/CH05_zones.json      q  save & quit

`inside_shelter` is pre-loaded from the calibration shelter quad (CHxx_calib.json). Reference
still comes from:
    python extract_clip.py --channel CH05 --frame
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE / "configs"
MAX_W, MAX_H = 1400, 760
BAR_H = 30

# item key -> (display label, kind, color BGR, tag)
ITEMS = [
    ("inside", "inside_shelter", "polygon", (60, 90, 230), "used now (Phase A)"),
    ("door1", "door 1", "line", (60, 210, 230), "gate for Phase B"),
    ("door2", "door 2", "line", (90, 210, 90), "gate for Phase B"),
]
KEYS = [it[0] for it in ITEMS]
META = {it[0]: it for it in ITEMS}


def _preload_inside(channel: str, ref_size) -> list:
    """The calibration shelter quad, scaled to the reference-still size, as the initial inside poly."""
    p = CONFIG_DIR / f"{channel}_calib.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8-sig"))
    pts = np.asarray(d.get("image_px", []), float).reshape(-1, 2)
    if len(pts) < 3:
        return []
    isz = d.get("image_size")
    if isz and ref_size and tuple(isz) != tuple(ref_size):
        pts = pts * np.array([ref_size[0] / isz[0], ref_size[1] / isz[1]], float)
    return [[float(x), float(y)] for x, y in pts]


def main() -> None:
    ap = argparse.ArgumentParser(description="Draw inside polygon + 2 door gate-lines for a shelter cam.",
                                 allow_abbrev=False)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--reference", help="reference still (default configs/<CH>_reference.png)")
    args = ap.parse_args()

    ref = Path(args.reference) if args.reference else CONFIG_DIR / f"{args.channel}_reference.png"
    if not ref.exists():
        raise SystemExit(f"no reference still {ref}\n  run: python extract_clip.py --channel {args.channel} --frame")
    img = cv2.imread(str(ref))
    if img is None:
        raise SystemExit(f"could not read {ref}")
    H, W = img.shape[:2]
    out_path = CONFIG_DIR / f"{args.channel}_zones.json"

    zones = {k: [] for k in KEYS}
    if out_path.exists():
        d = json.loads(out_path.read_text(encoding="utf-8-sig"))
        zones["inside"] = [[float(a), float(b)] for a, b in d.get("inside", [])]
        for i, door in enumerate(d.get("doors", [])[:2]):
            zones[f"door{i+1}"] = [[float(a), float(b)] for a, b in door]
        print(f"[{args.channel}] loaded existing {out_path.name}")
    if not zones["inside"]:
        zones["inside"] = _preload_inside(args.channel, (W, H))
        if zones["inside"]:
            print(f"[{args.channel}] pre-loaded inside_shelter from calibration quad")

    scale = min(1.0, MAX_W / W, (MAX_H - 2 * BAR_H) / H)
    dW, dH = int(W * scale), int(H * scale)
    st = {"active": "inside", "quit": False}
    win = f"zones {args.channel} (inside polygon + 2 door gates)"

    def buttons():
        specs = [("In:inside", "1"), ("D1:door1", "2"), ("D2:door2", "3"),
                 ("Undo", "u"), ("Clear", "c"), ("SAVE & QUIT", "q")]
        rects, x = [], 4
        y0 = BAR_H + dH + 2
        for lab, key in specs:
            w = 150 if "SAVE" in lab else 108
            rects.append((x, y0, x + w, y0 + BAR_H - 4, lab, key))
            x += w + 6
        return rects

    def redraw():
        canvas = np.full((BAR_H + dH + BAR_H, dW, 3), 40, np.uint8)
        disp = cv2.resize(img, (dW, dH))
        for k in KEYS:
            pts = (np.asarray(zones[k], float) * scale).astype(np.int32)
            col, kind = META[k][3], META[k][2]
            if kind == "polygon" and len(pts) >= 3:
                ov = disp.copy(); cv2.fillPoly(ov, [pts], col)
                disp = cv2.addWeighted(ov, 0.25, disp, 0.75, 0)
                cv2.polylines(disp, [pts], True, col, 2)
            elif kind == "line" and len(pts) >= 2:
                cv2.polylines(disp, [pts], False, col, 3)
            elif len(pts) >= 2:
                cv2.polylines(disp, [pts], False, col, 2)
            for p in pts:
                cv2.circle(disp, tuple(p), 4, col, -1)
        canvas[BAR_H:BAR_H + dH, :] = disp
        it = META[st["active"]]
        n = len(zones[st["active"]])
        hint = "click 2 points across the opening" if it[2] == "line" else "click to add vertices"
        cv2.putText(canvas, f"ACTIVE: {it[1]} [{it[4]}] ({n} pts)  -  {hint}, u=undo, c=clear",
                    (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, it[3], 1)
        for x0, y0, x1, y1, lab, key in buttons():
            on = (key in ("1", "2", "3") and st["active"] == KEYS[int(key) - 1])
            cv2.rectangle(canvas, (x0, y0), (x1, y1), (90, 90, 90) if on else (60, 60, 60), -1)
            cv2.putText(canvas, lab, (x0 + 6, y0 + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cv2.imshow(win, canvas)

    def do_key(k):
        if k in ("1", "2", "3"):
            st["active"] = KEYS[int(k) - 1]
        elif k == "u" and zones[st["active"]]:
            zones[st["active"]].pop()
        elif k == "c":
            zones[st["active"]] = []
        elif k == "q":
            st["quit"] = True
        redraw()

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            for x0, y0, x1, y1, lab, key in buttons():
                if x0 <= x <= x1 and y0 <= y <= y1:
                    do_key(key); return
            if BAR_H <= y < BAR_H + dH:
                # a door is a 2-point line: a 3rd click starts the line over
                if META[st["active"]][2] == "line" and len(zones[st["active"]]) >= 2:
                    zones[st["active"]] = []
                zones[st["active"]].append([x / scale, (y - BAR_H) / scale])
                redraw()
        elif ev == cv2.EVENT_RBUTTONDOWN and zones[st["active"]]:
            zones[st["active"]].pop(); redraw()

    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE | getattr(cv2, "WINDOW_GUI_NORMAL", 0))
    cv2.moveWindow(win, 0, 0)
    cv2.setMouseCallback(win, on_mouse)
    redraw()
    while not st["quit"]:
        k = cv2.waitKey(20) & 0xFF
        if k != 255:
            ch = chr(k) if k < 128 else ""
            if ch == "s":
                _save(out_path, args.channel, zones, W, H)
            elif ch in ("1", "2", "3", "u", "c", "q"):
                do_key(ch)
    cv2.destroyAllWindows()
    _save(out_path, args.channel, zones, W, H)


def _save(out_path, channel, zones, W, H):
    doors = [[[round(a, 1), round(b, 1)] for a, b in zones[k]]
             for k in ("door1", "door2") if len(zones[k]) >= 2]
    payload = {"channel": channel, "image_size": [int(W), int(H)],
               "inside": [[round(a, 1), round(b, 1)] for a, b in zones["inside"]],
               "doors": doors}
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[{channel}] saved {out_path}  (inside={len(zones['inside'])}pts, doors={len(doors)})")
    if 0 < len(zones["inside"]) < 3:
        print(f"  WARNING: inside_shelter has only {len(zones['inside'])} vertices (need >=3)")
    for k in ("door1", "door2"):
        if len(zones[k]) == 1:
            print(f"  WARNING: {k} has 1 point (a gate needs 2) - not saved")


if __name__ == "__main__":
    main()

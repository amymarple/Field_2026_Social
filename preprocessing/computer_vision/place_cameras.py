"""
Drag-and-drop GUI to position cameras AND shelters, saved into field_layout.json.

Top-down map: x = 40 ft length (horizontal), y = 20 ft width (vertical); 15-pole 3x5 grid.

How to use:
  - The ACTIVE item is highlighted (yellow ring). DRAG its square/box to move it.
    Cameras snap to the nearest pole while Snap is ON.
  - DRAG its small handle (the dot on a stick) to rotate it:
      camera handle  = aim direction
      shelter handle = long-axis (orientation)
  - Click another item's marker to make it active and drag it too.
  - Buttons at the bottom:  [< Prev]  [Next >]  [Snap: ON/OFF]  [SAVE & QUIT]
    (keys also work: p / n / s / q)

Writes camera pos_cm/pole/aim and shelter center_cm/orientation_deg back to the layout.

    python place_cameras.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

import field_coords as fc

LAYOUT = fc.LAYOUT_PATH
MARGIN = 80
SNAP_PX = 22
HIT_PX = 16
BTN_H = 38


def main() -> None:
    import cv2
    layout = fc.load_layout()
    X, Y = fc.FIELD_X_CM, fc.FIELD_Y_CM
    scale = 1000.0 / X
    field_w = int(X * scale); field_h = int(Y * scale)
    cw = field_w + 2 * MARGIN
    chh = field_h + 2 * MARGIN + BTN_H + 16

    def cm2px(x, y):
        return int(MARGIN + x * scale), int(MARGIN + (Y - y) * scale)

    def px2cm(px, py):
        return (px - MARGIN) / scale, Y - (py - MARGIN) / scale

    poles = layout.get("poles", {})
    shelters = {k: v for k, v in layout.get("shelters", {}).items() if not k.startswith("_")}
    cams = [c for c in layout.get("camera_mounts", {}) if not c.startswith("_")]
    mounts = layout["camera_mounts"]

    items = [("cam", c) for c in cams] + [("shelter", s) for s in shelters]
    pc = {c: {"pos_cm": mounts[c].get("pos_cm") or [X / 2, Y / 2],
              "pole": mounts[c].get("pole"),
              "aim_deg": _deg(mounts[c].get("aim", "deg 0")),
              "nadir": bool(mounts[c].get("shelter"))} for c in cams}   # shelter cams face straight down
    ps = {s: {"center_cm": shelters[s].get("center_cm") or [X / 2, Y / 2],
              "orientation_deg": float(shelters[s].get("orientation_deg", 90)),
              "size_cm": shelters[s].get("size_cm", [62.55, 45.72])} for s in shelters}

    st = {"active": 0, "drag": None, "snap": True}
    win = "Place cameras + shelters (drag to move/rotate)"

    # bottom buttons: (label, key) laid out left->right
    def button_rects():
        labels = [("< Prev", "p"), ("Next >", "n"),
                  (f"Snap: {'ON' if st['snap'] else 'OFF'}", "s"), ("SAVE & QUIT", "q")]
        y0 = MARGIN + field_h + 12
        rects = []
        x = MARGIN
        for lab, key in labels:
            w = 150 if "SAVE" in lab else 120
            rects.append((x, y0, x + w, y0 + BTN_H, lab, key))
            x += w + 12
        return rects

    def item_pos(idx):
        typ, key = items[idx]
        return (pc[key]["pos_cm"], pc[key]["aim_deg"]) if typ == "cam" \
            else (ps[key]["center_cm"], ps[key]["orientation_deg"])

    def handle_cm(idx):
        typ, key = items[idx]
        if typ == "cam" and pc[key]["nadir"]:          # nadir cam: no aim handle
            return None
        (px, py), deg = item_pos(idx)
        r = 70 if typ == "cam" else (ps[key]["size_cm"][0] / 2 + 25)
        th = math.radians(deg)
        return [px + r * math.cos(th), py + r * math.sin(th)]

    def nearest_pole(px, py):
        best, bd = None, 1e9
        for name, (x, y) in poles.items():
            qx, qy = cm2px(x, y); d = math.hypot(qx - px, qy - py)
            if d < bd:
                best, bd = name, d
        return best, bd

    def redraw():
        img = np.full((chh, cw, 3), 245, np.uint8)
        cv2.rectangle(img, cm2px(0, 0), cm2px(X, Y), (40, 40, 40), 2)
        for name, (x, y) in poles.items():
            p = cm2px(x, y); cv2.circle(img, p, 6, (180, 110, 30), -1)
            cv2.putText(img, name, (p[0] + 5, p[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 110, 30), 1)
        for idx, (typ, key) in enumerate(items):
            active = idx == st["active"]
            col = (0, 0, 220) if typ == "cam" else (0, 140, 220)
            (pcx, pcy), deg = item_pos(idx)
            p = cm2px(pcx, pcy)
            if typ == "shelter":
                corners = fc.shelter_corners({"center_cm": [pcx, pcy],
                                              "size_cm": ps[key]["size_cm"],
                                              "orientation_deg": deg})
                pts = np.array([cm2px(*corners[c]) for c in fc.CORNER_KEYS], np.int32)
                cv2.polylines(img, [pts], True, col, 2)
                cv2.circle(img, p, 4, col, -1)        # center grab dot
            else:
                cv2.drawMarker(img, p, col, cv2.MARKER_SQUARE, 14, 2)
            # handle (stick + dot), or "down" tag for nadir cams
            hc = handle_cm(idx)
            if hc is not None:
                h = cm2px(*hc); cv2.line(img, p, h, col, 1); cv2.circle(img, h, 6, col, -1)
            elif typ == "cam":
                cv2.putText(img, "down", (p[0] + 9, p[1] - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
            if active:
                cv2.circle(img, p, 16, (0, 215, 255), 2)
            cv2.putText(img, key, (p[0] + 8, p[1] + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
        # buttons
        for x0, y0, x1, y1, lab, key in button_rects():
            cv2.rectangle(img, (x0, y0), (x1, y1), (60, 60, 60), -1)
            cv2.putText(img, lab, (x0 + 8, y0 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        # banner
        typ, key = items[st["active"]]
        if typ == "cam" and pc[key]["nadir"]:
            tip = "drag box to move (nadir cam - no aim needed)"
        elif typ == "cam":
            tip = "drag box to move, drag handle to set aim"
        else:
            tip = "drag box to move, drag handle to set long-axis"
        cv2.rectangle(img, (0, 0), (cw, 26), (0, 0, 0), -1)
        cv2.putText(img, f"ACTIVE: {key} ({typ}) - {tip}. Next> when done.",
                    (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1)
        cv2.imshow(win, img)

    def set_active_pos(cursor_px):
        idx = st["active"]; typ, key = items[idx]
        xc, yc = px2cm(*cursor_px)
        if typ == "cam":
            pole = None
            if st["snap"]:
                pn, d = nearest_pole(*cursor_px)
                if d <= SNAP_PX:
                    xc, yc = poles[pn]; pole = pn
            pc[key]["pos_cm"] = [round(xc, 1), round(yc, 1)]; pc[key]["pole"] = pole
        else:
            ps[key]["center_cm"] = [round(xc, 1), round(yc, 1)]

    def set_active_rot(cursor_px):
        idx = st["active"]; typ, key = items[idx]
        if typ == "cam" and pc[key]["nadir"]:
            return
        (px, py), _ = item_pos(idx)
        xc, yc = px2cm(*cursor_px)
        deg = math.degrees(math.atan2(yc - py, xc - px))
        if typ == "cam":
            pc[key]["aim_deg"] = round(deg % 360, 1)
        else:
            ps[key]["orientation_deg"] = round(deg % 180, 1)

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            for x0, y0, x1, y1, lab, key in button_rects():     # buttons first
                if x0 <= x <= x1 and y0 <= y <= y1:
                    do_key(key); return
            # active item's handle?
            hc = handle_cm(st["active"])
            if hc is not None:
                hp = cm2px(*hc)
                if math.hypot(hp[0] - x, hp[1] - y) <= HIT_PX:
                    st["drag"] = "rot"; return
            # any item's marker -> select + move (shelters: anywhere inside the box)
            for idx in range(len(items)):
                typ, key = items[idx]
                (pcx, pcy), deg = item_pos(idx)
                mp = cm2px(pcx, pcy)
                hit = math.hypot(mp[0] - x, mp[1] - y) <= HIT_PX
                if not hit and typ == "shelter":
                    corners = fc.shelter_corners({"center_cm": [pcx, pcy],
                                                  "size_cm": ps[key]["size_cm"],
                                                  "orientation_deg": deg})
                    poly = np.array([cm2px(*corners[c]) for c in fc.CORNER_KEYS], np.int32)
                    hit = cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0
                if hit:
                    st["active"] = idx; st["drag"] = "pos"; redraw(); return
        elif ev == cv2.EVENT_MOUSEMOVE and st["drag"]:
            (set_active_pos if st["drag"] == "pos" else set_active_rot)((x, y)); redraw()
        elif ev == cv2.EVENT_LBUTTONUP:
            st["drag"] = None

    def do_key(k):
        if k == "n":
            st["active"] = (st["active"] + 1) % len(items)
        elif k == "p":
            st["active"] = (st["active"] - 1) % len(items)
        elif k == "s":
            st["snap"] = not st["snap"]
        elif k == "q":
            st["quit"] = True
        redraw()

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    redraw()
    st["quit"] = False
    while not st["quit"]:
        k = cv2.waitKey(20) & 0xFF
        if k in (ord("q"), ord("n"), ord("p"), ord("s")):
            do_key(chr(k))
    cv2.destroyAllWindows()

    for c in cams:
        mounts[c]["pos_cm"] = pc[c]["pos_cm"]
        if pc[c]["pole"]:
            mounts[c]["pole"] = pc[c]["pole"]
        mounts[c]["aim"] = "nadir" if pc[c]["nadir"] else f"deg {pc[c]['aim_deg']}"
    for s in shelters:
        layout["shelters"][s]["center_cm"] = ps[s]["center_cm"]
        layout["shelters"][s]["orientation_deg"] = ps[s]["orientation_deg"]
    LAYOUT.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    print(f"saved camera + shelter positions to {LAYOUT}")
    for c in cams:
        aim = "nadir" if pc[c]["nadir"] else f"deg {pc[c]['aim_deg']}"
        print(f"  {c}: pos={pc[c]['pos_cm']} pole={pc[c]['pole']} aim={aim}")
    for s in shelters:
        print(f"  {s} shelter: center={ps[s]['center_cm']} deg={ps[s]['orientation_deg']}")
    print("Regenerate the map:  python make_layout_map.py")


def _deg(aim) -> float:
    if isinstance(aim, str) and aim.startswith("deg"):
        try:
            return float(aim.split()[1])
        except (IndexError, ValueError):
            return 0.0
    return 0.0


if __name__ == "__main__":
    main()

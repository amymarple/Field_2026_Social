"""
Minimal box labeler for the rat detector (Stage 1) -> YOLO-format labels.

Draw a box around each rat (DRAW mode), or adjust/delete individual boxes (EDIT mode).
Labels are written next to the images as YOLO txt (`class cx cy w h`, normalized;
class 0 = rat). Resumable: existing labels reload. Frames with no rats save an EMPTY
txt (a valid negative example for YOLO).

    python label_frames.py                         # dataset/rat/images -> dataset/rat/labels
    python label_frames.py --dir other/images

On-canvas buttons (click): [Draw] [Edit] [Del] [Clear] [< Prev] [Next >] [Save&Quit]
  DRAW mode: left-drag = new box.
  EDIT mode: click a box to select; drag inside to MOVE; drag a corner handle to RESIZE;
             [Del] (or 'd') removes the SELECTED box.
Keys also work: w=draw  e=edit  d=del  c=clear  n/SPACE=next  p=prev  q=save+quit
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

MAX_W = 1400             # max on-screen image width; clicks map back to image px
MAX_H = 760              # max image height so image + button bar + title fit a 1080p screen
BTN_H = 34
HANDLE = 9               # corner-handle hit radius (display px)
BUTTONS = [("Draw", "w"), ("Edit", "e"), ("Del", "d"), ("Clear", "c"),
           ("< Prev", "p"), ("Next >", "n"), ("Save&Quit", "q")]


def yolo_load(txt: Path, W: int, H: int):
    boxes = []
    if txt.exists():
        for line in txt.read_text().splitlines():
            p = line.split()
            if len(p) >= 5:
                cx, cy, w, h = (float(v) for v in p[1:5])
                cx, cy, w, h = cx * W, cy * H, w * W, h * H
                boxes.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
    return boxes


def yolo_save(txt: Path, boxes, W: int, H: int, cls: int = 0):
    lines = []
    for x1, y1, x2, y2 in boxes:
        x1, x2 = sorted((x1, x2)); y1, y2 = sorted((y1, y2))
        w, h = (x2 - x1) / W, (y2 - y1) / H
        if w <= 0 or h <= 0:
            continue
        cx, cy = (x1 + x2) / 2 / W, (y1 + y2) / 2 / H
        lines.append(f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("\n".join(lines) + ("\n" if lines else ""))      # empty txt = negative frame


def _corners(b):
    x1, y1, x2, y2 = b
    return {"tl": (x1, y1), "tr": (x2, y1), "bl": (x1, y2), "br": (x2, y2)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Minimal YOLO-format box labeler (draw + edit).")
    here = Path(__file__).resolve().parent
    ap.add_argument("--dir", default=str(here / "dataset" / "rat" / "images"))
    ap.add_argument("--labels", default=None, help="labels dir (default: sibling 'labels')")
    ap.add_argument("--class-id", type=int, default=0)
    ap.add_argument("--min-box", type=int, default=4, help="ignore boxes smaller than this (px)")
    args = ap.parse_args()

    img_dir = Path(args.dir)
    lbl_dir = Path(args.labels) if args.labels else img_dir.parent / "labels"
    imgs = sorted([p for p in img_dir.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg")])
    if not imgs:
        raise SystemExit(f"no images in {img_dir}")

    win = "label rats"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE | getattr(cv2, "WINDOW_GUI_NORMAL", 0))
    cv2.moveWindow(win, 0, 0)                  # pin top-left so the bottom button bar isn't off-screen

    # shared mutable state across the callback and the main loop
    S = {"mode": "draw", "boxes": [], "sel": -1, "scale": 1.0, "dispH": 0,
         "action": None, "corner": None, "grab": (0, 0), "p0": None, "p1": None,
         "req": None, "min_box": args.min_box}

    def btn_rects(width):
        w = width / len(BUTTONS)
        return [(int(i * w), int((i + 1) * w), lab, key) for i, (lab, key) in enumerate(BUTTONS)]

    def on_mouse(ev, x, y, flags, param):
        s = S["scale"]
        if ev == cv2.EVENT_LBUTTONDOWN:
            if y >= S["dispH"]:                                   # button bar
                for x0, x1, lab, key in btn_rects(S["dispW"]):
                    if x0 <= x < x1:
                        S["req"] = key
                        return
                return
            ix, iy = x / s, y / s
            if S["mode"] == "draw":
                S["action"] = "new"; S["p0"] = (ix, iy); S["p1"] = (ix, iy)
                return
            # EDIT: try resize handle of selected box, else select/move, else deselect
            if S["sel"] >= 0:
                for cname, (cx, cy) in _corners(S["boxes"][S["sel"]]).items():
                    if abs(cx - ix) * s <= HANDLE and abs(cy - iy) * s <= HANDLE:
                        S["action"] = "resize"; S["corner"] = cname
                        return
            hit = -1
            for i in range(len(S["boxes"]) - 1, -1, -1):         # topmost first
                x1, y1, x2, y2 = S["boxes"][i]
                if min(x1, x2) <= ix <= max(x1, x2) and min(y1, y2) <= iy <= max(y1, y2):
                    hit = i
                    break
            S["sel"] = hit
            if hit >= 0:
                bx1, by1, _, _ = S["boxes"][hit]
                S["action"] = "move"; S["grab"] = (ix - bx1, iy - by1)
        elif ev == cv2.EVENT_MOUSEMOVE:
            ix, iy = x / s, y / s
            if S["action"] == "new":
                S["p1"] = (ix, iy)
            elif S["action"] == "resize" and S["sel"] >= 0:
                b = S["boxes"][S["sel"]]
                if "l" in S["corner"]:
                    b[0] = ix
                else:
                    b[2] = ix
                if "t" in S["corner"]:
                    b[1] = iy
                else:
                    b[3] = iy
            elif S["action"] == "move" and S["sel"] >= 0:
                b = S["boxes"][S["sel"]]
                w, h = b[2] - b[0], b[3] - b[1]
                gx, gy = S["grab"]
                b[0], b[1] = ix - gx, iy - gy
                b[2], b[3] = b[0] + w, b[1] + h
        elif ev == cv2.EVENT_LBUTTONUP:
            if S["action"] == "new" and S["p0"] and S["p1"]:
                x1, y1 = S["p0"]; x2, y2 = S["p1"]
                if abs(x2 - x1) >= S["min_box"] and abs(y2 - y1) >= S["min_box"]:
                    S["boxes"].append([x1, y1, x2, y2]); S["sel"] = len(S["boxes"]) - 1
            if S["action"] == "resize" and S["sel"] >= 0:        # normalize after resize
                b = S["boxes"][S["sel"]]
                b[0], b[2] = sorted((b[0], b[2])); b[1], b[3] = sorted((b[1], b[3]))
            S["action"] = None; S["corner"] = None; S["p0"] = S["p1"] = None

    cv2.setMouseCallback(win, on_mouse)

    i = 0
    while 0 <= i < len(imgs):
        path = imgs[i]
        img = cv2.imread(str(path))
        H, W = img.shape[:2]
        scale = min(MAX_W / W, MAX_H / H, 1.0)        # fit BOTH width and height
        dispW, dispH = int(W * scale), int(H * scale)
        txt = lbl_dir / f"{path.stem}.txt"
        S.update({"boxes": yolo_load(txt, W, H), "sel": -1, "scale": scale,
                  "dispW": dispW, "dispH": dispH, "action": None, "req": None})

        nav = None
        while nav is None:
            canvas = np.zeros((dispH + BTN_H, dispW, 3), np.uint8)
            canvas[:dispH] = cv2.resize(img, (dispW, dispH))
            for j, (x1, y1, x2, y2) in enumerate(S["boxes"]):
                col = (0, 255, 255) if j == S["sel"] else (0, 255, 0)
                p1 = (int(x1 * scale), int(y1 * scale)); p2 = (int(x2 * scale), int(y2 * scale))
                cv2.rectangle(canvas, p1, p2, col, 2)
                if j == S["sel"] and S["mode"] == "edit":
                    for (cx, cy) in _corners((x1, y1, x2, y2)).values():
                        cv2.rectangle(canvas, (int(cx * scale) - 4, int(cy * scale) - 4),
                                      (int(cx * scale) + 4, int(cy * scale) + 4), (0, 165, 255), -1)
            if S["action"] == "new" and S["p0"] and S["p1"]:
                cv2.rectangle(canvas, (int(S["p0"][0] * scale), int(S["p0"][1] * scale)),
                              (int(S["p1"][0] * scale), int(S["p1"][1] * scale)), (0, 200, 255), 1)
            # button bar
            for x0, x1, lab, key in btn_rects(dispW):
                active = (S["mode"] == "draw" and lab == "Draw") or (S["mode"] == "edit" and lab == "Edit")
                cv2.rectangle(canvas, (x0, dispH), (x1, dispH + BTN_H),
                              (90, 90, 90) if active else (45, 45, 45), -1)
                cv2.rectangle(canvas, (x0, dispH), (x1, dispH + BTN_H), (20, 20, 20), 1)
                cv2.putText(canvas, lab, (x0 + 6, dispH + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (255, 255, 255), 1)
            cv2.putText(canvas, f"[{i+1}/{len(imgs)}] {path.name}  boxes:{len(S['boxes'])}  "
                        f"mode:{S['mode'].upper()}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 220, 255), 1)
            cv2.imshow(win, canvas)

            k = cv2.waitKey(20) & 0xFF
            req = S["req"]; S["req"] = None
            cmd = req or (chr(k) if k != 255 else None)
            if cmd == "w":
                S["mode"] = "draw"; S["sel"] = -1
            elif cmd == "e":
                S["mode"] = "edit"
            elif cmd == "d":
                if S["mode"] == "edit" and S["sel"] >= 0:
                    S["boxes"].pop(S["sel"]); S["sel"] = -1
                elif S["boxes"]:
                    S["boxes"].pop(); S["sel"] = -1
            elif cmd == "c":
                S["boxes"].clear(); S["sel"] = -1
            elif cmd in ("n", " "):
                nav = "next"
            elif cmd == "p":
                nav = "prev"
            elif cmd == "q":
                nav = "quit"

        yolo_save(txt, S["boxes"], W, H, args.class_id)
        if nav == "next":
            i += 1
        elif nav == "prev":
            i = max(0, i - 1)
        else:
            cv2.destroyAllWindows()
            print(f"saved through {path.name}. labels in {lbl_dir}")
            return
    cv2.destroyAllWindows()
    n_lbl = len(list(lbl_dir.glob("*.txt"))) if lbl_dir.exists() else 0
    print(f"done all {len(imgs)} frames. {n_lbl} label files in {lbl_dir}")


if __name__ == "__main__":
    main()

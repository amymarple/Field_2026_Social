"""
Box labeler for the rat detector (Stage 1) -> YOLO-format labels. TKINTER GUI.

Built on Tkinter (not OpenCV HighGUI): the conda-forge OpenCV is a Qt6 build whose imshow window
has its own native zoom/pan that fought our drawing. Here we own every mouse/key event, so left-drag
only ever draws. Rendering still uses OpenCV to compose the frame into a numpy image, shown via PIL.

Full labeling rules (when to box / skip / huddle / empty): see LABELING_PROTOCOL.md in this folder.

Labels are YOLO txt (`class cx cy w h`, normalized; class 0 = rat) next to the images. A frame with
no rats saves an EMPTY txt (a valid negative). It STARTS on the first UNDECIDED frame (not labeled and
not skip/huddle'd); Next is context-aware (new frame -> next undecided; a decided frame you're reviewing
-> the adjacent frame); Prev steps back through ALL frames so you can review/correct anything. --all
starts at frame 1 and Next visits every frame.

    python label_frames.py            # start at first undecided; Prev reviews decided ones
    python label_frames.py --all      # start at frame 1; Next visits every frame

Controls
  MODE:  w = draw (left-drag = new box)   e = edit (click a box; drag body = move, drag corner = resize)
         d = delete selected/last box     c = clear all boxes
  ZOOM/PAN:  mouse WHEEL (or + / -) = zoom, centered on the cursor;  RIGHT-drag OR i/j/k/l = pan;
             0 (or f) = reset to fit.  (LEFT button is ONLY draw/edit.)
  VIEW:  box edges render SEMI-TRANSPARENT so they don't hide rat boundaries;  b = hide/show all boxes
         (peek at the raw pixels without deleting anything).
  JUDGE (no label written; excluded from training - train_detector ignores label-less images):
         s = toggle SKIP (fog / unlabelable)   g = toggle HUDDLE (indivisible pile; deferred)
         (or click the on-canvas Skip / Huddle buttons). The status shows as a banner + a lit button
         and persists (dataset/rat/status/); a toggle prints to the console; press again to un-mark;
         drawing a box overrides it and labels the frame. Works on ANY frame, incl. already-labeled
         ones from a prior session (pressing g/s there deletes the label and marks it deferred).
  NAV:   n / SPACE = next   p = prev   q = save + quit   (on-canvas buttons also work)
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np
import tkinter as tk
from PIL import Image, ImageTk

MAX_W = 1400             # max on-screen image width; events map back to image px
MAX_H = 760              # max image height so image + button bar fit a 1080p screen
BTN_H = 34
HANDLE = 9               # corner-handle hit radius (display px)
BOX_ALPHA = 0.5          # box-edge opacity (0..1); < 1 = semi-transparent so edges don't hide rat boundaries
BUTTONS = [("Draw", "w"), ("Edit", "e"), ("Del", "d"), ("Clear", "c"),
           ("Skip", "s"), ("Huddle", "g"), ("< Prev", "p"), ("Next >", "n"), ("Save&Quit", "q")]


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


class Labeler:
    def __init__(self, root, imgs, img_dir, lbl_dir, status_dir, args):
        self.root, self.imgs = root, imgs
        self.img_dir, self.lbl_dir, self.status_dir, self.args = img_dir, lbl_dir, status_dir, args
        # interaction state
        self.mode, self.boxes, self.sel = "draw", [], -1
        self.show_boxes = True
        self.action, self.corner, self.grab, self.p0, self.p1 = None, None, (0, 0), None, None
        self.zoom, self.cx, self.cy, self.pan_last = 1.0, 0.0, 0.0, None
        self.vx0 = self.vy0 = 0.0; self.vw0 = self.vh0 = 1.0
        self.W = self.H = 1; self.base = 1.0; self.dispW = self.dispH = 1
        self.i = 0; self.img = self.path = self.txt = None; self.was_decided = False
        self.photo = None

        self.label = tk.Label(root, bd=0)
        self.label.pack()
        self.label.bind("<Button-1>", self.on_lclick)
        self.label.bind("<B1-Motion>", self.on_ldrag)
        self.label.bind("<ButtonRelease-1>", self.on_lrelease)
        self.label.bind("<Button-3>", self.on_rpress)          # RIGHT-drag = pan (we own it now)
        self.label.bind("<B3-Motion>", self.on_rdrag)
        self.label.bind("<ButtonRelease-3>", self.on_rrelease)
        self.label.bind("<MouseWheel>", self.on_wheel)         # Windows wheel
        root.bind_all("<Key>", self.on_key)                    # bind_all: keys fire no matter which sub-widget has focus
        root.protocol("WM_DELETE_WINDOW", self.do_quit)
        root.title("label rats (Tkinter)")
        root.resizable(False, False)

        self.load_frame(self.start_index())
        self.render()
        root.focus_force()

    # ---- decision status (kept in place; skip/huddle get NO label so train_detector ignores them) ----
    def has_label(self, idx):
        return (self.lbl_dir / f"{self.imgs[idx].stem}.txt").exists()

    def status_of(self, idx):
        stem = self.imgs[idx].stem
        if (self.status_dir / f"{stem}.skip").exists():
            return "skip"
        if (self.status_dir / f"{stem}.huddle").exists():
            return "huddle"
        return None

    def toggle_status(self, idx, st):
        self.status_dir.mkdir(parents=True, exist_ok=True)
        stem = self.imgs[idx].stem
        cur = self.status_of(idx)
        for s in ("skip", "huddle"):
            f = self.status_dir / f"{stem}.{s}"
            if f.exists():
                f.unlink()
        if cur != st:                                          # same key again -> un-mark
            (self.status_dir / f"{stem}.{st}").write_text("")
            lt = self.lbl_dir / f"{stem}.txt"                  # skip/huddle is not a training frame
            if lt.exists():
                lt.unlink()

    def clear_status(self, idx):
        for s in ("skip", "huddle"):
            f = self.status_dir / f"{self.imgs[idx].stem}.{s}"
            if f.exists():
                f.unlink()

    def is_decided(self, idx):
        return self.has_label(idx) or self.status_of(idx) is not None

    def next_undecided(self, idx):
        j = idx + 1
        while j < len(self.imgs) and self.is_decided(j):
            j += 1
        return j

    def start_index(self):
        n_done = sum(self.is_decided(k) for k in range(len(self.imgs)))
        i = self.next_undecided(-1)
        if self.args.all or i >= len(self.imgs):
            if i >= len(self.imgs):
                print(f"all {len(self.imgs)} frames already decided; opening at frame 1 for review.")
            return 0
        print(f"{n_done}/{len(self.imgs)} already decided (labeled/skip/huddle); starting at first "
              f"undecided (frame {i+1}). Next -> next undecided; Prev -> step back through ALL.")
        print("[build: tkinter/no-Qt] LEFT-drag = draw only | wheel/+/- zoom (cursor-centered) | "
              "RIGHT-drag or i/j/k/l = pan | 0/f = fit")
        return i

    # ---- viewport coordinate mapping ----
    def d2i(self, x, y):
        return self.vx0 + x * self.vw0 / self.dispW, self.vy0 + y * self.vh0 / self.dispH

    def i2d(self, ix, iy):
        return (ix - self.vx0) * self.dispW / self.vw0, (iy - self.vy0) * self.dispH / self.vh0

    def zoom_at(self, x, y, factor):
        ix, iy = self.d2i(x, y)
        self.zoom = float(min(max(self.zoom * factor, 1.0), 20.0))
        eff = self.base * self.zoom
        vw, vh = self.dispW / eff, self.dispH / eff
        self.cx = ix - (x / self.dispW) * vw + vw / 2
        self.cy = iy - (y / self.dispH) * vh + vh / 2

    def btn_rects(self, width):
        w = width / len(BUTTONS)
        return [(int(k * w), int((k + 1) * w), lab, key) for k, (lab, key) in enumerate(BUTTONS)]

    # ---- frame lifecycle ----
    def load_frame(self, i):
        self.i = i
        if not (0 <= i < len(self.imgs)):
            return
        self.path = self.imgs[i]
        self.img = cv2.imread(str(self.path))
        if self.img is None:
            return
        self.was_decided = self.is_decided(i)
        self.H, self.W = self.img.shape[:2]
        self.base = min(MAX_W / self.W, MAX_H / self.H, 1.0)
        self.dispW, self.dispH = int(self.W * self.base), int(self.H * self.base)
        self.txt = self.lbl_dir / f"{self.path.stem}.txt"
        self.boxes = yolo_load(self.txt, self.W, self.H)
        self.sel, self.action, self.p0, self.p1, self.pan_last = -1, None, None, None, None
        self.zoom, self.cx, self.cy = 1.0, self.W / 2, self.H / 2
        self.vx0, self.vy0, self.vw0, self.vh0 = 0.0, 0.0, float(self.W), float(self.H)

    def save_current(self):
        # boxes -> labeled (clears any marker); no boxes + marker -> keep marker (no label);
        # no boxes + no marker -> deliberate empty negative.
        if self.boxes:
            self.clear_status(self.i)
            yolo_save(self.txt, self.boxes, self.W, self.H, self.args.class_id)
        elif self.status_of(self.i) is None:
            yolo_save(self.txt, [], self.W, self.H, self.args.class_id)

    # ---- rendering (OpenCV compose -> PIL -> Tk) ----
    def render(self):
        if self.img is None:
            return
        W, H, base, dispW, dispH = self.W, self.H, self.base, self.dispW, self.dispH
        eff = base * self.zoom
        vw, vh = dispW / eff, dispH / eff
        self.cx = W / 2 if vw >= W else min(max(self.cx, vw / 2), W - vw / 2)
        self.cy = H / 2 if vh >= H else min(max(self.cy, vh / 2), H - vh / 2)
        w0 = max(1, min(int(round(vw)), W)); h0 = max(1, min(int(round(vh)), H))
        x0 = max(0, min(int(round(self.cx - vw / 2)), W - w0))
        y0 = max(0, min(int(round(self.cy - vh / 2)), H - h0))
        self.vx0, self.vy0, self.vw0, self.vh0 = float(x0), float(y0), float(w0), float(h0)

        canvas = np.zeros((dispH + BTN_H, dispW, 3), np.uint8)
        canvas[:dispH] = cv2.resize(self.img[y0:y0 + h0, x0:x0 + w0], (dispW, dispH))
        if self.show_boxes and (self.boxes or self.action == "new"):
            region = canvas[:dispH]
            overlay = region.copy()                            # draw edges here, then alpha-blend into the image
            for j, (x1, y1, x2, y2) in enumerate(self.boxes):
                col = (0, 255, 255) if j == self.sel else (0, 255, 0)
                dx1, dy1 = self.i2d(x1, y1); dx2, dy2 = self.i2d(x2, y2)
                cv2.rectangle(overlay, (int(dx1), int(dy1)), (int(dx2), int(dy2)), col, 2)
                if j == self.sel and self.mode == "edit":
                    for (cxp, cyp) in _corners((x1, y1, x2, y2)).values():
                        ex, ey = self.i2d(cxp, cyp)
                        cv2.rectangle(overlay, (int(ex) - 4, int(ey) - 4), (int(ex) + 4, int(ey) + 4),
                                      (0, 165, 255), -1)
            if self.action == "new" and self.p0 and self.p1:
                dx1, dy1 = self.i2d(*self.p0); dx2, dy2 = self.i2d(*self.p1)
                cv2.rectangle(overlay, (int(dx1), int(dy1)), (int(dx2), int(dy2)), (0, 200, 255), 1)
            canvas[:dispH] = cv2.addWeighted(overlay, BOX_ALPHA, region, 1.0 - BOX_ALPHA, 0.0)
        st = self.status_of(self.i)
        for xb0, xb1, lab, key in self.btn_rects(dispW):
            active = ((self.mode == "draw" and lab == "Draw") or (self.mode == "edit" and lab == "Edit")
                      or (lab == "Skip" and st == "skip") or (lab == "Huddle" and st == "huddle"))
            cv2.rectangle(canvas, (xb0, dispH), (xb1, dispH + BTN_H),
                          (90, 90, 90) if active else (45, 45, 45), -1)
            cv2.rectangle(canvas, (xb0, dispH), (xb1, dispH + BTN_H), (20, 20, 20), 1)
            cv2.putText(canvas, lab, (xb0 + 6, dispH + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 255), 1)
        cv2.putText(canvas, f"[{self.i+1}/{len(self.imgs)}] {self.path.name}  boxes:{len(self.boxes)}"
                    f"{'' if self.show_boxes else ' (HIDDEN)'}  mode:{self.mode.upper()}  "
                    f"zoom:{self.zoom:.1f}x   s=skip  g=huddle  b=hide/show",
                    (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1)
        if st and not self.boxes:
            lab = "SKIPPED (s) - excluded from training" if st == "skip" \
                else "HUDDLE (g) - deferred, excluded from training"
            col = (60, 60, 255) if st == "skip" else (0, 200, 255)
            (tw, th), _ = cv2.getTextSize(lab, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            bx = max(0, (dispW - tw) // 2 - 8)
            cv2.rectangle(canvas, (bx, 30), (bx + tw + 16, 60), (0, 0, 0), -1)
            cv2.rectangle(canvas, (bx, 30), (bx + tw + 16, 60), col, 2)
            cv2.putText(canvas, lab, (bx + 8, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.label.config(image=self.photo)

    # ---- mouse: LEFT = draw/edit only ----
    def on_lclick(self, e):
        x, y = e.x, e.y
        if y >= self.dispH:                                    # button bar
            for x0, x1, lab, key in self.btn_rects(self.dispW):
                if x0 <= x < x1:
                    self.key_action(key)
                    return
            return
        ix, iy = self.d2i(x, y)
        if self.mode == "draw":
            self.action = "new"; self.p0 = (ix, iy); self.p1 = (ix, iy); self.render(); return
        if self.sel >= 0:
            for cn, (cxp, cyp) in _corners(self.boxes[self.sel]).items():
                dxp, dyp = self.i2d(cxp, cyp)
                if abs(dxp - x) <= HANDLE and abs(dyp - y) <= HANDLE:
                    self.action = "resize"; self.corner = cn; return
        hit = -1
        for k in range(len(self.boxes) - 1, -1, -1):
            x1, y1, x2, y2 = self.boxes[k]
            if min(x1, x2) <= ix <= max(x1, x2) and min(y1, y2) <= iy <= max(y1, y2):
                hit = k; break
        self.sel = hit
        if hit >= 0:
            bx1, by1, _, _ = self.boxes[hit]
            self.action = "move"; self.grab = (ix - bx1, iy - by1)
        self.render()

    def on_ldrag(self, e):
        if self.action not in ("new", "move", "resize"):
            return
        ix, iy = self.d2i(e.x, e.y)
        if self.action == "new":
            self.p1 = (ix, iy)
        elif self.action == "resize" and self.sel >= 0:
            b = self.boxes[self.sel]
            b[0 if "l" in self.corner else 2] = ix
            b[1 if "t" in self.corner else 3] = iy
        elif self.action == "move" and self.sel >= 0:
            b = self.boxes[self.sel]
            w, h = b[2] - b[0], b[3] - b[1]; gx, gy = self.grab
            b[0], b[1] = ix - gx, iy - gy; b[2], b[3] = b[0] + w, b[1] + h
        self.render()

    def on_lrelease(self, e):
        if self.action == "new" and self.p0 and self.p1:
            x1, y1 = self.p0; x2, y2 = self.p1
            if abs(x2 - x1) >= self.args.min_box and abs(y2 - y1) >= self.args.min_box:
                self.boxes.append([x1, y1, x2, y2]); self.sel = len(self.boxes) - 1
        if self.action == "resize" and self.sel >= 0:
            b = self.boxes[self.sel]
            b[0], b[2] = sorted((b[0], b[2])); b[1], b[3] = sorted((b[1], b[3]))
        self.action = None; self.corner = None; self.p0 = self.p1 = None
        self.render()

    # ---- mouse: RIGHT = pan ----
    def on_rpress(self, e):
        self.action = "pan"; self.pan_last = (e.x, e.y)

    def on_rdrag(self, e):
        if self.action == "pan" and self.pan_last is not None:
            eff = self.base * self.zoom
            self.cx -= (e.x - self.pan_last[0]) / eff
            self.cy -= (e.y - self.pan_last[1]) / eff
            self.pan_last = (e.x, e.y)
            self.render()

    def on_rrelease(self, e):
        if self.action == "pan":
            self.action = None; self.pan_last = None

    def on_wheel(self, e):
        if self.action in ("new", "move", "resize", "pan"):
            return
        x = min(max(e.x, 0), self.dispW); y = min(max(e.y, 0), self.dispH)
        self.zoom_at(x, y, 1.25 if e.delta > 0 else 0.8)
        self.render()

    # ---- keyboard ----
    def on_key(self, e):
        if e.char == " ":
            self.key_action("n")
        elif e.char:
            self.key_action(e.char.lower())

    def key_action(self, cmd):
        if cmd == "w":
            self.mode = "draw"; self.sel = -1
        elif cmd == "e":
            self.mode = "edit"
        elif cmd == "d":
            if self.mode == "edit" and self.sel >= 0:
                self.boxes.pop(self.sel); self.sel = -1
            elif self.boxes:
                self.boxes.pop(); self.sel = -1
        elif cmd == "c":
            self.boxes.clear(); self.sel = -1
        elif cmd == "b":
            self.show_boxes = not self.show_boxes
            print(f"  boxes {'shown' if self.show_boxes else 'HIDDEN (peek)'}")
        elif cmd in ("+", "="):
            self.zoom_at(self.dispW // 2, self.dispH // 2, 1.25)
        elif cmd == "-":
            self.zoom_at(self.dispW // 2, self.dispH // 2, 0.8)
        elif cmd in ("0", "f"):
            self.zoom = 1.0; self.cx = self.W / 2; self.cy = self.H / 2
        elif cmd in ("i", "j", "k", "l"):
            eff = self.base * self.zoom; vw, vh = self.dispW / eff, self.dispH / eff
            self.cx += (-vw * 0.25 if cmd == "j" else vw * 0.25 if cmd == "l" else 0)
            self.cy += (-vh * 0.25 if cmd == "i" else vh * 0.25 if cmd == "k" else 0)
        elif cmd == "s":
            self.toggle_status(self.i, "skip"); self.boxes = []; self.sel = -1
            print(f"  {self.path.name}: SKIP -> {self.status_of(self.i) or 'cleared'}")
        elif cmd == "g":
            self.toggle_status(self.i, "huddle"); self.boxes = []; self.sel = -1
            print(f"  {self.path.name}: HUDDLE -> {self.status_of(self.i) or 'cleared'}")
        elif cmd in ("n", " "):
            self.do_next(); return
        elif cmd == "p":
            self.do_prev(); return
        elif cmd == "q":
            self.do_quit(); return
        self.render()

    # ---- navigation ----
    def do_next(self):
        self.save_current()
        self.i = (self.i + 1) if (self.args.all or self.was_decided) else self.next_undecided(self.i)
        if self.i >= len(self.imgs):
            self.finish(); return
        self.load_frame(self.i); self.render()

    def do_prev(self):
        self.save_current()
        self.i = max(0, self.i - 1)
        self.load_frame(self.i); self.render()

    def _counts(self):
        nl = len(list(self.lbl_dir.glob("*.txt"))) if self.lbl_dir.exists() else 0
        ns = len(list(self.status_dir.glob("*.skip"))) if self.status_dir.exists() else 0
        nh = len(list(self.status_dir.glob("*.huddle"))) if self.status_dir.exists() else 0
        return nl, ns, nh

    def do_quit(self):
        self.save_current()
        nl, ns, nh = self._counts()
        print(f"saved through {self.path.name}. {nl} labels; {ns} skip + {nh} huddle markers.")
        self.root.destroy()

    def finish(self):
        nl, ns, nh = self._counts()
        print(f"done all {len(self.imgs)} frames. {nl} labels; {ns} skip + {nh} huddle markers "
              f"(skip/huddle excluded from training).")
        self.root.destroy()


def main() -> None:
    ap = argparse.ArgumentParser(description="Tkinter YOLO-format box labeler (draw + edit + skip/huddle).")
    here = Path(__file__).resolve().parent
    ap.add_argument("--dir", default=str(here / "dataset" / "rat" / "images"))
    ap.add_argument("--labels", default=None, help="labels dir (default: sibling 'labels')")
    ap.add_argument("--class-id", type=int, default=0)
    ap.add_argument("--min-box", type=int, default=4, help="ignore boxes smaller than this (px)")
    ap.add_argument("--all", action="store_true",
                    help="review mode: start at frame 1 and Next visits every frame (default: start at "
                         "first undecided, Next jumps to next undecided, Prev steps back through all)")
    args = ap.parse_args()

    img_dir = Path(args.dir)
    lbl_dir = Path(args.labels) if args.labels else img_dir.parent / "labels"
    status_dir = img_dir.parent / "status"
    exts = (".png", ".jpg", ".jpeg")

    # one-time migration of any old move-based buckets -> in-place status markers (idempotent)
    for old_name, st in (("excluded", "skip"), ("pending_huddle", "huddle")):
        old_dir = img_dir.parent / old_name
        if old_dir.is_dir():
            moved = 0
            for f in sorted(old_dir.glob("*")):
                if f.suffix.lower() in exts:
                    dest = img_dir / f.name
                    if not dest.exists():
                        try:
                            shutil.move(str(f), str(dest))
                        except Exception as e:
                            print(f"migrate skip {f.name}: {e}"); continue
                    status_dir.mkdir(parents=True, exist_ok=True)
                    (status_dir / f"{dest.stem}.{st}").write_text("")
                    moved += 1
            if moved:
                print(f"migrated {moved} '{old_name}' frame(s) back into images/ as '{st}' markers")

    imgs = sorted([p for p in img_dir.glob("*") if p.suffix.lower() in exts])
    if not imgs:
        raise SystemExit(f"no images in {img_dir}")

    root = tk.Tk()
    Labeler(root, imgs, img_dir, lbl_dir, status_dir, args)
    root.mainloop()


if __name__ == "__main__":
    main()

# Manual Labeling Protocol — Shelter Rat Detector (YOLO)

**One-sentence principle:** *Label what is visually localizable, not what we biologically know is present.*
A box means **"a visually localizable rat is present here."** WISER/behavioral context can tell us a rat
exists, but only **pixels** create boxes.

## Dataset convention (do NOT change in this batch)
- **Single class: `rat`** (class id `0`).
- **One tight box per visually separable individual rat.**
- Sleeping piles have historically been labeled as **best-estimate individual boxes** when individuals
  can be roughly separated (overlapping boxes are fine — up to ~0.5 IoU survives YOLO's default NMS).
- **Do not** introduce a `huddle` class or a one-big-box convention here. That decision is deferred until
  we count how many *indivisible* piles occur; mixing conventions mid-dataset is the one hard "don't."
- Coband color/pattern (rat identity) is **irrelevant** for these labels — the detector only learns `rat`.

## The four actions (`label_frames.py`)
| Action | Key | Button | Writes | Trained on? |
|---|---|---|---|---|
| **Draw box** | `w` + left-drag | Draw | box(es) → `labels/<stem>.txt` | ✅ positive |
| **Empty** (Next with no boxes) | `n` / Space | Next > | empty `labels/<stem>.txt` | ✅ negative |
| **Skip** (unusable) | `s` | Skip | `status/<stem>.skip` (no label) | ❌ excluded |
| **Huddle** (defer pile) | `g` | Huddle | `status/<stem>.huddle` (no label) | ❌ deferred |

- Skip/Huddle are **in-place markers** — the image stays in `images/`; nothing is moved.
- Markers **toggle**: press the same key/button again to un-mark; the button **lights up** when active and
  the console prints e.g. `CH05_....png: HUDDLE -> huddle`. **Drawing a box overrides a marker** (labels it).
- A frame is **"decided"** if it has a label OR a marker. `Next` jumps to the next *undecided* frame;
  `Prev` steps back through **all** frames so you can review/fix anything. `--all` visits every frame.
- Boxes smaller than **4 px** (`--min-box`) are ignored — don't chase sub-pixel specks.
- Box edges render **semi-transparent** so an existing box doesn't hide a neighbouring rat's boundary;
  press **`b`** to hide/show all boxes (peek at the raw pixels — nothing is deleted).

## Core rule — all-or-nothing per frame
**Never partially label a frame.** If a frame contains *any* visible rat-like region you cannot box,
do **not** label the frame — press **Skip** (or **Huddle** for a pile). A partial label teaches YOLO that
the unmarked rat pixels are **background**, which poisons the detector.

## Box mechanics
- Box the **visible extent** — tight to what you can see. **Do not** extend the box into an occluded or
  imagined body.
- Box the rat **through wire mesh / glass** — a thin see-through layer is *not* occlusion; label normally.
- Overlapping boxes on touching rats are expected and fine.

## Detailed rules
1. **Clear individual rat** — one tight box on the visible body. Inside or outside the shelter does not
   matter; the class is always `rat`.
2. **Partially occluded rat** (wall / object / frame edge) — if enough body is visible to identify a
   *distinct individual* and place its extent, box the visible part. If only a tiny fragment is visible and
   you can't confidently assign it to an individual, don't box that fragment — and if that leaves an
   unaccounted rat-like region in the frame, **Skip** the whole frame (core rule).
3. **Rats touching / loosely huddled** — if individuals are visually separable, box **each** rat
   (overlap OK; best-estimate individuals are fine, matching the prior convention).
4. **Tight fused huddle** (a clearly visible pile you **cannot** split into individuals) — press
   **`g` / Huddle**. Do **not** draw one big box, and do **not** guess individuals the boundaries don't
   support. These are deferred for the later huddle-class decision.
5. **Heavy fog / wet glass / glare / condensation** obscuring rat boundaries — press **`s` / Skip**.
   Don't guess boxes because we "know" rats are inside, and don't save as an empty negative.
6. **Mild / partial fog** — if rats are still localizable, label them normally. If **some** rats are
   localizable but **others** are visible only as unboxable foggy blobs, **Skip the whole frame** — do not
   label only the easy rats and leave rat-like blobs unlabeled.
7. **Inside vs outside shelter** — the detector is **zone-blind**; never encode inside/outside in the label
   (zones are assigned downstream from box coordinates). A clear **outside** rat is boxed. But if the same
   frame *also* has unboxable rats in a foggy **interior**, **Skip** the whole frame. If the interior is
   foggy yet shows **no** visible rat-like blob, label the clear outside rat and continue.
8. **Empty negatives** — save an empty frame only when the **visible area is clearly rat-free** (no rat and
   no ambiguous rat-like region). This is a valid **visual** negative even if a rat is biologically present
   but **fully hidden** in the wall-edge blind zone (0 visible pixels can't poison a negative). If any dark
   region *could* be a tucked-in rat, **Skip** instead. Never save fog/glare/uncertain frames as empty.
9. **Known-present from WISER/context** — this does **not** create a box and does **not** by itself force a
   skip. Zero visible rat pixels + clear view → **visual negative** (Next). A visible-but-unboxable rat-like
   region → **Skip**.
10. **When uncertain → Skip or Huddle, never guess.** Missing one hard frame is far better than teaching the
    detector a wrong label. **Consistency with the existing labeled set is the top priority.**

## Visual negative ≠ biological empty (why we can label a hidden-rat frame "empty")
An empty label means **"no rat is *visible*,"** not "no rat is *present*." The CH05/CH06 shelter cams are
top-down, so a band along each interior wall is occluded — a shelter can be **biologically occupied while
visually empty**. Such a frame is a **correct visual negative** for the detector. Recovering the true
headcount (wall-edge hiders) is a **downstream** job (prior-movement / doorway inference in
`shelter_sleep.py`) — **not** something you fix by inventing boxes while labeling.

## Quick decision table
| You see… | Do |
|---|---|
| Clear single rat | Draw one box |
| Multiple separable rats | One box per rat |
| Loose huddle, individuals separable | Best-estimate individual boxes |
| Fused huddle, individuals **not** separable | **`g` / Huddle** |
| Heavy fog / glare / wet glass, rats not localizable | **`s` / Skip** |
| Clear outside rat + foggy interior, **no** visible rat blob inside | Box the outside rat, continue |
| Clear outside rat + foggy interior **has** unboxable rats | **`s` / Skip** whole frame |
| A visible but unboxable rat-like blob anywhere | **`s` / Skip** |
| Visible area clearly rat-free (even if WISER says occupied) | Empty negative (**Next**) |
| Uncertain | Skip rather than guess |

---
*Tool: `label_frames.py`. Convention audited against the existing ~940-frame set (single class `rat`).
Huddle-class decision is deferred — see `change_log/2026-07-01-glass-degradation-zones.md`.*

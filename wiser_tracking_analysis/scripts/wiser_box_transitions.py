"""WISER box entry/exit events per tag (label source 1 for in-box video identity).

Reads a WISER *snapshot* only (never the live DB), classifies every fix as inside/outside each
house rectangle from the analysis repo's wiser_rois.json, smooths the occupancy over a short
window to kill boundary jitter, and emits ENTER / EXIT events with wall-clock times.

usage: python wiser_box_transitions.py <snapshot.sqlite> <YYYY-MM-DD HH:MM> <YYYY-MM-DD HH:MM> [--csv out.csv]
"""
import sys, json, math, sqlite3, csv, datetime as dt
from collections import defaultdict

ROIS = r"C:\Users\Cornell\Documents\GitHub\Field_2026_Social\wiser_tracking_analysis\configs\wiser_rois.json"
IDENT = r"C:\Users\Cornell\Documents\GitHub\Field_2026_Social\wiser_tracking_analysis\configs\rat_identities_cohort3.csv"
SMOOTH_S = 6.0          # majority vote window (s) for inside/outside
MIN_DWELL_S = 20.0      # ignore excursions shorter than this (fixes flickering at the door)
MARGIN_IN = 15.0        # dilate each house rect by this much: a pile against the wall sits ON the ROI edge

def load_houses():
    j = json.load(open(ROIS))
    out = []
    for r in j["rois"]:
        if r["type"] == "refuge" and r["shape"] == "rect":
            out.append((r["name"], r["x"], r["y"], r["width_in"], r["height_in"], math.radians(r.get("orientation_deg", 0))))
    return out

def inside_rect(x, y, cx, cy, w, h, th):
    dx, dy = x - cx, y - cy
    c, s = math.cos(-th), math.sin(-th)
    xr, yr = dx * c - dy * s, dx * s + dy * c
    return abs(xr) <= w / 2 + MARGIN_IN and abs(yr) <= h / 2 + MARGIN_IN

def load_ident():
    m = {}
    for row in csv.DictReader(open(IDENT, encoding="utf-8-sig")):
        m[int(row["shortid"])] = row["name"]
    return m

def main():
    snap, t0s, t1s = sys.argv[1], sys.argv[2], sys.argv[3]
    out_csv = sys.argv[sys.argv.index("--csv") + 1] if "--csv" in sys.argv else None
    global MARGIN_IN
    if "--margin" in sys.argv: MARGIN_IN = float(sys.argv[sys.argv.index("--margin") + 1])
    print(f"house rects dilated by {MARGIN_IN} in; smoothing {SMOOTH_S} s; min dwell {MIN_DWELL_S} s")
    t0 = dt.datetime.strptime(t0s, "%Y-%m-%d %H:%M"); t1 = dt.datetime.strptime(t1s, "%Y-%m-%d %H:%M")
    houses = load_houses(); ident = load_ident()
    con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    lo, hi = int(t0.timestamp() * 1000), int(t1.timestamp() * 1000)
    rows = con.execute("select shortid, timestamp, location_x, location_y from reports where timestamp between ? and ? order by shortid, timestamp", (lo, hi)).fetchall()
    print(f"{len(rows)} fixes {t0s} -> {t1s} from {snap}")
    by = defaultdict(list)
    for sid, ts, x, y in rows:
        if sid in ident and x is not None and y is not None:
            by[sid].append((ts / 1000.0, x, y))
    events = []
    for sid, fixes in sorted(by.items()):
        name = ident[sid]
        for hname, cx, cy, w, h, th in houses:
            # raw inside flags, then majority smoothing over SMOOTH_S
            ts = [f[0] for f in fixes]; ins = [1 if inside_rect(f[1], f[2], cx, cy, w, h, th) else 0 for f in fixes]
            sm = []; j0 = 0
            for i, t in enumerate(ts):
                while ts[j0] < t - SMOOTH_S: j0 += 1
                win = ins[j0:i + 1]; sm.append(1 if sum(win) * 2 >= len(win) else 0)
            # segments of constant state
            segs = []; st = sm[0]; s0 = ts[0]
            for t, v in zip(ts, sm):
                if v != st: segs.append((st, s0, t)); st = v; s0 = t
            segs.append((st, s0, ts[-1]))
            # drop short excursions by merging them into neighbours
            merged = []
            for seg in segs:
                if merged and seg[2] - seg[1] < MIN_DWELL_S and merged[-1][0] != seg[0]:
                    # too short: absorb into previous state
                    merged[-1] = (merged[-1][0], merged[-1][1], seg[2]); continue
                if merged and merged[-1][0] == seg[0]:
                    merged[-1] = (merged[-1][0], merged[-1][1], seg[2]); continue
                merged.append(seg)
            for a, b in zip(merged, merged[1:]):
                kind = "ENTER" if b[0] == 1 else "EXIT"
                events.append((b[1], name, sid, hname, kind))
            frac = sum(ins) / len(ins) if ins else 0
            print(f"  {name} ({sid}) {hname}: {len(fixes)} fixes, inside {frac*100:.0f}% of the time, {len(merged)-1} transitions")
    events.sort()
    print("\nevent               rat   tag    house    kind")
    for t, name, sid, hname, kind in events:
        print(f"{dt.datetime.fromtimestamp(t):%Y-%m-%d %H:%M:%S}  {name}  {sid}  {hname}  {kind}")
    if out_csv:
        with open(out_csv, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["time_local", "rat", "shortid", "house", "event"])
            for t, name, sid, hname, kind in events:
                w.writerow([dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"), name, sid, hname, kind])
        print("wrote", out_csv)

if __name__ == "__main__":
    main()

"""
Render a top-down reference map of the field from configs/field_layout.json:
pole indices (L0..R4, Bm, Tm), shelters, camera positions/aim, origin + axes.
Save to configs/field_layout_map.png so you know which physical pole is "L3" etc.

    python make_layout_map.py
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import field_coords as fc

OUT = fc.CONFIG_DIR / "field_layout_map.png"


def _aim_vector(aim: str):
    a = (aim or "").lower().strip()
    if a.startswith("deg"):
        try:
            th = math.radians(float(a.split()[1]))
            return math.cos(th), math.sin(th)
        except (IndexError, ValueError):
            return 0.0, 0.0
    dx = dy = 0.0
    if "+x" in a: dx = 1
    if "-x" in a: dx = -1
    if "+y" in a: dy = 1
    if "-y" in a: dy = -1
    return dx, dy


def main() -> None:
    L = fc.load_layout()
    W, Ln = fc.FIELD_X_CM, fc.FIELD_Y_CM           # x = 40 ft length, y = 20 ft width

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.add_patch(Rectangle((0, 0), W, Ln, fill=False, lw=2, ec="black"))

    # poles
    for name, (x, y) in L.get("poles", {}).items():
        ax.plot(x, y, "o", ms=10, color="#1f77b4")
        ax.annotate(name, (x, y), textcoords="offset points", xytext=(8, 6),
                    fontsize=11, fontweight="bold", color="#1f77b4")

    # temporary calibration markers (ground)
    for mname, (x, y) in L.get("markers", {}).items():
        ax.plot(x, y, "^", ms=8, color="#2ca02c", mec="black", mew=0.5)
        ax.annotate(mname, (x, y), textcoords="offset points", xytext=(6, 4),
                    fontsize=8, fontweight="bold", color="#2ca02c")

    # shelters
    for sname, sh in L.get("shelters", {}).items():
        if sname.startswith("_") or not sh.get("center_cm"):
            continue
        cx, cy = sh["center_cm"]; lw_, sh_ = sh["size_cm"]
        th = math.radians(sh.get("orientation_deg", 0))
        ax.add_patch(Rectangle((cx - lw_ / 2, cy - sh_ / 2), lw_, sh_,
                               angle=math.degrees(th), rotation_point="center",
                               fill=True, fc="#ffcc99", ec="#cc6600", alpha=0.7))
        ax.annotate(f"{sname} shelter", (cx, cy), ha="center", va="center", fontsize=8)
        for key, (xx, yy) in fc.shelter_corners(sh).items():     # label corners by +/-x,+/-y
            ax.plot(xx, yy, ".", color="#cc6600", ms=6)
            ax.annotate(fc.corner_label(key), (xx, yy), fontsize=6, color="#aa4400",
                        ha="center", va="center")

    # cameras
    for ch, m in L.get("camera_mounts", {}).items():
        if ch.startswith("_"):
            continue
        if m.get("pos_cm"):
            x, y = m["pos_cm"]
        elif m.get("shelter") and L["shelters"].get(m["shelter"], {}).get("center_cm"):
            x, y = L["shelters"][m["shelter"]]["center_cm"]
        else:
            continue
        ax.plot(x, y, "s", ms=9, color="#d62728")
        ax.annotate(f"{ch}\n({m.get('mapping','?')})", (x, y), textcoords="offset points",
                    xytext=(6, -18), fontsize=9, color="#d62728")
        dx, dy = _aim_vector(m.get("aim", ""))
        if dx or dy:
            ax.arrow(x, y, dx * 90, dy * 90, head_width=18, head_length=18,
                     fc="#d62728", ec="#d62728", length_includes_head=True)

    # origin + axes
    ax.plot(0, 0, "k*", ms=16)
    ax.annotate("origin (0,0)\n= pole A0", (0, 0), textcoords="offset points",
                xytext=(10, -30), fontsize=9)
    # +x / +y axis arrows (the canonical reference directions)
    ax.annotate("", xy=(260, 0), xytext=(0, 0), arrowprops=dict(arrowstyle="-|>", color="green", lw=2.5))
    ax.annotate("+x (length)", (265, 8), color="green", fontsize=10, va="center")
    ax.annotate("", xy=(0, 140), xytext=(0, 0), arrowprops=dict(arrowstyle="-|>", color="green", lw=2.5))
    ax.annotate("+y (width)", (8, 150), color="green", fontsize=10, ha="left")
    ax.set_xlabel("x  (cm along the 40 ft length)")
    ax.set_ylabel("y  (cm across the 20 ft width)")
    ax.set_title("Field layout — pole index reference\n(40 ft x 20 ft = 1219.2 x 609.6 cm)")
    ax.set_xlim(-90, W + 90); ax.set_ylim(-90, Ln + 90)
    ax.set_aspect("equal"); ax.grid(True, ls=":", alpha=0.5)
    ax.set_xticks([i * fc.FT_TO_CM for i in range(0, 41, 5)])
    ax.set_xticklabels([f"{i*fc.FT_TO_CM:.0f}\n({i}ft)" for i in range(0, 41, 5)], fontsize=8)
    ax.set_yticks([i * fc.FT_TO_CM for i in range(0, 21, 5)])
    ax.set_yticklabels([f"{i*fc.FT_TO_CM:.0f} ({i}ft)" for i in range(0, 21, 5)], fontsize=8)

    fig.tight_layout()
    fig.savefig(OUT, dpi=130)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

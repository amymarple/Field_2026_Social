"""
Ground-truth validation for the view-quality-aware shelter pipeline (CH05/CH06).

Samples random moments across the day's CLOSED footage, asks YOU for the truth (rat count
inside + still/moving), and compares against the pipeline the SAME way shelter_sleep runs it:
zone-inside detector count + glass-noise-resistant robust motion + view_quality + the conservative
state fusion. The detector's answer is HIDDEN while you judge (no biasing).

    python validate_shelter.py --date 2026-06-30 --n 60

Per sample a short multi-frame CLIP plays on a loop (real motion, not a 2-frame blink) so you can
judge movement, and you also label the true VIEW condition so fog/glass detection is validated too:
    digit 0-9 = rat count inside     m then digit = how many are MOVING   l = none moving   h = all moving
    c = view clear     f = view fogged/degraded (rain/condensation/glare)     u = view unusable
    n / SPACE = next   x = skip    b = back    q = finish + report
    (keys are case-insensitive; n needs count + view, plus #moving when occupied and the view is not unusable)

Writes outputs/validation_<date>.csv and prints, STRATIFIED BY view_quality: the VIEW/FOG
confusion (your view vs the pipeline's, incl. the unsafe "called clear but was fogged" count),
count MAE/bias, presence agreement, still/moving agreement on clear bins, and the SAFETY CHECK that
degraded/unusable (rain/fog) samples are never scored `occupied_high_motion`.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

import shelter_sleep as ss
import view_quality as vq

HERE = Path(__file__).resolve().parent


def build_samples(channels, date, hours, n, gap, n_burst, threads, W_by_ch, tmp,
                  judge_frames, judge_window):
    """Pick n random (channel, file, t) points across closed files. For each, grab the pipeline's
    scoring burst (n_burst frames, `gap` s apart -- exactly what shelter_sleep scores) AND a longer
    human-review CLIP (judge_frames over judge_window s) so a person can actually see motion."""
    pool = []
    for ch in channels:
        for f in ss.resolve_day_files(ch, date, hours):
            dur = ss.scan.video_duration(f)
            if dur:
                pool.append((ch, f, dur))
    if not pool:
        raise SystemExit("no closed files to sample.")
    rng = random.Random(0)
    clip_gap = judge_window / max(1, judge_frames - 1)
    samples = []
    for i in range(n):
        ch, f, dur = rng.choice(pool)
        t = rng.uniform(2, max(3, dur - 2))
        fr = ss.sample_burst(f, t, W_by_ch[ch], tmp, i, threads, gap, n_burst)   # pipeline burst
        if not fr:
            continue
        # richer clip for the human, at the SAME t; distinct index so filenames never collide
        clip = ss.sample_burst(f, t, W_by_ch[ch], tmp, 90000 + i, threads, clip_gap, judge_frames)
        samples.append({"i": i, "channel": ch, "file": f.name, "t": round(t, 1),
                        "burst": [str(p) for p in fr], "f0": fr[0],
                        "f1": fr[1] if len(fr) > 1 else fr[0],
                        "clip": [str(p) for p in (clip or fr)]})
    return samples


def _inside_mask(channel, shape, zones_cache, mask_cache):
    h, w = shape[:2]
    key = (channel, h, w)
    if key not in mask_cache:
        z = zones_cache.setdefault(channel, vq.load_zones(channel))
        mask_cache[key] = ss.zone_masks(z, w, h)["inside"]
    return mask_cache[key]


def score_pipeline(samples, model, args, cfg):
    """Run the SAME pipeline shelter_sleep uses: inside count + robust motion + view + state."""
    zones_cache, mask_cache = {}, {}
    cfg_by = {ch: vq.load_config(args.config, ch)               # per-channel (fog thresholds differ)
              for ch in {s["channel"] for s in samples}}
    for ci in range(0, len(samples), args.batch):
        chunk = samples[ci:ci + args.batch]
        res = model.predict([s["f0"] for s in chunk], conf=args.conf, classes=[0],
                            imgsz=args.imgsz, device=args.device, verbose=False)
        for s, r in zip(chunk, res):
            s["_centers"] = (np.empty((0, 2)) if r.boxes is None or not len(r.boxes)
                             else np.c_[(r.boxes.xyxy.cpu().numpy()[:, 0] + r.boxes.xyxy.cpu().numpy()[:, 2]) / 2,
                                        (r.boxes.xyxy.cpu().numpy()[:, 1] + r.boxes.xyxy.cpu().numpy()[:, 3]) / 2])
    for s in samples:
        grays = [ss.read_gray(p) for p in s["burst"]]
        grays = [g for g in grays if g is not None]
        mask = _inside_mask(s["channel"], grays[0].shape, zones_cache, mask_cache)
        pred_inside = int(sum(1 for cx, cy in s["_centers"]
                              if 0 <= int(cy) < mask.shape[0] and 0 <= int(cx) < mask.shape[1]
                              and mask[int(cy), int(cx)]))
        cfg_ch = cfg_by[s["channel"]]
        view, _ = vq.view_quality(grays[0], mask, cfg_ch)
        motion, _ = vq.robust_inside_motion(grays, mask, cfg_ch)
        present = view in ("clear", "degraded") and (pred_inside > 0 or motion > cfg_ch["present_motion_floor"])
        _, _, state = ss._fuse(view, pred_inside, present, motion, cfg_ch)
        s.update({"view_quality_inside": view, "pred_count": pred_inside,
                  "inside_motion_score": round(motion, 3), "pred_state": state})


VIEW_KEYS = {ord("c"): "clear", ord("f"): "degraded", ord("u"): "unusable"}


def _ready(s):
    """`n` gate: always need count + view; a #moving call is only required when the shelter is
    occupied AND the view is judgeable (an empty or unusable frame has no meaningful motion)."""
    if s["gt_count"] is None or s["gt_view"] is None:
        return False
    if s["gt_count"] > 0 and s["gt_view"] != "unusable":
        return s.get("gt_n_moving") is not None
    return True


def _set_moving(s, val):
    """Record how many animals are moving; derive the binary gt_motion the report compares against
    (still = nobody moving, moving = >=1 moving). Clamped to the total count."""
    cap = s["gt_count"] if s["gt_count"] is not None else 9
    val = max(0, min(int(val), cap))
    s["gt_n_moving"] = val
    s["gt_motion"] = "still" if val == 0 else "moving"


def collect_truth(samples, hold=3):
    """Play each sample's clip on a loop (motion visible); collect true inside count + still/moving
    + true view condition. Detector/pipeline answers stay HIDDEN so labels aren't biased."""
    win = "validate (hidden GT)"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE | getattr(cv2, "WINDOW_GUI_NORMAL", 0))
    cv2.moveWindow(win, 0, 0)
    i = 0
    while 0 <= i < len(samples):
        s = samples[i]
        clip = [cv2.imread(p) for p in s.get("clip") or [s["f0"]]]
        clip = [c for c in clip if c is not None] or [cv2.imread(str(s["f0"]))]
        H, Wd = clip[0].shape[:2]
        scale = min(1.0, 1100 / Wd, 760 / H)
        for key in ("gt_count", "gt_motion", "gt_view", "gt_n_moving"):
            s.setdefault(key, None)
        tick = 0
        await_moving = False           # set by 'm' -> the next digit is the moving-count, not the total
        while True:
            frame = clip[(tick // hold) % len(clip)]                 # loop the clip -> real motion
            d = cv2.resize(frame, (int(Wd * scale), int(H * scale)))
            mv = "?" if await_moving else s["gt_n_moving"]
            bar1 = (f"[{i+1}/{len(samples)}] {s['channel']}  count={s['gt_count']}  moving={mv}  "
                    f"view={s['gt_view']}   frame {(tick//hold)%len(clip)+1}/{len(clip)}")
            bar2 = "0-9=count  m+digit=#moving  l=none h=all  c=clear f=fog u=unusable  n=next x=skip b=back q=done"
            cv2.rectangle(d, (0, 0), (d.shape[1], 44), (0, 0, 0), -1)
            cv2.putText(d, bar1, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)
            cv2.putText(d, bar2, (6, 37), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 220, 255), 1)
            hint = "press #moving" if await_moving else (
                None if _ready(s) else
                ("need count" if s["gt_count"] is None else
                 "need view" if s["gt_view"] is None else "need #moving"))
            if hint:
                cv2.putText(d, hint, (d.shape[1] - 150, 17),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 60, 255), 2)
            cv2.imshow(win, d)
            k = cv2.waitKey(30) & 0xFF
            tick += 1
            if k == 255:
                continue
            if 65 <= k <= 90:                                        # normalize: CapsLock/Shift letters
                k += 32
            if ord("0") <= k <= ord("9"):
                if await_moving:
                    _set_moving(s, k - ord("0")); await_moving = False
                else:
                    s["gt_count"] = k - ord("0")
                    if s["gt_n_moving"] is not None:                 # re-clamp moving to the new count
                        _set_moving(s, s["gt_n_moving"])
            elif k == ord("m"):
                await_moving = True
            elif k == ord("l"):                                     # nobody moving (all still)
                _set_moving(s, 0); await_moving = False
            elif k == ord("h"):                                     # all present animals moving
                _set_moving(s, s["gt_count"] if s["gt_count"] is not None else 9); await_moving = False
            elif k in VIEW_KEYS:
                s["gt_view"] = VIEW_KEYS[k]; await_moving = False
            elif k == ord("x"):
                s["gt_count"] = s["gt_motion"] = s["gt_view"] = s["gt_n_moving"] = None; i += 1; break
            elif k == ord("b"):
                i = max(0, i - 1); break
            elif k in (ord("n"), ord(" ")):
                if _ready(s):
                    i += 1; break
            elif k == ord("q"):
                cv2.destroyAllWindows(); return
    cv2.destroyAllWindows()


def report(samples, date, cfg, out_csv):
    # a labeled row now needs count + view; motion is optional (only meaningful when occupied & viewable)
    rows = [s for s in samples if s.get("gt_count") is not None and s.get("gt_view") is not None]
    if not rows:
        print("no labeled samples."); return
    df = pd.DataFrame([{k: s.get(k) for k in ("channel", "file", "t", "view_quality_inside", "pred_count",
                                              "inside_motion_score", "pred_state", "gt_count", "gt_view",
                                              "gt_motion", "gt_n_moving")}
                       for s in rows])
    df.to_csv(out_csv, index=False)
    n = len(df)
    print(f"\n=== validation ({n} labeled samples, {date}) ===")
    vqc = df["view_quality_inside"].value_counts()
    print("pipeline view mix: " + "  ".join(f"{k} {vqc.get(k,0)}" for k in ("clear", "degraded", "unusable")))

    # ---- VIEW / FOG detection: your view label vs the pipeline's view_quality ----
    tiers = ["clear", "degraded", "unusable"]
    agree = float((df["gt_view"] == df["view_quality_inside"]).mean()) * 100
    print(f"\nVIEW/FOG (n={n}): exact 3-way agreement {agree:.0f}%   confusion [rows=you, cols=pipeline]:")
    print("                " + "".join(f"{c[:6]:>9}" for c in tiers))
    for gt in tiers:
        sub = df[df["gt_view"] == gt]
        cells = "".join(f"{int((sub['view_quality_inside'] == pv).sum()):>9}" for pv in tiers)
        print(f"    you {gt[:8]:<8}{cells}   (n={len(sub)})")
    # the safety-critical direction: pipeline called it clear but you saw fog/glass degradation
    gt_bad, pred_bad = df["gt_view"] != "clear", df["view_quality_inside"] != "clear"
    false_clear = int((gt_bad & ~pred_bad).sum())
    tp = int((gt_bad & pred_bad).sum()); fn_ = int((gt_bad & ~pred_bad).sum()); fp = int((~gt_bad & pred_bad).sum())
    recall = tp / (tp + fn_) * 100 if (tp + fn_) else float("nan")
    prec = tp / (tp + fp) * 100 if (tp + fp) else float("nan")
    print(f"    degraded/unusable detection: recall {recall:.0f}%  precision {prec:.0f}%")
    print(f"    UNSAFE 'called clear but you saw fog/degradation' = {false_clear}"
          f"  -> {'PASS (0)' if false_clear == 0 else 'REVIEW: fog threshold may be too lax'}")

    # ---- SAFETY CHECK (the primary bar): degraded/unusable never scored high-motion ----
    bad = df[df["view_quality_inside"] != "clear"]
    n_high_bad = int((bad["pred_state"] == "occupied_high_motion").sum())
    verdict = "PASS" if n_high_bad == 0 else f"FAIL ({n_high_bad} degraded bins scored high-motion!)"
    print(f"SAFETY: degraded/unusable samples scored occupied_high_motion = {n_high_bad}/{len(bad)}  -> {verdict}")

    # ---- count + presence on CLEAR samples (where the detector is trusted) ----
    clear = df[df["view_quality_inside"] == "clear"]
    if len(clear):
        err = clear["pred_count"] - clear["gt_count"]
        print(f"COUNT (clear, n={len(clear)}): MAE={err.abs().mean():.2f}  bias={err.mean():+.2f} "
              f"(undercounts huddles if <0)")
        gp, pp = clear["gt_count"] > 0, clear["pred_count"] > 0
        tp, fp = int((gp & pp).sum()), int((~gp & pp).sum())
        fn, tn = int((gp & ~pp).sum()), int((~gp & ~pp).sum())
        agree = (tp + tn) / len(clear) * 100 if len(clear) else float("nan")
        print(f"PRESENCE (clear): agreement {agree:.0f}%  (TP{tp} FP{fp} FN{fn} TN{tn})")
        # still/moving only where truly occupied AND clear AND you gave a motion label
        occ = clear[(clear["gt_count"] > 0) & clear["gt_motion"].notna()]
        if len(occ):
            gm = occ["gt_motion"].to_numpy()
            cur = np.where(occ["inside_motion_score"].to_numpy() >= cfg["motion_thresh"], "moving", "still")
            print(f"MOTION (clear occupied, n={len(occ)}): agreement at thresh "
                  f"{cfg['motion_thresh']:.2f} = {(cur == gm).mean()*100:.0f}%")
            cand = np.unique(occ["inside_motion_score"].to_numpy())
            best_thr, best = cfg["motion_thresh"], -1.0
            for t in np.append(cand, cand + 1e-3):
                a = (np.where(occ["inside_motion_score"].to_numpy() >= t, "moving", "still") == gm).mean()
                if a > best:
                    best, best_thr = a, float(t)
            print(f"        SUGGESTED motion_thresh {best_thr:.2f} -> {best*100:.0f}%")
    else:
        print("(no clear-view samples to score count/motion)")
    print(f"saved {out_csv}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ground-truth validation for the view-aware shelter pipeline.",
                                 allow_abbrev=False)
    ap.add_argument("--channels", nargs="+", default=["CH05", "CH06"])
    ap.add_argument("--date", required=True)
    ap.add_argument("--hours", type=int, nargs="+")
    ap.add_argument("--n", type=int, default=60, help="random samples to label")
    ap.add_argument("--weights", default=str(ss.DEF_WEIGHTS))
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--n-burst", type=int, default=3, help="frames in the pipeline motion burst (matches shelter_sleep)")
    ap.add_argument("--motion-gap", type=float, default=0.5, help="seconds between pipeline burst frames")
    ap.add_argument("--judge-frames", type=int, default=12, help="frames in the human review clip (playback)")
    ap.add_argument("--judge-window", type=float, default=3.0, help="seconds the review clip spans")
    # batch>1 at imgsz>=960 trips a CUDA illegal-memory-access on the analysis PC's torch build; 1 is safe.
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--ffmpeg-threads", type=int, default=2)
    ap.add_argument("--device", default="0")
    ap.add_argument("--config", default=None, help="view_quality.yaml (default configs/view_quality.yaml)")
    args = ap.parse_args()

    cfg = vq.load_config(args.config)
    from ultralytics import YOLO
    model = YOLO(args.weights)
    W_by_ch = {ch: int((vq.load_zones(ch).get("image_size") or [1280, 960])[0]) for ch in args.channels}

    tmp = HERE / "scratch" / "validate"
    if tmp.exists():
        import shutil; shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    samples = build_samples(args.channels, args.date, args.hours, args.n, args.motion_gap,
                            args.n_burst, args.ffmpeg_threads, W_by_ch, tmp,
                            args.judge_frames, args.judge_window)
    print(f"grabbed {len(samples)} samples; scoring pipeline (detector + robust motion + view)...")
    score_pipeline(samples, model, args, cfg)
    print("Label each (clip loops): 0-9 count, m+digit #moving (l=none h=all), c/f/u view, "
          "n next, x skip, b back, q finish.  Keys are case-insensitive.")
    collect_truth(samples)
    outputs = HERE / "outputs"; outputs.mkdir(exist_ok=True)
    report(samples, args.date, cfg, outputs / f"validation_{args.date}.csv")
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

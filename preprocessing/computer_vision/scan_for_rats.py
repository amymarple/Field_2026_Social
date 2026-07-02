"""
Detector-assisted footage scanner. TWO decoupled modes, separate outputs:

  LABELING HARVEST (default) -- diverse frames to label, NOT an occupancy estimate.
    Sparse seek-sampling (every ~5 s, fast: decodes only sampled frames) + frame-difference
    DEDUP: keeps a rat-present/borderline frame only if it differs from the last kept one,
    so resting near-duplicates are dropped. Copies frames -> dataset/rat/images and writes
    outputs/<CH>_harvest_manifest.csv.
        python scan_for_rats.py --channel CH05 --date 2026-06-28 --hours 19 20

  OCCUPANCY (--occupancy-hz) -- shelter occupancy time series, saves NO frames.
    One streamed full decode at ~N Hz (vid_stride); the small shelter FOV means brief visits,
    so dense sampling avoids undercounting. Counts only -> outputs/<CH>_occupancy.csv (+plot)
    with timestamp, n_rats, max/mean conf.
        python scan_for_rats.py --channel CH05 --date 2026-06-28 --hours 19 20 --occupancy-hz 1

Default weights: runs/detect/rat_feasibility/weights/best.pt. Inference runs on the GPU.
Harvest -> label with label_frames.py, then retrain. Occupancy -> analysis only.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

import extract_clip as ec        # reuse FFMPEG path + _run_ffmpeg

HERE = Path(__file__).resolve().parent
FFPROBE = ec.REC_ROOT / "bin" / "ffprobe.exe"
_TS_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")


def video_duration(src: Path):
    cp = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=nk=1:nw=1", str(src)], capture_output=True, text=True)
    try:
        return float(cp.stdout.strip())
    except (ValueError, AttributeError):
        return None


def video_fps(src: Path, default: float = 15.0) -> float:
    cp = subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=r_frame_rate", "-of", "default=nk=1:nw=1",
                         str(src)], capture_output=True, text=True)
    try:
        num, den = cp.stdout.strip().split("/")
        return float(num) / float(den)
    except (ValueError, ZeroDivisionError, AttributeError):
        return default


def clip_start(name: str):
    m = _TS_RE.search(name)
    if not m:
        return None, None
    ts = pd.Timestamp(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}")
    return ts, int(m.group(2))                       # (start datetime, start hour)


def is_closed(name: str) -> bool:
    """A recording is finalized once renamed to '..._<start>_to_<end>.mp4'. The in-progress
    hour has no '_to_' — CV must NEVER read it (contends with / can corrupt the live write)."""
    return "_to_" in name


def resolve_files(args) -> list[Path]:
    if args.src:
        files = [Path(p) for p in args.src]
    else:
        d = ec.REC_ROOT / args.channel
        files = sorted(d.glob(f"{args.channel}_{args.date}_*.mp4"))
        if args.hours:
            files = [f for f in files if (clip_start(f.name)[1] in set(args.hours))]
    # CAPTURE SAFETY: only closed files, never the file being recorded (see CLAUDE.md)
    kept = [f for f in files if is_closed(f.name)]
    dropped = [f.name for f in files if not is_closed(f.name)]
    if dropped:
        print(f"skipping {len(dropped)} OPEN (still-recording) file(s): {dropped}")
    return kept


def sample_frames(src: Path, every_sec: float, scale: int, tmp: Path, threads: int = 2):
    """Seek to each sample time and decode ONLY that frame (fast: no full-hour decode).
    `threads` caps ffmpeg CPU so we don't disturb the live capture. Returns (frame_paths,
    offset_seconds). Falls back to the fps filter if duration unknown."""
    tmp.mkdir(parents=True, exist_ok=True)
    th = ["-threads", str(threads)]
    dur = video_duration(src)
    if not dur:                                          # fallback: decode-all fps filter
        pat = tmp / f"{src.stem}_%05d.png"
        ec._run_ffmpeg(th + ["-i", str(src), "-vf", f"fps=1/{every_sec},scale={scale}:-2", str(pat)])
        fr = sorted(tmp.glob(f"{src.stem}_*.png"))
        return fr, [i * every_sec for i in range(len(fr))]
    frames, offs = [], []
    for i, t in enumerate(np.arange(0, dur, every_sec)):
        out = tmp / f"{src.stem}_{i:05d}.png"
        # -ss BEFORE -i = fast input seek to the nearest keyframe -> tiny decode
        ec._run_ffmpeg(th + ["-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                             "-vf", f"scale={scale}:-2", str(out)])
        if out.exists():
            frames.append(out); offs.append(float(t))
    return frames, offs


def _gray64(img) -> np.ndarray:
    """Tiny grayscale thumbnail for cheap frame-difference dedup."""
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (64, 64)).astype(np.float32)


def harvest(args, files, model) -> None:
    """LABELING harvest: sparse seek-sampling + frame-difference dedup -> diverse frames.

    Keeps only rat-present/borderline frames that DIFFER from the last kept one (so resting
    near-duplicates are dropped). Copies frames into the dataset; writes a selection manifest.
    Does NOT produce occupancy estimates.
    """
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    tmp = HERE / "scratch" / f"harvest_{args.channel}"
    if tmp.exists():
        shutil.rmtree(tmp)
    kept, last_small, copied = [], None, 0
    for f in files:
        start, _ = clip_start(f.name)
        frames, offs = sample_frames(f, args.every_sec, args.scale, tmp, threads=args.ffmpeg_threads)
        print(f"{f.name}: {len(frames)} frames sampled @ {args.every_sec:.0f}s")
        # predict in small batches; passing the whole list = one giant batch -> CUDA OOM
        for ci in range(0, len(frames), args.batch):
          chunk = frames[ci:ci + args.batch]
          for j, r in enumerate(model.predict(chunk, conf=args.conf_low, classes=[0], imgsz=args.imgsz,
                                              device=args.device, verbose=False)):
            idx = ci + j
            off = offs[idx]
            n = 0 if r.boxes is None else len(r.boxes)
            mc = float(r.boxes.conf.max()) if n else 0.0
            tag = "present" if mc >= args.conf_high else ("borderline" if mc >= args.conf_low else None)
            if not tag:
                continue                                   # no (even tentative) rat -> not a label candidate
            small = _gray64(r.orig_img)
            if last_small is not None and float(np.abs(small - last_small).mean()) < args.dedup_thresh:
                continue                                   # too similar to the last kept frame -> redundant
            last_small = small
            t = (start + pd.Timedelta(seconds=off)) if start is not None else off
            dest = out_dir / f"{args.channel}_{f.stem}_{int(off)}s.png"
            kept.append({"dest": dest.name, "src_file": f.name, "t": t, "offset_s": off,
                         "n_rats": n, "max_conf": round(mc, 3), "tag": tag, "_path": frames[idx]})
    if len(kept) > args.max_keep:                          # cap, spaced evenly across time
        step = len(kept) / args.max_keep
        kept = [kept[int(i * step)] for i in range(args.max_keep)]
    for k in kept:
        d = out_dir / k["dest"]
        if not d.exists():
            shutil.copy(k["_path"], d); copied += 1
    shutil.rmtree(tmp, ignore_errors=True)

    outputs = HERE / "outputs"; outputs.mkdir(exist_ok=True)
    man = outputs / f"{args.channel}_harvest_manifest.csv"
    pd.DataFrame([{kk: v for kk, v in k.items() if kk != "_path"} for k in kept]).to_csv(man, index=False)
    npres = sum(k["tag"] == "present" for k in kept)
    print(f"\nHARVEST: copied {copied} new frames to {out_dir} "
          f"({npres} present, {len(kept) - npres} borderline after dedup, thresh={args.dedup_thresh})")
    print(f"manifest -> {man}")
    print("next: label them -> python label_frames.py ; then retrain -> python train_detector.py")


def occupancy(args, files, model) -> None:
    """OCCUPANCY time series: dense single streamed decode (vid_stride) at ~--occupancy-hz.
    Counts-only -> CSV (t, n_rats, max/mean conf) + plot. Saves NO frames."""
    rows = []
    for f in files:
        start, _ = clip_start(f.name)
        fps = video_fps(f)
        stride = max(1, round(fps / args.occupancy_hz))
        print(f"{f.name}: streaming @ ~{fps/stride:.2f} Hz (fps {fps:.1f}, stride {stride})")
        for i, r in enumerate(model.predict(source=str(f), vid_stride=stride, conf=args.conf_low,
                                            classes=[0], imgsz=args.imgsz, device=args.device,
                                            stream=True, verbose=False)):
            n = 0 if r.boxes is None else len(r.boxes)
            confs = r.boxes.conf.cpu().numpy() if n else np.array([])
            off = i * stride / fps
            t = (start + pd.Timedelta(seconds=off)) if start is not None else off
            rows.append({"file": f.name, "t": t, "offset_s": round(off, 2), "n_rats": n,
                         "max_conf": round(float(confs.max()), 3) if n else 0.0,
                         "mean_conf": round(float(confs.mean()), 3) if n else 0.0})

    df = pd.DataFrame(rows)
    outputs = HERE / "outputs"; outputs.mkdir(exist_ok=True)
    csv = outputs / f"{args.channel}_occupancy.csv"; df.to_csv(csv, index=False)
    occ = float((df["n_rats"] > 0).mean()) * 100 if len(df) else 0.0
    print(f"\nOCCUPANCY: {len(df)} samples, shelter occupied {occ:.0f}% of the time, "
          f"max {int(df['n_rats'].max()) if len(df) else 0} rats")
    print(f"occupancy CSV -> {csv}  (no frames saved)")
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 3.2))
        ax.plot(df["t"], df["n_rats"], lw=0.8); ax.fill_between(df["t"], df["n_rats"], alpha=0.3)
        ax.set_ylabel("rats detected"); ax.set_title(f"{args.channel} shelter occupancy")
        ax.grid(True, ls=":", alpha=0.5); fig.autofmt_xdate()
        png = outputs / f"{args.channel}_occupancy.png"; fig.tight_layout(); fig.savefig(png, dpi=120)
        print(f"occupancy plot -> {png}")
    except Exception as e:
        print(f"(plot skipped: {e})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Scan footage with a rat detector. Two modes: "
                                 "LABELING harvest (default) or --occupancy-hz time series.",
                                 allow_abbrev=False)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--src", nargs="+", help="explicit recording file(s); else use --date")
    ap.add_argument("--date", help="YYYY-MM-DD to glob all that day's files for the channel")
    ap.add_argument("--hours", type=int, nargs="+", help="restrict --date to these start hours")
    ap.add_argument("--weights", default=str(HERE / "runs/detect/rat_feasibility/weights/best.pt"))
    ap.add_argument("--scale", type=int, default=1280)
    ap.add_argument("--conf-low", type=float, default=0.20, help="below this = treated as empty")
    ap.add_argument("--conf-high", type=float, default=0.40, help="at/above this = confident rat")
    ap.add_argument("--device", default="0", help="inference device (0 = first GPU, or 'cpu')")
    ap.add_argument("--imgsz", type=int, default=1280, help="detector inference size (match training)")
    ap.add_argument("--batch", type=int, default=8, help="frames per inference batch (lower if CUDA OOM)")
    ap.add_argument("--ffmpeg-threads", type=int, default=2,
                    help="cap ffmpeg decode threads to stay light on the live-capture PC")
    # labeling-harvest options
    ap.add_argument("--every-sec", type=float, default=5.0, help="harvest: seek-sample every N s")
    ap.add_argument("--dedup-thresh", type=float, default=3.0,
                    help="harvest: keep a frame only if its 64x64 gray mean-abs diff from the last "
                         "kept >= this. Lower keeps MORE (safer); raise it if you get near-duplicates")
    ap.add_argument("--max-keep", type=int, default=300, help="harvest: cap frames copied for labeling")
    ap.add_argument("--out-dir", default=str(HERE / "dataset" / "rat" / "images"))
    # occupancy mode (presence -> dense streamed decode, counts only, no frames)
    ap.add_argument("--occupancy-hz", type=float, nargs="?", const=1.0, default=None,
                    help="run OCCUPANCY mode at this rate (default 1.0 Hz if flag given with no value)")
    args = ap.parse_args()

    files = resolve_files(args)
    if not files:
        raise SystemExit("no files (give --src, or --date [--hours]).")
    if not Path(args.weights).exists():
        raise SystemExit(f"weights not found: {args.weights} (train one with train_detector.py)")

    from ultralytics import YOLO
    model = YOLO(args.weights)
    if args.occupancy_hz is not None:
        occupancy(args, files, model)
    else:
        harvest(args, files, model)


if __name__ == "__main__":
    main()

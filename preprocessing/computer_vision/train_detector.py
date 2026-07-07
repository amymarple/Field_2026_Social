"""
Light rat-detector fine-tune (Stage 1 feasibility) with Ultralytics YOLO11.

Takes the labeled frames from label_frames.py, makes an 80/20 train/val split,
writes a data.yaml, and fine-tunes a small pretrained YOLO (COCO backbone) on the
single class `rat`. Prints validation mAP so you can judge feasibility before scaling.

    python train_detector.py                       # dataset/rat, yolo11s, 80 epochs, imgsz 1280
    python train_detector.py --epochs 120 --model yolo11n.pt --predict-clip scratch/CH05_clip.mp4

Output: runs/detect/<name>/weights/best.pt  (+ val plots). Plug into the tracker:
    python animal_tracking.py --channel CH05 --clip <clip> --weights runs/detect/<name>/weights/best.pt --classes 0
"""

from __future__ import annotations

import argparse
import csv
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _final_val_metrics(results, results_csv: Path):
    """(mAP50, mAP50-95, precision, recall) from training's OWN final validation.

    Prefers the metrics object `model.train()` returns (the trainer's validator ran final_eval on
    best.pt); falls back to the last row of results.csv. This avoids a second standalone GPU
    `model.val()` - it is redundant (training already validated the held-out videos) and re-running
    batched inference at imgsz>=960 trips the RTX 3060 CUDA illegal-access / cuDNN 'no engine' crash.
    """
    box = getattr(results, "box", None)
    if box is not None:
        return float(box.map50), float(box.map), float(box.mp), float(box.mr)
    if not results_csv.exists():
        return None
    reader = csv.DictReader(results_csv.read_text().splitlines())
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    if not rows:
        return None
    last = rows[-1]

    def g(key):
        v = last.get(key, "")
        return float(v) if v else float("nan")

    return (g("metrics/mAP50(B)"), g("metrics/mAP50-95(B)"),
            g("metrics/precision(B)"), g("metrics/recall(B)"))


def session_key(p: Path) -> str:
    """Camera+date+hour (one recording) from a frame name, e.g. 'CH05_2026-06-28_20'."""
    m = re.search(r"CH0\d_\d{4}-\d{2}-\d{2}_\d{2}", p.name)
    return m.group(0) if m else p.stem


def build_split(data_root: Path, val_frac: float, seed: int = 0, val_sessions=None):
    """List images that HAVE a label file (labeled, empty allowed); write train/val txt + data.yaml.
    Splits by SESSION (whole videos held out for val) so the metric reflects generalization."""
    img_dir, lbl_dir = data_root / "images", data_root / "labels"
    imgs = [p for p in sorted(img_dir.glob("*"))
            if p.suffix.lower() in (".png", ".jpg", ".jpeg") and (lbl_dir / f"{p.stem}.txt").exists()]
    if not imgs:
        raise SystemExit(f"no labeled images in {img_dir} (label some with label_frames.py first)")
    n_pos = sum(1 for p in imgs if (lbl_dir / f"{p.stem}.txt").read_text().strip())
    # Split by SESSION (camera+date+hour = one video), holding out whole videos for val, so val
    # measures generalization to footage the model never saw - not memorized near-duplicate frames.
    groups: dict[str, list] = {}
    for p in imgs:
        groups.setdefault(session_key(p), []).append(p)
    sessions = sorted(groups)
    if val_sessions:
        val_keys = [s for s in sessions if any(v in s for v in val_sessions)]
    else:
        order = sessions[:]; random.Random(seed).shuffle(order)
        target, acc, val_keys = len(imgs) * val_frac, 0, []
        for s in order:                       # accumulate whole sessions until ~val_frac of images
            if acc >= target:
                break
            val_keys.append(s); acc += len(groups[s])
        if len(val_keys) >= len(sessions):    # never leave train empty
            val_keys = val_keys[:-1]
    val = [p for s in val_keys for p in groups[s]]
    train = [p for s in sessions if s not in val_keys for p in groups[s]]
    (data_root / "train.txt").write_text("\n".join(str(p.resolve()) for p in train) + "\n")
    (data_root / "val.txt").write_text("\n".join(str(p.resolve()) for p in val) + "\n")
    yaml = data_root / "data.yaml"
    yaml.write_text(
        f"path: {data_root.resolve()}\n"
        f"train: train.txt\n"
        f"val: val.txt\n"
        f"names:\n  0: rat\n")
    print(f"split by session: {len(sessions)} videos -> {len(train)} train / {len(val)} val frames "
          f"({len(imgs)} labeled, {n_pos} with >=1 rat)")
    print(f"  held-out val videos: {sorted(val_keys)}")
    return yaml


def main() -> None:
    ap = argparse.ArgumentParser(description="Light YOLO rat-detector fine-tune.")
    ap.add_argument("--data-root", default=str(HERE / "dataset" / "rat"))
    ap.add_argument("--model", default="yolo11s.pt", help="pretrained base (yolo11n/s/m.pt)")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--batch", type=float, default=-1, help="-1 = auto (~60%% VRAM)")
    ap.add_argument("--val-frac", type=float, default=0.2, help="approx val fraction (by whole videos)")
    ap.add_argument("--val-sessions", nargs="+",
                    help="explicit session substrings to hold out for val, e.g. CH06_2026-06-28_21")
    ap.add_argument("--name", default="rat_feasibility")
    ap.add_argument("--device", default="0")
    ap.add_argument("--predict-clip", help="after training, run best.pt on this clip (save annotated)")
    args = ap.parse_args()

    from ultralytics import YOLO

    data_yaml = build_split(Path(args.data_root), args.val_frac, val_sessions=args.val_sessions)
    model = YOLO(args.model)
    results = model.train(data=str(data_yaml), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
                          device=args.device, project=str(HERE / "runs" / "detect"), name=args.name,
                          patience=30, seed=0)

    # Training already validated best.pt on the held-out videos; report THOSE metrics (no second
    # model.val(): it's redundant and its batched imgsz-1280 inference crashes the RTX 3060). Read the
    # real run dir from the trainer - ultralytics auto-increments `name` on repeat runs (rat_feasibility-6).
    save_dir = Path(getattr(model.trainer, "save_dir", HERE / "runs" / "detect" / args.name))
    best = Path(getattr(model.trainer, "best", save_dir / "weights" / "best.pt"))
    m = _final_val_metrics(results, save_dir / "results.csv")
    if m:
        print(f"\n=== feasibility: val mAP50={m[0]:.3f}  mAP50-95={m[1]:.3f}  "
              f"precision={m[2]:.3f}  recall={m[3]:.3f} ===")
    else:
        print("\n=== training done; val metrics unavailable - see results.csv / val plots ===")
    print(f"best weights: {best}")
    if args.predict_clip:
        YOLO(str(best)).predict(source=args.predict_clip, save=True, conf=0.25,
                                project=str(HERE / "runs" / "detect"), name=f"{args.name}_pred")
        print("annotated predictions saved under runs/detect/")


if __name__ == "__main__":
    main()

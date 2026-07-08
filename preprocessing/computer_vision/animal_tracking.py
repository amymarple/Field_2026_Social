"""
Animal position tracking -> per-camera tracks in the common field frame.

Output goal (per the project): per-detection **x, y position + animal ID** in
field cm. No pose/keypoints. This module turns detections into the canonical CSV:

    camera, frame, time_s, track_id, conf, x_img, y_img, x_field_cm, y_field_cm

A detection's ground point is its box bottom-centre; that pixel is mapped through
the camera's homography (field_coords) to field cm. `track_id` is a provisional
animal_id (stable identity comes later from the colour/symbol marks).

Three input modes:
  --synthetic   generate a moving point (no video, no detector) -> CSV
  --manual J    read detections from JSON {"detections":[{frame,time_s,x_img,y_img,
                track_id?,conf?,bbox?}...]} -> CSV
  --clip V      OPTIONAL real detection: Ultralytics YOLO + ByteTrack on a clip
                (only if `ultralytics` is installed; placeholder weights for now)

Stage 0 is validated with --synthetic / --manual (no detector required).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import field_coords as fc

CSV_COLUMNS = ["camera", "frame", "time_s", "track_id", "conf",
               "x_img", "y_img", "x_field_cm", "y_field_cm"]


def detections_to_tracks(detections: pd.DataFrame, channel: str,
                         calib: Optional[dict], config_dir=fc.CONFIG_DIR,
                         src_size=None) -> pd.DataFrame:
    """Attach field-cm coords to a detections frame and return the canonical schema.

    `detections` must have: frame, time_s, x_img, y_img (ground point). Optional:
    track_id, conf. `calib` is a per-camera calibration (homography OR poly) from
    fc.load_calib; if None the field columns are left as NaN. `src_size` = (w, h) the
    detection pixels are in (so to_field can scale to the calibration resolution).
    """
    df = detections.copy()
    df["camera"] = channel
    if "track_id" not in df:
        df["track_id"] = 0
    if "conf" not in df:
        df["conf"] = 1.0
    if calib is not None and len(df):
        cm = fc.to_field(channel, df[["x_img", "y_img"]].to_numpy(),
                         config_dir=config_dir, calib=calib, src_size=src_size)
        df["x_field_cm"] = cm[:, 0]
        df["y_field_cm"] = cm[:, 1]
    else:
        df["x_field_cm"] = np.nan
        df["y_field_cm"] = np.nan
    return df[CSV_COLUMNS]


def synthetic_detections(n: int = 40, fps: int = 5, track_id: int = 1,
                         w: int = 960, h: int = 540) -> pd.DataFrame:
    """A single point walking a smooth path (for self-test). The path is kept
    inside the synthetic-calibration trapezoid so it maps to inside-field cm."""
    frac = np.arange(n) / max(n - 1, 1)
    x = 380 + 360 * frac                      # 380 -> 740 px
    y = 560 - 220 * frac                       # 560 -> 340 px (near -> far)
    t = np.arange(n)
    return pd.DataFrame({
        "frame": t,
        "time_s": t / fps,
        "x_img": x,
        "y_img": y,
        "track_id": track_id,
        "conf": 0.9,
    })


def load_manual(path: Path) -> pd.DataFrame:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    df = pd.DataFrame(spec["detections"])
    # if a bbox [x1,y1,x2,y2] is given, derive the ground point (bottom-centre)
    if "bbox" in df and ("x_img" not in df or df["x_img"].isna().any()):
        b = np.array(df["bbox"].tolist(), dtype=float)
        df["x_img"] = (b[:, 0] + b[:, 2]) / 2
        df["y_img"] = b[:, 3]
    for col, default in (("frame", range(len(df))), ("time_s", None),
                         ("track_id", 0), ("conf", 1.0)):
        if col not in df:
            df[col] = list(default) if default is not None else np.arange(len(df))
    return df


def run_yolo(clip: Path, channel: str, weights: str = "yolo11n.pt",
             conf: float = 0.25, fps: float = 5.0, classes: Optional[list] = None,
             device: Optional[str] = None, ground_point: str = "bottom") -> pd.DataFrame:
    """OPTIONAL real path: YOLO + ByteTrack -> detections. Needs ultralytics.

    `classes` filters COCO class ids (e.g. [0] = person, for testing the pipeline on a
    person walking the paddock before a rat detector exists). `fps` should match the clip.
    `ground_point`: "bottom" (box bottom-centre, for oblique cams = feet) or "center" (box
    centre, correct for ~nadir cams CH05/CH06 where the animal is seen from straight above).
    """
    from ultralytics import YOLO
    model = YOLO(weights)
    rows = []
    for i, r in enumerate(model.track(source=str(clip), persist=True, conf=conf,
                                      classes=classes, device=device,
                                      tracker="bytetrack.yaml", stream=True, verbose=False)):
        if r.boxes is None:
            continue
        ids = r.boxes.id
        for j, box in enumerate(r.boxes.xyxy.cpu().numpy()):
            x1, y1, x2, y2 = box
            rows.append({
                "frame": i,
                "time_s": i / fps,
                "x_img": (x1 + x2) / 2,
                "y_img": (y1 + y2) / 2 if ground_point == "center" else y2,
                "track_id": int(ids[j]) if ids is not None else -1,
                "conf": float(r.boxes.conf[j].cpu().numpy()),
            })
    return pd.DataFrame(rows)


def write_csv(df: pd.DataFrame, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return out


def _cli() -> None:
    # allow_abbrev=False: otherwise '--conf' would prefix-match '--config-dir' (silent footgun)
    ap = argparse.ArgumentParser(description="Detections -> per-camera field-cm tracks CSV.",
                                 allow_abbrev=False)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config-dir", default=str(fc.CONFIG_DIR))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--synthetic", action="store_true")
    g.add_argument("--manual", help="JSON of manual detections")
    g.add_argument("--clip", help="video clip for optional YOLO detection")
    ap.add_argument("--weights", default="yolo11n.pt")
    ap.add_argument("--classes", type=int, nargs="+",
                    help="COCO class ids to keep (e.g. 0 = person, to test on a walking person)")
    ap.add_argument("--conf", type=float, default=0.25, help="detector confidence threshold")
    ap.add_argument("--fps", type=float, default=5.0, help="clip fps (for time_s)")
    ap.add_argument("--ground-point", choices=["auto", "center", "bottom"], default="auto",
                    help="image point taken as the animal's ground position "
                         "(auto: 'center' for nadir cams, else 'bottom')")
    args = ap.parse_args()

    ground_point = args.ground_point
    if ground_point == "auto":
        aim = fc.load_layout().get("camera_mounts", {}).get(args.channel, {}).get("aim", "")
        ground_point = "center" if "nadir" in str(aim).lower() else "bottom"

    try:
        calib = fc.load_calib(args.channel, args.config_dir)
    except FileNotFoundError as e:
        print(f"WARNING: {e}\n  -> field-cm columns will be NaN (calibrate to fill them).")
        calib = None

    src_size = None
    if args.synthetic:
        det = synthetic_detections()
    elif args.manual:
        det = load_manual(Path(args.manual))
    else:
        det = run_yolo(Path(args.clip), args.channel, weights=args.weights, conf=args.conf,
                       classes=args.classes, fps=args.fps, ground_point=ground_point)
        import cv2
        cap = cv2.VideoCapture(args.clip)
        src_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        cap.release()
        print(f"detector ground point: {ground_point}  clip size: {src_size}  "
              f"calib size: {calib.get('image_size') if calib else '-'}")

    tracks = detections_to_tracks(det, args.channel, calib, args.config_dir, src_size=src_size)
    out = write_csv(tracks, Path(args.out))
    n_field = int(tracks["x_field_cm"].notna().sum())
    print(f"wrote {out}  ({len(tracks)} rows, {n_field} with field-cm)")
    if len(tracks):
        print(tracks.head().to_string(index=False))


if __name__ == "__main__":
    _cli()

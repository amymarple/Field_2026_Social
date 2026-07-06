"""
Light clip / frame extraction from the Reolink recordings.

Wraps the pinned ffmpeg at E:\\Reolink_record\\bin so we get a tiny, downscaled
sample (default 8 s, 960 px long edge, 5 fps) for prototyping the CV pipeline,
without touching the originals. Tries GPU (NVDEC) decode and falls back to CPU.

Examples
--------
    python extract_clip.py --channel CH05 --seconds 8
    python extract_clip.py --channel CH05 --frame          # one still for calibration
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Optional

# Recording root and ffmpeg are env-overridable so the pipeline runs off the field PC.
# Field PC (default): recordings on E:\Reolink_record with the pinned ffmpeg in .\bin.
# Analysis PC: set REOLINK_REC_ROOT (e.g. the synced D:\...\audio_in\Reolink_record) and
# REOLINK_FFMPEG (e.g. the conda env's ffmpeg.exe). Unset -> unchanged field-PC behavior.
REC_ROOT = Path(os.environ.get("REOLINK_REC_ROOT", r"E:\Reolink_record"))
FFMPEG = Path(os.environ.get("REOLINK_FFMPEG", str(REC_ROOT / "bin" / "ffmpeg.exe")))
# ffprobe defaults next to ffmpeg, so setting REOLINK_FFMPEG alone (e.g. to the conda env's
# bin) locates both; REOLINK_FFPROBE can override it independently if needed.
FFPROBE = Path(os.environ.get("REOLINK_FFPROBE", str(FFMPEG.parent / "ffprobe.exe")))
SCRATCH = Path(__file__).resolve().parent / "scratch"
CONFIG_DIR = Path(__file__).resolve().parent / "configs"


def find_source(channel: str, root: Path = REC_ROOT) -> Path:
    """Pick a finished (start_to_end) recording for a channel: the most recent one."""
    d = root / channel
    if not d.is_dir():
        raise FileNotFoundError(f"No channel folder: {d}")
    finished = sorted(
        [f for f in d.glob("*.mp4") if "_to_" in f.name],
        key=lambda f: f.name,
    )
    if not finished:
        raise FileNotFoundError(f"No finished *_to_*.mp4 files in {d}")
    return finished[-1]


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(FFMPEG), "-y", *args],
                          capture_output=True, text=True)


def _decode_prefix(gpu: bool) -> list[str]:
    return ["-hwaccel", "cuda"] if gpu else []


def extract_clip(channel: str, seconds: int = 8, scale: int = 960, fps: int = 5,
                 start: str = "00:00:30", src: Optional[Path] = None,
                 out: Optional[Path] = None, gpu: bool = False) -> Path:
    """Extract a short, downscaled, silent clip. Returns the output path."""
    src = src or find_source(channel)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    out = out or SCRATCH / f"{channel}_clip_{seconds}s.mp4"
    vf = f"scale={scale}:-2,fps={fps}"
    common = ["-ss", start, "-i", str(src), "-t", str(seconds),
              "-vf", vf, "-an", str(out)]
    for use_gpu in ([True, False] if gpu else [False]):
        cp = _run_ffmpeg(_decode_prefix(use_gpu) + common)
        if cp.returncode == 0 and out.exists() and out.stat().st_size > 0:
            print(f"[{channel}] {'GPU' if use_gpu else 'CPU'} decode -> {out} "
                  f"({out.stat().st_size/1e6:.1f} MB)  src={src.name}")
            return out
        if use_gpu:
            print(f"[{channel}] GPU decode failed, retrying on CPU...")
    raise RuntimeError(f"ffmpeg failed for {channel}:\n{cp.stderr[-800:]}")


def grab_frame(channel: str, at: str = "00:00:30", scale: int = 1280,
               src: Optional[Path] = None, out: Optional[Path] = None,
               gpu: bool = False) -> Path:
    """Grab a single reference still (for calibration). Returns the PNG path."""
    src = src or find_source(channel)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out = out or CONFIG_DIR / f"{channel}_reference.png"
    common = ["-ss", at, "-i", str(src), "-frames:v", "1",
              "-vf", f"scale={scale}:-2", str(out)]
    for use_gpu in ([True, False] if gpu else [False]):
        cp = _run_ffmpeg(_decode_prefix(use_gpu) + common)
        if cp.returncode == 0 and out.exists() and out.stat().st_size > 0:
            print(f"[{channel}] reference still -> {out}")
            return out
    raise RuntimeError(f"ffmpeg frame grab failed for {channel}:\n{cp.stderr[-800:]}")


def grab_frames(channel: str, count: int = 60, start: str = "00:00:00",
                window: int = 3600, scale: int = 1280, src: Optional[Path] = None,
                out_dir: Optional[Path] = None, gpu: bool = False) -> Path:
    """Sample `count` stills evenly across a `window` (s) of a recording, for labeling.

    Saves out_dir/<CH>_<src-stem>_%04d.png. Returns the output directory.
    """
    src = src or find_source(channel)
    out_dir = out_dir or (Path(__file__).resolve().parent / "dataset" / "rat" / "images")
    out_dir.mkdir(parents=True, exist_ok=True)
    rate = max(count, 1) / float(max(window, 1))           # frames/sec to emit ~count over window
    pat = out_dir / f"{channel}_{src.stem}_%04d.png"
    common = ["-ss", start, "-i", str(src), "-t", str(window),
              "-vf", f"fps={rate:.6f},scale={scale}:-2", "-frames:v", str(count), str(pat)]
    for use_gpu in ([True, False] if gpu else [False]):
        cp = _run_ffmpeg(_decode_prefix(use_gpu) + common)
        made = sorted(out_dir.glob(f"{channel}_{src.stem}_*.png"))
        if cp.returncode == 0 and made:
            print(f"[{channel}] sampled {len(made)} frames -> {out_dir}  src={src.name}")
            return out_dir
        if use_gpu:
            print(f"[{channel}] GPU decode failed, retrying on CPU...")
    raise RuntimeError(f"ffmpeg frame-sampling failed for {channel}:\n{cp.stderr[-800:]}")


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Extract a light clip/frame from a channel.")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--seconds", type=int, default=8)
    ap.add_argument("--scale", type=int, default=960, help="long-edge px (clip)")
    ap.add_argument("--fps", type=int, default=5)
    ap.add_argument("--start", default="00:00:30", help="seek time HH:MM:SS")
    ap.add_argument("--frame", action="store_true", help="grab ONE still instead of a clip")
    ap.add_argument("--frames", type=int, help="sample THIS many stills across --window (for labeling)")
    ap.add_argument("--window", type=int, default=3600, help="seconds to span when sampling --frames")
    ap.add_argument("--src", help="explicit source mp4 (else newest finished file for the channel)")
    ap.add_argument("--out-dir", help="output dir for --frames (default dataset/rat/images)")
    ap.add_argument("--gpu", action="store_true",
                    help="try NVDEC (off by default; this ffmpeg build's NVDEC errors on Blackwell)")
    ap.add_argument("--root", default=str(REC_ROOT))
    args = ap.parse_args()

    if not FFMPEG.exists():
        raise SystemExit(f"ffmpeg not found at {FFMPEG}")
    src = Path(args.src) if args.src else find_source(args.channel, Path(args.root))
    if args.frames:
        grab_frames(args.channel, count=args.frames, start=args.start, window=args.window,
                    scale=args.scale if args.scale != 960 else 1280, src=src,
                    out_dir=Path(args.out_dir) if args.out_dir else None, gpu=args.gpu)
    elif args.frame:
        grab_frame(args.channel, at=args.start, src=src, gpu=args.gpu)
    else:
        extract_clip(args.channel, seconds=args.seconds, scale=args.scale, fps=args.fps,
                     start=args.start, src=src, gpu=args.gpu)


if __name__ == "__main__":
    _cli()

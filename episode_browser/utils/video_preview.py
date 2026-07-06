"""
video_preview.py — LIGHT, subsampled frame preview for an episode. Data-layer ONLY.

The browser must never full-decode footage (see the CV-pipeline warning in CLAUDE.md).
This module grabs only a HANDFUL of frames across an episode's time span using fast
ffmpeg seeks (`-ss` before `-i`, one frame per grab, downscaled). It decodes N frames,
not N hours.

Safety carried from the recorder rules:
  * Only CLOSED recordings are read. A live Reolink hour is `..._<start>.mp4` (no
    `_to_`); a finalized hour is `..._<start>_to_<end>.mp4`. `is_closed_recording`
    flags an open file so the UI can refuse it — reading the open file can contend
    with the active write.
  * Nothing here writes to or near the source footage.

Video is located per-episode via `linked_assets` (video_path + video_t_offset_s +
optional preview_span_s). Synthetic episodes point at a tiny generated sample clip so
the pipeline is exercisable without real data; `ensure_sample_clip` builds it on demand
if ffmpeg is available.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# episode_browser/ root (this file is utils/video_preview.py).
ROOT = Path(__file__).resolve().parent.parent

# A finalized Reolink hour ends in _<start>_to_<end>; an open one does not.
_CLOSED_RE = re.compile(r"_to_", re.IGNORECASE)
_REOLINK_RE = re.compile(r"CH\d{2}", re.IGNORECASE)

SAMPLE_CLIP = ROOT / "data" / "sample_clip.mp4"
DEFAULT_PREVIEW_SPAN_S = 6.0     # how wide a slice a filmstrip spans, by default
DEFAULT_N_FRAMES = 6
DEFAULT_WIDTH = 320


def find_ffmpeg() -> Optional[str]:
    """Locate ffmpeg without installing: env override -> PATH -> known field-PC bin."""
    for env in ("EPISODE_BROWSER_FFMPEG", "FFMPEG"):
        p = os.environ.get(env)
        if p and Path(p).exists():
            return p
    which = shutil.which("ffmpeg")
    if which:
        return which
    for cand in (r"E:\Reolink_record\bin\ffmpeg.exe", r"D:\Reolink_record\bin\ffmpeg.exe"):
        if Path(cand).exists():
            return cand
    return None


def is_closed_recording(path: str | Path) -> bool:
    """True unless the path looks like an OPEN Reolink hour (CHxx file lacking `_to_`).

    Non-Reolink files (e.g. the sample clip) are treated as safe to read.
    """
    name = Path(path).name
    if not _REOLINK_RE.search(name):
        return True
    return bool(_CLOSED_RE.search(name))


def resolve_video(episode: dict, base_dir: Path = ROOT) -> Optional[dict]:
    """Map an episode to a previewable clip via linked_assets.

    Returns {path, start_s, end_s, synthetic, exists, closed} or None when the
    episode carries no video link. `path` is resolved relative to base_dir when
    linked_assets stored a repo-relative path.
    """
    la = episode.get("linked_assets")
    if not isinstance(la, dict):
        return None
    vp = la.get("video_path")
    if not vp:
        return None
    path = Path(vp)
    if not path.is_absolute():
        path = (base_dir / vp).resolve()
    start_s = float(la.get("video_t_offset_s") or 0.0)
    span = float(la.get("preview_span_s") or DEFAULT_PREVIEW_SPAN_S)
    return {
        "path": path,
        "start_s": start_s,
        "end_s": start_s + span,
        "synthetic": bool(la.get("synthetic")),
        "exists": path.exists(),
        "closed": is_closed_recording(path),
    }


def ensure_sample_clip(seconds: int = 60, size: str = "320x240", fps: int = 5,
                       out: Path = SAMPLE_CLIP) -> Optional[Path]:
    """Create a tiny labeled test clip (once) so previews work without real footage.

    Uses ffmpeg's lavfi `testsrc` (built-in timecode overlay). Low res + low fps keep
    it a few hundred KB. Returns the path, or None if ffmpeg is unavailable.
    """
    if out.exists():
        return out
    ff = find_ffmpeg()
    if not ff:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ff, "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={fps}",
           "-pix_fmt", "yuv420p", str(out)]
    try:
        subprocess.run(cmd, check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception:  # noqa: BLE001
        return None
    return out if out.exists() else None


def extract_frames(path: str | Path, start_s: float, end_s: float,
                   n: int = DEFAULT_N_FRAMES, width: int = DEFAULT_WIDTH,
                   ffmpeg: Optional[str] = None) -> list[dict]:
    """Grab `n` evenly-spaced, downscaled PNG frames from [start_s, end_s].

    Returns [{t_s, png}] where png is raw PNG bytes. Light by construction: one fast
    seek + single-frame decode per sample, scaled to `width`. Refuses OPEN recordings.
    Returns [] if ffmpeg is missing, the file is absent, or it is an open hour.
    """
    path = Path(path)
    if not path.exists() or not is_closed_recording(path):
        return []
    ff = ffmpeg or find_ffmpeg()
    if not ff:
        return []
    n = max(1, int(n))
    if end_s <= start_s:
        end_s = start_s + DEFAULT_PREVIEW_SPAN_S
    step = (end_s - start_s) / n
    times = [start_s + step * (i + 0.5) for i in range(n)]   # frame centers

    frames: list[dict] = []
    for t in times:
        cmd = [ff, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
               "-frames:v", "1", "-vf", f"scale={int(width)}:-1",
               "-f", "image2pipe", "-vcodec", "png", "-"]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=20)
        except Exception:  # noqa: BLE001
            continue
        if res.returncode == 0 and res.stdout:
            frames.append({"t_s": round(t, 2), "png": res.stdout})
    return frames

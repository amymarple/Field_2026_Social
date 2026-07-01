"""Discover Reolink hourly MP4s and stream their audio via ffmpeg, chunk by chunk.

Lightweight by design: audio is decoded to mono float32 PCM on stdout
(`ffmpeg -i <mp4> -vn -ac 1 -ar <sr> -f f32le -`) and yielded one analysis window at a
time. A whole hour is NEVER held in memory and NO temporary WAV is written.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List, Optional

import numpy as np

from .time_utils import RecordingFile, parse_recording_filename

# Reolink hourly segments are nominally 3600 s; used to estimate the active file's span.
DEFAULT_SEGMENT_S = 3600


class DecodeError(RuntimeError):
    """Raised when ffmpeg fails to decode a file's audio."""


@dataclass
class Window:
    """One analysis window of decoded audio."""
    offset_start_s: float
    offset_end_s: float
    samples: np.ndarray  # float32, mono, in [-1, 1]
    is_partial: bool     # trailing remainder shorter than a full window


def find_recording_files(input_root, channel: str, *, date: Optional[str] = None,
                         start: Optional[datetime] = None, end: Optional[datetime] = None,
                         max_files: Optional[int] = None) -> List[RecordingFile]:
    """Return the channel's hourly MP4s (sorted by start) matching the filters.

    date  : 'YYYY-MM-DD' keeps files whose filename start-date equals it.
    start/end : keep files whose [start, end] overlaps the [start, end] window.
    """
    ch_dir = Path(input_root) / channel
    files: List[RecordingFile] = []
    for p in sorted(ch_dir.glob(f"{channel}_*.mp4")):
        rf = parse_recording_filename(p)
        if rf is None:
            continue
        if date is not None and rf.start.strftime("%Y-%m-%d") != date:
            continue
        if start is not None or end is not None:
            f_end = rf.end or (rf.start + timedelta(seconds=DEFAULT_SEGMENT_S))
            if start is not None and f_end <= start:
                continue
            if end is not None and rf.start >= end:
                continue
        files.append(rf)
    files.sort(key=lambda r: r.start)
    if max_files is not None:
        files = files[:max_files]
    return files


def _stream_pcm(ffmpeg: str, path: Path, sample_rate: int,
                chunk_frames: int = 65536) -> Iterator[np.ndarray]:
    """Yield mono float32 chunks decoded from one file; raise DecodeError on failure."""
    cmd = [str(ffmpeg), "-nostdin", "-loglevel", "error", "-i", str(path),
           "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise DecodeError(f"could not launch ffmpeg: {e}") from e

    bytes_per_chunk = chunk_frames * 4  # float32 = 4 bytes
    got_any = False
    try:
        while True:
            raw = proc.stdout.read(bytes_per_chunk)
            if not raw:
                break
            # guard against a torn read not aligned to 4 bytes
            usable = len(raw) - (len(raw) % 4)
            if usable:
                got_any = True
                yield np.frombuffer(raw[:usable], dtype="<f4")
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.stderr.close()
        ret = proc.wait()
    if ret != 0 and not got_any:
        raise DecodeError(f"ffmpeg exit {ret} for {path.name}: {err.strip()[:200]}")


def iter_windows(ffmpeg: str, rec_file: RecordingFile, sample_rate: int,
                 window_s: float) -> Iterator[Window]:
    """Yield fixed-length Windows from a file, streaming; a trailing remainder is
    yielded with ``is_partial=True``. Raises DecodeError if the file cannot be read."""
    win_n = int(round(window_s * sample_rate))
    parts: List[np.ndarray] = []
    parts_len = 0
    emitted_frames = 0
    for chunk in _stream_pcm(ffmpeg, rec_file.path, sample_rate):
        parts.append(chunk)
        parts_len += len(chunk)
        while parts_len >= win_n:
            buf = np.concatenate(parts) if len(parts) > 1 else parts[0]
            win = buf[:win_n]
            remainder = buf[win_n:]
            parts = [remainder] if len(remainder) else []
            parts_len = len(remainder)
            yield Window(offset_start_s=emitted_frames / sample_rate,
                         offset_end_s=(emitted_frames + win_n) / sample_rate,
                         samples=win, is_partial=False)
            emitted_frames += win_n
    if parts_len > 0:
        buf = np.concatenate(parts) if len(parts) > 1 else parts[0]
        yield Window(offset_start_s=emitted_frames / sample_rate,
                     offset_end_s=(emitted_frames + parts_len) / sample_rate,
                     samples=buf, is_partial=True)

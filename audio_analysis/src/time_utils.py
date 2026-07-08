"""Parse Reolink recorder filenames into absolute (local wallclock) timestamps.

The RTSP recorder writes hourly fragmented MP4s named either:
    CH01_2026-06-29_22-00-00.mp4                      (active file: start only)
    CH01_2026-06-29_21-00-04_to_22-00-00.mp4          (closed file: start_to_end)

Times are the recorder's local wallclock (NOT UTC). For any analysis window inside a
file:  absolute_timestamp = file_start + intra_file_offset_seconds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# CHxx _ YYYY-MM-DD _ HH-MM-SS [ _to_ HH-MM-SS ] .mp4
_FILENAME_RE = re.compile(
    r"^(?P<channel>CH\d{2})_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<start>\d{2}-\d{2}-\d{2})"
    r"(?:_to_(?P<end>\d{2}-\d{2}-\d{2}))?"
    r"\.mp4$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RecordingFile:
    """A parsed Reolink hourly recording file."""
    path: Path
    channel: str
    start: datetime
    end: Optional[datetime]  # from the *_to_HH-MM-SS suffix; None for the active file

    @property
    def is_active(self) -> bool:
        """True for the still-recording file (no _to_ end marker)."""
        return self.end is None

    @property
    def nominal_duration_s(self) -> Optional[float]:
        if self.end is None:
            return None
        return (self.end - self.start).total_seconds()


def parse_recording_filename(path) -> Optional[RecordingFile]:
    """Parse one filename -> RecordingFile, or None if it does not match."""
    path = Path(path)
    m = _FILENAME_RE.match(path.name)
    if not m:
        return None
    date = m.group("date")
    start = datetime.strptime(f"{date}_{m.group('start')}", "%Y-%m-%d_%H-%M-%S")
    end = None
    if m.group("end"):
        end = datetime.strptime(f"{date}_{m.group('end')}", "%Y-%m-%d_%H-%M-%S")
        # A file that rolls past midnight ends "earlier" than it starts -> add a day.
        if end < start:
            end = end + timedelta(days=1)
    return RecordingFile(path=path, channel=m.group("channel").upper(),
                         start=start, end=end)


def window_timestamp(file_start: datetime, offset_s: float) -> datetime:
    """Absolute timestamp of a window/sample at offset_s seconds into a file."""
    return file_start + timedelta(seconds=float(offset_s))


def fmt(ts: datetime) -> str:
    """Canonical ISO-ish string used in CSV output (second resolution)."""
    return ts.strftime("%Y-%m-%d %H:%M:%S")

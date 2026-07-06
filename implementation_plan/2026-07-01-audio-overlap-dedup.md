# Audio extractor — drop nested/overlapping duplicate segments

## Context / Why (concrete problem observed)

On 2026-06-29, NVR playback exports were manually copied back into the CH01/CH02 folders to backfill
the ~2h46m recording gap from the NVR IP change. Those exports landed **alongside** the RTSP
recorder's own hour-aligned files, with the same `CHxx_<start>_to_<end>.mp4` naming, and several of
them **overlap** the real RTSP hourly file (e.g. `CH01_2026-06-29_14-20-52_to_14-41-18.mp4` and
`14-41-18_to_14-59-59.mp4` sit entirely inside `14-00-00_to_15-00-04.mp4`).

Result after the first analysis-PC extraction (see
[`change_log/2026-07-01-audio-extraction-on-analysis-pc.md`](../change_log/2026-07-01-audio-extraction-on-analysis-pc.md)):
hour 14 has ~101 windows instead of ~60 — the real RTSP `ok` windows **plus** duplicate windows from
the nested NVR segments. The NVR audio is digital silence (Leq −120), so `valid_audio` filtering
keeps the science correct, but the QC window/`silent` counts are inflated and the CSV double-covers
14:20–15:00. If a future backfill segment carried real audio, this would double-count `ok` windows.

`find_recording_files` currently returns **every** matching file sorted by start, with no overlap
handling. This is a new hazard class beyond the existing "active-file skip" TODO: overlapping
same-timespan segments from a second source.

## Goal

Make discovery skip **fully-nested duplicate** segments deterministically, without decoding audio or
needing to know a file's provenance, so each wall-clock instant is represented once. Keep the
earliest/longest-reaching file (the RTSP hour-aligned chain), drop segments whose span is already
fully covered.

## Design

Add a pure, testable helper in `audio_analysis/src/audio_io.py` and call it from
`find_recording_files` after the existing sort:

```
_dedup_overlapping(files) -> (kept, dropped)
  sort by (start, -duration)          # longest-reaching first on ties
  covered_end = None
  for rf in sorted:
      f_end = rf.end or rf.start + DEFAULT_SEGMENT_S
      if covered_end is not None and f_end <= covered_end:
          dropped.append(rf)          # fully inside already-covered span -> redundant
      else:
          kept.append(rf); covered_end = max(covered_end or f_end, f_end)
```

- A contiguous RTSP chain never nests (each file starts at the prior's end), so it is never dropped.
- Only segments whose `end <= covered_end` (fully covered) are dropped. Partially-overlapping or
  gap-filling segments that **extend** coverage are kept (the true-gap silent NVR fillers stay, are
  flagged `silent`, and are excluded downstream by `valid_audio` — an honest "audio absent" marker).
- Surface every drop with `warnings.warn` (no silent culling, per `AGENTS.md`); the CLI's
  "N file(s) match" line then reflects the kept count.

Non-goal: distinguishing RTSP vs NVR by provenance, or removing non-overlapping silent fillers. This
change only removes redundant duplicate coverage.

## Affected files

- `audio_analysis/src/audio_io.py` — new `_dedup_overlapping`; call it in `find_recording_files`.
- `audio_analysis/scripts/selftest_features.py` (or a new `selftest_discovery.py`) — offline test
  building synthetic `RecordingFile`s for the 6/29 14:00 scenario; assert the two nested segments are
  dropped and the RTSP chain + gap fillers are kept.
- Re-extract CH01/CH02 2026-06-29 with `--overwrite` to regenerate clean CSVs.

## Verification

- Self-test PASS: nested-segment scenario drops exactly the two fully-covered files; a plain
  contiguous chain is returned unchanged.
- Re-extraction: hour 14 returns to ~60 windows/channel; the previously double-counted silent
  windows are gone; hour-12 baseline (Leq −48.4) and the `ok` Leq/index medians are unchanged from
  the first run (the science was already correct). `summarize_soundscape.py` QC counts drop by the
  removed duplicates.
- Raw MP4s untouched; outputs only under git-ignored `audio_analysis/outputs/`.

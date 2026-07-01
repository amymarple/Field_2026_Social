# Daily Recording Health Check

A small, **fast, read-only** watchdog that answers ONE question every morning:

> *Did each camera appear to record continuously during the last 24 hours?*

It is a **smoke alarm, not a video validator.** By default it uses only
**filenames + filesystem metadata** (path, size, modified time). It does **not**
read video content, does **not** decode or count frames, and does **not** run
ffprobe on every file. A full run over all 10 cameras takes **~1 second**.

Built in PowerShell to match the recorder stack in this folder
(`rtsp_record.ps1` = 6 Reolink channels, `thermal_record.ps1` = 4 thermal/visual
streams). No Python / extra runtime needed.

## What it checks (fast, from filenames + fs metadata)

| Check | Meaning |
|---|---|
| **gap** | Recording stopped between two files (a real outage). WARN if short, ERROR if > `FailGapSeconds` (120 s). |
| **gap-start / gap-end** | The 24 h window isn't covered to its edge (started late / stopped early). |
| **overlap** | A file starts before the previous one ended (clock/restart glitch). |
| **low-coverage** | Less than `MinCoveragePercent` (99%) of the 24 h is covered. ERROR. |
| **zero-byte** | A finished file with 0 bytes. ERROR. |
| **tiny-file** | A non-boundary finished file under `TinyFileBytes` (1 MB). WARN. |
| **low-bitrate** | A finished file whose **data rate** (bytes/sec) is under 25% of the group median — catches a stalled/near-empty stream. Judged by rate, *not* raw size, so naturally short segments are NOT flagged. WARN. |
| **stale-active** | The "still recording" file stopped growing for > `StaleActiveMinutes` (10) -> recorder is probably down. ERROR. |
| **no-active-file** | No in-progress file at all for a group -> recorder may be down. WARN. |
| **being-written** | The active file, written within the last 10 min — informational, normal. |
| **unparseable / bad-timestamp** | A filename that doesn't fit the scheme, or an impossible/negative duration. ERROR. |

Optional, **only when you ask** (see flags): ffprobe **container metadata**
(duration/readability — never frames), with a per-file timeout:

| Check | Meaning |
|---|---|
| **unreadable** | ffprobe couldn't open the file. ERROR. |
| **metadata_timeout** | ffprobe exceeded `ProbeTimeoutSeconds` (5 s) — skipped, not failed. WARN. |
| **duration-mismatch** | (deep mode) real duration differs from the filename by > 30 s. WARN. |

Each group gets a coverage % and PASS / WARN / FAIL. Overall **exit code**:
`0` = PASS, `1` = warnings only, `2` = errors.

## Folders & grouping (auto-detected)

- Roots scanned: `E:\Reolink_record` and `E:\thermal_record`.
- Each immediate subfolder is one **group** (`CH01`..`CH06`, `108_thermal`,
  `108_visual`, `109_thermal`, `109_visual`). `bin` and `logs` are ignored.
- Filenames parsed:
  - finished:  `<group>_YYYY-MM-DD_HH-MM-SS_to_HH-MM-SS.mp4`
  - recording: `<group>_YYYY-MM-DD_HH-MM-SS.mp4`  (no `_to_` = active file)
- The end time has no date; if earlier than the start it's treated as
  past-midnight (+1 day). Segment seams within `GapToleranceSeconds` (5 s) are
  normal and ignored.

## Reports

Written to `E:\recording_health_reports`:

```
latest_health.md      <- open this; summary table + per-group details
latest_health.csv     <- per-file rows (start/end, filename duration, size, suspect, probe status)
health_YYYYMMDD_HHMMSS.md  / .csv   <- timestamped history
```

The report header always states `check_mode` (`fast_filename_only` or
`deep_ffprobe`) and whether `video_metadata` was skipped. See
`example_health_report.md` / `.csv` in this folder for real samples.

## Install (daily 05:00 task)

From an **Administrator** PowerShell (it self-elevates if you forget):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\setup_daily_health_check.ps1"
```

Registers a SYSTEM task **"Recording Health Check"**, daily at 05:00, and runs it
once immediately so you can confirm `latest_health.md` appears. Use `-At HH:mm`
to change the time (keep it at/after `CheckHour`).

## Run by hand

```powershell
$h = "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\recording_health_check.ps1"

# DEFAULT: fast, filename + filesystem metadata only (~1 s). No ffprobe.
powershell -NoProfile -ExecutionPolicy Bypass -File $h
powershell -NoProfile -ExecutionPolicy Bypass -File $h -DryRun     # print, write nothing (also fast)
powershell -NoProfile -ExecutionPolicy Bypass -File $h -SelfTest   # fake-filename logic test, no disk

# Opt-in video metadata (container only, never frames):
powershell -NoProfile -ExecutionPolicy Bypass -File $h -ProbeSuspicious   # ffprobe ONLY the flagged files
powershell -NoProfile -ExecutionPolicy Bypass -File $h -DeepCheck         # ffprobe all in-window files (slow)

# Knobs:
... -ProbeTimeoutSeconds 8     # per-file ffprobe timeout (default 5) -> metadata_timeout if exceeded
... -MaxProbeFiles 50          # cap on how many files ffprobe touches (default 20)
... -SkipFfprobe               # never run ffprobe, even with -ProbeSuspicious/-DeepCheck
... -RefTime "2026-06-20 05:00:00"   # re-check a past day's window
```

**ffprobe runs only with `-DeepCheck` or `-ProbeSuspicious`** — never by default,
and never on a dry-run unless you pass one of those explicitly. Even then it reads
only container metadata (duration), never decodes frames, and gives up per file
after `ProbeTimeoutSeconds` (marked `metadata_timeout`, then continues).

## Configuration

All thresholds live in the `$Config` block at the top of
`recording_health_check.ps1`: roots, excluded dirs, report path, extensions,
`CheckHour`, gap tolerance / fail-gap, min coverage %, tiny-file bytes,
low-bitrate factor + min duration, stale-active minutes, and the ffprobe
path + duration tolerance.

## Notes

- **Why bitrate, not raw size:** the recorder produces many naturally short
  segments after a restart; those are small but fine. Flagging by *data rate*
  (bytes/sec vs the group median) catches a genuinely starved/corrupt stream
  without drowning the report in false alarms. CH03 is a low-motion scene, so its
  files are small but at a normal-for-it rate — not flagged.
- The active file's true size is read via a shared open handle (so it isn't
  reported as 0 bytes while ffmpeg writes it). The tool never writes to the
  recording folders.
- Small gaps (tens of seconds) across all cameras at once usually mark a
  system-wide event (a reboot or network blip) rather than a single-camera fault.

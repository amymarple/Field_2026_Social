# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Data acquisition + preprocessing for the **Field 2026 Social** neuroscience project (multi-camera behavioral video + LFP recordings of social behavior). It has two distinct halves:

1. **Video capture automation** (`reolink_record/`, `reolink_export/`) — Windows-only PowerShell + AutoHotkey that pull recordings off a **Reolink NVR** (6 channels, `192.168.1.151`). These are operational scripts driven by Windows **scheduled tasks**, not a buildable app.
2. **Preprocessing pipeline** (`preprocessing/`) — Python (`cv2`, `numpy`) for merging cameras and tracking animals. **Currently scaffolding**: the classes are defined but nearly every method body is a `# TODO` / `pass`. Expect to implement, not modify.

There is no build system, package manifest, test suite, or linter config. Don't look for `npm`/`pip install -e`/CI — none exists yet.

## Critical: config and data live off-repo on `D:\`

Secrets (NVR username/password) and all runtime config live in files under `D:\` that are **deliberately not in git**:

- `D:\Reolink_record\recorder.config.psd1` — NVR IP, credentials, channels, paths, `RetentionDays`, `MinFreeGB`. Read by `rtsp_record.ps1` via `Import-PowerShellDataFile`.
- `reolink_export/config.ini` **is** in git but holds machine-specific calibrated screen coordinates (see below).

Recorded video also lives on `D:\` (`D:\Reolink_record\`, `D:\Reolink_export\`, `D:\Reolink_staging\`), never in the repo. **Never** hardcode credentials into the `.ps1`/`.ahk` files — the design keeps `rtsp_record.ps1` secret-free on purpose.

## The two capture approaches (record is current, export is legacy)

**`reolink_record/` — RTSP continuous recorder (use this going forward).** `rtsp_record.ps1` is a supervisor loop: one `ffmpeg` per channel pulls `rtsp://…/Preview_0N_main` over TCP and writes **hourly fragmented-MP4 segments** with `-c copy` (no re-encode). Key invariants if you edit it:
- Segments are **fragmented MP4** (`frag_keyframe+empty_moov`) so the file stays playable if the process is killed. Don't switch to plain MP4.
- The active segment's true size must be read via an **open file handle** (`Get-HandleLen`) — `Get-ChildItem.Length` reports a stale `0` while ffmpeg writes.
- "Newest segment" is chosen by **filename sort**, not `LastWriteTime` (a just-closed file's mtime updates last and would win incorrectly at rollover).
- A **stall watchdog** kills+restarts a stream whose file stops growing (~240 s), because ffmpeg 8 has no usable RTSP read-timeout. A **single-instance mutex** (`ReolinkRtspRecorder`) prevents duplicate supervisors. Retention + a disk guard delete oldest files.
- Finished segments are renamed `CH01_<start>_to_<end>.mp4`; the one still recording keeps only its start timestamp.
- Runs under scheduled task **"Reolink RTSP Recorder"** (at logon). `check_recording.ps1` is a safe read-only health check.

**`reolink_export/` — AutoHotkey GUI automation (legacy, backfill only).** Reolink Client is an Electron app whose buttons aren't real Windows controls, so `reolink_export.ahk` (AutoHotkey **v2**, not v1) drives it by **clicking calibrated screen coordinates** stored in `config.ini`. It runs nightly (task "Reolink Daily Export", 00:01) to export the previous day per channel. Coordinates are machine/display-specific — if clicks miss, the fix is re-calibration (`/calibrate` and `/caldate`), not code changes. **Requires the desktop logged in AND unlocked.** Only kept for manually backfilling days recorded before the RTSP recorder existed.

## Common commands

All PowerShell, run from a Windows session on the capture PC.

```powershell
# Recorder health / are all 6 ffmpeg up?
& "reolink_record\check_recording.ps1"
Get-Process ffmpeg | Measure-Object
Get-Content D:\Reolink_record\logs\recorder.log -Tail 20

# Start/stop the recorder
Start-ScheduledTask -TaskName 'Reolink RTSP Recorder'
Get-Process ffmpeg | Stop-Process -Force   # then kill the supervisor powershell.exe running rtsp_record

# Legacy export: dry-run one channel before trusting a real run
$ahk = "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"
& $ahk "reolink_export\reolink_export.ahk" /channel 1 /dryrun   # abort any run: Ctrl+Alt+Q
& "reolink_export\run_daily_export.ps1"                         # full nightly run
& "reolink_export\check_exports.ps1" -Date 2026-06-14           # per-channel report

# Preprocessing (plain scripts; run directly, no package install)
python preprocessing/computer_vision/animal_tracking.py
python preprocessing/data_merging/merge_cameras.py
```

There are no automated tests. "Testing" a capture change means a `/dryrun` or single-channel run and watching the log/output folder, as described in each subdir's `README.md`.

## Preprocessing pipeline intent

`preprocessing/README.md` describes the target flow: collect (per the `copy_and_storage_protocol.md` in `security_camera/` and `lfp_recording/`) → `data_merging/merge_cameras.py` (`CameraMerger`: temporal alignment across the 6 channels) → `computer_vision/animal_tracking.py` (`AnimalTracker`: position/trajectory extraction, e.g. background subtraction or DeepLabCut/YOLO) → analysis. **Temporal continuity is the hard requirement** the whole project rests on: recordings must be gap-free 24/7 and the merge/tracking code must preserve frame-accurate alignment (see the gap-detection thresholds in `security_camera/copy_and_storage_protocol.md`). A `verify_continuity.py` is referenced in that protocol but does not exist yet.

## When editing

- Each subdirectory has a detailed `README.md` — read the relevant one before changing capture scripts; they document the NVR/UI quirks already worked around.
- Windows environment: paths are `D:\…`, scripts assume PowerShell and Windows scheduled tasks. The Bash tool is available but the operational scripts are PowerShell/AHK.

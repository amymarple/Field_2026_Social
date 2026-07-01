# Daily Recording Continuity Check

## Date

2026-06-25. Change is currently uncommitted.

## Plan

Implemented from
[`implementation_plan/2026-06-25-daily-recording-continuity-check.md`](../implementation_plan/2026-06-25-daily-recording-continuity-check.md).

## What Changed

- Added `reolink_record/check_recording_continuity.ps1`.
  - Audits Reolink hourly MP4 files.
  - Audits thermal/visual hourly MP4 files.
  - Checks newest file growth with an active sample.
  - Checks adjacent file gaps over a configurable lookback window.
  - Checks SmartPSS PC-NVR placeholder write recency when `E:\media` exists.
  - Writes timestamped text and JSON reports.
- Added `reolink_record/install_recording_continuity_check_task_system.ps1`.
  - Installs the checker as a daily SYSTEM scheduled task.
  - Default run time is 00:10.
- Updated `reolink_record/README.md` with manual and scheduled-task commands.
- Added implementation/change log indexes.

## Why

The recording setup needs daily evidence that 24/7 capture is continuous. A
recorder can be running while one channel stalls, a drive path goes missing, or
adjacent hourly files have gaps. This check creates a durable QC report instead
of relying on visual spot checks.

## Verification

Commands run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\check_recording_continuity.ps1" -ActiveSampleSeconds 2 -ReportRoot "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\tmp_recording_qc_test"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\check_recording_continuity.ps1" -LookbackHours 1 -ActiveSampleSeconds 5 -ReportRoot "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\tmp_recording_qc_test" -Quiet
```

Observed behavior:

- Reports were written under `tmp_recording_qc_test`.
- `ffprobe` resolved to `E:\Reolink_record\bin\ffprobe.exe`.
- Reolink channels `CH01` through `CH06` showed active newest-file growth.
- Thermal/visual channels were detected from `E:\thermal_record\thermal.config.psd1`.
- The checker reported several current thermal gaps above the default
  15-second tolerance.
- `E:\media` was not visible during verification, so the SmartPSS placeholder
  check reported that root as missing.

## QC Output

Temporary test reports were written under `tmp_recording_qc_test` during
verification, then removed from the worktree.

Production reports default to:

- `E:\recording_qc\latest_recording_continuity.txt`
- `E:\recording_qc\latest_recording_continuity.json`

## Known Limitations

- SmartPSS PC-NVR placeholder files do not expose per-channel time coverage from
  the filesystem. The checker can confirm recent write activity only.
- Exact SmartPSS playback continuity still requires SmartPSS playback/index
  inspection.
- Very low frame-rate streams may produce warning-level "did not grow during
  sample" messages even when recently touched.

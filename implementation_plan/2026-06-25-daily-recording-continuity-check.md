# Daily Recording Continuity Check

## Goal And Motivation

Create a Windows-side daily check that verifies 24/7 video recording continuity
for the current field recording setup. The check should catch missing files,
time gaps between hourly segment files, stopped streams, and stale SmartPSS
placeholder writes before a long recording loss goes unnoticed.

## Current Problem

The Reolink and thermal RTSP recorders write hourly fragmented MP4 files, but
there is no daily automated audit that compares adjacent file start/end times.
SmartPSS PC-NVR writes fixed-size placeholder blocks under `E:\media`, where
file size does not grow, so ordinary size checks are misleading. A manual check
confirmed that SmartPSS updates placeholder timestamps while recording, but this
is not yet monitored.

## Why This Is Needed Now

The recording system is intended to run continuously. A camera disconnect,
recorder stall, scheduled task failure, drive issue, or PC crash could create a
silent gap. Daily continuity reports provide a reproducible QC trail for field
data collection.

## Relevant State

- Worktree contains unrelated local changes; this task will leave them alone.
- Existing recorder scripts live under `reolink_record/`.
- Current observed recording roots:
  - `E:\Reolink_record`
  - `E:\thermal_record`
  - `E:\media` for SmartPSS placeholder blocks

## Affected Files

- Add `reolink_record/check_recording_continuity.ps1`.
- Add `reolink_record/install_recording_continuity_check_task_system.ps1`.
- Update `reolink_record/README.md`.
- Add or update implementation/change log indexes.

## Inputs And Outputs

Inputs:

- Hourly MP4 files under `E:\Reolink_record\CH01` through `CH06`.
- Hourly MP4 files under `E:\thermal_record\108` and `109`.
- SmartPSS placeholder files under `E:\media`.

Outputs:

- Timestamped text and JSON reports under `E:\recording_qc` by default.
- `latest_recording_continuity.txt` and `.json` summaries.
- Scheduled task result code: `0` OK, `1` warning-only, `2` error/gap/stale
  recording.

## Timestamp And Synchronization Assumptions

- MP4 filenames encode local computer wall-clock start time as
  `YYYY-MM-DD_HH-MM-SS`.
- Closed segments may include `_to_HH-MM-SS`; that end time is interpreted on
  the start date, rolling forward one day if needed.
- For files without `_to_`, duration is read with `ffprobe` when available and
  used only as a fallback for continuity estimation.
- A configurable small gap tolerance is allowed because segment close/open times
  can differ by a few seconds.
- SmartPSS placeholder blocks do not expose per-channel time ranges from the
  filesystem. The check can only verify that the placeholder store has recent
  write activity.

## Expected Behavior

- Audit each configured channel over a recent lookback window.
- Report gaps between adjacent segment files above `MaxGapSeconds`.
- Report missing channel folders or channels with no files.
- Sample newest files to confirm active growth.
- Report SmartPSS placeholder write staleness.
- Save durable QC reports without modifying raw recording files.

## Verification

- Run the checker against current recording folders.
- Confirm it produces a report.
- Confirm it detects current live growth for Reolink/thermal recordings where
  available.
- Confirm it reports SmartPSS placeholder recency rather than claiming exact
  per-channel SmartPSS continuity.

## Non-Goals

- Do not modify recorder behavior.
- Do not parse SmartPSS proprietary placeholder/index internals.
- Do not delete, rename, transcode, or rewrite recording files.
- Do not add email/SMS notification in this change.

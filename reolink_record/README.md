# Reolink RTSP Continuous Recorder

Records all six NVR channels **continuously, in real time**, straight from the
cameras' RTSP streams with ffmpeg (`-c copy`, no re-encode — bit-identical to the
NVR's "Clear" recording). This replaces the fragile GUI export automation in
`reolink_export/` for everything going forward.

Output: one **fragmented MP4 per hour, per channel**. A finished file is renamed to
show its **start and end time**; the file currently being written keeps just its
start until it closes:

```
D:\Reolink_record\CH01\CH01_2026-06-15_14-00-00_to_15-00-00.mp4   <- finished (start->end)
D:\Reolink_record\CH01\CH01_2026-06-15_15-00-00.mp4               <- still recording
```

Why this design beats clicking through Reolink Client: real-time (no overnight
catch-up), full quality, no UI automation to break, and the NVR keeps its own
copy as backup.

## Files

| File | Purpose |
|---|---|
| `rtsp_record.ps1` | Supervisor: launches/keeps-alive one ffmpeg per channel, retention, disk guard. **No secrets.** |
| `check_recording_continuity.ps1` | Read-only QC audit for time gaps between recording files and stale SmartPSS placeholder writes. |
| `install_recording_continuity_check_task_system.ps1` | Installs the continuity audit as a daily SYSTEM scheduled task. |
| `D:\Reolink_record\recorder.config.psd1` | NVR IP, **credentials**, channels, paths, retention. **Lives on D:, not in git.** |
| `D:\Reolink_record\bin\ffmpeg.exe` | Pinned ffmpeg copy (version-stable). |
| `D:\Reolink_record\logs\` | `recorder.log` (supervisor) + `CH0x.ffmpeg.log` (per-stream stderr). |

## How it works

- **One ffmpeg per channel** pulls `rtsp://<user>:<pass>@<nvr>:554/Preview_0N_main`
  over TCP and writes hourly segments aligned to the clock.
- **Fragmented MP4** (`movflags=+frag_keyframe+empty_moov+default_base_moof`,
  `frag_duration=2s`): the file grows in real time and stays **playable even if
  the process is killed** (power loss / reboot only risks the final ~2 s).
- **Supervisor** restarts any ffmpeg that exits, and a **stall watchdog** kills +
  restarts a stream whose file stops growing for 180 s (handles a silently hung
  connection — RTSP has no usable read-timeout flag in ffmpeg 8).
  - Size is read via an **open file handle**, because `Get-ChildItem` reports a
    stale 0 for a file ffmpeg is still writing.
- **Single-instance mutex** so the logon task can't spawn a duplicate.
- **Retention** deletes recordings older than `RetentionDays`; the **disk guard**
  deletes the oldest files if free space drops below `MinFreeGB`.

## Running / autostart

It runs under the scheduled task **"Reolink RTSP Recorder"** (trigger: **At log
on**, hidden, auto-restart on failure, no time limit). Because RTSP capture needs
no desktop, you do **not** need the screen unlocked — only **logged in** (the task
starts on logon and keeps running). It also survives reboots once you log back in.

```powershell
# status: are all 6 ffmpeg up?
Get-Process ffmpeg | Measure-Object

# tail the supervisor log
Get-Content D:\Reolink_record\logs\recorder.log -Tail 20

# daily continuity/gap QC, manual run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\check_recording_continuity.ps1"

# install the daily 00:10 SYSTEM continuity task; run from Administrator PowerShell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\install_recording_continuity_check_task_system.ps1"

# start / stop the whole thing
Start-ScheduledTask  -TaskName 'Reolink RTSP Recorder'
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
  Where-Object { $_.CommandLine -like '*rtsp_record*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-Process ffmpeg | Stop-Process -Force
```

## Daily continuity QC

`check_recording_continuity.ps1` is the daily 24/7 recording audit. It checks:

- Reolink RTSP hourly MP4 files under the configured `E:\Reolink_record` root.
- EmpireTech thermal/visual hourly MP4 files under the configured
  `E:\thermal_record` root.
- SmartPSS PC-NVR numbered placeholder files under `E:\media`, when present.

Reports are written to `E:\recording_qc` by default:

```
E:\recording_qc\latest_recording_continuity.txt
E:\recording_qc\latest_recording_continuity.json
E:\recording_qc\recording_continuity_YYYYMMDD_HHMMSS.txt
E:\recording_qc\recording_continuity_YYYYMMDD_HHMMSS.json
```

Exit codes:

- `0`: no errors or warnings.
- `1`: warning only, such as a very low-rate stream not growing during the short
  sample but still recently touched.
- `2`: error, such as a gap between files, missing channel folder, no files, or a
  stale active recording.

Important SmartPSS limitation: the `E:\media` placeholder blocks do not expose
per-channel recording start/end times through the filesystem. The QC script can
confirm recent placeholder write activity, but exact SmartPSS playback
continuity still has to be checked through SmartPSS playback/index tools.

## Storage reality (important)

At full resolution the six channels total **~400+ GB/day**. On the 4 TB `D:` drive
that is only ~7 days, so:
- `RetentionDays = 6` and `MinFreeGB = 250` (in `recorder.config.psd1`).
- The disk guard auto-deletes oldest files before the disk fills.
- If you need longer history, add a bigger/dedicated drive, drop channels, or
  record some channels at sub-stream (`Preview_0N_sub`).

## Setup notes (already done on this PC)

1. ffmpeg installed (winget `Gyan.FFmpeg`), copied to `D:\Reolink_record\bin`.
2. NVR RTSP enabled (Reolink → Network → Advanced → Port Settings → **RTSP** on, port 554).
3. `recorder.config.psd1` created on `D:` with NVR IP `192.168.1.151`, user/pass,
   channels 1–6.
4. Scheduled task registered (above).

To change settings, edit `D:\Reolink_record\recorder.config.psd1` and restart the task.

## Backfilling already-recorded days

RTSP records from setup-time forward; it can't fetch the past. For days already on
the NVR (before recording started), export them **manually** in Reolink Client
(Playback → Download → Choose All → Download) — that works fine by hand. The
`reolink_export/` automation is retained only for that occasional manual backfill.

# Reolink Daily Export Automation (AutoHotkey v2)

Automates the Reolink Client **Playback → Download** workflow so the PC exports
the NVR's recordings once per night, for 6 channels, into dated per-channel
folders:

```
D:\Reolink_export\YYYY-MM-DD\CH01\
D:\Reolink_export\YYYY-MM-DD\CH02\
...
D:\Reolink_export\YYYY-MM-DD\CH06\
```

A scheduled task runs it at **00:01**, so each night it grabs the full previous
day. In the morning you check one log to confirm everything downloaded.

This was built and calibrated for a specific rig (Reolink NVR, 12 channels,
exporting channels 1–6) for a field season. The approach is general, but the
calibrated coordinates in `config.ini` are specific to that machine/display.

---

## How it works (and why it's coordinate-based)

Reolink Client is an **Electron app** — its buttons are not standard Windows
controls, so `ControlClick`/UI Automation can't see them. The only reliable way
to drive it is **clicking known screen positions**, which you capture once with
the built-in calibration modes. The script then:

- Forces the Reolink window onto a fixed monitor and maximizes it, so every
  click lands consistently (see *Multiple monitors* below).
- Navigates Playback, picks the date, selects each channel, opens the Download
  window, clicks **Choose All**, then **Download**.
- Watches Reolink's download folder (**staging**), waits for files to finish,
  then **moves** them into the dated `CH0x` folder.

### UI quirks this rig has (already handled)

| Element | Reality | How it's handled |
|---|---|---|
| Channel | a **dropdown** "Channel x/12" | open dropdown, click the channel item |
| Type / Stream | **dropdowns** (File / Clear) | left at defaults (`SetTypeStream=0`); set once by hand |
| Start/End time | **dropdowns** (HH/MM/SS) | not touched — **full-day mode** uses the defaults |
| Date | a **calendar popup**, no keyboard nav | **grid geometry** computes the right day cell |

Because a full day is only ~27 files per channel, **full-day mode** is the
default (one Download per channel), which avoids ever operating the time
dropdowns.

---

## Files

| File | Purpose |
|---|---|
| `reolink_export.ahk` | Main automation + calibration (AutoHotkey v2). |
| `config.ini` | All settings and calibrated coordinates. |
| `run_daily_export.ps1` | Master script: export → check → cleanup. Scheduled at 00:01. |
| `check_exports.ps1` | Per-channel file count/size report; flags empties. |
| `README.md` | This document. |

---

## One-time setup

### 1. Install AutoHotkey v2
Download AutoHotkey **v2** from <https://www.autohotkey.com/> (v1 syntax is not
compatible). Default exe: `C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe`.

### 2. Give Reolink its own download folder
The script must move files out of Reolink's download folder, so that folder must
be **separate** from the export destination.
- In Reolink: **Settings → Download Settings → Download Path → Browse →**
  set it to `D:\Reolink_staging`.
- This must match `[Monitor] StagingDir` in `config.ini` (already set).
- Leave **Temporary Folder** as-is.

### 3. Edit `config.ini`
At minimum confirm:
- `[General] ReolinkExe` — path to `Reolink.exe`.
- `[General] ExportRoot` — final destination (`D:\Reolink_export`).
- `[Monitor] StagingDir` — `D:\Reolink_staging` (Reolink's Download Path).
- `[Channels] CH01..CH06` — friendly names (logs/reports only).

### 4. Calibrate the UI  (`/calibrate`)
Open Reolink on the **Playback** page, then run:
```powershell
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" `
  "<folder>\reolink_export.ahk" /calibrate
```
A tooltip follows the mouse. For each prompt: hover the element, **F8** capture /
**F9** skip / **Esc** cancel. Capture: Playback tab, date field, download icon,
the channel **dropdown** + each channel item (open the dropdown first), and the
Download-window items (Choose All, Download, Close). Skip Type/Stream/time.

### 5. Calibrate the calendar  (`/caldate`)
The date is a calendar popup, so it needs grid geometry — captured **once**, valid
for every month:
```powershell
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" `
  "<folder>\reolink_export.ahk" /caldate
```
Open the calendar, then capture the **day 1**, **first Saturday**, and **last
day** cells, plus the **previous-month arrow**. The script derives `OriginX/Y`,
`ColPitch`, `RowPitch` and writes them to `[Calendar]`. It then computes the
correct cell for any date itself (no per-month calibration).

> **Re-calibrate** only if the Reolink window size changes — i.e. a different
> monitor resolution or Windows display-scaling change, or a Reolink UI update.

---

## Testing before trusting it overnight

```powershell
$ahk    = "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"
$script = "<folder>\reolink_export.ahk"
```

1. **Dry run, one channel** (goes through the motions, never clicks Download):
   ```powershell
   & $ahk $script /channel 1 /dryrun
   ```
   Watch the date land on yesterday and Choose All get checked.
2. **Real, one channel** (confirms staging → move):
   ```powershell
   & $ahk $script /channel 1
   ```
   Files should appear in `D:\Reolink_staging`, then move to
   `D:\Reolink_export\<yesterday>\CH01\`.
3. **Full run** (all 6 channels) — usually just let the schedule do it:
   ```powershell
   & "<folder>\run_daily_export.ps1"
   ```

**Abort any run:** `Ctrl + Alt + Q`. Don't move the mouse while it runs.

CLI flags override `config.ini`: `/dryrun`, `/channel N`, `/hour H`,
`/fullday`, `/hourly`, `/calibrate`, `/caldate`.

---

## Daily automation

`run_daily_export.ps1` runs the export (all channels, full day, **yesterday**),
then `check_exports.ps1`, then deletes export folders older than `-RetentionDays`
(default **14**), and appends a one-line summary to
`D:\Reolink_export\daily_runs.log`.

The scheduled task (created via PowerShell):
```powershell
$ps1 = "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_export\run_daily_export.ps1"
$action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ps1)
$trigger   = New-ScheduledTaskTrigger -Daily -At ([datetime]'00:01')
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName 'Reolink Daily Export' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
```

Manage it:
```powershell
Start-ScheduledTask      -TaskName 'Reolink Daily Export'   # run now
Disable-ScheduledTask    -TaskName 'Reolink Daily Export'   # pause
Enable-ScheduledTask     -TaskName 'Reolink Daily Export'   # resume
Unregister-ScheduledTask -TaskName 'Reolink Daily Export' -Confirm:$false  # remove
```

> **The PC must be logged in and UNLOCKED at 00:01.** GUI automation needs a live
> desktop — a locked screen or screensaver will break the clicks. Disable the
> lock screen / screensaver, or stay logged in.

### Change retention
Edit the schedule's argument to pass e.g. `-RetentionDays 30`, or run manually:
```powershell
& "<folder>\run_daily_export.ps1" -RetentionDays 30
```

---

## Morning check

- **Quick:** open `D:\Reolink_export\daily_runs.log` — one line per run, ending in
  `CHECK: OK` or `CHECK: WARNING`.
- **Detail:** `D:\Reolink_export\<date>\check_report.txt` (per-channel counts/size)
  and `export_log.txt` (one `RESULT` line per channel: downloaded?, file count,
  size, errors).
- **On demand:** `& "<folder>\check_exports.ps1" -Date 2026-06-14`

---

## Multiple monitors

Clicks use window-relative coordinates, valid only for the maximized size on a
specific monitor. The script therefore moves Reolink onto a fixed monitor before
acting, controlled by `[Window] ForceMonitor`:
- `primary` (default) — the main display.
- `current` — leave it wherever it is.
- `1`, `2`, … — a specific monitor number.

Calibrate and run with the **same** value. (Both displays here are 1920×1080, so
either works, but `primary` keeps it deterministic.)

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `WARN: missing coordinate` | Not calibrated. Run `/calibrate` (and `/caldate`). |
| Clicks land in the wrong place | Wrong monitor/resolution/scaling, or window not maximized. Confirm `ForceMonitor`, then re-`/calibrate`. |
| Files download but don't sort into `CH0x` | Reolink Download Path ≠ `StagingDir`. Make both `D:\Reolink_staging`. |
| Every channel logs `downloaded=no` | Empty day, or Choose All / Download coords wrong. Test with `/dryrun`. |
| `StagingDir missing/invalid` | Fix `[Monitor] StagingDir` / create the folder. |
| Date lands on the wrong day | Re-run `/caldate`; verify `[Calendar]` geometry and the previous-month arrow. |
| Calendar opens on the wrong month | The script assumes Reolink opens on the current month. If yours remembers a different month, tell me. |
| Download "never finishes" | Increase `[Waits] DownloadTimeoutMs`. It times out and moves whatever completed. |
| Task ran but nothing happened | Screen was locked, or PC asleep/off. Keep it logged in + unlocked; `-WakeToRun` is set but the session must be interactive. |
| Reolink updated and moved buttons | Re-run `/calibrate` (and `/caldate` if the calendar changed). |

### When the Reolink UI changes
1. Open Reolink and reproduce one manual export to see the new layout.
2. Re-run `/calibrate`; re-run `/caldate` if the calendar moved.
3. Re-test with `/channel 1 /dryrun`, then a real single-channel run.

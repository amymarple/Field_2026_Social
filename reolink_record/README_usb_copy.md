# Copy a day's recordings to USB (for the lab hand-off)

`copy_day_to_usb.ps1` copies one day of footage from **all** recorders to a USB
drive so it can be carried back to the lab. It is built to be **idiot-proof for a
coworker**: one command, clear PASS/FAIL summary, and it **physically cannot
modify the originals**.

## *** COPY ONLY — the source is never touched ***

The script only ever **reads** from `E:\Reolink_record` / `E:\thermal_record` and
**writes** to the USB drive. It never deletes, moves, renames, or edits a source
file. This is enforced in code:

- it uses `[System.IO.File]::Copy` (copy), never move/delete/overwrite-source;
- every write path is asserted to live **under the USB day folder** or the script
  aborts;
- it **refuses to run** if the destination is on the same drive as the recordings.

So even if your coworker fumbles the command, the recordings on `E:` are safe.

## What your coworker types

Plug in the USB drive, note its letter (say `F:`), open PowerShell, and run:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\copy_day_to_usb.ps1" -Usb F:
```

That copies **yesterday** (the last complete day) to `F:\<yesterday>\`, e.g.
`F:\2026-06-26\CH01\...`, with one subfolder per camera, then prints a summary and
writes `copy_report.txt` + `copy_manifest.csv` into that folder.

Read the last line:

- `>>> COPY COMPLETE - all files copied and all 24 hours present <<<` → done, unplug.
- `>>> COPY INCOMPLETE - some files FAILED ... <<<` → just run the **same command
  again**; it skips what's already there and retries only the failures.
- `>>> COPY OK, but some hours are MISSING ... <<<` → the copy is fine, but the
  recorder had a gap that day (nothing the USB copy can fix).

## Options

| Flag | Effect |
|---|---|
| `-Usb F:` | **Required.** USB drive or folder. Refuses if it's the recordings' drive. |
| `-Date 2026-06-20` | Copy a specific day instead of yesterday (`yyyy-MM-dd`). |
| `-DryRun` | Show exactly what would be copied + the completeness check; copy nothing. |
| `-Hash` | Verify every copy with a full SHA-256 compare (slow but definitive). Default is a fast size compare. |
| `-StopOnError` | Halt on the first failure instead of continuing. |
| `-IncludeActive` | Also copy the still-recording file (incomplete; only matters when copying *today*). |

Examples:

```powershell
# preview yesterday without copying
... copy_day_to_usb.ps1 -Usb F: -DryRun

# copy a specific earlier day, with full hash verification
... copy_day_to_usb.ps1 -Usb F: -Date 2026-06-20 -Hash
```

## How it decides things

- **Which files:** every `.mp4` whose filename contains the chosen date, in each
  camera subfolder of `E:\Reolink_record` and `E:\thermal_record` (`bin`/`logs`
  ignored). The still-recording file (no `_to_` in its name) is skipped unless
  `-IncludeActive`.
- **Where:** `<USB>\<date>\<camera>\<original filename>` — structure preserved,
  names unchanged.
- **Completeness:** for each camera it checks that **all 24 hours are covered** by
  the files' start/end times (so a segment spanning an hour after a restart still
  counts). Missing hours are listed per camera.
- **Verify:** after each copy it confirms the destination size matches the source
  (or SHA-256 with `-Hash`); a mismatch is a failure and is reported, never hidden.
- **Resumable:** files already present and matching are skipped, so re-running
  after an interruption only copies what's missing.
- **Space:** it checks the USB has room for the whole day before starting.

## Heads-up on size

A full day of all 10 streams is **~450 GB** (the six Reolink channels dominate;
the thermal sensors are tiny). Make sure the USB drive is big enough — the script
will refuse and copy nothing if there isn't room.

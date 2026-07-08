# Recording-system reference — `Field_2026_Social_Recording`

The capture/recording scripts for this project live in a **separate cloned repo** (not this one):

- **Path:** `C:\Users\Cornell\Documents\GitHub\Field_2026_Social_Recording`
- **Remote:** https://github.com/amymarple/Field_2026_Social_Recording (`amymarple`)
- **What it is:** the standalone PowerShell **recording / capture tooling** — the full source of this
  repo's in-tree `reolink_record/` subsystem. Runs 24/7 on the **FIELD PC**: continuous real-time RTSP
  capture (`ffmpeg -c copy`, no re-encode) → one fragmented MP4 per hour per channel, plus SYSTEM
  scheduled tasks, QC audits, disk/USB data-lifecycle, and Slack alerts.
- **Reference only — READ, do not change** anything in that repo from here.

## Important docs (Markdown, repo root)
| Doc | Covers |
|---|---|
| `README.md` | **Main.** Reolink RTSP continuous recorder (6 NVR channels): design, how it works (per-channel ffmpeg, fragmented MP4, supervisor + stall watchdog, single-instance mutex, retention + disk guard), autostart task, **daily continuity QC**, storage reality (~400+ GB/day, 6-day retention), backfill note |
| `README_health_check.md` | Daily recording health check |
| `README_overexposure.md` | Overexposure / near-black-frame QC with Slack alerts |
| `README_disk_space.md` | Disk-space Slack warning (drive-full thresholds) |
| `README_failover.md` | Storage failover recorder (E: → D:) |
| `README_usb_copy.md` | Copy a day's recordings to USB (lab hand-off) |
| `README_delete.md` | Delete one day's recordings (targeted, safe cleanup) |
| `example_health_report.md` / `.csv` | Example health-check report output |

## Key scripts
| Script | Purpose |
|---|---|
| `rtsp_record.ps1` | **PRIMARY** — continuous RTSP recorder for the 6 Reolink NVR channels; supervisor + stall watchdog + retention/disk guard. No secrets in it. |
| `thermal_record.ps1` | Continuous recorder for the **EmpireTech THERMAL** cameras (Dahua-format, direct IP) |
| **`extra_cam_record.ps1`** | Continuous recorder for **EXTRA direct-IP cameras (Dahua-format)** — **this is the CH07/CH08 interior in-house pinhole-camera recorder** (EmpireTech = Dahua OEM), matching the 2026-07-07 camera addition |
| `failover_recorder.ps1` | Dedicated **storage-failover** recorder (E: → D: when the primary drive fills/fails) |
| `copy_to_analysis.ps1` | Copy **everything** needed for offline analysis (Reolink video + thermal + WISER) → the analysis PC |
| `copy_day_to_usb.ps1` → `delete_day.ps1` | Data lifecycle: hand **one** day to USB, **then** delete after confirming it copied. `delete_day.ps1` is the *only* remover (requires `-Date`, no "delete all"). |
| QC: `check_recording_continuity.ps1`, `recording_health_check.ps1`, `recording_alive_check.ps1`, `overexposure_check.ps1`, `disk_space_check.ps1` | Continuity/gap audit · daily health smoke · near-real-time "RECORDING STOPPED" Slack alert · exposure/black-frame alert · disk-space alert |
| `install_*_task_system.ps1`, `setup_daily_health_check.ps1` | Register each recorder/QC job as a SYSTEM scheduled task (incl. `install_extra_cam_task_system.ps1`, `install_thermal_task_system.ps1`) |

## Relationship to this repo
- This repo's `reolink_record/` subsystem is the in-tree copy/subset; the **recording repo is the
  standalone source of truth for capture**. Secrets/config live in `D:\Reolink_record\recorder.config.psd1`
  (NOT in git); pinned ffmpeg in `D:\Reolink_record\bin\`.
- **CH07/CH08** (interior in-house EmpireTech pinhole cams added 2026-07-07 — registered here in
  `preprocessing/computer_vision/configs/field_layout.json` as `view: interior_in_house`, glass-free) are
  captured by **`extra_cam_record.ps1`** + `install_extra_cam_task_system.ps1` in the recording repo.
- Closed hourly files are `..._<start>_to_<end>.mp4`; the open in-progress hour is `..._<start>.mp4`
  (never read the open file). This is the capture contract every analysis in *this* repo depends on.

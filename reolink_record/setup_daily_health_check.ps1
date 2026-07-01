<#
.SYNOPSIS
    Run ONCE from an elevated (Administrator) PowerShell. Registers the daily
    recording health check as a SYSTEM scheduled task that runs every day at
    05:00 (after the previous 24 h of footage is complete).

    The check is read-only; it never touches the recordings. It writes reports
    to E:\recording_health_reports (latest_health.md / .csv + timestamped copies).

.PARAMETER At
    Time of day to run, "HH:mm". Default 05:00. Must match (or be after) the
    CheckHour in recording_health_check.ps1 so the full previous day is present.

.NOTES
    Self-elevates via UAC if not already Administrator.
#>

param([string]$At = '05:00')

$ErrorActionPreference = 'Continue'
$script   = 'C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\recording_health_check.ps1'
$taskName = 'Recording Health Check'

# self-elevate
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
    Write-Host "Not elevated - relaunching as Administrator. Click YES on the UAC prompt..." -ForegroundColor Yellow
    try { Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"",'-At',$At }
    catch { Write-Host "UAC declined/failed: $($_.Exception.Message)" -ForegroundColor Red }
    return
}
Write-Host "Elevated: OK" -ForegroundColor Green

if (-not (Test-Path $script)) { Write-Error "Health-check script not found: $script"; return }

$hh,$mm = $At.Split(':')
$runAt  = (Get-Date).Date.AddHours([int]$hh).AddMinutes([int]$mm)

Write-Host "=== Installing daily SYSTEM health-check task at $At ===" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $script)
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
# if the PC was off at 05:00, run as soon as it's available again
$settings.ExecutionTimeLimit = 'PT1H'
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName
Write-Host ("`nTask: {0}  State={1}  RunAs={2}  NextRun={3}" -f $t.TaskName, $t.State, $t.Principal.UserId, (Get-ScheduledTaskInfo -TaskName $taskName).NextRunTime)

Write-Host "`nRunning it once now to prove it works..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $taskName
Write-Host "Done. Check E:\recording_health_reports\latest_health.md in a minute." -ForegroundColor Green
Write-Host "To run by hand anytime:" -ForegroundColor DarkGray
Write-Host ('  powershell -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $script) -ForegroundColor DarkGray

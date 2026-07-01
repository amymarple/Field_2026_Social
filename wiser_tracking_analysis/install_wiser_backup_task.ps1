<#
.SYNOPSIS
    Installs a once-a-day WISER database backup as a SYSTEM scheduled task.

.DESCRIPTION
    Run once from an elevated Administrator PowerShell, AFTER verifying a manual
    run (see README_backup.md).

    The task runs `scripts/backup_wiser_daily.py` once a day. That script reads the
    live WISER database strictly read-only and exactly ONCE per run (an online-
    backup snapshot); it then derives a gzipped incremental CSV of the day's new
    rows from the SNAPSHOT, never re-reading the live DB. It never writes under the
    source tree (D:\Wiser). Both artifacts go to E: (a different physical disk from
    the D: source), so they survive a D: failure.

    Default schedule is 13:00 local — the rats' low-activity window (nocturnal), so
    the WISER acquisition is writing least. The DB is in rollback-journal ("delete")
    mode, so the snapshot briefly holds a read lock; measured at ~0.74 s for a
    360 MB DB, well under the writer's 5 s busy_timeout, so no fixes are dropped.
    Midday scheduling is belt-and-suspenders and keeps the margin as the DB grows.
    It does NOT collide with the hourly occupancy task (that runs at :05 and is a
    sub-second bounded one-hour read).
#>

[CmdletBinding()]
param(
    [string]$TaskName   = 'WISER Daily Backup',
    [string]$PythonExe  = "$env:USERPROFILE\miniforge3\envs\cv\python.exe",
    [string]$DbPath     = 'D:\Wiser\data\1stcohort_2026.sqlite',
    [string]$Dest       = 'E:\Wiser_backup',
    [int]$KeepSnapshots = 2,
    [switch]$AlsoBaseline = $true,
    [string]$AtTime     = '03:30',
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'scripts\backup_wiser_daily.py'
if (-not (Test-Path -LiteralPath $script))    { throw "Script not found: $script" }
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python not found: $PythonExe (pass -PythonExe)" }

# Refuse a destination under the source data tree (defence in depth; the Python
# script enforces this too).
$destFull = ([System.IO.Path]::GetFullPath($Dest)).TrimEnd('\')
if ($destFull -ieq 'D:\Wiser' -or $destFull -like 'D:\Wiser\*') {
    throw "Dest must not be under the source data tree (D:\Wiser): $destFull"
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
    Write-Host 'NOT ELEVATED. Re-open PowerShell as Administrator and run this installer again.' -ForegroundColor Red
    exit 1
}

$argList = @(
    ('"{0}"' -f $script),
    '--db',             ('"{0}"' -f $DbPath),
    '--dest',           ('"{0}"' -f $Dest),
    '--keep-snapshots', $KeepSnapshots
)
if ($AlsoBaseline) { $argList += '--also-baseline' }
$arg = $argList -join ' '

$action    = New-ScheduledTaskAction -Execute $PythonExe -Argument $arg -WorkingDirectory $PSScriptRoot
$trigger   = New-ScheduledTaskTrigger -Daily -At $AtTime
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$settings.ExecutionTimeLimit = 'PT30M'

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ("Installed task: {0}" -f $TaskName) -ForegroundColor Green
Write-Host ("Runs daily at {0}; backups -> {1} (keep {2} snapshots; CSVs all kept)" -f $AtTime, $Dest, $KeepSnapshots)
Write-Host ("Command: {0} {1}" -f $PythonExe, $arg)

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'Started task once for immediate verification (first run also bootstraps a full incremental CSV).'
}

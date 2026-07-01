<#
.SYNOPSIS
    Installs an hourly WISER occupancy-map generator as a SYSTEM scheduled task.

.DESCRIPTION
    Run once from an elevated Administrator PowerShell, AFTER you have manually
    verified a single-hour run (see README_occupancy.md / the script's --hour mode).

    The task runs `scripts/plot_hourly_occupancy.py --backfill` every hour. That
    script reads the live WISER database strictly read-only (mode=ro +
    PRAGMA query_only=ON), plots only hours that are complete per the DB's own
    MAX(timestamp), and skips hours already plotted. It never writes to the
    source data tree. Running a few minutes past the hour is intentional so the
    just-finished hour has settled; exact timing does not matter because the
    completed-hour test is data-driven, not wall-clock driven.
#>

[CmdletBinding()]
param(
    [string]$TaskName  = 'WISER Hourly Occupancy',
    [string]$PythonExe = "$env:USERPROFILE\miniforge3\envs\cv\python.exe",
    [string]$DbPath    = 'D:\Wiser\data\1stcohort_2026.sqlite',
    [string]$OutputDir = 'D:\Wiser_plot',
    [ValidateSet('local','utc')]
    [string]$Tz        = 'local',
    [ValidateSet('scatter','occupancy')]
    [string]$Style     = 'scatter',
    [double]$BinInches = 4.0,
    [int]$AtMinute     = 5,
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'scripts\plot_hourly_occupancy.py'
if (-not (Test-Path -LiteralPath $script))    { throw "Script not found: $script" }
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python not found: $PythonExe (pass -PythonExe)" }

# Refuse to point outputs at the source data tree (defence in depth; the Python
# script enforces this too). Note the trailing separator so a sibling like
# D:\Wiser_plot is allowed while D:\Wiser and D:\Wiser\... are blocked.
$outFull = ([System.IO.Path]::GetFullPath($OutputDir)).TrimEnd('\')
if ($outFull -ieq 'D:\Wiser' -or $outFull -like 'D:\Wiser\*') {
    throw "OutputDir must not be under the source data tree (D:\Wiser): $outFull"
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
    Write-Host 'NOT ELEVATED. Re-open PowerShell as Administrator and run this installer again.' -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$arg = @(
    ('"{0}"' -f $script),
    '--db',         ('"{0}"' -f $DbPath),
    '--output',     ('"{0}"' -f $OutputDir),
    '--tz',         $Tz,
    '--style',      $Style,
    '--bin-inches', $BinInches,
    '--backfill'
) -join ' '

$action  = New-ScheduledTaskAction -Execute $PythonExe -Argument $arg -WorkingDirectory $PSScriptRoot
$at      = (Get-Date).Date.AddMinutes($AtMinute)        # today HH:05-ish anchor
$trigger = New-ScheduledTaskTrigger -Once -At $at `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$settings.ExecutionTimeLimit = 'PT20M'

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ("Installed task: {0}" -f $TaskName) -ForegroundColor Green
Write-Host ("Runs hourly at minute {0}; outputs -> {1}" -f $AtMinute, $OutputDir)
Write-Host ("Command: {0} {1}" -f $PythonExe, $arg)

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'Started task once for immediate verification.'
}

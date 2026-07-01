<#
.SYNOPSIS
    Installs the daily field recording continuity checker as a SYSTEM task.

.DESCRIPTION
    Run once from an elevated Administrator PowerShell. The task runs every day
    and writes reports to E:\recording_qc by default.
#>

[CmdletBinding()]
param(
    [string]$TaskName = 'Field Recording Continuity Check',
    [string]$At = '00:10',
    [string]$ReportRoot = 'E:\recording_qc',
    [int]$LookbackHours = 26,
    [int]$MaxGapSeconds = 15,
    [int]$MaxActiveStaleMinutes = 10,
    [int]$ActiveSampleSeconds = 15,
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'check_recording_continuity.ps1'
if (-not (Test-Path -LiteralPath $script)) {
    throw "Checker script not found: $script"
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
    Write-Host 'NOT ELEVATED. Re-open PowerShell as Administrator and run this installer again.' -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null

$arg = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $script),
    '-LookbackHours', $LookbackHours,
    '-MaxGapSeconds', $MaxGapSeconds,
    '-MaxActiveStaleMinutes', $MaxActiveStaleMinutes,
    '-ActiveSampleSeconds', $ActiveSampleSeconds,
    '-ReportRoot', ('"{0}"' -f $ReportRoot),
    '-Quiet'
) -join ' '

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$settings.ExecutionTimeLimit = 'PT30M'

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ("Installed task: {0}" -f $TaskName) -ForegroundColor Green
Write-Host ("Daily time: {0}" -f $At)
Write-Host ("Reports: {0}" -f $ReportRoot)
Write-Host ("Command: powershell.exe {0}" -f $arg)

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'Started task once for immediate verification.'
}

<#
.SYNOPSIS
    Check Reolink export output for a given day.

.DESCRIPTION
    Lists per-channel file counts and total size, flags empty channels,
    and writes a check_report.txt into the day's folder.

.EXAMPLE
    .\check_exports.ps1
        Checks yesterday under D:\Reolink_export.

.EXAMPLE
    .\check_exports.ps1 -Date 2026-06-13 -Root D:\Reolink_export -Channels 6
#>

param(
    [string]$Date     = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd'),
    [string]$Root     = 'D:\Reolink_export',
    [int]   $Channels = 6,
    # File extensions counted as finished recordings.
    [string[]]$Include = @('*.mp4', '*.avi', '*.mkv')
)

$dayDir = Join-Path $Root $Date
if (-not (Test-Path $dayDir)) {
    Write-Host "No export folder found: $dayDir" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Reolink export check - $Date" -ForegroundColor Cyan
Write-Host "Folder: $dayDir"
Write-Host ("-" * 60)

$rows        = @()
$grandFiles  = 0
$grandBytes  = 0L
$emptyChans  = @()

for ($i = 1; $i -le $Channels; $i++) {
    $ch    = 'CH{0:D2}' -f $i
    $chDir = Join-Path $dayDir $ch

    if (-not (Test-Path $chDir)) {
        $rows += [pscustomobject]@{ Channel = $ch; Files = 0; SizeMB = 0; Status = 'MISSING FOLDER' }
        $emptyChans += $ch
        continue
    }

    $files = Get-ChildItem -Path $chDir -Recurse -File -Include $Include -ErrorAction SilentlyContinue
    $count = ($files | Measure-Object).Count
    $bytes = ($files | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $bytes) { $bytes = 0 }

    $grandFiles += $count
    $grandBytes += [long]$bytes

    $status = if ($count -eq 0) { 'EMPTY'; $emptyChans += $ch } else { 'ok' }

    $rows += [pscustomobject]@{
        Channel = $ch
        Files   = $count
        SizeMB  = [math]::Round($bytes / 1MB, 1)
        Status  = $status
    }
}

$rows | Format-Table -AutoSize | Out-Host

Write-Host ("-" * 60)
Write-Host ("Total: {0} file(s), {1} MB" -f $grandFiles, [math]::Round($grandBytes / 1MB, 1))

if ($emptyChans.Count -gt 0) {
    Write-Host ("WARNING - empty / missing channels: {0}" -f ($emptyChans -join ', ')) -ForegroundColor Yellow
} else {
    Write-Host "All channels contain files." -ForegroundColor Green
}

# Write a report next to the day's log.
$report = Join-Path $dayDir 'check_report.txt'
$lines  = @()
$lines += "Reolink export check - $Date"
$lines += "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$lines += "Folder: $dayDir"
$lines += ("-" * 60)
$lines += ($rows | Format-Table -AutoSize | Out-String).TrimEnd()
$lines += ("-" * 60)
$lines += ("Total: {0} file(s), {1} MB" -f $grandFiles, [math]::Round($grandBytes / 1MB, 1))
if ($emptyChans.Count -gt 0) {
    $lines += ("WARNING - empty / missing channels: {0}" -f ($emptyChans -join ', '))
}
$lines -join "`r`n" | Out-File -FilePath $report -Encoding utf8
Write-Host "Report written: $report"

# Non-zero exit if anything is empty, so Task Scheduler / scripts can detect problems.
if ($emptyChans.Count -gt 0) { exit 2 } else { exit 0 }

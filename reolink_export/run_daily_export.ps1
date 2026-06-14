<#
.SYNOPSIS
    Master daily Reolink export. Schedule at 00:01 so it grabs the full
    previous day, then auto-checks the result for a morning review.

    Steps:
      1. Run reolink_export.ahk  (all 6 channels, full day, yesterday).
      2. Run check_exports.ps1   (per-channel file counts / empties).
      3. Append a one-line summary to D:\Reolink_export\daily_runs.log.

.NOTES
    The PC must be logged in and UNLOCKED at run time (GUI automation needs
    a live desktop). See README for Task Scheduler setup.
#>

param(
    # Delete exported day-folders older than this many days. 0 = keep everything.
    # ~200 GB/day -> 10 days ~= 2 TB, leaving headroom on a 4 TB drive.
    [int]$RetentionDays = 10
)

$ErrorActionPreference = 'Continue'

# --- paths (edit if you move the folder) ---
$ahk    = 'C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe'
$dir    = 'C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_export'
$script = Join-Path $dir 'reolink_export.ahk'
$check  = Join-Path $dir 'check_exports.ps1'
$root   = 'D:\Reolink_export'

$date   = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')   # yesterday
$master = Join-Path $root 'daily_runs.log'

function Write-Log([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Write-Host $line
    Add-Content -Path $master -Value $line
}

New-Item -ItemType Directory -Force -Path $root | Out-Null
Write-Log "===== Daily export START (target day $date) ====="

# sanity checks
if (-not (Test-Path $ahk))    { Write-Log "FATAL: AutoHotkey not found at $ahk";   exit 1 }
if (-not (Test-Path $script)) { Write-Log "FATAL: script not found at $script";    exit 1 }

# clear any stray AHK instance so coordinates aren't fought over
Get-Process AutoHotkey64 -ErrorAction SilentlyContinue | Stop-Process -Force

# 1) run the export (all channels, full day) and wait for it to finish
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$exp = Start-Process -FilePath $ahk -ArgumentList "`"$script`"" -Wait -PassThru
$sw.Stop()
Write-Log ('Export finished in {0:N1} min (ahk exit {1})' -f $sw.Elapsed.TotalMinutes, $exp.ExitCode)

# 2) check the result (isolated process so its exit code is clean)
if (Test-Path $check) {
    $chk = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$check`"",'-Date',$date,'-Root',"`"$root`"" `
        -Wait -PassThru -NoNewWindow
    switch ($chk.ExitCode) {
        0       { Write-Log 'CHECK: OK - every channel has files.' }
        2       { Write-Log 'CHECK: WARNING - one or more channels empty/missing. See check_report.txt.' }
        default { Write-Log "CHECK: exit $($chk.ExitCode)" }
    }
} else {
    Write-Log "CHECK: skipped (check_exports.ps1 not found)."
}

# 3) cleanup: remove export day-folders older than RetentionDays
if ($RetentionDays -gt 0) {
    $cutoff = (Get-Date).Date.AddDays(-$RetentionDays)
    Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
        ForEach-Object {
            $d = [datetime]::ParseExact($_.Name, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
            if ($d -lt $cutoff) {
                try {
                    $sz = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue |
                           Measure-Object -Property Length -Sum).Sum
                    Remove-Item $_.FullName -Recurse -Force
                    Write-Log ('CLEANUP removed {0} ({1:N1} GB)' -f $_.Name, ($sz / 1GB))
                } catch {
                    Write-Log ('CLEANUP failed for {0}: {1}' -f $_.Name, $_.Exception.Message)
                }
            }
        }
}

# tidy stray files left in staging (older than 1 day) so it doesn't accumulate
$staging = 'D:\Reolink_staging'
if (Test-Path $staging) {
    Get-ChildItem -Path $staging -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) } |
        ForEach-Object {
            try { Remove-Item $_.FullName -Force; Write-Log ("CLEANUP staging leftover removed: " + $_.Name) } catch {}
        }
}

Write-Log "===== Daily export DONE (target day $date) ====="
Write-Log "Review: $root\$date\export_log.txt  and  check_report.txt"

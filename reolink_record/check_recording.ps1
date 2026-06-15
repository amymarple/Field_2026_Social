<#
.SYNOPSIS
    Health check for the RTSP recorder. Shows per-channel file counts/size,
    whether each stream is actively growing right now, free disk, and any
    stalls/restarts logged. Safe to run anytime.
#>
param([string]$Root = 'D:\Reolink_record')

function Get-HandleLen($p) {
    try { $fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite'); $l = $fs.Length; $fs.Dispose(); $l } catch { 0 }
}

$ff = (Get-Process ffmpeg -EA SilentlyContinue | Measure-Object).Count
Write-Host ("Time: {0}   ffmpeg processes: {1} (expect 6)" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $ff) -ForegroundColor Cyan

# first size sample
$first = @{}
foreach ($n in 1..6) { $ch = '{0:D2}' -f $n; $f = Get-ChildItem "$Root\CH$ch" -File -Filter *.mp4 -EA SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1; $first[$ch] = if ($f) { Get-HandleLen $f.FullName } else { -1 } }
Start-Sleep -Seconds 4

$grandGB = 0
foreach ($n in 1..6) {
    $ch = '{0:D2}' -f $n
    $files = Get-ChildItem "$Root\CH$ch" -File -Filter *.mp4 -EA SilentlyContinue
    $cnt = ($files | Measure-Object).Count
    $gb = [math]::Round((($files | Measure-Object -Property Length -Sum).Sum) / 1GB, 2)
    $grandGB += $gb
    $newest = $files | Sort-Object Name -Descending | Select-Object -First 1
    $now = if ($newest) { Get-HandleLen $newest.FullName } else { -1 }
    $growing = if ($now -gt $first[$ch]) { 'GROWING' } else { 'NOT growing' }
    $color = if ($now -gt $first[$ch]) { 'Green' } else { 'Red' }
    Write-Host ("CH{0}: {1,3} files  {2,7} GB  newest {3,6} MB  {4}" -f $ch, $cnt, $gb, [math]::Round($now / 1MB, 0), $growing) -ForegroundColor $color
}
Write-Host ("TOTAL recorded: {0} GB    Free on drive: {1} GB" -f [math]::Round($grandGB, 1), [math]::Round((Get-PSDrive ((Split-Path $Root -Qualifier).TrimEnd(':'))).Free / 1GB, 0))

# recorder.log health
$log = "$Root\logs\recorder.log"
if (Test-Path $log) {
    $restarts = (Select-String -Path $log -Pattern 'restarting' -EA SilentlyContinue | Measure-Object).Count
    $stalls = (Select-String -Path $log -Pattern 'stalled' -EA SilentlyContinue | Measure-Object).Count
    Write-Host ("recorder.log: {0} restart(s), {1} stall(s)" -f $restarts, $stalls)
    Write-Host "last log lines:"
    Get-Content $log -Tail 4
}

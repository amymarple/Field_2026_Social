<#
.SYNOPSIS
    Copy ONE day of recordings from all recorders to a USB drive, for hand-off to
    the lab. Designed so a coworker can run one command and not worry about
    mistakes.

    *** READ-ONLY AT THE SOURCE. THIS SCRIPT ONLY EVER COPIES. ***
    It NEVER deletes, moves, renames, or modifies anything under the recording
    folders. The only thing it writes to is the USB destination. This is enforced
    in code (see Assert-DestUnderUsb / no Remove/Move/Set on source).

    What it does:
      - Finds every finished .mp4 for the chosen day (default = yesterday) in
        E:\Reolink_record\CHxx and E:\thermal_record\1xx_* .
      - Copies them to  <USB>\<day>\<group>\  preserving the per-camera folders.
      - Verifies each copy (size; SHA-256 with -Hash) and re-copies only what's
        missing/different, so it is safe to re-run / resume.
      - Checks completeness: for each camera, are all 24 hours of the day present?
        Reports any missing hours and any copy/verify failures.
      - Writes copy_report.txt + copy_manifest.csv into the USB day folder.

.PARAMETER Usb
    REQUIRED. The USB drive or target folder, e.g.  F:   or  F:\   or  F:\fielddata .
    Must NOT be on the same drive as the recordings.

.PARAMETER Date
    Day to copy, yyyy-MM-dd. Default = yesterday (the last complete day).

.PARAMETER Hash
    Verify each copy with a full SHA-256 compare (slow but definitive). Default is
    a fast size compare (catches truncated/incomplete copies).

.PARAMETER IncludeActive
    Also copy the still-recording file (no _to_ in the name). Off by default
    because it is incomplete; only relevant if you copy today's date.

.PARAMETER StopOnError
    Halt on the first copy/verify failure. Default is to continue and report all
    failures at the end (source is never touched either way).

.PARAMETER DryRun
    Show exactly what would be copied and the completeness check, but copy nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File copy_day_to_usb.ps1 -Usb F:
    # copies yesterday to F:\<yesterday>\

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File copy_day_to_usb.ps1 -Usb F: -Date 2026-06-20 -Hash
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Usb,
    [string]$Date,
    [switch]$Hash,
    [switch]$IncludeActive,
    [switch]$StopOnError,
    [switch]$DryRun,
    # The recorders. Each immediate subfolder = one camera/group. Override only for testing.
    [string[]]$SourceRoots = @('E:\Reolink_record', 'E:\thermal_record')
)

# ============================ CONFIG ============================
$ExcludeDirs   = @('bin', 'logs')
$Extensions    = @('.mp4')
$ExpectedHours = 24                       # a full day = 24 hourly slots per camera
# ===============================================================

$ErrorActionPreference = 'Stop'

function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }

# ---- resolve the day ----
if ($Date) {
    try { $dayObj = [datetime]::ParseExact($Date, 'yyyy-MM-dd', $null) }
    catch { Say "Bad -Date '$Date'. Use yyyy-MM-dd (e.g. 2026-06-26)." Red; exit 2 }
} else {
    $dayObj = (Get-Date).Date.AddDays(-1)
}
$dayStr = $dayObj.ToString('yyyy-MM-dd')
$isToday = ($dayObj.Date -eq (Get-Date).Date)

# ---- resolve + sanity-check the USB destination ----
$usbRoot = $Usb.Trim()
if ($usbRoot -match '^[A-Za-z]:$') { $usbRoot += '\' }      # "F:" -> "F:\"
try { $usbRoot = (Resolve-Path -LiteralPath $usbRoot -ErrorAction Stop).Path }
catch { Say "USB path not found / not mounted: $Usb" Red; exit 2 }

$usbQual = (Split-Path $usbRoot -Qualifier)
foreach ($r in $SourceRoots) {
    $srcQual = (Split-Path $r -Qualifier)
    if ($usbQual -ieq $srcQual) {
        Say "REFUSING: the destination ($usbQual) is the SAME drive as the recordings ($srcQual)." Red
        Say "Pick a real USB drive so nothing can be copied onto the source." Red
        exit 2
    }
}

$destDay = Join-Path $usbRoot $dayStr

# Hard guard: every file we write MUST live under the USB day folder.
$destDayFull = [System.IO.Path]::GetFullPath($destDay)
function Assert-DestUnderUsb([string]$path) {
    $full = [System.IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($destDayFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "SAFETY ABORT: refused to write outside the USB day folder: $full"
    }
}

Say ("==================================================================") Cyan
Say ("  COPY DAY -> USB    (copy-only; source is never modified)") Cyan
Say ("  Day:         $dayStr" + $(if ($isToday) { '  (TODAY - may be incomplete)' } else { '' })) Cyan
Say ("  Destination: $destDay") Cyan
Say ("  Verify:      " + $(if ($Hash) { 'SHA-256 (full)' } else { 'size compare (fast)' })) Cyan
Say ("  Mode:        " + $(if ($DryRun) { 'DRY RUN (nothing will be copied)' } else { 'COPY' })) Cyan
Say ("==================================================================") Cyan

# ---- gather source files for the day, per group ----
$dayTag = '_' + $dayStr + '_'
$groups = [ordered]@{}    # groupName -> list of FileInfo

foreach ($root in $SourceRoots) {
    if (-not (Test-Path $root)) { Say "WARNING: source root missing: $root" Yellow; continue }
    foreach ($dir in (Get-ChildItem $root -Directory -EA SilentlyContinue | Where-Object { $ExcludeDirs -notcontains $_.Name })) {
        $files = Get-ChildItem $dir.FullName -File -EA SilentlyContinue |
            Where-Object { ($Extensions -contains $_.Extension.ToLower()) -and ($_.Name -like "*$dayTag*") }
        if (-not $IncludeActive) { $files = $files | Where-Object { $_.BaseName -like '*_to_*' } }  # drop the still-recording file
        if ($files) { $groups[$dir.Name] = @($files | Sort-Object Name) }
        elseif (-not $groups.Contains($dir.Name)) { $groups[$dir.Name] = @() }
    }
}

if ($groups.Count -eq 0) { Say "No camera folders found under $($SourceRoots -join ', ')." Red; exit 2 }

# ---- size + free-space preflight ----
$allFiles = @(); foreach ($g in $groups.Keys) { $allFiles += $groups[$g] }
$totalBytes = ($allFiles | Measure-Object Length -Sum).Sum
if (-not $totalBytes) { $totalBytes = 0 }
$totalGB = [math]::Round($totalBytes / 1GB, 2)
$freeGB  = [math]::Round((Get-PSDrive -Name ($usbQual.TrimEnd(':'))).Free / 1GB, 2)
Say ("`nFound $($allFiles.Count) file(s) for $dayStr across $($groups.Count) camera(s): $totalGB GB") White
Say ("USB free space: $freeGB GB") White
if ($allFiles.Count -eq 0) { Say "Nothing to copy for $dayStr." Yellow; exit 1 }
if (-not $DryRun -and ($totalBytes -gt (Get-PSDrive -Name ($usbQual.TrimEnd(':'))).Free)) {
    Say "NOT ENOUGH SPACE on the USB drive ($freeGB GB free, need $totalGB GB). Aborting (nothing copied)." Red
    exit 2
}

# ---- copy ----
$manifest = New-Object System.Collections.Generic.List[object]
$copied = 0; $skipped = 0; $failed = 0; $copiedBytes = 0
$idx = 0; $n = $allFiles.Count
$swAll = [System.Diagnostics.Stopwatch]::StartNew()

if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $destDay | Out-Null }

foreach ($g in $groups.Keys) {
    $destGroup = Join-Path $destDay $g
    if (-not $DryRun -and $groups[$g].Count -gt 0) {
        Assert-DestUnderUsb $destGroup
        New-Item -ItemType Directory -Force -Path $destGroup | Out-Null
    }
    foreach ($f in $groups[$g]) {
        $idx++
        $dest = Join-Path $destGroup $f.Name
        Assert-DestUnderUsb $dest
        $status = ''
        $need = $true

        if (Test-Path -LiteralPath $dest) {
            $dl = (Get-Item -LiteralPath $dest).Length
            if ($dl -eq $f.Length) {
                if ($Hash) {
                    $sh = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
                    $dh = (Get-FileHash -LiteralPath $dest      -Algorithm SHA256).Hash
                    if ($sh -eq $dh) { $need = $false; $status = 'already-present (hash ok)' }
                } else { $need = $false; $status = 'already-present (size ok)' }
            }
        }

        if (-not $need) {
            $skipped++
            Say ("[{0}/{1}] {2}\{3}  -> {4}" -f $idx, $n, $g, $f.Name, $status) DarkGray
        }
        elseif ($DryRun) {
            $status = 'would-copy'
            Say ("[{0}/{1}] {2}\{3}  ({4} MB)  -> would copy" -f $idx, $n, $g, $f.Name, [math]::Round($f.Length/1MB,1)) Gray
        }
        else {
            $ok = $false; $err = $null
            for ($try = 1; $try -le 2 -and -not $ok; $try++) {
                try {
                    [System.IO.File]::Copy($f.FullName, $dest, $true)   # COPY ONLY (overwrite dest, never source)
                    $dl = (Get-Item -LiteralPath $dest).Length
                    if ($dl -ne $f.Length) { throw "size mismatch after copy (src $($f.Length) / dst $dl)" }
                    if ($Hash) {
                        $sh = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
                        $dh = (Get-FileHash -LiteralPath $dest      -Algorithm SHA256).Hash
                        if ($sh -ne $dh) { throw "SHA-256 mismatch after copy" }
                    }
                    $ok = $true
                } catch { $err = $_.Exception.Message; Start-Sleep -Milliseconds 500 }
            }
            if ($ok) {
                $copied++; $copiedBytes += $f.Length; $status = 'copied+verified'
                Say ("[{0}/{1}] {2}\{3}  ({4} MB)  -> OK" -f $idx, $n, $g, $f.Name, [math]::Round($f.Length/1MB,1)) Green
            } else {
                $failed++; $status = "FAILED: $err"
                Say ("[{0}/{1}] {2}\{3}  -> FAILED: {4}" -f $idx, $n, $g, $f.Name, $err) Red
                if ($StopOnError) { Say "Stopping on first error (-StopOnError). Source untouched." Red; break }
            }
        }

        $manifest.Add([pscustomobject]@{
            Group=$g; File=$f.Name; SizeBytes=$f.Length
            SizeMB=[math]::Round($f.Length/1MB,1); Status=$status
            SourcePath=$f.FullName; DestPath=$dest
        })
    }
    if ($StopOnError -and $failed -gt 0) { break }
}
$swAll.Stop()

# ---- completeness check: is every hour of the day COVERED per camera? ----
# Uses the start/end times in the filename as intervals, so a segment that spans
# an hour boundary (after a restart) still counts that hour as covered. An hour H
# is "covered" if some file's [start,end) overlaps [H:00, H+1:00).
$nowHour = (Get-Date).Hour
$startEndRe = '_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})(?:_to_(\d{2}-\d{2}-\d{2}))?'
$completeness = New-Object System.Collections.Generic.List[object]
foreach ($g in $groups.Keys) {
    $intervals = @()
    foreach ($f in $groups[$g]) {
        $m = [regex]::Match($f.Name, $startEndRe)
        if (-not $m.Success) { continue }
        $d = $m.Groups[1].Value
        try { $st = [datetime]::ParseExact("$d $($m.Groups[2].Value)", 'yyyy-MM-dd HH-mm-ss', $null) } catch { continue }
        if ($m.Groups[3].Success) {
            try { $en = [datetime]::ParseExact("$d $($m.Groups[3].Value)", 'yyyy-MM-dd HH-mm-ss', $null) } catch { $en = $st }
            if ($en -le $st) { $en = $en.AddDays(1) }     # wrapped past midnight
        } else { $en = $f.LastWriteTime }                  # active file (only if -IncludeActive)
        $intervals += ,@($st, $en)
    }
    $expected = if ($isToday) { 0..([math]::Max($nowHour-1,0)) } else { 0..($ExpectedHours-1) }
    $covered = @{}
    foreach ($h in $expected) {
        $h0 = $dayObj.AddHours($h); $h1 = $h0.AddHours(1)
        foreach ($iv in $intervals) { if ($iv[0] -lt $h1 -and $iv[1] -gt $h0) { $covered[$h] = $true; break } }
    }
    $missing = @($expected | Where-Object { -not $covered.ContainsKey($_) })
    $completeness.Add([pscustomobject]@{
        Group=$g; Files=$groups[$g].Count; HoursPresent=$covered.Keys.Count
        HoursExpected=$expected.Count; MissingHours=$missing
    })
}

# ---- summary ----
Say ("`n==================================================================") Cyan
Say ("  SUMMARY  -  day $dayStr") Cyan
Say ("==================================================================") Cyan
$incomplete = $false
foreach ($c in $completeness) {
    $miss = if ($c.MissingHours.Count -eq 0) { 'all hours present' }
            else { $incomplete=$true; ("MISSING hours: " + (($c.MissingHours | ForEach-Object { '{0:d2}' -f $_ }) -join ',')) }
    $col = if ($c.MissingHours.Count -eq 0) { 'Green' } else { 'Yellow' }
    Say ("  {0,-14} {1,3} files  {2,2}/{3,2} hrs  {4}" -f $c.Group, $c.Files, $c.HoursPresent, $c.HoursExpected, $miss) $col
}
Say ("------------------------------------------------------------------") Cyan
Say ("  copied: $copied   already-present: $skipped   FAILED: $failed") $(if($failed){'Red'}else{'White'})
Say ("  data copied this run: $([math]::Round($copiedBytes/1GB,2)) GB in $([math]::Round($swAll.Elapsed.TotalMinutes,1)) min") White

$result = if ($failed -gt 0) { 'COPY INCOMPLETE - some files FAILED (re-run to retry)'; }
          elseif ($incomplete) { 'COPY OK, but some hours are MISSING from the source (recording gap that day)' }
          else { 'COPY COMPLETE - all files copied and all 24 hours present' }
$rc = if ($failed -gt 0) { 2 } elseif ($incomplete) { 1 } else { 0 }
Say ("`n  >>> $result <<<") $(if($rc -eq 0){'Green'}elseif($rc -eq 1){'Yellow'}else{'Red'})

# ---- write report + manifest into the USB day folder ----
if (-not $DryRun -and (Test-Path $destDay)) {
    $rep = New-Object System.Collections.Generic.List[string]
    $rep.Add("Recording USB copy report")
    $rep.Add("=========================")
    $rep.Add("generated:   $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))")
    $rep.Add("day copied:  $dayStr")
    $rep.Add("source:      $($SourceRoots -join ' ; ')   (READ-ONLY - never modified)")
    $rep.Add("destination: $destDay")
    $rep.Add("verify:      $(if ($Hash) {'SHA-256'} else {'size compare'})")
    $rep.Add("result:      $result")
    $rep.Add("copied=$copied  already-present=$skipped  FAILED=$failed  ($([math]::Round($copiedBytes/1GB,2)) GB)")
    $rep.Add("")
    $rep.Add("Per-camera completeness:")
    foreach ($c in $completeness) {
        $miss = if ($c.MissingHours.Count -eq 0) { 'all hours present' } else { 'MISSING: ' + (($c.MissingHours | ForEach-Object { '{0:d2}' -f $_ }) -join ',') }
        $rep.Add(("  {0,-14} {1,3} files  {2,2}/{3,2} hrs  {4}" -f $c.Group, $c.Files, $c.HoursPresent, $c.HoursExpected, $miss))
    }
    if ($failed -gt 0) {
        $rep.Add(""); $rep.Add("FAILURES:")
        foreach ($m in ($manifest | Where-Object { $_.Status -like 'FAILED*' })) { $rep.Add("  $($m.Group)\$($m.File)  -  $($m.Status)") }
    }
    Set-Content -Path (Join-Path $destDay 'copy_report.txt') -Value ($rep -join "`r`n") -Encoding UTF8
    $manifest | Export-Csv -Path (Join-Path $destDay 'copy_manifest.csv') -NoTypeInformation -Encoding UTF8
    Say ("`n  report:   $(Join-Path $destDay 'copy_report.txt')") DarkGray
    Say ("  manifest: $(Join-Path $destDay 'copy_manifest.csv')") DarkGray
}

exit $rc

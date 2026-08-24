<#
.SYNOPSIS
    Verify copied recordings ON THE ANALYSIS COMPUTER - no load on the field PC.

    Runs entirely against the local copy tree (the audio_in share's local folder).
    The field PC only ever contributes a metadata manifest (copy_manifest_*.csv,
    written by copy_to_analysis.ps1 at copy time - names + sizes, no disk reads).

    Checks, per file in scope:
      1. MANIFEST  - file present locally? size matches the source's size at copy
                     time?  -> failures are COPY ERRORS (re-run the copy; it
                     self-heals partial/missing files).
      2. CONTENT   - actual media duration vs the duration encoded in the
                     filename (<name>_<date>_<start>_to_<end>):
                       .wav  : native RIFF/RF64 header parse (fast, no tools)
                       .mp4  : ffprobe (auto-detected; skipped if not installed)
                     -> a SHORTER duration than the name-span is flagged as a
                     WARNING, because it usually means a recording-side gap
                     (stream dropped mid-segment), NOT a bad copy. Unreadable/
                     unparseable files are ERRORS.

    Exit codes (repo convention): 0 = pass, 1 = warnings only, 2 = errors.

.PARAMETER Root
    Local path of the copied tree on THIS machine (the folder that contains
    Reolink_record\, thermal_record\, ultramic_record\). E.g. D:\audio_in

.PARAMETER Date
    One or more yyyy-MM-dd days to verify (comma-separated ok). Omit = everything.

.PARAMETER Ffprobe
    Path to ffprobe.exe. Auto-detected from PATH and common install spots; MP4
    content checks are skipped (with a notice) if unavailable.

.PARAMETER TolSec
    Allowed |actual - filename-span| difference in seconds (default 5).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\verify_on_analysis.ps1 -Root D:\audio_in -Date 2026-08-17
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string[]]$Date,
    [string]$Ffprobe = '',
    [double]$TolSec = 5,
    [switch]$SkipVideo,
    [switch]$SkipThermal,
    [switch]$SkipAudio
)

$ErrorActionPreference = 'Stop'
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }

if ($Date) { $Date = @($Date | ForEach-Object { $_ -split ',' } | Where-Object { $_ }) }
if (-not (Test-Path -LiteralPath $Root)) { Say "Root not found: $Root" Red; exit 2 }

# ---- ffprobe autodetect ----
if (-not $Ffprobe) {
    $cand = @((Get-Command ffprobe.exe -EA SilentlyContinue | Select-Object -First 1).Source)
    $cand += 'C:\ffmpeg\bin\ffprobe.exe', "$env:ProgramFiles\ffmpeg\bin\ffprobe.exe"
    $Ffprobe = ($cand | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1)
}
$haveProbe = [bool]($Ffprobe -and (Test-Path -LiteralPath $Ffprobe))
if (-not $haveProbe) { Say 'NOTE: ffprobe not found - MP4 duration checks will be skipped (WAV checks still run).' DarkYellow }

# ---- expected duration from the filename contract ----
function Get-NameSpanSeconds([string]$name) {
    if ($name -match '_(\d{2})-(\d{2})-(\d{2})_to_(\d{2})-(\d{2})-(\d{2})\.') {
        $s = New-TimeSpan -Hours ([int]$Matches[1]) -Minutes ([int]$Matches[2]) -Seconds ([int]$Matches[3])
        $e = New-TimeSpan -Hours ([int]$Matches[4]) -Minutes ([int]$Matches[5]) -Seconds ([int]$Matches[6])
        $d = ($e - $s).TotalSeconds
        if ($d -lt 0) { $d += 86400 }   # midnight wrap
        return $d
    }
    return $null
}

# ---- native WAV/RF64 duration (header only - fast) ----
function Get-WavDurationSeconds([string]$path) {
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
        $br = New-Object IO.BinaryReader($fs)
        $riff = [Text.Encoding]::ASCII.GetString($br.ReadBytes(4))
        if ($riff -ne 'RIFF' -and $riff -ne 'RF64') { throw "not a RIFF/RF64 file" }
        [void]$br.ReadUInt32()
        if ([Text.Encoding]::ASCII.GetString($br.ReadBytes(4)) -ne 'WAVE') { throw "no WAVE tag" }
        $byteRate = 0; [long]$dataSize = -1; [long]$ds64Data = -1
        while ($fs.Position -le ($fs.Length - 8)) {
            $cid = [Text.Encoding]::ASCII.GetString($br.ReadBytes(4))
            $csz = $br.ReadUInt32()
            if ($cid -eq 'ds64') {
                [void]$br.ReadUInt64()               # riffSize64
                $ds64Data = [long]$br.ReadUInt64()   # dataSize64
                $fs.Seek($csz - 16, 'Current') | Out-Null
            }
            elseif ($cid -eq 'fmt ') {
                $fmtStart = $fs.Position
                $fs.Seek(8, 'Current') | Out-Null    # tag,ch,rate
                $byteRate = $br.ReadUInt32()
                $fs.Seek($fmtStart + $csz, 'Begin') | Out-Null
            }
            elseif ($cid -eq 'data') {
                $dataSize = if ($csz -eq 0xFFFFFFFFL -and $ds64Data -ge 0) { $ds64Data } else { [long]$csz }
                break                                 # data is last in our files
            }
            else { $fs.Seek($csz, 'Current') | Out-Null }
            if ($csz % 2 -eq 1) { $fs.Seek(1, 'Current') | Out-Null }  # chunk padding
        }
        if ($byteRate -le 0 -or $dataSize -lt 0) { throw "fmt/data chunk not found" }
        return $dataSize / [double]$byteRate
    } finally { $fs.Dispose() }
}

function Get-Mp4DurationSeconds([string]$path) {
    $out = & $Ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 -- $path 2>$null
    $d = 0.0
    if ([double]::TryParse(($out | Select-Object -First 1), [ref]$d)) { return $d }
    throw "ffprobe could not read duration"
}

# ---- scope: date-filtered closed files under the three modality trees ----
$patterns = @()
$exts = @()
if (-not $SkipVideo -or -not $SkipThermal) { $exts += 'mp4' }
if (-not $SkipAudio) { $exts += 'wav', 'flac' }
foreach ($ext in $exts) {
    if ($Date) { foreach ($d0 in $Date) { $patterns += "*_${d0}_*_to_*.$ext" } }
    else { $patterns += "*_to_*.$ext" }
}
$treeFilter = @()
if (-not $SkipVideo)   { $treeFilter += 'Reolink_record' }
if (-not $SkipThermal) { $treeFilter += 'thermal_record' }
if (-not $SkipAudio)   { $treeFilter += 'ultramic_record' }

# ---- manifest(s): newest entry wins per RelPath ----
$manifest = @{}
$manFiles = @(Get-ChildItem -LiteralPath $Root -Filter 'copy_manifest*.csv' -File -EA SilentlyContinue | Sort-Object LastWriteTime)
foreach ($mf in $manFiles) {
    foreach ($row in (Import-Csv $mf.FullName)) { $manifest[$row.RelPath] = [long]$row.Bytes }
}
Say ("manifests loaded: {0} ({1} entries)" -f $manFiles.Count, $manifest.Count) Cyan

$errors = 0; $warns = 0; $checked = 0; $mp4Skipped = 0
$sw = [Diagnostics.Stopwatch]::StartNew()

foreach ($tree in $treeFilter) {
    $treePath = Join-Path $Root $tree
    if (-not (Test-Path -LiteralPath $treePath)) { continue }
    foreach ($dir in (Get-ChildItem -LiteralPath $treePath -Directory -EA SilentlyContinue)) {
        $files = @{}
        foreach ($p in $patterns) {
            foreach ($f in (Get-ChildItem -LiteralPath $dir.FullName -Filter $p -File -EA SilentlyContinue)) { $files[$f.Name] = $f }
        }
        $dirBad = 0
        foreach ($f in ($files.Values | Sort-Object Name)) {
            $checked++
            $rel = ('{0}/{1}/{2}' -f $tree, $dir.Name, $f.Name)
            # 1) manifest size check (copy fidelity)
            if ($manifest.Count -and $manifest.ContainsKey($rel)) {
                if ($manifest[$rel] -ne $f.Length) {
                    Say ("  ERROR size mismatch (copy problem): {0}  local {1:N0} vs source {2:N0}" -f $rel, $f.Length, $manifest[$rel]) Red
                    $errors++; $dirBad++; continue
                }
            }
            # 2) content duration vs filename span
            $expect = Get-NameSpanSeconds $f.Name
            if ($null -eq $expect) { continue }
            try {
                $actual = $null
                if ($f.Extension -ieq '.wav') { $actual = Get-WavDurationSeconds $f.FullName }
                elseif ($f.Extension -ieq '.mp4') {
                    if ($haveProbe) { $actual = Get-Mp4DurationSeconds $f.FullName } else { $mp4Skipped++ }
                }
                if ($null -ne $actual -and [math]::Abs($actual - $expect) -gt $TolSec) {
                    Say ("  WARN duration {0:N1}s vs name-span {1:N1}s: {2}  (likely a recording-side gap, not a copy error)" -f $actual, $expect, $rel) Yellow
                    $warns++; $dirBad++
                }
            } catch {
                Say ("  ERROR unreadable ({0}): {1}" -f $_.Exception.Message, $rel) Red
                $errors++; $dirBad++
            }
        }
        if ($files.Count) { Say ("  {0}/{1}: {2} file(s), {3} flagged" -f $tree, $dir.Name, $files.Count, $dirBad) $(if ($dirBad) { 'Yellow' } else { 'Gray' }) }
    }
}

# files listed in the manifest but absent locally = copy never completed them
foreach ($rel in $manifest.Keys) {
    if ($Date -and -not ($Date | Where-Object { $rel -like "*_${_}_*" })) { continue }
    $local = Join-Path $Root ($rel -replace '/', '\')
    if (-not (Test-Path -LiteralPath $local)) {
        Say ("  ERROR missing locally (in manifest, never copied): {0}" -f $rel) Red
        $errors++
    }
}

$sw.Stop()
Say ("`nverified {0} file(s) in {1:N1} min - {2} error(s), {3} warning(s){4}" -f `
    $checked, $sw.Elapsed.TotalMinutes, $errors, $warns, $(if ($mp4Skipped) { ", $mp4Skipped mp4 skipped (no ffprobe)" } else { '' })) `
    $(if ($errors) { 'Red' } elseif ($warns) { 'Yellow' } else { 'Green' })
if ($errors) { Say 'fix: re-run copy_to_analysis.ps1 on the field PC for the affected date(s) - it re-copies bad/missing files automatically.' Yellow }
if ($errors) { exit 2 } elseif ($warns) { exit 1 } else { exit 0 }

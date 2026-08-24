<#
.SYNOPSIS
    Copy EVERYTHING needed for offline analysis (Reolink video + thermal + UltraMic
    audio + WISER backup) from the field PC to the analysis computer over the direct
    USB-Ethernet link, in ONE command. So you can run the audio, CV, and WISER
    analysis on the other machine.

    *** READ-ONLY AT THE SOURCE. THIS SCRIPT ONLY EVER COPIES. ***
    It uses robocopy in copy-only mode: NO /MIR, NO /MOV, NO /PURGE (all three are
    hard-blocked below), so it can never delete/move/modify anything on E:. It only
    writes to the destination you give.

    Safety built in:
      - Closed files only: the still-recording file (CHxx_<start>.mp4, no "_to_") is
        SKIPPED via the "*_to_*.mp4" filter, honoring the open-file rule. -IncludeActive
        overrides (not recommended).
      - WISER: copies the SNAPSHOT backups in E:\Wiser_backup (consistent), NOT the live
        D:\Wiser\data DB (which is mid-write and unsafe to copy).
      - Refuses if the destination is on the recording drive.
      - Resumable/idempotent: re-run to copy only what's new (robocopy skips in-sync files).

.PARAMETER Dest
    Destination root on the analysis computer, e.g.  \\192.168.50.2\audio_in  (a writable
    share over the USB link) or any folder/drive NOT on the recording disk. Required.

.PARAMETER Date
    Limit VIDEO to one day (yyyy-MM-dd). Omit to copy ALL closed recordings. (WISER backup
    is always fully mirrored; it is small.)

.PARAMETER IncludeActive
    Also copy the still-recording (open) file. OFF by default. Leave off on the live rig.

.PARAMETER SkipVideo / SkipThermal / SkipAudio / SkipWiser
    Turn off a modality. All ON by default ("copy everything"). Audio = every MIC*
    folder under E:\ultramic_record (MIC01 384K, MIC02 250K, any future mic), same
    closed-files-only + -Date rules as video.

.PARAMETER Fast
    Full-speed mode: 8 robocopy threads, no pacing (~118 MB/s, the link's wire
    limit; a ~500 GB day in ~75 min). The DEFAULT (without -Fast) is gentle:
    SINGLE-threaded with inter-packet pacing /IPG:5 (~10-25 MB/s, day copies
    overnight) so E: seek pressure stays minimal while the rig records.
    NOTE: robocopy forbids /IPG with /MT (exit 16), so gentle mode never uses
    threads - -Threads only applies with -Fast. Measurements (2026-08-18/19)
    showed even full speed never disturbed capture - gentle default is margin.

.PARAMETER Gentle
    Legacy switch: forces pacing even with -Fast. (Pacing is already the default.)

.PARAMETER Threads
    robocopy /MT threads, FAST MODE ONLY (default 8 with -Fast; ignored when pacing).

.PARAMETER DryRun
    List what would be copied and total sizes; copy nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File copy_to_analysis.ps1 -Dest \\192.168.50.2\audio_in
    # copies ALL closed video + thermal + WISER backup to the analysis computer

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File copy_to_analysis.ps1 -Dest \\192.168.50.2\audio_in -Date 2026-06-29 -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Dest,
    [string[]]$Date,                       # one or more days, e.g. -Date 2026-07-24,2026-07-25
    [string[]]$Channels,                   # limit video to these channel folders, e.g. -Channels CH07,CH08
    [switch]$IncludeActive,
    [switch]$SkipVideo,
    [switch]$SkipThermal,
    [switch]$SkipAudio,
    [switch]$SkipWiser,
    [switch]$Fast,          # full speed (8 threads, no pacing); default is gentle
    [switch]$Gentle,
    [switch]$Restartable,   # robocopy /Z: resume WITHIN a partly-copied file. Costs ~3x
                            # write speed (measured 20 vs 60 MB/s on the analysis link
                            # 2026-08-18), so OFF by default - re-runs already skip
                            # completed files, and a re-copied in-flight file is cheap.
    [int]$Threads = 2,
    [switch]$DryRun,
    [string]$ReolinkRoot  = 'E:\Reolink_record',
    [string]$ThermalRoot  = 'E:\thermal_record',
    [string]$UltramicRoot = 'E:\ultramic_record',
    [string]$WiserSource  = 'E:\Wiser_backup',
    [string]$RescueRoot   = 'E:\nvr_rescue',    # NVR-exported rescue footage (PC-time names)
    [switch]$SkipRescue
)

$ErrorActionPreference = 'Stop'
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }

# GENTLE-BY-DEFAULT (2026-08-19): pace robocopy unless -Fast; -Fast raises threads
# to 8 unless the caller set -Threads explicitly. -Gentle forces pacing regardless.
if ($Fast -and -not $PSBoundParameters.ContainsKey('Threads')) { $Threads = 8 }
$UsePacing = (-not $Fast) -or $Gentle

$ExcludeDirs = @('bin', 'logs')
$logFile = Join-Path $env:TEMP ("copy_to_analysis_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

# `powershell -File` passes "a,b" as ONE string (no array binding) - split ourselves
if ($Date)     { $Date     = @($Date     | ForEach-Object { $_ -split ',' } | Where-Object { $_ }) }
if ($Channels) { $Channels = @($Channels | ForEach-Object { $_ -split ',' } | Where-Object { $_ }) }

# ---- validate date(s) ----
if ($Date) {
    foreach ($d0 in $Date) {
        try { [void][datetime]::ParseExact($d0, 'yyyy-MM-dd', $null) }
        catch { Say "Bad -Date '$d0'. Use yyyy-MM-dd." Red; exit 2 }
    }
}

# ---- destination sanity: must NOT be on a recording source drive ----
$srcQuals = @($ReolinkRoot, $ThermalRoot, $UltramicRoot, $WiserSource, $RescueRoot | ForEach-Object { (Split-Path $_ -Qualifier) } | Where-Object { $_ })
# NOTE: Split-Path -Qualifier THROWS on a UNC path (\\server\share), so only ask for a
# qualifier when the dest is a drive-letter path. A UNC dest has no drive qualifier -> ''
# -> the same-drive-as-source guard is skipped (a UNC dest can't be a local recording drive).
$destQual = if ($Dest -match '^[A-Za-z]:') { Split-Path $Dest -Qualifier } else { '' }
if ($destQual -and ($srcQuals -contains $destQual)) {
    Say "REFUSING: destination ($destQual) is a recording drive. Pick the analysis computer's share/drive." Red
    exit 2
}
# reachability check (share up? drive present?) - guard Split-Path against UNC edge cases
$destParent = try { Split-Path $Dest -Parent } catch { $null }
if (-not (Test-Path -LiteralPath $Dest) -and -not ($destParent -and (Test-Path -LiteralPath $destParent))) {
    Say "Destination not reachable: $Dest" Red
    Say "  Is the USB link up (Test-Connection 192.168.50.2) and the share created + writable?" Yellow
    exit 2
}

# ---- the safe robocopy runner: constructs args itself so /MIR /MOV /PURGE can't appear ----
function Invoke-Robo {
    param([string]$Src, [string]$Dst, [string[]]$Patterns, [switch]$Recurse)
    if (-not (Test-Path -LiteralPath $Src)) { Say "  (skip, missing source: $Src)" DarkGray; return 0 }
    # NO /XO: a Ctrl+C'd partial file at the dest has a NEWER timestamp than the
    # source, and /XO would skip it forever, leaving it silently truncated. Without
    # /XO robocopy still skips identical files (size+time "Same") but re-copies any
    # partial/differing dest file - source is the truth on this link.
    # robocopy REFUSES /IPG together with /MT (exit 16, nothing copied - hit live
    # 2026-08-19). Gentle mode = single-threaded + /IPG pacing; fast mode = /MT only.
    $flags = @('/R:2', '/W:5', '/NP', '/NDL', '/NJH', '/NJS',
               '/XD') + $ExcludeDirs + @('/TEE', "/LOG+:$logFile")
    if ($UsePacing) { $flags += @('/IPG:5') }
    else            { $flags += @("/MT:$Threads") }
    if ($Restartable) { $flags = @('/Z') + $flags }
    if ($Recurse) { $flags += '/E' }
    if ($DryRun)  { $flags += '/L' }
    # HARD BLOCK: never allow destructive/mirroring flags
    foreach ($bad in @('/MIR', '/MOV', '/MOVE', '/PURGE')) {
        if ($flags -contains $bad) { throw "SAFETY ABORT: destructive robocopy flag $bad present." }
    }
    $argList = @("`"$Src`"", "`"$Dst`"") + $Patterns + $flags
    $p = Start-Process robocopy -ArgumentList $argList -NoNewWindow -Wait -PassThru
    return $p.ExitCode
}

# robocopy exit codes: <8 = success (0 none,1 copied,2 extra,4 mismatch); >=8 = failure
function Robo-Ok([int]$rc) { return ($rc -lt 8) }

# video/audio filename filters (closed files only unless -IncludeActive); one
# pattern per date and extension - the mic WAV/FLAC segments follow the same
# <name>_<date>_<start>_to_<end> contract as the video
$videoPatterns = @(); $audioPatterns = @()
if ($Date) {
    foreach ($d0 in $Date) {
        $videoPatterns += $(if ($IncludeActive) { "*_${d0}_*.mp4" } else { "*_${d0}_*_to_*.mp4" })
        foreach ($ext in 'wav', 'flac') {
            $audioPatterns += $(if ($IncludeActive) { "*_${d0}_*.$ext" } else { "*_${d0}_*_to_*.$ext" })
        }
    }
} else {
    $videoPatterns = if ($IncludeActive) { @('*.mp4') } else { @('*_to_*.mp4') }
    $audioPatterns = if ($IncludeActive) { @('*.wav', '*.flac') } else { @('*_to_*.wav', '*_to_*.flac') }
}

Say ("==================================================================") Cyan
Say ("  COPY -> ANALYSIS COMPUTER   (copy-only; source never modified)") Cyan
Say ("  Dest:   $Dest") Cyan
Say ("  Scope:  " + $(if ($Date) { "video for $($Date -join ', ')" } else { 'ALL closed video' }) +
     $(if ($Channels) { "  |  channels: $($Channels -join ',')" } else { '' }) +
     "  |  filter: $($videoPatterns -join ',')") Cyan
Say ("  Modes:  video=$(-not $SkipVideo) thermal=$(-not $SkipThermal) audio=$(-not $SkipAudio) wiser=$(-not $SkipWiser)" +
     "  |  $(if ($DryRun){'DRY RUN'}else{'COPY'})$(if($UsePacing){" (GENTLE: $Threads threads + pacing)"}else{" (FAST: $Threads threads)"})") Cyan
Say ("  Log:    $logFile") Cyan
Say ("==================================================================") Cyan

$fail = 0

# ---- Reolink video: each CHxx -> Dest\Reolink_record\CHxx ----
if (-not $SkipVideo -and (Test-Path $ReolinkRoot)) {
    Say "`n[Reolink video]" White
    foreach ($d in (Get-ChildItem $ReolinkRoot -Directory -EA SilentlyContinue | Where-Object { ($ExcludeDirs -notcontains $_.Name) -and ((-not $Channels) -or ($Channels -contains $_.Name)) })) {
        $dst = Join-Path (Join-Path $Dest 'Reolink_record') $d.Name
        Say "  $($d.Name) -> $dst"
        $rc = Invoke-Robo -Src $d.FullName -Dst $dst -Patterns $videoPatterns
        if (-not (Robo-Ok $rc)) { $fail++; Say "    robocopy FAILED (exit $rc)" Red }
    }
}

# ---- thermal: each group -> Dest\thermal_record\<group> ----
if (-not $SkipThermal -and (Test-Path $ThermalRoot)) {
    Say "`n[thermal]" White
    foreach ($d in (Get-ChildItem $ThermalRoot -Directory -EA SilentlyContinue | Where-Object { $ExcludeDirs -notcontains $_.Name })) {
        $dst = Join-Path (Join-Path $Dest 'thermal_record') $d.Name
        Say "  $($d.Name) -> $dst"
        $rc = Invoke-Robo -Src $d.FullName -Dst $dst -Patterns $videoPatterns
        if (-not (Robo-Ok $rc)) { $fail++; Say "    robocopy FAILED (exit $rc)" Red }
    }
}

# ---- UltraMic audio: each MIC* -> Dest\ultramic_record\MICxx ----
if (-not $SkipAudio -and (Test-Path $UltramicRoot)) {
    Say "`n[UltraMic audio]" White
    foreach ($d in (Get-ChildItem $UltramicRoot -Directory -EA SilentlyContinue | Where-Object { $_.Name -like 'MIC*' })) {
        $dst = Join-Path (Join-Path $Dest 'ultramic_record') $d.Name
        Say "  $($d.Name) -> $dst"
        $rc = Invoke-Robo -Src $d.FullName -Dst $dst -Patterns $audioPatterns
        if (-not (Robo-Ok $rc)) { $fail++; Say "    robocopy FAILED (exit $rc)" Red }
    }
}

# ---- NVR rescue footage: mirror the whole tree additively (small; like WISER) ----
if (-not $SkipRescue -and (Test-Path $RescueRoot)) {
    Say "`n[NVR rescue]" White
    $dst = Join-Path $Dest 'nvr_rescue'
    Say "  $RescueRoot -> $dst  (all rescued dates/channels)"
    $rc = Invoke-Robo -Src $RescueRoot -Dst $dst -Patterns @('*.mp4') -Recurse
    if (-not (Robo-Ok $rc)) { $fail++; Say "    robocopy FAILED (exit $rc)" Red }
}

# ---- WISER: mirror the whole backup tree (snapshots + incremental) additively ----
if (-not $SkipWiser) {
    Say "`n[WISER backup]" White
    if (-not (Test-Path $WiserSource)) {
        Say "  no WISER backup at $WiserSource (run backup_wiser_daily.py first) - skipping" DarkYellow
    } else {
        $dst = Join-Path $Dest 'Wiser_backup'
        Say "  $WiserSource -> $dst  (all snapshots + incremental CSVs)"
        $rc = Invoke-Robo -Src $WiserSource -Dst $dst -Patterns @('*.*') -Recurse
        if (-not (Robo-Ok $rc)) { $fail++; Say "    robocopy FAILED (exit $rc)" Red }
    }
}

# ---- manifest: name+size of every source file in scope, written to the dest ----
# Metadata-only (NO content reads on E:, safe while recording). Consumed by
# verify_on_analysis.ps1 ON THE ANALYSIS COMPUTER, which does all heavy content
# checking against its own local disk - never load the recording drive to verify.
# (The old field-side -Verify hashed E: at full disk speed and starved the
# recorders - see incident_log 2026-08-19.)
if (-not $DryRun) {
    Say "`n[manifest for analysis-side verify]" White

    function Get-ScopeFiles([string]$dir, [string[]]$patterns) {
        $seen = @{}
        foreach ($p in $patterns) {
            foreach ($f in (Get-ChildItem -LiteralPath $dir -Filter $p -File -EA SilentlyContinue)) { $seen[$f.Name] = $f }
        }
        $seen.Values
    }

    $vTargets = @()
    if (-not $SkipVideo -and (Test-Path $ReolinkRoot)) {
        Get-ChildItem $ReolinkRoot -Directory -EA SilentlyContinue | Where-Object { ($ExcludeDirs -notcontains $_.Name) -and ((-not $Channels) -or ($Channels -contains $_.Name)) } |
            ForEach-Object { $vTargets += @{ src = $_.FullName; dst = Join-Path (Join-Path $Dest 'Reolink_record') $_.Name; pat = $videoPatterns; name = $_.Name } }
    }
    if (-not $SkipThermal -and (Test-Path $ThermalRoot)) {
        Get-ChildItem $ThermalRoot -Directory -EA SilentlyContinue | Where-Object { $ExcludeDirs -notcontains $_.Name } |
            ForEach-Object { $vTargets += @{ src = $_.FullName; dst = Join-Path (Join-Path $Dest 'thermal_record') $_.Name; pat = $videoPatterns; name = $_.Name } }
    }
    if (-not $SkipAudio -and (Test-Path $UltramicRoot)) {
        Get-ChildItem $UltramicRoot -Directory -EA SilentlyContinue | Where-Object { $_.Name -like 'MIC*' } |
            ForEach-Object { $vTargets += @{ src = $_.FullName; dst = Join-Path (Join-Path $Dest 'ultramic_record') $_.Name; pat = $audioPatterns; name = $_.Name } }
    }

    # Rows: dest-relative path + source size. Directory metadata only - this
    # NEVER opens/reads file contents, so it cannot load E: while recording.
    $rows = @()
    foreach ($t in $vTargets) {
        $rel = ($t.dst.Substring($Dest.Length).Trim('\', '/')) -replace '\\', '/'
        foreach ($sf in @(Get-ScopeFiles $t.src $t.pat)) {
            $rows += [pscustomobject]@{ RelPath = ('{0}/{1}' -f $rel, $sf.Name); Bytes = $sf.Length }
        }
    }
    $manName = if ($Date) { 'copy_manifest_{0}.csv' -f ($Date -join '_') } else { 'copy_manifest_all.csv' }
    try {
        $rows | Export-Csv -NoTypeInformation -Path (Join-Path $Dest $manName)
        Say ("  {0} file entrie(s) (metadata only) -> {1}" -f $rows.Count, $manName)
        $vScript = Join-Path $PSScriptRoot 'verify_on_analysis.ps1'
        if (Test-Path -LiteralPath $vScript) {
            Copy-Item -LiteralPath $vScript -Destination (Join-Path $Dest 'verify_on_analysis.ps1') -Force
            Say '  verify_on_analysis.ps1 refreshed at dest root - run it ON the analysis computer'
        }
    } catch { Say ("  manifest write failed: {0}" -f $_.Exception.Message) DarkYellow }
}

Say ("`n==================================================================") Cyan
if ($DryRun) {
    Say "  DRY RUN complete - see $logFile for the file list + sizes (robocopy /L)." Yellow
    exit 0
}
if ($fail -eq 0) {
    Say "  >>> DONE - everything copied (or already in sync). Source untouched. <<<" Green
    Say "  Re-run any time to copy only what's new (resumable)." DarkGray
    exit 0
} else {
    Say "  >>> $fail group(s) had robocopy failures - see $logFile. Re-run to retry. <<<" Red
    exit 2
}


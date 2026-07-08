<#
.SYNOPSIS
    Daily continuity QC for field video recorders.

.DESCRIPTION
    Checks hourly MP4 recorders for time gaps between adjacent files and checks
    the SmartPSS PC-NVR placeholder store for recent write activity. It writes
    timestamped text/JSON reports and exits nonzero when gaps or stale recording
    activity are found.

    This script is read-only with respect to recording files.
#>

[CmdletBinding()]
param(
    [string]$ReolinkConfigPath = 'E:\Reolink_record\recorder.config.psd1',
    [string]$ThermalConfigPath = 'E:\thermal_record\thermal.config.psd1',
    [string]$SmartPssMediaRoot = 'E:\media',
    [string]$ReportRoot = 'E:\recording_qc',
    [string]$FfprobePath = '',
    [int]$LookbackHours = 26,
    [datetime]$WindowStart = [datetime]::MinValue,
    [datetime]$WindowEnd = [datetime]::MinValue,
    [int]$MaxGapSeconds = 15,
    [int]$ActiveSampleSeconds = 15,
    [int]$MaxActiveStaleMinutes = 10,
    [switch]$SkipActiveGrowth,
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$script:culture = [System.Globalization.CultureInfo]::InvariantCulture

if ($WindowEnd -eq [datetime]::MinValue) { $WindowEnd = Get-Date }
if ($WindowStart -eq [datetime]::MinValue) { $WindowStart = $WindowEnd.AddHours(-1 * $LookbackHours) }

$script:issues = @()
$script:sourceSummaries = @()
$script:placeholderSummaries = @()

function Add-Issue([string]$Severity, [string]$Source, [string]$Channel, [string]$Message, $Details = $null) {
    $script:issues += [pscustomobject]@{
        Severity = $Severity
        Source   = $Source
        Channel  = $Channel
        Message  = $Message
        Details  = $Details
    }
}

function Get-HandleLen([string]$Path, [double]$Fallback = 0) {
    try {
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $len = $fs.Length
        $fs.Dispose()
        return $len
    } catch {
        return $Fallback
    }
}

function Resolve-FfprobePath([string[]]$Candidates) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $cmd = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-VideoDurationSeconds([string]$ProbePath, [string]$Path) {
    if (-not $ProbePath) { return $null }
    try {
        $raw = & $ProbePath -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $Path 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
        $value = 0.0
        if ([double]::TryParse(($raw | Select-Object -First 1), [System.Globalization.NumberStyles]::Float, $script:culture, [ref]$value)) {
            if ($value -gt 0) { return $value }
        }
    } catch {}
    return $null
}

function Parse-RecordingFile([System.IO.FileInfo]$File, [string]$ProbePath) {
    $rx = '^(?<prefix>.+?)_(?<date>\d{4}-\d{2}-\d{2})_(?<start>\d{2}-\d{2}-\d{2})(?:_to_(?<end>\d{2}-\d{2}-\d{2}))?\.mp4$'
    if ($File.Name -notmatch $rx) { return $null }

    try {
        $start = [datetime]::ParseExact(('{0} {1}' -f $Matches.date, $Matches.start), 'yyyy-MM-dd HH-mm-ss', $script:culture)
    } catch {
        return $null
    }

    $end = $null
    $endSource = 'unknown'
    $durationSeconds = $null

    if ($Matches.end) {
        try {
            $end = [datetime]::ParseExact(('{0} {1}' -f $Matches.date, $Matches.end), 'yyyy-MM-dd HH-mm-ss', $script:culture)
            if ($end -lt $start) { $end = $end.AddDays(1) }
            $endSource = 'filename'
        } catch {
            $end = $null
        }
    }

    if (-not $end) {
        $durationSeconds = Get-VideoDurationSeconds -ProbePath $ProbePath -Path $File.FullName
        if ($durationSeconds) {
            $end = $start.AddSeconds($durationSeconds)
            $endSource = 'ffprobe_duration'
        } elseif ($File.LastWriteTime -gt $start) {
            $end = $File.LastWriteTime
            $endSource = 'last_write_fallback'
        }
    }

    [pscustomobject]@{
        Prefix          = $Matches.prefix
        Name            = $File.Name
        Path            = $File.FullName
        Length          = $File.Length
        LastWriteTime   = $File.LastWriteTime
        Start           = $start
        End             = $end
        EndSource       = $endSource
        DurationSeconds = $durationSeconds
    }
}

function Get-NewestSegment($Segments) {
    @($Segments | Sort-Object Start, Name -Descending | Select-Object -First 1)[0]
}

function Get-SourceSpecs {
    $specs = @()
    $candidateProbePaths = @()

    if (Test-Path -LiteralPath $ReolinkConfigPath) {
        try {
            $cfg = Import-PowerShellDataFile -LiteralPath $ReolinkConfigPath
            $channels = @($cfg.Channels | ForEach-Object { 'CH{0:D2}' -f [int]$_ })
            $specs += [pscustomobject]@{
                Name     = 'Reolink RTSP'
                Root     = $cfg.Root
                Channels = $channels
            }
            if ($cfg.Ffmpeg) { $candidateProbePaths += ($cfg.Ffmpeg -replace 'ffmpeg\.exe$', 'ffprobe.exe') }
            $candidateProbePaths += (Join-Path $cfg.Root 'bin\ffprobe.exe')
        } catch {
            Add-Issue 'ERROR' 'Reolink RTSP' '' ("Failed to read config: {0}" -f $_.Exception.Message)
        }
    } elseif (Test-Path -LiteralPath 'E:\Reolink_record') {
        $specs += [pscustomobject]@{
            Name     = 'Reolink RTSP'
            Root     = 'E:\Reolink_record'
            Channels = @(1..6 | ForEach-Object { 'CH{0:D2}' -f $_ })
        }
        $candidateProbePaths += 'E:\Reolink_record\bin\ffprobe.exe'
    }

    if (Test-Path -LiteralPath $ThermalConfigPath) {
        try {
            $cfg = Import-PowerShellDataFile -LiteralPath $ThermalConfigPath
            $channels = @($cfg.Streams | ForEach-Object { $_.Name })
            $specs += [pscustomobject]@{
                Name     = 'Thermal/Visual RTSP'
                Root     = $cfg.Root
                Channels = $channels
            }
            if ($cfg.Ffmpeg) { $candidateProbePaths += ($cfg.Ffmpeg -replace 'ffmpeg\.exe$', 'ffprobe.exe') }
        } catch {
            Add-Issue 'ERROR' 'Thermal/Visual RTSP' '' ("Failed to read config: {0}" -f $_.Exception.Message)
        }
    } elseif (Test-Path -LiteralPath 'E:\thermal_record') {
        $thermalChannels = Get-ChildItem -LiteralPath 'E:\thermal_record' -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne 'logs' } |
            Select-Object -ExpandProperty Name
        $specs += [pscustomobject]@{
            Name     = 'Thermal/Visual RTSP'
            Root     = 'E:\thermal_record'
            Channels = @($thermalChannels)
        }
    }

    if ($FfprobePath) { $candidateProbePaths = @($FfprobePath) + $candidateProbePaths }
    $probe = Resolve-FfprobePath -Candidates @($candidateProbePaths)
    if (-not $probe) {
        Add-Issue 'WARN' 'All MP4 sources' '' 'ffprobe was not found; files without _to_ end times use LastWriteTime fallback.'
    }

    [pscustomobject]@{
        Specs   = @($specs)
        Ffprobe = $probe
    }
}

function Get-ChannelContexts($Specs, [string]$ProbePath) {
    $contexts = @()

    foreach ($spec in $Specs) {
        if (-not (Test-Path -LiteralPath $spec.Root)) {
            Add-Issue 'ERROR' $spec.Name '' ("Root path not found: {0}" -f $spec.Root)
            continue
        }

        foreach ($channel in $spec.Channels) {
            $dir = Join-Path $spec.Root $channel
            if (-not (Test-Path -LiteralPath $dir)) {
                Add-Issue 'ERROR' $spec.Name $channel ("Channel folder not found: {0}" -f $dir)
                continue
            }

            $files = @(Get-ChildItem -LiteralPath $dir -File -Filter '*.mp4' -ErrorAction SilentlyContinue)
            $segments = @()
            $unparsed = 0
            foreach ($file in $files) {
                $segment = Parse-RecordingFile -File $file -ProbePath $ProbePath
                if ($segment) { $segments += $segment } else { $unparsed++ }
            }
            if ($unparsed -gt 0) {
                Add-Issue 'WARN' $spec.Name $channel ("Skipped {0} file(s) with unrecognized names." -f $unparsed)
            }

            $newest = Get-NewestSegment -Segments $segments
            $contexts += [pscustomobject]@{
                Source   = $spec.Name
                Root     = $spec.Root
                Channel  = $channel
                Directory = $dir
                FileCount = $files.Count
                Segments = @($segments)
                Newest   = $newest
            }
        }
    }

    $contexts
}

function Get-ActiveSamples($Contexts) {
    $initial = @{}
    foreach ($ctx in $Contexts) {
        if (-not $ctx.Newest) { continue }
        $item = Get-Item -LiteralPath $ctx.Newest.Path -ErrorAction SilentlyContinue
        if (-not $item) { continue }
        $initial[$ctx.Newest.Path] = [pscustomobject]@{
            Length        = Get-HandleLen -Path $ctx.Newest.Path -Fallback $item.Length
            LastWriteTime = $item.LastWriteTime
        }
    }

    if (($initial.Count -gt 0) -and (-not $SkipActiveGrowth) -and ($ActiveSampleSeconds -gt 0)) {
        Start-Sleep -Seconds $ActiveSampleSeconds
    }

    $samples = @{}
    foreach ($path in $initial.Keys) {
        $item = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
        if (-not $item) { continue }
        $finalLength = Get-HandleLen -Path $path -Fallback $item.Length
        $finalWrite = $item.LastWriteTime
        $samples[$path] = [pscustomobject]@{
            InitialLength        = $initial[$path].Length
            FinalLength          = $finalLength
            DeltaBytes           = $finalLength - $initial[$path].Length
            InitialLastWriteTime = $initial[$path].LastWriteTime
            FinalLastWriteTime   = $finalWrite
            WriteTimeChanged     = ($finalWrite -gt $initial[$path].LastWriteTime)
            Grew                 = ($finalLength -gt $initial[$path].Length)
            RecentlyTouched      = ($finalWrite -ge (Get-Date).AddMinutes(-1 * $MaxActiveStaleMinutes))
        }
    }
    $samples
}

function Audit-Continuity($Contexts, $ActiveSamples) {
    foreach ($ctx in $Contexts) {
        if ($ctx.FileCount -eq 0) {
            Add-Issue 'ERROR' $ctx.Source $ctx.Channel 'No MP4 files found for channel.'
            continue
        }
        if (-not $ctx.Newest) {
            Add-Issue 'ERROR' $ctx.Source $ctx.Channel 'No parseable MP4 segment files found for channel.'
            continue
        }

        $relevant = @($ctx.Segments | Where-Object {
            $_.Start -le $WindowEnd -and ((-not $_.End) -or $_.End -ge $WindowStart)
        } | Sort-Object Start, Name)

        $newestActive = $null
        if ($ctx.Newest -and $ActiveSamples.ContainsKey($ctx.Newest.Path)) {
            $newestActive = $ActiveSamples[$ctx.Newest.Path]
            if (-not $newestActive.Grew) {
                if ($newestActive.RecentlyTouched) {
                    Add-Issue 'WARN' $ctx.Source $ctx.Channel ("Newest file did not grow during {0}s sample, but was recently touched." -f $ActiveSampleSeconds) @{
                        Path = $ctx.Newest.Path
                        LastWriteTime = $newestActive.FinalLastWriteTime
                    }
                } else {
                    Add-Issue 'ERROR' $ctx.Source $ctx.Channel ("Newest file is stale and did not grow during {0}s sample." -f $ActiveSampleSeconds) @{
                        Path = $ctx.Newest.Path
                        LastWriteTime = $newestActive.FinalLastWriteTime
                    }
                }
            }
        }

        if ($relevant.Count -eq 0) {
            Add-Issue 'ERROR' $ctx.Source $ctx.Channel ("No segment overlaps the audit window {0} to {1}." -f $WindowStart, $WindowEnd)
            continue
        }

        $first = $relevant[0]
        $last = $relevant[$relevant.Count - 1]
        if (($first.Start - $WindowStart).TotalSeconds -gt $MaxGapSeconds) {
            Add-Issue 'ERROR' $ctx.Source $ctx.Channel ("Coverage starts late by {0:n1}s." -f (($first.Start - $WindowStart).TotalSeconds)) @{
                ExpectedFrom = $WindowStart
                FirstStart   = $first.Start
                FirstFile    = $first.Path
            }
        }

        for ($i = 0; $i -lt ($relevant.Count - 1); $i++) {
            $a = $relevant[$i]
            $b = $relevant[$i + 1]
            if (-not $a.End) {
                Add-Issue 'WARN' $ctx.Source $ctx.Channel ("Cannot determine end time for {0}; gap check to next file is uncertain." -f $a.Name)
                continue
            }
            $gap = ($b.Start - $a.End).TotalSeconds
            if ($gap -gt $MaxGapSeconds) {
                Add-Issue 'ERROR' $ctx.Source $ctx.Channel ("Gap of {0:n1}s between adjacent files." -f $gap) @{
                    PreviousFile = $a.Path
                    PreviousEnd  = $a.End
                    NextFile     = $b.Path
                    NextStart    = $b.Start
                    GapSeconds   = [math]::Round($gap, 3)
                }
            }
        }

        $lastIsNewest = ($ctx.Newest -and ($last.Path -eq $ctx.Newest.Path))
        $lastIsLive = $lastIsNewest -and $newestActive -and ($newestActive.Grew -or $newestActive.RecentlyTouched)
        if ((-not $lastIsLive) -and $last.End -and (($WindowEnd - $last.End).TotalSeconds -gt $MaxGapSeconds)) {
            Add-Issue 'ERROR' $ctx.Source $ctx.Channel ("Coverage ends early by {0:n1}s." -f (($WindowEnd - $last.End).TotalSeconds)) @{
                ExpectedUntil = $WindowEnd
                LastEnd       = $last.End
                LastFile      = $last.Path
            }
        }

        $script:sourceSummaries += [pscustomobject]@{
            Source              = $ctx.Source
            Channel             = $ctx.Channel
            Directory           = $ctx.Directory
            FileCount           = $ctx.FileCount
            RelevantFileCount   = $relevant.Count
            FirstRelevantStart  = $first.Start
            LastRelevantStart   = $last.Start
            LastRelevantEnd     = $last.End
            NewestFile          = $ctx.Newest.Path
            NewestDeltaBytes    = if ($newestActive) { $newestActive.DeltaBytes } else { $null }
            NewestLastWriteTime = if ($newestActive) { $newestActive.FinalLastWriteTime } else { $ctx.Newest.LastWriteTime }
        }
    }
}

function Audit-SmartPssPlaceholderStore([string]$Root) {
    if (-not $Root) { return }
    if (-not (Test-Path -LiteralPath $Root)) {
        Add-Issue 'WARN' 'SmartPSS PC-NVR' '' ("Placeholder root not found: {0}" -f $Root)
        return
    }

    $files = @(Get-ChildItem -LiteralPath $Root -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d+$' })
    if ($files.Count -eq 0) {
        Add-Issue 'ERROR' 'SmartPSS PC-NVR' 'media' ("No numbered placeholder files found in {0}." -f $Root)
        return
    }

    $initial = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $initialWrite = $initial.LastWriteTime
    if (-not $SkipActiveGrowth -and $ActiveSampleSeconds -gt 0) {
        Start-Sleep -Seconds $ActiveSampleSeconds
    }
    $latest = Get-ChildItem -LiteralPath $Root -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+$' } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    $recent = $latest.LastWriteTime -ge (Get-Date).AddMinutes(-1 * $MaxActiveStaleMinutes)
    $changed = $latest.LastWriteTime -gt $initialWrite
    if (-not $recent) {
        Add-Issue 'ERROR' 'SmartPSS PC-NVR' 'media' ("Newest placeholder file is stale: {0}" -f $latest.FullName) @{
            LastWriteTime = $latest.LastWriteTime
            MaxActiveStaleMinutes = $MaxActiveStaleMinutes
        }
    } elseif (-not $changed) {
        Add-Issue 'WARN' 'SmartPSS PC-NVR' 'media' ("Newest placeholder did not change during {0}s sample, but was recently touched." -f $ActiveSampleSeconds) @{
            LastWriteTime = $latest.LastWriteTime
        }
    }

    $script:placeholderSummaries += [pscustomobject]@{
        Source            = 'SmartPSS PC-NVR'
        Root              = $Root
        PlaceholderCount  = $files.Count
        NewestPlaceholder = $latest.FullName
        InitialWriteTime  = $initialWrite
        FinalWriteTime    = $latest.LastWriteTime
        WriteTimeChanged  = $changed
        RecentlyTouched   = $recent
        Note              = 'Filesystem check confirms placeholder write recency only; exact per-channel SmartPSS gaps require SmartPSS playback/index inspection.'
    }
}

$sourceInfo = Get-SourceSpecs
$contexts = Get-ChannelContexts -Specs $sourceInfo.Specs -ProbePath $sourceInfo.Ffprobe
$activeSamples = Get-ActiveSamples -Contexts $contexts
Audit-Continuity -Contexts $contexts -ActiveSamples $activeSamples
Audit-SmartPssPlaceholderStore -Root $SmartPssMediaRoot

$result = [pscustomobject]@{
    RunTime                 = (Get-Date)
    WindowStart             = $WindowStart
    WindowEnd               = $WindowEnd
    MaxGapSeconds           = $MaxGapSeconds
    ActiveSampleSeconds     = $ActiveSampleSeconds
    MaxActiveStaleMinutes   = $MaxActiveStaleMinutes
    Ffprobe                 = $sourceInfo.Ffprobe
    Sources                 = @($script:sourceSummaries)
    PlaceholderStores       = @($script:placeholderSummaries)
    Issues                  = @($script:issues)
}

New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$stamp = (Get-Date -Format 'yyyyMMdd_HHmmss')
$txtPath = Join-Path $ReportRoot ("recording_continuity_{0}.txt" -f $stamp)
$jsonPath = Join-Path $ReportRoot ("recording_continuity_{0}.json" -f $stamp)
$latestTxt = Join-Path $ReportRoot 'latest_recording_continuity.txt'
$latestJson = Join-Path $ReportRoot 'latest_recording_continuity.json'

$errorCount = @($script:issues | Where-Object Severity -eq 'ERROR').Count
$warnCount = @($script:issues | Where-Object Severity -eq 'WARN').Count
$status = if ($errorCount -gt 0) { 'ERROR' } elseif ($warnCount -gt 0) { 'WARN' } else { 'OK' }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add(("Recording continuity status: {0}" -f $status)) | Out-Null
$lines.Add(("Run time: {0}" -f $result.RunTime.ToString('yyyy-MM-dd HH:mm:ss'))) | Out-Null
$lines.Add(("Audit window: {0} to {1}" -f $WindowStart.ToString('yyyy-MM-dd HH:mm:ss'), $WindowEnd.ToString('yyyy-MM-dd HH:mm:ss'))) | Out-Null
$lines.Add(("Gap tolerance: {0}s; active stale threshold: {1} min" -f $MaxGapSeconds, $MaxActiveStaleMinutes)) | Out-Null
$lines.Add(("ffprobe: {0}" -f ($(if ($sourceInfo.Ffprobe) { $sourceInfo.Ffprobe } else { '(not found)' })))) | Out-Null
$lines.Add('') | Out-Null
$lines.Add('Channel summaries:') | Out-Null
foreach ($s in $script:sourceSummaries) {
    $lines.Add(("- {0} {1}: {2} file(s), {3} in window, newest delta {4} bytes, newest write {5}" -f
        $s.Source, $s.Channel, $s.FileCount, $s.RelevantFileCount, $s.NewestDeltaBytes, $s.NewestLastWriteTime)) | Out-Null
}
$lines.Add('') | Out-Null
$lines.Add('SmartPSS placeholder summaries:') | Out-Null
foreach ($p in $script:placeholderSummaries) {
    $lines.Add(("- {0}: {1} placeholders, newest {2}, write changed={3}, recent={4}" -f
        $p.Root, $p.PlaceholderCount, $p.NewestPlaceholder, $p.WriteTimeChanged, $p.RecentlyTouched)) | Out-Null
    $lines.Add(("  Note: {0}" -f $p.Note)) | Out-Null
}
$lines.Add('') | Out-Null
$lines.Add(("Issues: {0} error(s), {1} warning(s)" -f $errorCount, $warnCount)) | Out-Null
if ($script:issues.Count -eq 0) {
    $lines.Add('- none') | Out-Null
} else {
    foreach ($issue in $script:issues) {
        $where = (($issue.Source, $issue.Channel) | Where-Object { $_ }) -join ' / '
        $lines.Add(("- [{0}] {1}: {2}" -f $issue.Severity, $where, $issue.Message)) | Out-Null
        if ($issue.Details) {
            $lines.Add(("  Details: {0}" -f (($issue.Details | ConvertTo-Json -Compress -Depth 5)))) | Out-Null
        }
    }
}

$text = $lines -join [Environment]::NewLine
Set-Content -LiteralPath $txtPath -Value $text -Encoding UTF8
Set-Content -LiteralPath $latestTxt -Value $text -Encoding UTF8
$json = $result | ConvertTo-Json -Depth 8
Set-Content -LiteralPath $jsonPath -Value $json -Encoding UTF8
Set-Content -LiteralPath $latestJson -Value $json -Encoding UTF8

if (-not $Quiet) { Write-Output $text }
if ($errorCount -gt 0) { exit 2 }
if ($warnCount -gt 0) { exit 1 }
exit 0

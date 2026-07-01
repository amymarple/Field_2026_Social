<#
.SYNOPSIS
    Daily continuity SMOKE ALARM for the 24/7 field recordings.

    It answers one question fast: "Did each camera appear to record continuously
    during the last 24 hours (yesterday 05:00 -> today 05:00)?"

    By DEFAULT it is filename + filesystem-metadata only. It does NOT read every
    video, does NOT decode/count frames, and does NOT run ffprobe on every file.
    ffprobe (container metadata only, short timeout) runs ONLY when you pass
    -DeepCheck, or against the few files flagged suspicious by fast metadata.

    Scans E:\Reolink_record (CHxx) and E:\thermal_record (1xx_*). Groups by
    folder. Per group, over the window, it reports:
        - gaps / overlaps      (from filename start/end timestamps)
        - suspicious files     (zero-byte, tiny, small-vs-neighbors, stale active,
                                impossible/negative duration, unparseable names)
        - coverage %           (how much of the 24 h is covered)
    Writes a .csv (per-file) and a readable .md report to E:\recording_health_reports.
    READ-ONLY: never modifies the recordings.

.PARAMETER Fast            (default) filename + filesystem metadata only. No ffprobe.
.PARAMETER DeepCheck       Also run ffprobe (container metadata) on in-window files.
.PARAMETER ProbeSuspicious Run ffprobe ONLY on files fast-metadata flagged suspicious.
.PARAMETER SkipFfprobe     Never run ffprobe.
.PARAMETER MaxProbeFiles      Cap on how many files ffprobe is run against. Default 20.
.PARAMETER ProbeTimeoutSeconds Per-file ffprobe timeout. Default 5. Timeout -> metadata_timeout.
.PARAMETER DryRun      Print the report to console; write nothing. Also fast by default.
.PARAMETER SelfTest    Run parser + gap logic on built-in FAKE filenames (no disk). Exit.
.PARAMETER RefTime     Override "now" (yyyy-MM-dd HH:mm:ss) to re-check a past day.

.NOTES
    Exit codes: 0 = OK, 1 = warnings, 2 = errors.
#>

param(
    [switch]$Fast,
    [switch]$DeepCheck,
    [switch]$ProbeSuspicious,
    [switch]$SkipFfprobe,
    [int]$MaxProbeFiles = 20,
    [int]$ProbeTimeoutSeconds = 5,
    [switch]$DryRun,
    [switch]$SelfTest,
    [string]$RefTime
)

# ============================ CONFIG (edit here) ============================
$Config = @{
    Roots       = @('E:\Reolink_record', 'E:\thermal_record')  # each subfolder = one group
    ExcludeDirs = @('bin', 'logs')
    ReportDir   = 'E:\recording_health_reports'
    Extensions  = @('.mp4')

    CheckHour   = 5            # window ends at the most recent 05:00 at/before "now"

    # --- continuity (filename timestamps) ---
    GapToleranceSeconds = 5    # seams within +/- this are normal (wallclock chaining)
    FailGapSeconds      = 120  # a single gap bigger than this -> ERROR (else WARN)
    MinCoveragePercent  = 99.0 # covered < this % of 24 h -> ERROR
    ExpectedSegmentSeconds = 3600

    # --- suspicious files (fast filesystem metadata) ---
    ZeroByteIsError          = $true
    TinyFileBytes            = 1048576   # < 1 MB (non-active, non-boundary) -> suspicious
    # "unusually small" is judged by DATA RATE, not raw size, so naturally-short
    # segments (a few-minute file after a restart) are NOT flagged.
    LowBitrateFactor         = 0.25      # bytes/sec < 25% of the group's median rate -> suspicious
    LowBitrateMinDurationSeconds = 60    # ...only for files at least this long (bps is noisy below this)
    StillBeingWrittenMinutes = 10        # mtime within this -> "being written" (info, for the active file)
    StaleActiveMinutes       = 10        # active file not written within this -> ERROR (recorder down)

    # --- ffprobe (only when DeepCheck or for suspicious files; container metadata only) ---
    FfprobePath = 'E:\Reolink_record\bin\ffprobe.exe'   # falls back to PATH
    FfprobeDurationToleranceSeconds = 30                 # |ffprobe - filename| over this -> flag (deep mode)
}
# ===========================================================================

$ErrorActionPreference = 'Continue'

# <group>_YYYY-MM-DD_HH-MM-SS_to_HH-MM-SS.mp4 (finished) | ..._HH-MM-SS.mp4 (active, no _to_)
$NamePattern = '^(?<grp>.+)_(?<date>\d{4}-\d{2}-\d{2})_(?<start>\d{2}-\d{2}-\d{2})(?:_to_(?<end>\d{2}-\d{2}-\d{2}))?$'

function Parse-RecordingName {
    param([string]$BaseName, [string]$GroupFolder)
    $r = [ordered]@{ Group=$GroupFolder; BaseName=$BaseName; Start=$null; End=$null; IsActive=$false; Parsed=$false; Problem=$null }
    $m = [regex]::Match($BaseName, $NamePattern)
    if (-not $m.Success) { $r.Problem = 'unparseable filename'; return [pscustomobject]$r }
    $datePart = $m.Groups['date'].Value
    try { $start = [datetime]::ParseExact("$datePart $($m.Groups['start'].Value)", 'yyyy-MM-dd HH-mm-ss', $null) }
    catch { $r.Problem = 'bad start timestamp'; return [pscustomobject]$r }
    $r.Start = $start
    if ($m.Groups['end'].Success) {
        try { $end = [datetime]::ParseExact("$datePart $($m.Groups['end'].Value)", 'yyyy-MM-dd HH-mm-ss', $null) }
        catch { $r.Problem = 'bad end timestamp'; return [pscustomobject]$r }
        if ($end -le $start) { $end = $end.AddDays(1) }              # past-midnight wrap
        if (($end - $start).TotalHours -gt 3) { $r.Problem = 'impossible/negative duration' }
        $r.End = $end
    } else { $r.IsActive = $true }
    $r.Parsed = $true
    [pscustomobject]$r
}

function Get-HandleLen {   # true size of an actively-written file, without scanning content
    param([string]$Path, [double]$Fallback = 0)
    try { $fs=[System.IO.File]::Open($Path,'Open','Read','ReadWrite'); $l=$fs.Length; $fs.Dispose(); return $l }
    catch { return $Fallback }
}

# ffprobe container metadata ONLY, with a hard timeout. Never decodes frames.
function Invoke-Ffprobe {
    param([string]$Path, [string]$Exe, [int]$TimeoutSec)
    $outF = [IO.Path]::GetTempFileName(); $errF = [IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $Exe -PassThru -NoNewWindow -RedirectStandardOutput $outF -RedirectStandardError $errF `
             -ArgumentList @('-v','error','-show_entries','format=duration','-of','csv=p=0','--', $Path)
        if (-not $p.WaitForExit($TimeoutSec * 1000)) { try { $p.Kill() } catch {}; return @{ Status='metadata_timeout'; Duration=$null } }
        $out = (Get-Content $outF -Raw -EA SilentlyContinue)
        if ($out) { $out = $out.Trim() }
        if ($out -and ($out -as [double])) { return @{ Status='ok'; Duration=[double]$out } }
        return @{ Status='unreadable'; Duration=$null }
    } catch { return @{ Status='unreadable'; Duration=$null } }
    finally { Remove-Item $outF,$errF -Force -EA SilentlyContinue }
}

function Get-Window { param([datetime]$Ref,[int]$Hour)
    $end = $Ref.Date.AddHours($Hour); if ($Ref -lt $end) { $end = $end.AddDays(-1) }
    @{ Start = $end.AddDays(-1); End = $end }
}

function fmtDur { param([double]$s) $s=[math]::Round($s)
    if ($s -lt 60) { return "${s}s" }
    if ($s -lt 3600) { return ("{0}m{1:d2}s" -f [int]($s/60),[int]($s%60)) }
    return ("{0}h{1:d2}m" -f [int]($s/3600),[int](($s%3600)/60))
}
function fmtT { param([datetime]$t) $t.ToString('MM-dd HH:mm:ss') }

# ---------- per-group analysis (fast: filenames + filesystem metadata) ----------
function Analyze-Group {
    param($GroupName,$Files,$WinStart,$WinEnd,$Cfg)
    $issues = New-Object System.Collections.Generic.List[object]
    $tol = $Cfg.GapToleranceSeconds
    $now = if ($script:RefNow) { $script:RefNow } else { Get-Date }

    # tag in-window + collect for continuity
    $intervals = @()
    foreach ($f in $Files) {
        $eff = if ($f.IsActive) { $f.LastWrite } else { $f.End }
        $inWin = $f.Parsed -and $f.Start -and $eff -and ($f.Start -lt $WinEnd) -and ($eff -gt $WinStart)
        Add-Member -InputObject $f -NotePropertyName InWindow  -NotePropertyValue $inWin -Force
        Add-Member -InputObject $f -NotePropertyName Suspicious -NotePropertyValue $false -Force
        Add-Member -InputObject $f -NotePropertyName SuspectReason -NotePropertyValue $null -Force
        if (-not $f.Parsed) {
            $issues.Add([pscustomobject]@{ Severity='ERROR'; Type='unparseable'; Detail=$f.BaseName })
            continue
        }
        if ($f.Problem) {
            $issues.Add([pscustomobject]@{ Severity='ERROR'; Type='bad-timestamp'; Detail=("{0}: {1}" -f $f.BaseName,$f.Problem) })
        }
        if ($inWin) {
            $intervals += [pscustomobject]@{
                Start=$f.Start; End=$eff
                ClipStart=([datetime]([math]::Max($f.Start.Ticks,$WinStart.Ticks)))
                ClipEnd  =([datetime]([math]::Min($eff.Ticks,$WinEnd.Ticks)))
                File=$f
            }
        }
    }

    # ---- suspicious files from fast metadata (in-window only) ----
    $inFiles = $Files | Where-Object { $_.InWindow }
    $finished = $inFiles | Where-Object { -not $_.IsActive -and $_.End }
    # median DATA RATE (bytes/sec) over stable finished files, so short segments
    # aren't mistaken for small ones.
    $bpsList = @()
    foreach ($f in $finished) {
        $d = ($f.End - $f.Start).TotalSeconds
        if ($d -ge $Cfg.LowBitrateMinDurationSeconds -and $f.SizeBytes -gt 0) { $bpsList += ($f.SizeBytes / $d) }
    }
    $bpsList = @($bpsList | Sort-Object)
    $medianBps = if ($bpsList.Count -gt 0) { $bpsList[[int]([math]::Floor(($bpsList.Count-1)/2))] } else { 0 }

    foreach ($f in $inFiles) {
        $boundary = ($f.Start -lt $WinStart) -or ($f.End -gt $WinEnd) -or $f.IsActive
        if ($f.IsActive) {
            $ageMin = ($now - $f.LastWrite).TotalMinutes
            if ($ageMin -le $Cfg.StillBeingWrittenMinutes) {
                $issues.Add([pscustomobject]@{ Severity='INFO'; Type='being-written'; Detail=("{0} (active, written {1:n0} min ago)" -f $f.BaseName,$ageMin) })
            }
            continue   # active file is judged by stale-active below, not by size
        }
        if ($f.SizeBytes -eq 0) {
            $f.Suspicious=$true; $f.SuspectReason='zero-byte'
            $sev = if ($Cfg.ZeroByteIsError) {'ERROR'} else {'WARN'}
            $issues.Add([pscustomobject]@{ Severity=$sev; Type='zero-byte'; Detail=$f.BaseName })
        }
        elseif (-not $boundary -and $f.SizeBytes -lt $Cfg.TinyFileBytes) {
            $f.Suspicious=$true; $f.SuspectReason='tiny-file'
            $issues.Add([pscustomobject]@{ Severity='WARN'; Type='tiny-file'; Detail=("{0} is only {1} KB" -f $f.BaseName,[math]::Round($f.SizeBytes/1KB)) })
        }
        elseif (-not $boundary -and $medianBps -gt 0) {
            $d = ($f.End - $f.Start).TotalSeconds
            if ($d -ge $Cfg.LowBitrateMinDurationSeconds) {
                $bps = $f.SizeBytes / $d
                if ($bps -lt ($medianBps * $Cfg.LowBitrateFactor)) {
                    $f.Suspicious=$true; $f.SuspectReason='low-bitrate'
                    $issues.Add([pscustomobject]@{ Severity='WARN'; Type='low-bitrate'
                        Detail=("{0}: {1} kbps vs group median {2} kbps ({3} over {4})" -f $f.BaseName,[math]::Round($bps*8/1000),[math]::Round($medianBps*8/1000),("{0} MB" -f [math]::Round($f.SizeBytes/1MB,1)),(fmtDur $d)) })
                }
            }
        }
    }

    # ---- continuity from filename timestamps ----
    $coverage = 0
    if ($intervals.Count -eq 0) {
        $issues.Add([pscustomobject]@{ Severity='ERROR'; Type='no-data'; Detail='no files in the 24 h window' })
    } else {
        $intervals = $intervals | Sort-Object Start
        $coveredSec = 0.0
        $firstClip = ($intervals | Select-Object -First 1).ClipStart
        $lead = ($firstClip - $WinStart).TotalSeconds
        if ($lead -gt $tol) {
            $sev = if ($lead -gt $Cfg.FailGapSeconds) {'ERROR'} else {'WARN'}
            $issues.Add([pscustomobject]@{ Severity=$sev; Type='gap-start'; Detail=("missing {0} at window start ({1} -> {2})" -f (fmtDur $lead),(fmtT $WinStart),(fmtT $firstClip)) })
        }
        $cursor = $firstClip
        foreach ($iv in $intervals) {
            $delta = ($iv.ClipStart - $cursor).TotalSeconds
            if ($delta -gt $tol) {
                $sev = if ($delta -gt $Cfg.FailGapSeconds) {'ERROR'} else {'WARN'}
                $issues.Add([pscustomobject]@{ Severity=$sev; Type='gap'; Detail=("{0} gap ({1} -> {2}) before {3}" -f (fmtDur $delta),(fmtT $cursor),(fmtT $iv.ClipStart),$iv.File.BaseName) })
            } elseif ($delta -lt (-1*$tol)) {
                $issues.Add([pscustomobject]@{ Severity='WARN'; Type='overlap'; Detail=("{0} overlap before {1}" -f (fmtDur ([math]::Abs($delta))),$iv.File.BaseName) })
            }
            if ($iv.ClipEnd -gt $cursor) {
                $coveredSec += ($iv.ClipEnd - ([datetime]([math]::Max($cursor.Ticks,$iv.ClipStart.Ticks)))).TotalSeconds
                $cursor = $iv.ClipEnd
            }
        }
        $trail = ($WinEnd - $cursor).TotalSeconds
        if ($trail -gt $tol) {
            $sev = if ($trail -gt $Cfg.FailGapSeconds) {'ERROR'} else {'WARN'}
            $issues.Add([pscustomobject]@{ Severity=$sev; Type='gap-end'; Detail=("missing {0} at window end ({1} -> {2})" -f (fmtDur $trail),(fmtT $cursor),(fmtT $WinEnd)) })
        }
        $coverage = [math]::Round(($coveredSec/86400.0)*100,2)
        if ($coverage -lt $Cfg.MinCoveragePercent) {
            $issues.Add([pscustomobject]@{ Severity='ERROR'; Type='low-coverage'; Detail=("only {0}% of the 24 h window covered (min {1}%)" -f $coverage,$Cfg.MinCoveragePercent) })
        }
    }

    # ---- stale active file (recorder-down detector) ----
    $activeNow = $Files | Where-Object { $_.IsActive } | Sort-Object Start | Select-Object -Last 1
    if ($activeNow) {
        $ageMin = ($now - $activeNow.LastWrite).TotalMinutes
        if ($ageMin -gt $Cfg.StaleActiveMinutes) {
            $issues.Add([pscustomobject]@{ Severity='ERROR'; Type='stale-active'; Detail=("active file {0} not written for {1:n0} min - recorder may be down" -f $activeNow.BaseName,$ageMin) })
        }
    } else {
        $issues.Add([pscustomobject]@{ Severity='WARN'; Type='no-active-file'; Detail='no in-progress recording file found - recorder may be down' })
    }

    [pscustomobject]@{ Group=$GroupName; Issues=$issues; CoveragePercent=$coverage; FileCount=($inFiles | Measure-Object).Count; Records=$inFiles }
}

# ============================ SELF TEST ============================
if ($SelfTest) {
    Write-Host "=== SELF TEST (fake filenames, no disk access) ===" -ForegroundColor Cyan
    $script:RefNow = [datetime]'2026-06-26 05:00:01'
    $win = Get-Window -Ref $script:RefNow -Hour 5
    Write-Host ("window: {0} -> {1}" -f $win.Start,$win.End)
    $fake=@(); $base=[datetime]'2026-06-25 05:00:00'
    for ($h=0;$h -lt 24;$h++){
        $s=$base.AddHours($h); $e=$s.AddHours(1)
        if ($h -eq 9){ continue }                # missing hour -> gap
        if ($h -eq 14){ $e=$s.AddMinutes(3) }    # short segment -> creates a gap too
        $name="FAKE_{0}_{1}_to_{2}" -f $s.ToString('yyyy-MM-dd'),$s.ToString('HH-mm-ss'),$e.ToString('HH-mm-ss')
        $sz = 4GB
        if ($h -eq 20){ $sz = 300MB }            # full hour but low data rate -> low-bitrate
        if ($h -eq 21){ $sz = 200000 }           # < 1 MB full hour -> tiny-file
        $fake += [pscustomobject]@{ Group='FAKE';BaseName=$name;Start=$s;End=$e;IsActive=$false;Parsed=$true;Problem=$null;LastWrite=$e;SizeBytes=$sz;FullName="X:\$name.mp4" }
    }
    $fake += (Parse-RecordingName -BaseName 'totally_wrong_name' -GroupFolder 'FAKE')
    $res = Analyze-Group -GroupName 'FAKE' -Files $fake -WinStart $win.Start -WinEnd $win.End -Cfg $Config
    Write-Host ("`nGroup FAKE  coverage={0}%  in-window files={1}" -f $res.CoveragePercent,$res.FileCount)
    foreach ($i in $res.Issues){ Write-Host ("  [{0}] {1}: {2}" -f $i.Severity,$i.Type,$i.Detail) }
    Write-Host "`nExpected: gap ~09:00, short+gap ~14:00, low-bitrate ~20:00, tiny-file ~21:00, unparseable, no-active-file, coverage<100%." -ForegroundColor DarkGray
    exit 0
}

# ============================ MAIN ============================
$script:RefNow = if ($RefTime) { [datetime]$RefTime } else { Get-Date }
$win = Get-Window -Ref $script:RefNow -Hour $Config.CheckHour
$checkMode = if ($DeepCheck) { 'deep_ffprobe' } else { 'fast_filename_only' }

Write-Host ("Health check  mode={0}  window={1} -> {2}" -f $checkMode,$win.Start,$win.End) -ForegroundColor Cyan

# ffprobe runs ONLY when explicitly requested (-DeepCheck or -ProbeSuspicious),
# never by default. -SkipFfprobe forces it off entirely.
$wantProbe = (($DeepCheck) -or ($ProbeSuspicious)) -and (-not $SkipFfprobe)
$ffprobe = $null
if ($wantProbe) {
    if (Test-Path $Config.FfprobePath) { $ffprobe = $Config.FfprobePath }
    else { $c = Get-Command ffprobe -EA SilentlyContinue; if ($c) { $ffprobe = $c.Source } }
    if (-not $ffprobe) { Write-Host "ffprobe requested but not found - continuing filename-only." -ForegroundColor Yellow }
}

# discover groups
$groups = [ordered]@{}
foreach ($root in $Config.Roots) {
    if (-not (Test-Path $root)) { Write-Host ("WARNING: root missing: {0}" -f $root) -ForegroundColor Yellow; continue }
    Get-ChildItem $root -Directory -EA SilentlyContinue | Where-Object { $Config.ExcludeDirs -notcontains $_.Name } |
        ForEach-Object { $groups[$_.FullName] = $_.Name }
}

$allFileRows  = New-Object System.Collections.Generic.List[object]
$groupResults = New-Object System.Collections.Generic.List[object]
$script:ProbesUsed = 0
$probedAny = $false

foreach ($dir in $groups.Keys) {
    $gname = $groups[$dir]
    $records = @()
    foreach ($file in (Get-ChildItem $dir -File -EA SilentlyContinue | Where-Object { $Config.Extensions -contains $_.Extension.ToLower() })) {
        $rec = Parse-RecordingName -BaseName $file.BaseName -GroupFolder $gname
        Add-Member -InputObject $rec -NotePropertyName LastWrite -NotePropertyValue $file.LastWriteTime -Force
        Add-Member -InputObject $rec -NotePropertyName SizeBytes -NotePropertyValue (Get-HandleLen $file.FullName $file.Length) -Force
        Add-Member -InputObject $rec -NotePropertyName FullName  -NotePropertyValue $file.FullName -Force
        Add-Member -InputObject $rec -NotePropertyName Probe       -NotePropertyValue $null -Force
        Add-Member -InputObject $rec -NotePropertyName ProbeStatus -NotePropertyValue $null -Force
        $records += $rec
    }

    $res = Analyze-Group -GroupName $gname -Files $records -WinStart $win.Start -WinEnd $win.End -Cfg $Config

    # ---- optional ffprobe (container metadata only, budgeted, timed out) ----
    if ($ffprobe) {
        $toProbe = if ($DeepCheck) {
            $res.Records | Where-Object { -not $_.IsActive -and $_.Parsed }   # all in-window finished
        } else {
            $res.Records | Where-Object { $_.Suspicious }                     # -ProbeSuspicious: flagged only
        }
        foreach ($r in $toProbe) {
            if ($script:ProbesUsed -ge $MaxProbeFiles) { break }
            $script:ProbesUsed++; $probedAny = $true
            $pr = Invoke-Ffprobe -Path $r.FullName -Exe $ffprobe -TimeoutSec $ProbeTimeoutSeconds
            $r.ProbeStatus = $pr.Status; $r.Probe = $pr.Duration
            if ($pr.Status -eq 'metadata_timeout') {
                $res.Issues.Add([pscustomobject]@{ Severity='WARN'; Type='metadata_timeout'; Detail=("ffprobe timed out on {0}" -f $r.BaseName) })
            } elseif ($pr.Status -eq 'unreadable') {
                $res.Issues.Add([pscustomobject]@{ Severity='ERROR'; Type='unreadable'; Detail=("ffprobe could not read {0}" -f $r.BaseName) })
            } elseif ($DeepCheck -and $r.End) {
                $fnDur = ($r.End - $r.Start).TotalSeconds
                if ([math]::Abs($pr.Duration - $fnDur) -gt $Config.FfprobeDurationToleranceSeconds) {
                    $res.Issues.Add([pscustomobject]@{ Severity='WARN'; Type='duration-mismatch'; Detail=("{0}: filename {1} vs ffprobe {2}" -f $r.BaseName,(fmtDur $fnDur),(fmtDur $pr.Duration)) })
                }
            }
        }
    }

    $groupResults.Add($res)

    foreach ($r in $res.Records) {
        $allFileRows.Add([pscustomobject]@{
            Group=$gname; File=$r.BaseName+'.mp4'
            Start=if($r.Start){$r.Start.ToString('yyyy-MM-dd HH:mm:ss')}else{''}
            End  =if($r.End){$r.End.ToString('yyyy-MM-dd HH:mm:ss')}else{''}
            Active=$r.IsActive
            DurFilenameS=if($r.End -and $r.Start){[math]::Round(($r.End-$r.Start).TotalSeconds)}else{''}
            DurFfprobeS =if($null -ne $r.Probe){[math]::Round($r.Probe)}else{''}
            ProbeStatus =$r.ProbeStatus
            SizeMB=[math]::Round($r.SizeBytes/1MB,1)
            LastWrite=$r.LastWrite.ToString('yyyy-MM-dd HH:mm:ss')
            Suspect=$r.SuspectReason; Problem=$r.Problem
        })
    }
}

# ---------- overall ----------
$errCount=0; $warnCount=0
foreach ($g in $groupResults) {
    $errCount  += ($g.Issues | Where-Object { $_.Severity -eq 'ERROR' }).Count
    $warnCount += ($g.Issues | Where-Object { $_.Severity -eq 'WARN'  }).Count
}
$overall = if ($errCount -gt 0){'FAIL'} elseif ($warnCount -gt 0){'WARN'} else {'PASS'}
$exit    = if ($errCount -gt 0){2} elseif ($warnCount -gt 0){1} else {0}
$videoMetaNote = if ($SkipFfprobe) { 'skipped (ffprobe disabled)' }
                 elseif ($probedAny) { "checked via ffprobe ($script:ProbesUsed file(s), container metadata only)" }
                 else { 'skipped (filename + filesystem metadata only)' }

# ---------- markdown ----------
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$md = New-Object System.Collections.Generic.List[string]
$md.Add("# Recording health report")
$md.Add("")
$md.Add("- generated:      $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))")
$md.Add("- check_mode:     $checkMode")
$md.Add("- video_metadata: $videoMetaNote")
$md.Add("- window:         $($win.Start.ToString('yyyy-MM-dd HH:mm')) -> $($win.End.ToString('yyyy-MM-dd HH:mm'))  (24 h, continuity from filename timestamps)")
$md.Add("- groups:         $($groupResults.Count)")
$md.Add("- **overall:      $overall**  ($errCount error(s), $warnCount warning(s))")
$md.Add("")
$md.Add("| Group | Coverage | Files | Errors | Warnings | Status |")
$md.Add("|---|---:|---:|---:|---:|---|")
foreach ($g in $groupResults) {
    $e=($g.Issues|Where-Object{$_.Severity -eq 'ERROR'}).Count
    $w=($g.Issues|Where-Object{$_.Severity -eq 'WARN'}).Count
    $st=if($e -gt 0){'FAIL'}elseif($w -gt 0){'WARN'}else{'PASS'}
    $md.Add(("| {0} | {1}% | {2} | {3} | {4} | {5} |" -f $g.Group,$g.CoveragePercent,$g.FileCount,$e,$w,$st))
}
$md.Add(""); $md.Add("## Details")
foreach ($g in $groupResults) {
    $md.Add(""); $md.Add("### $($g.Group)  -  $($g.CoveragePercent)% coverage, $($g.FileCount) files")
    $real = $g.Issues | Where-Object { $_.Severity -ne 'INFO' }
    if (($real | Measure-Object).Count -eq 0) { $md.Add("- OK: continuous, no issues."); }
    foreach ($i in ($g.Issues | Sort-Object @{e={switch($_.Severity){'ERROR'{0}'WARN'{1}default{2}}}}, Type)) {
        $md.Add(("- **{0}** ``{1}`` -- {2}" -f $i.Severity,$i.Type,$i.Detail))
    }
}
$mdText = $md -join "`r`n"

# ---------- output ----------
if ($DryRun) {
    Write-Host "`n--- DRY RUN (no files written) ---`n" -ForegroundColor Yellow
    Write-Host $mdText
    Write-Host ("`nOverall: {0}  (exit {1})" -f $overall,$exit)
    exit $exit
}

New-Item -ItemType Directory -Force -Path $Config.ReportDir | Out-Null
$csvPath = Join-Path $Config.ReportDir "health_$stamp.csv"
$mdPath  = Join-Path $Config.ReportDir "health_$stamp.md"
$allFileRows | Sort-Object Group,Start | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
Set-Content -Path $mdPath -Value $mdText -Encoding UTF8
Copy-Item $csvPath (Join-Path $Config.ReportDir 'latest_health.csv') -Force
Copy-Item $mdPath  (Join-Path $Config.ReportDir 'latest_health.md')  -Force

$col = if ($overall -eq 'PASS') {'Green'} elseif ($overall -eq 'WARN') {'Yellow'} else {'Red'}
Write-Host ("`nOverall: {0}   errors={1} warnings={2}" -f $overall,$errCount,$warnCount) -ForegroundColor $col
Write-Host ("Reports: {0}" -f $csvPath); Write-Host ("         {0}" -f $mdPath)
exit $exit

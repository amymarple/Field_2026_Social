<#
.SYNOPSIS
    Continuous RTSP recorder for the Reolink NVR. Pulls each channel's stream
    with ffmpeg (-c copy, no re-encode) and writes hourly files per channel:

        D:\Reolink_record\CH01\CH01_2026-06-15_13-00-00.mp4
        ...

    Supervises the ffmpeg processes: restarts any that drop, runs retention,
    and frees space if the disk gets low. Runs forever; meant to be launched by
    a scheduled task at logon.

.NOTES
    Settings (incl. NVR password) live in recorder.config.psd1, which is kept
    OUT of git. This script contains no secrets.
#>

param(
    [string]$ConfigPath = 'D:\Reolink_record\recorder.config.psd1'
)

$ErrorActionPreference = 'Continue'

if (-not (Test-Path $ConfigPath)) { Write-Error "Config not found: $ConfigPath"; exit 1 }
$cfg = Import-PowerShellDataFile -Path $ConfigPath

$root   = $cfg.Root
$ffmpeg = $cfg.Ffmpeg
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$masterLog = Join-Path $logDir 'recorder.log'

function Log([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Write-Host $line
    Add-Content -Path $masterLog -Value $line
}

# single-instance guard: if a recorder is already running, exit quietly
$script:recorderMutex = New-Object System.Threading.Mutex($false, 'ReolinkRtspRecorder')
if (-not $script:recorderMutex.WaitOne(0)) {
    Write-Host 'Another recorder instance is already running; exiting.'
    exit 0
}

function Get-Url([int]$n) {
    $ch = '{0:D2}' -f $n
    'rtsp://{0}:{1}@{2}:{3}/Preview_{4}_main' -f $cfg.User, $cfg.Pass, $cfg.NvrIp, $cfg.RtspPort, $ch
}

function Get-FreeGB {
    $name = (Split-Path $root -Qualifier).TrimEnd(':')
    [math]::Round((Get-PSDrive -Name $name).Free / 1GB, 1)
}

# Free space by deleting OLDEST files across all channels until above MinFreeGB.
function Invoke-DiskGuard {
    if (Get-FreeGB -ge $cfg.MinFreeGB) { return }
    Log ("DISK GUARD: only {0} GB free (< {1}); deleting oldest files" -f (Get-FreeGB), $cfg.MinFreeGB)
    $all = Get-ChildItem (Join-Path $root 'CH*') -File -Filter '*.mp4' -EA SilentlyContinue | Sort-Object LastWriteTime
    foreach ($f in $all) {
        if (Get-FreeGB -ge $cfg.MinFreeGB) { break }
        try { Remove-Item $f.FullName -Force; Log ("DISK GUARD removed " + $f.Name) } catch {}
    }
}

# Delete recordings older than RetentionDays.
function Invoke-Retention {
    if ($cfg.RetentionDays -le 0) { return }
    $cut = (Get-Date).AddDays(-$cfg.RetentionDays)
    Get-ChildItem (Join-Path $root 'CH*') -File -Filter '*.mp4' -EA SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cut } |
        ForEach-Object { try { Remove-Item $_.FullName -Force; Log ("retention removed " + $_.Name) } catch {} }
}

function Start-Channel([int]$n) {
    $ch = '{0:D2}' -f $n
    $outDir = Join-Path $root "CH$ch"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $args = @(
        '-nostdin', '-loglevel', 'warning',
        '-rtsp_transport', 'tcp', '-use_wallclock_as_timestamps', '1',
        '-i', (Get-Url $n),
        '-c', 'copy', '-f', 'segment',
        '-segment_time', $cfg.SegmentSeconds,
        '-segment_atclocktime', '1', '-reset_timestamps', '1', '-strftime', '1',
        # fragmented mp4: writes in real time, survives an abrupt kill, still plays
        '-segment_format', 'mp4',
        '-segment_format_options', 'movflags=+frag_keyframe+empty_moov+default_base_moof:frag_duration=2000000',
        (Join-Path $outDir ("CH{0}_%Y-%m-%d_%H-%M-%S.mp4" -f $ch))
    )
    $errLog = Join-Path $logDir "CH$ch.ffmpeg.log"
    Start-Process -FilePath $ffmpeg -ArgumentList $args -WindowStyle Hidden -RedirectStandardError $errLog -PassThru
}

# --- startup: clear any orphaned ffmpeg from a previous (crashed) supervisor ---
Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

function Get-NewestFile([string]$ch) {
    Get-ChildItem (Join-Path $root "CH$ch") -File -Filter '*.mp4' -EA SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

# True length of a file ffmpeg is still writing (Get-ChildItem .Length is stale 0).
# On a transient open failure return $fallback so it doesn't look like shrinkage.
function Get-HandleLen([string]$path, [double]$fallback = 0) {
    try {
        $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $len = $fs.Length
        $fs.Dispose()
        return $len
    } catch { return $fallback }
}

Log ("=== recorder starting: channels {0}, segment {1}s, retention {2}d ===" -f ($cfg.Channels -join ','), $cfg.SegmentSeconds, $cfg.RetentionDays)
Log ("free space: {0} GB" -f (Get-FreeGB))

$procs    = @{}
$lastSize = @{}
$lastGrew = @{}
$lastName = @{}
$StallSeconds = 240
$lastMaint = (Get-Date).AddHours(-1)

while ($true) {
    foreach ($n in $cfg.Channels) {
        $ch = '{0:D2}' -f $n
        $alive = $procs.ContainsKey($ch) -and (-not $procs[$ch].HasExited)

        # stall watchdog: alive but output file not growing -> kill so it restarts
        if ($alive) {
            $nf = Get-NewestFile $ch
            if ($nf) {
                if ($nf.Name -ne $lastName[$ch]) {   # new hourly segment -> reset peak
                    $lastName[$ch] = $nf.Name; $lastSize[$ch] = 0; $lastGrew[$ch] = Get-Date
                }
                $sz = Get-HandleLen $nf.FullName $lastSize[$ch]
                if ($sz -gt $lastSize[$ch]) {
                    $lastSize[$ch] = $sz; $lastGrew[$ch] = Get-Date
                } elseif (((Get-Date) - $lastGrew[$ch]).TotalSeconds -gt $StallSeconds) {
                    Log ("CH{0} stalled (no growth for {1}s); killing to restart" -f $ch, $StallSeconds)
                    try { Stop-Process -Id $procs[$ch].Id -Force } catch {}
                    $alive = $false
                }
            }
        }

        if (-not $alive) {
            if ($procs.ContainsKey($ch)) {
                Log ("CH{0} ffmpeg stopped (exit {1}); restarting" -f $ch, $procs[$ch].ExitCode)
            }
            $procs[$ch]    = Start-Channel $n
            $lastSize[$ch] = 0
            $lastGrew[$ch] = Get-Date
            $lastName[$ch] = ''
            Log ("CH{0} started (pid {1})" -f $ch, $procs[$ch].Id)
            Start-Sleep -Seconds 2   # stagger connections
        }
    }

    # hourly maintenance
    if (((Get-Date) - $lastMaint).TotalMinutes -ge 60) {
        $lastMaint = Get-Date
        Invoke-Retention
        Invoke-DiskGuard
        Log ("heartbeat: free {0} GB" -f (Get-FreeGB))
    }

    Start-Sleep -Seconds 15
}

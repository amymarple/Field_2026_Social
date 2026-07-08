<#
.SYNOPSIS
  Analysis-PC launcher for validate_shelter.py. Sets the transferred-footage paths + the conda
  `cv` env's ffmpeg, cd's to this folder, and runs the interactive shelter validation in the `cv`
  env, forwarding every argument you pass. Run it from ANY directory:

      .\run_validate.ps1 --date 2026-06-30 --n 60

  Weights default to shelter_sleep.DEF_WEIGHTS (rat_feasibility-6) and --batch is 1, so you don't
  pass them. Env vars already set in your session are respected (not overwritten), so you can point
  at a different footage root just by exporting REOLINK_REC_ROOT first.
#>
$ErrorActionPreference = "Stop"

# Analysis-PC defaults (only set if you haven't already) — see memory cv-pipeline-analysis-pc-run.
if (-not $env:REOLINK_REC_ROOT)     { $env:REOLINK_REC_ROOT = "D:\Reolink_record\audio_in\Reolink_record" }
if (-not $env:REOLINK_FFMPEG)       { $env:REOLINK_FFMPEG   = "C:\Users\Cornell\.conda\envs\cv\Library\bin\ffmpeg.exe" }
if (-not $env:KMP_DUPLICATE_LIB_OK) { $env:KMP_DUPLICATE_LIB_OK = "TRUE" }

$py = "C:\Users\Cornell\.conda\envs\cv\python.exe"
if (-not (Test-Path $py)) {
    Write-Warning "cv env python not found at $py; falling back to 'python' on PATH."
    $py = "python"
}

Set-Location $PSScriptRoot
Write-Host "REOLINK_REC_ROOT = $env:REOLINK_REC_ROOT"
Write-Host "running: $py validate_shelter.py $args`n"
& $py "validate_shelter.py" @args

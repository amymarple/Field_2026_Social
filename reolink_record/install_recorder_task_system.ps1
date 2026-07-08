<#
.SYNOPSIS
    Run ONCE from an elevated (Administrator) PowerShell. Replaces the logon-based
    recorder task with a SYSTEM task that runs At Startup, self-heals every 5 min,
    and is immune to user sign-out. Also reports what signed the session out on
    2026-06-20 (the cause of the 06-20 -> 06-24 recording gap).
#>

$ErrorActionPreference = 'Continue'
$rec = 'C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\rtsp_record.ps1'
$taskName = 'Reolink RTSP Recorder'

# --- must be elevated ---
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "NOT ELEVATED. Re-open Terminal as Administrator and run this again." -ForegroundColor Red; return }
Write-Host "Elevated: OK" -ForegroundColor Green

# --- 1) why did it sign out on 06-20? (Security log: 4647 user logoff, 4634 logoff) ---
Write-Host "`n=== Sign-out / logoff events 2026-06-20 15:00-16:00 ===" -ForegroundColor Cyan
try {
    Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4647,4634; StartTime=(Get-Date '2026-06-20 15:00'); EndTime=(Get-Date '2026-06-20 16:00')} -EA Stop |
        Sort-Object TimeCreated | Select-Object TimeCreated, Id, @{n='Account';e={($_.Properties[1].Value)}} | Format-Table -AutoSize
} catch { Write-Host "  (no logoff events found in that window)" }
Write-Host "=== Other notable System events 2026-06-20 15:00-16:00 (updates/policy) ===" -ForegroundColor Cyan
try {
    Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date '2026-06-20 15:00'); EndTime=(Get-Date '2026-06-20 16:00')} -EA Stop |
        Sort-Object TimeCreated | Select-Object TimeCreated, Id, ProviderName | Format-Table -AutoSize
} catch { Write-Host "  (none)" }

# --- 2) stop the current (logon-session) recorder so we don't double-run ---
Write-Host "`n=== Stopping current recorder ===" -ForegroundColor Cyan
$me = $PID
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -like '*rtsp_record.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Start-Sleep -Seconds 1
Get-Process ffmpeg -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Sleep -Seconds 3

# --- 3) (re)create the task as SYSTEM, At Startup, repeating every 5 min ---
Write-Host "=== Installing SYSTEM/startup task ===" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $rec)
$trigger = New-ScheduledTaskTrigger -AtStartup
$repTrig = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)  # no duration = indefinite
$trigger.Repetition = $repTrig.Repetition
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$settings.ExecutionTimeLimit = 'PT0S'   # no time limit
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

# --- 4) verify ---
Start-Sleep -Seconds 14
$t = Get-ScheduledTask -TaskName $taskName
Write-Host ("`nTask: {0}  State={1}  RunAs={2}  RunLevel={3}" -f $t.TaskName, $t.State, $t.Principal.UserId, $t.Principal.RunLevel)
Write-Host ("ffmpeg processes: {0} (expect 6)" -f (Get-Process ffmpeg -EA SilentlyContinue | Measure-Object).Count)
function HL($p){ try{$fs=[IO.File]::Open($p,'Open','Read','ReadWrite');$l=$fs.Length;$fs.Dispose();$l}catch{0} }
foreach($n in 1..6){ $ch='{0:D2}' -f $n; $f=Get-ChildItem "E:\Reolink_record\CH$ch" -File -Filter *.mp4 -EA SilentlyContinue | Sort-Object Name -Desc | Select-Object -First 1; if($f){ Write-Host ("CH{0}: {1} MB" -f $ch,[math]::Round((HL $f.FullName)/1MB,1)) } }
Write-Host "`nDone. The recorder now runs as SYSTEM at startup and self-heals every 5 min." -ForegroundColor Green

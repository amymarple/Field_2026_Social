<#
.SYNOPSIS
    Run ONCE from an elevated (Administrator) PowerShell. Installs the EmpireTech
    thermal-camera recorder as a SYSTEM task (At Startup, self-heals every 5 min) -
    separate from the main 6-channel recorder. Records both thermal (ch 2) and
    visual (ch 1) of 192.168.1.108/.109 to E:\thermal_record, one hour per file.
#>

$ErrorActionPreference = 'Continue'
$script = 'C:\Users\Cornell\Documents\GitHub\Field_2026_Social\reolink_record\thermal_record.ps1'
$taskName = 'EmpireTech Thermal Cameras Recorder'
# remove any task created under the old (misnamed) name
Unregister-ScheduledTask -TaskName 'Reolink Thermal Recorder' -Confirm:$false -EA SilentlyContinue

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
    Write-Host "Not elevated - relaunching as Administrator. Click YES on the UAC prompt..." -ForegroundColor Yellow
    try { Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"" }
    catch { Write-Host "UAC was declined or failed: $($_.Exception.Message)" -ForegroundColor Red }
    return
}
Write-Host "Elevated: OK" -ForegroundColor Green

# stop any stray thermal supervisor + thermal ffmpeg (matched by the thermal IPs so the
# main recorder's ffmpeg are NOT touched)
$me = $PID
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -like '*thermal_record.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" |
    Where-Object { $_.CommandLine -like '*192.168.1.108*' -or $_.CommandLine -like '*192.168.1.109*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Start-Sleep -Seconds 2

Write-Host "=== Installing SYSTEM/startup thermal task ===" -ForegroundColor Cyan
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $script)
$trigger = New-ScheduledTaskTrigger -AtStartup
$repTrig = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
$trigger.Repetition = $repTrig.Repetition
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$settings.ExecutionTimeLimit = 'PT0S'
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

Start-Sleep -Seconds 14
$t = Get-ScheduledTask -TaskName $taskName
Write-Host ("`nTask: {0}  State={1}  RunAs={2}" -f $t.TaskName, $t.State, $t.Principal.UserId)
function HL($p){ try{$fs=[IO.File]::Open($p,'Open','Read','ReadWrite');$l=$fs.Length;$fs.Dispose();$l}catch{0} }
foreach($cam in '108','109'){ $f=Get-ChildItem "E:\thermal_record\$cam" -File -Filter *.mp4 -EA SilentlyContinue | Sort-Object Name -Desc | Select-Object -First 1; if($f){ Write-Host ("CAM {0} thermal: {1}  {2} MB" -f $cam,$f.Name,[math]::Round((HL $f.FullName)/1MB,2)) } else { Write-Host ("CAM {0}: (no file yet)" -f $cam) } }
Write-Host "`nDone. Thermal recorder runs as SYSTEM at startup, self-heals every 5 min." -ForegroundColor Green

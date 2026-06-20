param(
  [string]$TaskName = "ModelPlaneCapsuleHandoffTray",
  [int]$Port = 19112
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $ScriptDir "run_capsule_handoff_tray.ps1"
$BackendDir = Resolve-Path (Join-Path $ScriptDir "..")
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -HostAddress 127.0.0.1 -Port $Port"

$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $BackendDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel LeastPrivilege
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DisallowStartIfOnBatteries:$false -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Write-Output "Installed Capsule Handoff Tray startup task: $TaskName"
Write-Output "Tray URL: http://127.0.0.1:$Port/tray/status"
Write-Output "Compatibility wrapper: launches Model Plane Local Helper with capsule_handoff enabled."

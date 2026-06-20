param(
  [string]$TaskName = "ModelPlaneLocalHelper",
  [int]$Port = 19112,
  [string[]]$Modules = @()
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $ScriptDir "run_model_plane_helper.ps1"
$BackendDir = Resolve-Path (Join-Path $ScriptDir "..")
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$ModuleArgs = if ($Modules.Count -gt 0) { " -Modules " + (($Modules | ForEach-Object { "`"$_`"" }) -join ",") } else { "" }
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -HostAddress 127.0.0.1 -Port $Port$ModuleArgs"

$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $BackendDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel LeastPrivilege
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DisallowStartIfOnBatteries:$false -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Write-Output "Installed Model Plane Local Helper startup task: $TaskName"
Write-Output "Helper URL: http://127.0.0.1:$Port/helper/status"
Write-Output "Enabled modules: $(if ($Modules.Count -gt 0) { $Modules -join ',' } else { 'status only' })"


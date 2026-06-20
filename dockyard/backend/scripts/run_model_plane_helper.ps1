param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 19112,
  [string[]]$Modules = @()
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Resolve-Path (Join-Path $ScriptDir "..")
$VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
  $Python = $VenvPython
} else {
  $Python = "py"
}

$env:MODEL_PLANE_HELPER_MODULES = ($Modules -join ",")

Set-Location $BackendDir
& $Python -m uvicorn model_plane_helper.app:app --host $HostAddress --port $Port


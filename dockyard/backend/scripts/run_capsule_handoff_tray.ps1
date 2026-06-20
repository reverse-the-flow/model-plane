param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 19112
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

Set-Location $BackendDir
$env:MODEL_PLANE_HELPER_MODULES = "capsule_handoff"
& $Python -m uvicorn model_plane_helper.app:app --host $HostAddress --port $Port

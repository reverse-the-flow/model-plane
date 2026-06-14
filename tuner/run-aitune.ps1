param(
    [ValidateSet("list-backends", "probe-imports", "env-report", "shell")]
    [string]$Action = "list-backends",

    [string]$Backends = "all",

    [string]$BaseImage = "nvcr.io/nvidia/pytorch:25.02-py3",

    [string]$InstallTarget = "git+https://github.com/ai-dynamo/aitune.git",

    [string]$ExtraPipPackages = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeFile = Join-Path $scriptDir "docker-compose.aitune-lab.yaml"

$serviceMap = @{
    "tensorrt" = "aitune-backend-tensorrt"
    "torch_tensorrt_jit" = "aitune-backend-torch-tensorrt-jit"
    "torch_tensorrt_aot" = "aitune-backend-torch-tensorrt-aot"
    "torchao" = "aitune-backend-torchao"
    "torch_inductor" = "aitune-backend-torch-inductor"
}

$service = "aitune-runner"
$backendTokens = @(
    $Backends.Split(",") |
    ForEach-Object { $_.Trim().ToLowerInvariant() } |
    Where-Object { $_ }
)

if ($backendTokens.Count -eq 1 -and $serviceMap.ContainsKey($backendTokens[0])) {
    $service = $serviceMap[$backendTokens[0]]
}

$env:AITUNE_BASE_IMAGE = $BaseImage
$env:AITUNE_INSTALL_TARGET = $InstallTarget
$env:AITUNE_EXTRA_PIP_PACKAGES = $ExtraPipPackages
$env:AITUNE_BACKENDS = $Backends

$composeArgs = @(
    "compose",
    "-f",
    $composeFile,
    "run",
    "--rm",
    $service,
    $Action
)

if ($Action -eq "shell") {
    if ($Backends -ne "all") {
        $composeArgs += @("--backends", $Backends)
    }
} elseif ($service -eq "aitune-runner" -and $Backends -ne "all") {
    $composeArgs += @("--backends", $Backends)
}

Write-Host "Launching AITune lab service '$service' with action '$Action' and backends '$Backends'..."
& docker @composeArgs

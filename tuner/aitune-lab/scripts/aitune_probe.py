from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendSpec:
    key: str
    family: str
    class_name: str
    summary: str
    runtime_dependency: str | None = None


BACKENDS: dict[str, BackendSpec] = {
    "tensorrt": BackendSpec(
        key="tensorrt",
        family="TensorRT",
        class_name="TensorRTBackend",
        summary="TensorRT engine path with Model Optimizer integration.",
        runtime_dependency="tensorrt",
    ),
    "torch_tensorrt_jit": BackendSpec(
        key="torch_tensorrt_jit",
        family="Torch-TensorRT",
        class_name="TorchTensorRTJitBackend",
        summary="Torch-TensorRT JIT path via torch.compile.",
        runtime_dependency="torch_tensorrt",
    ),
    "torch_tensorrt_aot": BackendSpec(
        key="torch_tensorrt_aot",
        family="Torch-TensorRT",
        class_name="TorchTensorRTAotBackend",
        summary="Torch-TensorRT AOT path via torch_tensorrt.compile.",
        runtime_dependency="torch_tensorrt",
    ),
    "torchao": BackendSpec(
        key="torchao",
        family="TorchAO",
        class_name="TorchAOBackend",
        summary="TorchAO acceleration and quantization path.",
        runtime_dependency="torchao",
    ),
    "torch_inductor": BackendSpec(
        key="torch_inductor",
        family="Torch Inductor",
        class_name="TorchInductorBackend",
        summary="PyTorch Inductor compiler backend.",
    ),
}


def parse_backends(raw: str | None) -> list[BackendSpec]:
    selected = raw or os.environ.get("AITUNE_BACKENDS", "all")
    tokens = [token.strip().lower() for token in selected.split(",") if token.strip()]
    if not tokens or "all" in tokens:
        return list(BACKENDS.values())

    resolved: list[BackendSpec] = []
    for token in tokens:
        if token not in BACKENDS:
            valid = ", ".join(sorted(BACKENDS))
            raise SystemExit(f"Unknown backend '{token}'. Valid values: {valid}, all")
        resolved.append(BACKENDS[token])
    return resolved


def module_available(module_name: str) -> tuple[bool, str | None]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def get_torch_details() -> dict[str, object]:
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
    return {
        "available": True,
        "version": getattr(torch, "__version__", "unknown"),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_version": cuda_version,
        "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
    }


def get_nvidia_smi() -> dict[str, object]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi not found"}
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        return {"available": False, "error": message}

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {"available": True, "gpus": lines}


def build_backend_report(selected: list[BackendSpec]) -> list[dict[str, object]]:
    try:
        backend_module = importlib.import_module("aitune.torch.backend")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Unable to import aitune.torch.backend: {type(exc).__name__}: {exc}") from exc

    report: list[dict[str, object]] = []
    for spec in selected:
        runtime_ok = None
        runtime_error = None
        if spec.runtime_dependency:
            runtime_ok, runtime_error = module_available(spec.runtime_dependency)

        backend_class = getattr(backend_module, spec.class_name, None)
        report.append(
            {
                "key": spec.key,
                "family": spec.family,
                "class_name": spec.class_name,
                "summary": spec.summary,
                "backend_class_found": backend_class is not None,
                "runtime_dependency": spec.runtime_dependency,
                "runtime_dependency_ok": runtime_ok,
                "runtime_dependency_error": runtime_error,
            }
        )
    return report


def print_list_backends(selected: list[BackendSpec]) -> None:
    family_count = len({spec.family for spec in BACKENDS.values()})
    print("AITune backend inventory")
    print(f"- backend families: {family_count}")
    print(f"- selectable backend targets: {len(BACKENDS)}")
    print("- selected targets:")
    for spec in selected:
        suffix = f" (needs {spec.runtime_dependency})" if spec.runtime_dependency else ""
        print(f"  - {spec.key}: {spec.class_name} [{spec.family}]{suffix}")
        print(f"    {spec.summary}")


def print_probe_imports(selected: list[BackendSpec]) -> None:
    payload = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "aitune": module_available("aitune"),
        "torch": get_torch_details(),
        "nvidia_smi": get_nvidia_smi(),
        "selected_backends": build_backend_report(selected),
    }
    print(json.dumps(payload, indent=2))


def print_env_report() -> None:
    payload = {
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "aitune_install_target": os.environ.get("AITUNE_INSTALL_TARGET"),
        "aitune_backends": os.environ.get("AITUNE_BACKENDS"),
        "torch": get_torch_details(),
        "nvidia_smi": get_nvidia_smi(),
    }
    print(json.dumps(payload, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Lightweight AITune environment probe.")
    parser.add_argument("action", choices=["list-backends", "probe-imports", "env-report"])
    parser.add_argument("--backends", default=None, help="Comma-separated backend keys or 'all'.")
    args = parser.parse_args()

    selected = parse_backends(args.backends)
    if args.action == "list-backends":
        print_list_backends(selected)
    elif args.action == "probe-imports":
        print_probe_imports(selected)
    else:
        print_env_report()


if __name__ == "__main__":
    main()

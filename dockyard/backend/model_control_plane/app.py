from __future__ import annotations

import shlex
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .moe_probe_manifest import build_moe_probe_manifest

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"

app = FastAPI(title="Model Control Plane", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:19111", "http://localhost:19111"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def profile_files() -> list[Path]:
    return sorted(PROFILES.glob("*.yaml")) + sorted(PROFILES.glob("*.yml"))


def all_profiles() -> list[dict[str, Any]]:
    profiles = []
    for path in profile_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            profiles.append(data)
    return profiles


def load_profile(profile_id: str) -> dict[str, Any]:
    for profile in all_profiles():
        if profile.get("id") == profile_id:
            return profile
    raise KeyError(profile_id)


def validate_profile(profile: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    image = profile.get("runtime", {}).get("image", "")
    name = profile.get("container", {}).get("name", "")
    model_path = profile.get("model", {}).get("local_path")
    args = " ".join(str(arg) for arg in profile.get("runtime", {}).get("args", []))
    if not name.startswith("dockyard-"):
        messages.append({"level": "error", "code": "container_name", "message": "Container name must start with dockyard-."})
    if image.endswith(":latest"):
        messages.append({"level": "warning", "code": "latest", "message": "Image uses latest; pin an exact tag."})
    if model_path:
        try:
            exists = Path(model_path).exists()
        except OSError as exc:
            messages.append({"level": "warning", "code": "model_path_io", "message": f"Could not read model path: {model_path} ({exc})"})
        else:
            if not exists:
                messages.append({"level": "warning", "code": "model_path", "message": f"Model path does not exist: {model_path}"})
    if "--trust-remote-code" in args:
        messages.append({"level": "warning", "code": "trust_remote_code", "message": "Profile uses --trust-remote-code; verify model code source."})
    return messages


def render_command(profile: dict[str, Any]) -> list[str]:
    runtime = profile["runtime"]
    container = profile["container"]
    command = [
        "docker", "run", "--rm", "-d",
        "--name", container["name"],
        "--gpus", str(container.get("gpu", "all")),
        "-p", f"127.0.0.1:{container['host_port']}:{container['internal_port']}",
    ]
    if container.get("shm_size"):
        command += ["--shm-size", str(container["shm_size"])]
    for key, value in container.get("env", {}).items():
        command += ["-e", f"{key}={value}"]
    for volume in container.get("volumes", []):
        command += ["-v", volume]
    command.append(runtime["image"])
    command += runtime.get("command", [])
    command += runtime.get("args", [])
    return command


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/profiles")
def profiles() -> list[dict[str, Any]]:
    rows = []
    for profile in all_profiles():
        messages = validate_profile(profile)
        rows.append({
            "id": profile["id"],
            "name": profile["name"],
            "backend": profile["runtime"]["backend"],
            "model_id": profile["model"]["id"],
            "image": profile["runtime"].get("image"),
            "host_port": profile["container"]["host_port"],
            "health_url": profile["health"]["url"],
            "warnings": sum(1 for message in messages if message["level"] == "warning"),
            "errors": sum(1 for message in messages if message["level"] == "error"),
        })
    return rows


@app.get("/profiles/{profile_id}")
def profile(profile_id: str) -> dict[str, Any]:
    try:
        return load_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Profile not found") from exc


@app.post("/profiles/{profile_id}/validate")
def validate(profile_id: str) -> list[dict[str, str]]:
    return validate_profile(profile(profile_id))


@app.post("/profiles/{profile_id}/render")
def render(profile_id: str) -> dict[str, Any]:
    command = render_command(profile(profile_id))
    return {"profile_id": profile_id, "docker_command": command, "shell_command": shlex.join(command)}


@app.get("/profiles/{profile_id}/moe-probe-manifest")
def export_moe_probe_manifest(profile_id: str) -> dict[str, Any]:
    return build_moe_probe_manifest(profile(profile_id))


@app.post("/profiles/{profile_id}/health")
def check_profile_health(profile_id: str) -> dict[str, Any]:
    url = profile(profile_id)["health"]["url"]
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return {"ok": 200 <= response.status < 300, "status": response.status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/profiles/{profile_id}/launch")
def launch(profile_id: str) -> dict[str, Any]:
    selected = profile(profile_id)
    errors = [message for message in validate_profile(selected) if message["level"] == "error"]
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    command = render_command(selected)
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    return {"ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


@app.post("/containers/{container_name}/stop")
def stop(container_name: str) -> dict[str, Any]:
    if not container_name.startswith("dockyard-"):
        raise HTTPException(status_code=400, detail="Refusing to stop containers not created by Dockyard.")
    result = subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=30, check=False)
    return {"ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

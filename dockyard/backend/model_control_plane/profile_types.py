from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from .network_policy import (
    local_url_for_port,
    network_bind_host,
    network_mode,
    rewrite_local_url_for_policy,
    url_for_port,
    validate_network_policy,
)


CAPSULE_GATEWAY_BACKENDS = {"capsule_gateway", "session_capsule_gateway"}
CAPSULE_CHECKPOINT_MODES = {"soft", "hard", "none"}
LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def runtime_backend(profile: dict[str, Any]) -> str:
    return str(profile.get("runtime", {}).get("backend") or "").strip().lower()


def profile_type(profile: dict[str, Any]) -> str:
    return str(profile.get("profile_type") or profile.get("type") or "").strip().lower()


def is_capsule_gateway_profile(profile: dict[str, Any]) -> bool:
    return profile_type(profile) == "capsule_gateway" or runtime_backend(profile) in CAPSULE_GATEWAY_BACKENDS


def capsule_gateway_config(profile: dict[str, Any]) -> dict[str, Any]:
    configured = profile.get("capsule_gateway")
    return configured if isinstance(configured, dict) else {}


def capsule_host(profile: dict[str, Any]) -> str:
    return network_bind_host(profile)


def capsule_port(profile: dict[str, Any]) -> int | None:
    value = capsule_gateway_config(profile).get("port")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def capsule_endpoint_id(profile: dict[str, Any]) -> str | None:
    value = capsule_gateway_config(profile).get("endpoint_id")
    return str(value) if value is not None and str(value).strip() else None


def capsule_healthcheck_url(profile: dict[str, Any]) -> str:
    capsule = capsule_gateway_config(profile)
    configured = capsule.get("healthcheck_url") or profile.get("health", {}).get("url")
    if configured:
        return str(configured)
    port = capsule_port(profile)
    return local_url_for_port(port, "/api/capsules/status") if port is not None else ""


def capsule_client_base_url(profile: dict[str, Any]) -> str:
    capsule = capsule_gateway_config(profile)
    configured = capsule.get("client_base_url")
    if configured:
        return rewrite_local_url_for_policy(profile, str(configured).rstrip("/"))
    port = capsule_port(profile)
    return url_for_port(profile, port, "/v1") if port is not None else ""


def profile_host_port(profile: dict[str, Any]) -> int | None:
    if is_capsule_gateway_profile(profile):
        return capsule_port(profile)
    value = profile.get("container", {}).get("host_port")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def profile_health_url(profile: dict[str, Any]) -> str:
    if is_capsule_gateway_profile(profile):
        return capsule_healthcheck_url(profile)
    return str(profile.get("health", {}).get("url") or "")


def profile_model_id(profile: dict[str, Any]) -> str | None:
    model_id = profile.get("model", {}).get("id")
    if model_id:
        return str(model_id)
    return capsule_endpoint_id(profile)


def capsule_gateway_script_path(profile: dict[str, Any]) -> Path | None:
    capsule = capsule_gateway_config(profile)
    executable_path = capsule.get("executable_path")
    if executable_path:
        return Path(str(executable_path)).expanduser()
    repo_path = capsule.get("repo_path")
    if repo_path:
        return Path(str(repo_path)).expanduser() / "scripts" / "capsule_gateway.py"
    return None


def capsule_gateway_cwd(profile: dict[str, Any]) -> str | None:
    repo_path = capsule_gateway_config(profile).get("repo_path")
    return str(Path(str(repo_path)).expanduser()) if repo_path else None


def capsule_python_command(profile: dict[str, Any]) -> list[str]:
    configured = capsule_gateway_config(profile).get("python_command")
    if isinstance(configured, list) and configured:
        return [str(part) for part in configured]
    if isinstance(configured, str) and configured.strip():
        return shlex.split(configured)
    return ["py", "-3"] if os.name == "nt" else ["python3"]


def render_capsule_gateway_command(profile: dict[str, Any]) -> list[str]:
    capsule = capsule_gateway_config(profile)
    script_path = capsule_gateway_script_path(profile)
    if script_path is None:
        raise ValueError("Capsule gateway profile must set repo_path or executable_path.")
    command = capsule_python_command(profile)
    command += [
        str(script_path),
        "--state-dir",
        str(capsule["state_dir"]),
        "--endpoint",
        str(capsule["endpoint_id"]),
        "--host",
        capsule_host(profile),
        "--port",
        str(capsule["port"]),
        "--checkpoint-mode",
        str(capsule["checkpoint_mode"]),
        "--slot",
        str(capsule["slot"]),
        "--default-prefill",
        str(capsule["default_prefill"]),
    ]
    return command


def validate_capsule_gateway_profile(profile: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = validate_network_policy(profile)
    capsule = capsule_gateway_config(profile)
    if not profile.get("name"):
        messages.append({"level": "error", "code": "name", "message": "Capsule gateway profile must set name."})
    if not capsule:
        return [{"level": "error", "code": "capsule_gateway", "message": "Capsule gateway profile must include capsule_gateway settings."}]
    if not capsule.get("repo_path") and not capsule.get("executable_path"):
        messages.append({
            "level": "error",
            "code": "capsule_gateway_source",
            "message": "Capsule gateway profile must set repo_path or executable_path.",
        })
    required = [
        "state_dir",
        "endpoint_id",
        "host",
        "port",
        "checkpoint_mode",
        "slot",
        "default_prefill",
        "healthcheck_url",
        "client_base_url",
    ]
    for key in required:
        if capsule.get(key) in (None, ""):
            messages.append({
                "level": "error",
                "code": f"capsule_gateway_{key}",
                "message": f"Capsule gateway profile must set {key}.",
            })
    checkpoint_mode = str(capsule.get("checkpoint_mode") or "")
    if checkpoint_mode and checkpoint_mode not in CAPSULE_CHECKPOINT_MODES:
        messages.append({
            "level": "error",
            "code": "capsule_gateway_checkpoint_mode",
            "message": "checkpoint_mode must be one of: soft, hard, none.",
        })
    port = capsule_port(profile)
    if port is None or not (1 <= port <= 65535):
        messages.append({"level": "error", "code": "capsule_gateway_port", "message": "port must be a TCP port from 1 to 65535."})
    try:
        int(capsule.get("slot"))
    except (TypeError, ValueError):
        messages.append({"level": "error", "code": "capsule_gateway_slot", "message": "slot must be an integer runtime slot id."})
    host = capsule_host(profile)
    if network_mode(profile) == "local_only" and host not in LOCALHOST_HOSTS:
        messages.append({
            "level": "warning",
            "code": "capsule_gateway_host",
            "message": "Local-only capsule gateway profiles should bind to localhost.",
        })
    health_url = str(capsule.get("healthcheck_url") or "")
    if health_url and not health_url.rstrip("/").endswith("/api/capsules/status"):
        messages.append({
            "level": "warning",
            "code": "capsule_gateway_healthcheck_url",
            "message": "healthcheck_url should target /api/capsules/status.",
        })
    client_base_url = str(capsule.get("client_base_url") or "")
    if client_base_url and not client_base_url.rstrip("/").endswith("/v1"):
        messages.append({
            "level": "warning",
            "code": "capsule_gateway_client_base_url",
            "message": "client_base_url should be the OpenAI-compatible /v1 base URL.",
        })
    if capsule.get("fallback_replay") is False:
        messages.append({
            "level": "warning",
            "code": "capsule_gateway_fallback_replay",
            "message": "Transcript replay fallback should remain enabled for capsule gateway profiles.",
        })

    repo_path = capsule.get("repo_path")
    if repo_path:
        try:
            exists = Path(str(repo_path)).expanduser().exists()
        except OSError as exc:
            messages.append({"level": "warning", "code": "capsule_gateway_repo_path_io", "message": f"Could not read repo_path: {repo_path} ({exc})"})
        else:
            if not exists:
                messages.append({"level": "warning", "code": "capsule_gateway_repo_path", "message": f"repo_path does not exist: {repo_path}"})
    script_path = capsule_gateway_script_path(profile)
    if script_path is not None:
        try:
            script_exists = script_path.exists()
        except OSError as exc:
            messages.append({"level": "warning", "code": "capsule_gateway_executable_path_io", "message": f"Could not read gateway executable path: {script_path} ({exc})"})
        else:
            if not script_exists:
                messages.append({
                    "level": "warning",
                    "code": "capsule_gateway_executable_path",
                    "message": f"Gateway executable path does not exist: {script_path}",
                })
    return messages

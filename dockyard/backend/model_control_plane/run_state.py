from __future__ import annotations

import json
import re
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .moe_probe_manifest import (
    backend_family,
    endpoint_base_url,
    primary_probe_hint,
    semantic_expert_ids_status,
)

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "state" / "runs.json"
RUN_STATE_SCHEMA_VERSION = "model-plane-run-state-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_run_id_part(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "profile"


def new_run_id(profile_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{clean_run_id_part(profile_id)}-{stamp}-{uuid.uuid4().hex[:8]}"


def empty_store() -> dict[str, Any]:
    return {"schema_version": RUN_STATE_SCHEMA_VERSION, "runs": []}


def read_store(path: Path | None = None) -> dict[str, Any]:
    selected = path or RUNS_PATH
    if not selected.exists():
        return empty_store()
    data = json.loads(selected.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return empty_store()
    runs = data.get("runs")
    if not isinstance(runs, list):
        runs = []
    return {
        "schema_version": str(data.get("schema_version") or RUN_STATE_SCHEMA_VERSION),
        "runs": [run for run in runs if isinstance(run, dict)],
    }


def write_store(store: dict[str, Any], path: Path | None = None) -> None:
    selected = path or RUNS_PATH
    selected.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": str(store.get("schema_version") or RUN_STATE_SCHEMA_VERSION),
        "runs": list(store.get("runs", [])),
    }
    tmp = selected.with_suffix(selected.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(selected)


def list_runs(path: Path | None = None) -> list[dict[str, Any]]:
    runs = read_store(path)["runs"]
    return sorted(runs, key=lambda run: str(run.get("created_at", "")), reverse=True)


def get_run(run_id: str, path: Path | None = None) -> dict[str, Any] | None:
    for run in read_store(path)["runs"]:
        if run.get("run_id") == run_id:
            return run
    return None


def save_run(run: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    store = read_store(path)
    runs = store["runs"]
    for index, existing in enumerate(runs):
        if existing.get("run_id") == run.get("run_id"):
            runs[index] = run
            break
    else:
        runs.append(run)
    write_store(store, path)
    return run


def log_file_path(profile: dict[str, Any]) -> str | None:
    explicit = profile.get("moe_probe", {}).get("log_file_path")
    if explicit:
        return str(explicit)
    configured = profile.get("logs", {}).get("file_path")
    if configured:
        return str(configured)
    return None


def initial_run_record(profile: dict[str, Any], command: list[str]) -> dict[str, Any]:
    created_at = utc_now()
    profile_id = str(profile.get("id") or "unknown")
    shell_command = shlex.join(command)
    model = profile.get("model", {})
    container = profile.get("container", {})
    moe_probe = profile.get("moe_probe", {})
    launch = {
        "command": command,
        "shell_command": shell_command,
        "returncode": None,
        "stdout": None,
        "stderr": None,
    }
    return {
        "run_id": new_run_id(profile_id),
        "profile_id": profile_id,
        "profile_name": profile.get("name"),
        "created_at": created_at,
        "updated_at": created_at,
        "status": "launching",
        "container_name": container.get("name"),
        "base_url": endpoint_base_url(profile),
        "health_url": profile.get("health", {}).get("url"),
        "log_file_path": log_file_path(profile),
        "model_id": model.get("id"),
        "model_path": model.get("local_path"),
        "backend_family": backend_family(profile),
        "primary_probe_hint": primary_probe_hint(profile),
        "semantic_expert_ids": semantic_expert_ids_status(profile),
        "hookable_runtime_available": bool(moe_probe.get("hookable_runtime_available")),
        "passive_sidecar_requested": bool(moe_probe.get("passive_sidecar_requested")),
        "launch_command": command,
        "launch_shell_command": shell_command,
        "launch_returncode": None,
        "launch_stdout": None,
        "launch_stderr": None,
        "launch": launch,
        "last_health_result": None,
    }


def create_run(profile: dict[str, Any], command: list[str], path: Path | None = None) -> dict[str, Any]:
    return save_run(initial_run_record(profile, command), path)


def record_launch_result(
    run_id: str,
    returncode: int | None,
    stdout: str | None,
    stderr: str | None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    run = get_run(run_id, path)
    if run is None:
        return None
    run["updated_at"] = utc_now()
    run["status"] = "launched" if returncode == 0 else "launch_failed"
    run["launch_returncode"] = returncode
    run["launch_stdout"] = stdout
    run["launch_stderr"] = stderr
    launch = dict(run.get("launch") or {})
    launch.update({"returncode": returncode, "stdout": stdout, "stderr": stderr})
    run["launch"] = launch
    return save_run(run, path)


def record_launch_exception(run_id: str, error: str, path: Path | None = None) -> dict[str, Any] | None:
    run = get_run(run_id, path)
    if run is None:
        return None
    run["updated_at"] = utc_now()
    run["status"] = "launch_error"
    run["launch_returncode"] = None
    run["launch_stdout"] = None
    run["launch_stderr"] = error
    launch = dict(run.get("launch") or {})
    launch.update({"returncode": None, "stdout": None, "stderr": error})
    run["launch"] = launch
    return save_run(run, path)


def record_health_result(run_id: str, result: dict[str, Any], path: Path | None = None) -> dict[str, Any] | None:
    run = get_run(run_id, path)
    if run is None:
        return None
    checked = dict(result)
    checked["checked_at"] = utc_now()
    run["updated_at"] = checked["checked_at"]
    run["last_health_result"] = checked
    run["status"] = "healthy" if checked.get("ok") is True else "unhealthy"
    return save_run(run, path)

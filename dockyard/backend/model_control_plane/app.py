from __future__ import annotations

import os
import shlex
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import callable_functions, cron_tick as cron_tick_planner
from . import orchestration_jobs, run_state
from .moe_probe_manifest import backend_family, build_moe_probe_manifest, build_run_moe_probe_manifest

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"

app = FastAPI(title="Model Control Plane", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:19111", "http://localhost:19111"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CLEANUP_CANDIDATE_STATUSES = {"launch_failed", "launch_error", "unhealthy"}
DEFAULT_STALE_LAUNCHING_SECONDS = 30 * 60
LLAMA_CPP_MOE_OBSERVABILITY_FLAGS = ("--metrics", "--slots", "--props", "--perf")
LLAMA_CPP_LOG_FILE_FLAGS = ("--log-file", "--log-file-path")


class CleanupRequest(BaseModel):
    remove_container: bool = False
    notes: str | None = None


class CronTickRequest(BaseModel):
    health_stale_seconds: int = cron_tick_planner.DEFAULT_HEALTH_STALE_SECONDS


class JobCompletionRequest(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class HfTokenRequest(BaseModel):
    token: str
    remember: bool = False


class HfTokenStatus(BaseModel):
    env_var: str
    configured: bool
    process_configured: bool
    persistent_configured: bool
    scope: str
    redacted: str
    token_path_source: str
    restart_notice: str
    inheritance_notice: str


HF_TOKEN_ENV_VAR = "HF_TOKEN"
HF_TOKEN_PATH_ENV_VAR = "HF_TOKEN_PATH"
DEFAULT_HF_TOKEN_PATH = ROOT / "state" / "secrets" / "hf_token"


def hf_token_path() -> tuple[Path, str]:
    configured_path = os.environ.get(HF_TOKEN_PATH_ENV_VAR, "").strip()
    if configured_path:
        return Path(configured_path).expanduser(), HF_TOKEN_PATH_ENV_VAR
    return DEFAULT_HF_TOKEN_PATH, "dockyard_state"


def secure_secret_parent(path: Path, source: str) -> None:
    parent = path.parent
    existed = parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix" and (source == "dockyard_state" or not existed):
        os.chmod(parent, 0o700)


def write_persistent_hf_token(token: str) -> None:
    path, source = hf_token_path()
    secure_secret_parent(path, source)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
        handle.write("\n")
    if os.name == "posix":
        os.chmod(path, 0o600)


def read_persistent_hf_token() -> str | None:
    path, _source = hf_token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def remove_persistent_hf_token() -> None:
    path, _source = hf_token_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


def load_persistent_hf_token_if_needed() -> bool:
    if os.environ.get(HF_TOKEN_ENV_VAR):
        return False
    token = read_persistent_hf_token()
    if not token:
        return False
    os.environ[HF_TOKEN_ENV_VAR] = token
    return True


@app.on_event("startup")
def load_hf_token_on_startup() -> None:
    load_persistent_hf_token_if_needed()


def hf_token_metadata() -> dict[str, str | bool]:
    load_persistent_hf_token_if_needed()
    process_configured = bool(os.environ.get(HF_TOKEN_ENV_VAR))
    persistent_configured = read_persistent_hf_token() is not None
    configured = process_configured or persistent_configured
    _path, token_path_source = hf_token_path()
    if process_configured and persistent_configured:
        scope = "process_env+persistent_file"
    elif process_configured:
        scope = "process_env"
    elif persistent_configured:
        scope = "persistent_file"
    else:
        scope = "unset"
    return {
        "env_var": HF_TOKEN_ENV_VAR,
        "configured": configured,
        "process_configured": process_configured,
        "persistent_configured": persistent_configured,
        "scope": scope,
        "redacted": "set" if configured else "unset",
        "token_path_source": token_path_source,
        "restart_notice": (
            "Remembered local token is loaded into HF_TOKEN when the backend starts or status is checked."
            if persistent_configured
            else "Session-only unless remembered on this machine; process env is lost on backend restart."
        ),
        "inheritance_notice": "Set before model pulls or launches; already-running subprocesses do not inherit changes.",
    }


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
    if backend_family(profile) == "llama_cpp":
        arg_tokens = {str(arg) for arg in profile.get("runtime", {}).get("args", [])}
        missing_flags = [flag for flag in LLAMA_CPP_MOE_OBSERVABILITY_FLAGS if flag not in arg_tokens]
        if missing_flags:
            messages.append({
                "level": "warning",
                "code": "llama_cpp_moe_observability_flags",
                "message": "llama.cpp profile is missing MoE observability flags: " + ", ".join(missing_flags),
            })
        logs = profile.get("logs", {})
        moe_probe = profile.get("moe_probe", {})
        log_metadata = moe_probe.get("log_file_path") or logs.get("file_path") or logs.get("host_path")
        runtime_log_flag = any(flag in arg_tokens for flag in LLAMA_CPP_LOG_FILE_FLAGS)
        if not log_metadata or not runtime_log_flag:
            missing = []
            if not log_metadata:
                missing.append("log_file_path metadata")
            if not runtime_log_flag:
                missing.append("--log-file or --log-file-path runtime flag")
            messages.append({
                "level": "warning",
                "code": "llama_cpp_moe_log_file",
                "message": "llama.cpp profile is missing MoE log-file configuration: " + ", ".join(missing),
            })
        observability_paths = moe_probe.get("observability_paths")
        if not isinstance(observability_paths, list) or not observability_paths:
            messages.append({
                "level": "warning",
                "code": "llama_cpp_moe_observability_paths",
                "message": "llama.cpp profile should declare moe_probe.observability_paths for MoE manifest export.",
            })
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


@app.get("/functions")
def functions() -> list[dict[str, Any]]:
    return callable_functions.list_function_descriptors()


@app.get("/functions/{function_id}")
def function(function_id: str) -> dict[str, Any]:
    descriptor = callable_functions.get_function_descriptor(function_id)
    if descriptor is None:
        raise HTTPException(status_code=404, detail="Function descriptor not found")
    return descriptor


@app.get("/secrets/hf-token", response_model=HfTokenStatus)
def get_hf_token_status() -> dict[str, str | bool]:
    return hf_token_metadata()


@app.post("/secrets/hf-token", response_model=HfTokenStatus)
def set_hf_token(request: HfTokenRequest) -> dict[str, str | bool]:
    token = request.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="HF_TOKEN must not be empty.")
    os.environ[HF_TOKEN_ENV_VAR] = token
    if request.remember:
        try:
            write_persistent_hf_token(token)
        except OSError as exc:
            os.environ.pop(HF_TOKEN_ENV_VAR, None)
            raise HTTPException(status_code=500, detail="Could not persist HF_TOKEN on this machine.") from exc
    return hf_token_metadata()


@app.delete("/secrets/hf-token", response_model=HfTokenStatus)
def clear_hf_token() -> dict[str, str | bool]:
    os.environ.pop(HF_TOKEN_ENV_VAR, None)
    try:
        remove_persistent_hf_token()
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not remove persisted HF_TOKEN from this machine.") from exc
    return hf_token_metadata()


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


@app.get("/cleanup/plan")
def cleanup_plan(
    run_id: list[str] | None = Query(default=None),
    stale_launching_after_seconds: int = DEFAULT_STALE_LAUNCHING_SECONDS,
) -> dict[str, Any]:
    return build_cleanup_plan(run_state.list_runs(), run_id, stale_launching_after_seconds)


def run_cron_tick(request: CronTickRequest | None = None) -> dict[str, Any]:
    selected_request = request or CronTickRequest()
    selected_runs = run_state.list_runs()
    return cron_tick_planner.cron_tick(
        profiles=all_profiles(),
        runs=selected_runs,
        cleanup_plan=build_cleanup_plan(
            selected_runs,
            stale_launching_after_seconds=cron_tick_planner.DEFAULT_STALE_LAUNCHING_SECONDS,
        ),
        validate_profile=validate_profile,
        health_stale_seconds=selected_request.health_stale_seconds,
    )


@app.post("/cron/tick")
def cron_tick(request: CronTickRequest | None = None) -> dict[str, Any]:
    return run_cron_tick(request)


@app.get("/agent-jobs")
def agent_jobs(status: str | None = None) -> list[dict[str, Any]]:
    return orchestration_jobs.list_jobs(status=status)


@app.get("/agent-jobs/{job_id}")
def agent_job(job_id: str) -> dict[str, Any]:
    selected = orchestration_jobs.get_job(job_id)
    if selected is None:
        raise HTTPException(status_code=404, detail="Agent job not found")
    return selected


@app.post("/agent-jobs/{job_id}/complete")
def complete_agent_job(job_id: str, request: JobCompletionRequest | None = None) -> dict[str, Any]:
    selected_request = request or JobCompletionRequest()
    result = dict(selected_request.result)
    if selected_request.notes is not None:
        result["notes"] = selected_request.notes
    completed = orchestration_jobs.complete_job(job_id, result)
    if completed is None:
        raise HTTPException(status_code=404, detail="Agent job not found")
    return completed


def check_health_url(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return {"ok": 200 <= response.status < 300, "status": response.status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_age_seconds(selected: dict[str, Any]) -> float | None:
    timestamp = parse_utc_timestamp(selected.get("updated_at") or selected.get("created_at"))
    if timestamp is None:
        return None
    return (datetime.now(timezone.utc) - timestamp).total_seconds()


def cleanup_candidate_reasons(
    selected: dict[str, Any],
    explicit_run_ids: set[str],
    stale_launching_after_seconds: int,
) -> list[str]:
    reasons: list[str] = []
    run_id = str(selected.get("run_id") or "")
    status = str(selected.get("status") or "")
    if status in CLEANUP_CANDIDATE_STATUSES:
        reasons.append(status)
    if status == "launching":
        age = run_age_seconds(selected)
        if age is None:
            reasons.append("launching_without_timestamp")
        elif age >= stale_launching_after_seconds:
            reasons.append(f"stale_launching:{int(age)}s")
    if run_id in explicit_run_ids:
        reasons.append("explicitly_requested")
    return reasons


def cleanup_action_notes(selected: dict[str, Any], reasons: list[str]) -> list[str]:
    notes = ["Review the run state and record cleanup notes before taking action."]
    container_name = str(selected.get("container_name") or "")
    if container_name.startswith("dockyard-"):
        notes.append("Container removal is available only through run-scoped cleanup with remove_container=true.")
    elif container_name:
        notes.append("Container removal will be refused because the run container name is not dockyard-scoped.")
    else:
        notes.append("No concrete container name is recorded; cleanup can only record review notes.")
    if any(reason.startswith("stale_launching") for reason in reasons):
        notes.append("Launching state is stale; verify whether Docker ever created a runtime container.")
    return notes


def cleanup_plan_candidate(selected: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    container_name = selected.get("container_name")
    return {
        "run_id": selected.get("run_id"),
        "profile_id": selected.get("profile_id"),
        "status": selected.get("status"),
        "container_name": container_name,
        "health_url": selected.get("health_url"),
        "log_path": selected.get("log_file_path"),
        "candidate_reasons": reasons,
        "proposed_actions": [
            {
                "action": "record_cleanup_review",
                "docker_called": False,
                "notes": "POST /runs/{run_id}/cleanup without remove_container to record review notes.",
            },
            {
                "action": "remove_run_container",
                "docker_called": bool(container_name and str(container_name).startswith("dockyard-")),
                "notes": "POST /runs/{run_id}/cleanup with remove_container=true; only dockyard-* names are eligible.",
            },
        ],
        "action_notes": cleanup_action_notes(selected, reasons),
    }


def build_cleanup_plan(
    selected_runs: list[dict[str, Any]],
    requested_run_ids: list[str] | None = None,
    stale_launching_after_seconds: int = DEFAULT_STALE_LAUNCHING_SECONDS,
) -> dict[str, Any]:
    explicit_run_ids = set(requested_run_ids or [])
    seen_run_ids = {str(selected.get("run_id") or "") for selected in selected_runs}
    candidates = []
    for selected in selected_runs:
        reasons = cleanup_candidate_reasons(selected, explicit_run_ids, stale_launching_after_seconds)
        if reasons:
            candidates.append(cleanup_plan_candidate(selected, reasons))
    return {
        "schema_version": "model-plane-cleanup-plan-v1",
        "generated_at": run_state.utc_now(),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "missing_run_ids": sorted(run_id for run_id in explicit_run_ids if run_id not in seen_run_ids),
        "safety_notes": [
            "Cleanup planning is read-only and does not call Docker.",
            "Cleanup execution is run-scoped and never calls broad Docker prune.",
            "Container removal is only attempted for explicit cleanup requests on dockyard-* container names.",
        ],
    }


def cleanup_refusal_result(selected: dict[str, Any], request: CleanupRequest, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "action": "refused",
        "reason": reason,
        "requested_remove_container": request.remove_container,
        "docker_called": False,
        "container_name": selected.get("container_name"),
        "notes": request.notes,
    }


def cleanup_review_result(selected: dict[str, Any], request: CleanupRequest) -> dict[str, Any]:
    return {
        "ok": True,
        "action": "reviewed",
        "requested_remove_container": False,
        "docker_called": False,
        "container_name": selected.get("container_name"),
        "notes": request.notes,
    }


def cleanup_remove_container_result(selected: dict[str, Any], request: CleanupRequest) -> dict[str, Any]:
    container_name = str(selected.get("container_name") or "")
    if not container_name:
        return cleanup_refusal_result(selected, request, "Run does not record a concrete container name.")
    if not container_name.startswith("dockyard-"):
        return cleanup_refusal_result(
            selected,
            request,
            "Refusing to remove containers not created by Dockyard.",
        )
    result = subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=30, check=False)
    return {
        "ok": result.returncode == 0,
        "action": "container_removed" if result.returncode == 0 else "container_remove_failed",
        "requested_remove_container": True,
        "docker_called": True,
        "container_name": container_name,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "notes": request.notes,
    }


@app.post("/profiles/{profile_id}/health")
def check_profile_health(profile_id: str) -> dict[str, Any]:
    url = profile(profile_id)["health"]["url"]
    return check_health_url(url)


@app.post("/profiles/{profile_id}/launch")
def launch(profile_id: str) -> dict[str, Any]:
    selected = profile(profile_id)
    errors = [message for message in validate_profile(selected) if message["level"] == "error"]
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    command = render_command(selected)
    run = run_state.create_run(selected, command)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:
        run = run_state.record_launch_exception(run["run_id"], str(exc)) or run
        return {
            "ok": False,
            "run_id": run["run_id"],
            "returncode": None,
            "stdout": None,
            "stderr": str(exc),
            "run": run,
        }
    run = run_state.record_launch_result(run["run_id"], result.returncode, result.stdout, result.stderr) or run
    return {
        "ok": result.returncode == 0,
        "run_id": run["run_id"],
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "run": run,
    }


@app.get("/runs")
def runs() -> list[dict[str, Any]]:
    return run_state.list_runs()


@app.get("/runs/{run_id}")
def run(run_id: str) -> dict[str, Any]:
    selected = run_state.get_run(run_id)
    if selected is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return selected


@app.post("/runs/{run_id}/health")
def check_run_health(run_id: str) -> dict[str, Any]:
    selected = run(run_id)
    health_url = selected.get("health_url")
    if not health_url:
        raise HTTPException(status_code=400, detail="Run does not have a health URL.")
    result = check_health_url(str(health_url))
    updated = run_state.record_health_result(run_id, result)
    if updated is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return updated["last_health_result"]


@app.get("/runs/{run_id}/moe-probe-manifest")
def export_run_moe_probe_manifest(run_id: str) -> dict[str, Any]:
    selected = run(run_id)
    try:
        selected_profile = load_profile(str(selected["profile_id"]))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run profile not found") from exc
    return build_run_moe_probe_manifest(selected_profile, selected)


@app.post("/runs/{run_id}/cleanup")
def cleanup_run(run_id: str, request: CleanupRequest | None = None) -> dict[str, Any]:
    selected = run(run_id)
    cleanup_request = request or CleanupRequest()
    result = (
        cleanup_remove_container_result(selected, cleanup_request)
        if cleanup_request.remove_container
        else cleanup_review_result(selected, cleanup_request)
    )
    updated = run_state.record_cleanup_result(run_id, result)
    if updated is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"cleanup": updated["last_cleanup_result"], "run": updated}


@app.post("/containers/{container_name}/stop")
def stop(container_name: str) -> dict[str, Any]:
    if not container_name.startswith("dockyard-"):
        raise HTTPException(status_code=400, detail="Refusing to stop containers not created by Dockyard.")
    result = subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=30, check=False)
    return {"ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

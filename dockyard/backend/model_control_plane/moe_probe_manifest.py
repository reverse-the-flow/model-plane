from __future__ import annotations

from typing import Any

from .profile_types import capsule_client_base_url, is_capsule_gateway_profile, profile_health_url


MOE_MANIFEST_SCHEMA_VERSION = "model-plane-moe-probe-manifest-v1"
LLAMA_CPP_REQUIRED_OBSERVABILITY_PATHS = ["/metrics", "/slots", "/props", "/perf"]


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def endpoint_base_url(profile: dict[str, Any]) -> str:
    if is_capsule_gateway_profile(profile):
        return capsule_client_base_url(profile)
    health_url = str(profile.get("health", {}).get("url", "")).strip()
    if health_url:
        for suffix in ("/health", "/v1/models", "/models", "/api/capsules/status"):
            if health_url.endswith(suffix):
                return normalize_base_url(health_url[: -len(suffix)])
        return normalize_base_url(health_url)

    container = profile.get("container", {})
    host_port = container.get("host_port")
    if host_port:
        return f"http://127.0.0.1:{host_port}"
    return ""


def backend_family(profile: dict[str, Any]) -> str:
    backend = str(profile.get("runtime", {}).get("backend", "")).lower()
    aliases = {
        "llama.cpp": "llama_cpp",
        "llamacpp": "llama_cpp",
        "llama_cpp": "llama_cpp",
        "capsule_gateway": "capsule_gateway",
        "session_capsule_gateway": "capsule_gateway",
        "vllm": "vllm_openai_compatible",
        "ollama": "ollama_openai_compatible",
        "openai": "openai_compatible",
        "openai_compatible": "openai_compatible",
        "pytorch": "pytorch_transformers",
        "transformers": "pytorch_transformers",
        "pocketpal": "android_pocketpal",
        "android_pocketpal": "android_pocketpal",
        "android_llama_cpp": "android_llama_cpp",
        "android_emulator": "android_emulator",
    }
    return aliases.get(backend, backend or "unknown")


def is_android_edge_family(family: str) -> bool:
    return family.startswith("android_")


def semantic_expert_ids_status(profile: dict[str, Any]) -> str:
    moe_probe = profile.get("moe_probe", {})
    explicit = moe_probe.get("semantic_expert_ids")
    if isinstance(explicit, str):
        return explicit
    if bool(moe_probe.get("hookable_runtime_available")):
        return "expected_when_router_outputs_are_exposed"
    return "not_exposed"


def primary_probe_hint(profile: dict[str, Any]) -> str:
    moe_probe = profile.get("moe_probe", {})
    if is_android_edge_family(backend_family(profile)):
        return "edge_runtime_baseline"
    explicit = moe_probe.get("primary_probe_hint")
    if isinstance(explicit, str) and explicit:
        return explicit
    if bool(moe_probe.get("passive_sidecar_requested")):
        return "passive_sidecar"
    if bool(moe_probe.get("hookable_runtime_available")):
        return "hookable_pytorch"
    return "runtime_baseline"


def configured_log_file_path(profile: dict[str, Any]) -> Any:
    moe_probe = profile.get("moe_probe", {})
    logs = profile.get("logs", {})
    return moe_probe.get("log_file_path") or logs.get("file_path") or logs.get("host_path")


def configured_log_container_path(profile: dict[str, Any]) -> Any:
    moe_probe = profile.get("moe_probe", {})
    logs = profile.get("logs", {})
    return moe_probe.get("container_log_file_path") or logs.get("container_path")


def observability_paths(profile: dict[str, Any]) -> list[str]:
    moe_probe = profile.get("moe_probe", {})
    configured = moe_probe.get("observability_paths")
    if isinstance(configured, list) and configured:
        return [str(path) for path in configured]
    family = backend_family(profile)
    if family == "llama_cpp":
        return list(LLAMA_CPP_REQUIRED_OBSERVABILITY_PATHS)
    if is_android_edge_family(family):
        return []
    return ["/props", "/metrics", "/slots"]


def readiness_paths(profile: dict[str, Any]) -> list[str]:
    moe_probe = profile.get("moe_probe", {})
    configured = moe_probe.get("readiness_paths")
    if isinstance(configured, list) and configured:
        return [str(path) for path in configured]
    health_url = str(profile.get("health", {}).get("url") or "")
    if health_url.endswith("/health"):
        return ["/health"]
    if health_url.endswith("/v1/models"):
        return ["/v1/models"]
    if health_url.endswith("/models"):
        return ["/models"]
    return []


def build_moe_probe_manifest(profile: dict[str, Any]) -> dict[str, Any]:
    moe_probe = profile.get("moe_probe", {})
    model = profile.get("model", {})
    container = profile.get("container", {})
    family = backend_family(profile)
    android_edge = is_android_edge_family(family)
    semantic_status = "not_exposed" if android_edge else semantic_expert_ids_status(profile)
    hookable = False if android_edge else bool(moe_probe.get("hookable_runtime_available"))
    expected_paths = observability_paths(profile)
    configured_required_paths = moe_probe.get("required_observability_paths")
    required_paths = (
        expected_paths
        if family == "llama_cpp"
        else [str(path) for path in configured_required_paths]
        if isinstance(configured_required_paths, list)
        else []
    )
    ready_paths = readiness_paths(profile)
    log_file_path = configured_log_file_path(profile)
    container_log_file_path = configured_log_container_path(profile)
    return {
        "schema_version": MOE_MANIFEST_SCHEMA_VERSION,
        "profile_id": profile.get("id"),
        "profile_name": profile.get("name"),
        "model_id": model.get("id"),
        "model_path": model.get("local_path"),
        "backend_family": family,
        "base_url": endpoint_base_url(profile),
        "client_base_url": endpoint_base_url(profile),
        "health_url": profile_health_url(profile),
        "log_file_path": log_file_path,
        "container_name": container.get("name"),
        "primary_probe_hint": primary_probe_hint(profile),
        "semantic_expert_ids": semantic_status,
        "hookable_runtime_available": hookable,
        "passive_sidecar_requested": bool(moe_probe.get("passive_sidecar_requested")),
        "observability_paths": expected_paths,
        "readiness_paths": ready_paths,
        "log_paths": {
            "host_log_file_path": log_file_path,
            "container_log_file_path": container_log_file_path,
        },
        "runtime_observability": {
            "kind": "manual_edge_evidence" if android_edge else "runtime_evidence",
            "expected_paths": expected_paths,
            "required_paths": required_paths,
            "readiness_paths": ready_paths,
            "log_file_path": log_file_path,
            "container_log_file_path": container_log_file_path,
        },
        "safety_notes": [
            "Manifest export does not start containers or model servers.",
            "Manifest export does not inspect tokens or download models.",
            "Stock llama.cpp, vLLM, Ollama, and OpenAI-compatible telemetry is runtime evidence, not semantic expert ids.",
            "Android edge manifests are manual runtime baselines and never claim semantic expert ids.",
            "Use the hookable PyTorch path only when hookable_runtime_available is true and router outputs are exposed locally.",
            "Session Capsule gateway profiles supervise a proxy service; they do not move model weights or live KV tensors into Model Plane.",
        ],
    }


def build_run_moe_probe_manifest(profile: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    manifest = build_moe_probe_manifest(profile)
    latest_health = run.get("last_health_result")
    log_file_path = run.get("log_file_path") or manifest.get("log_file_path")
    runtime_observability = dict(manifest.get("runtime_observability") or {})
    runtime_observability["log_file_path"] = log_file_path
    manifest.update(
        {
            "run_id": run.get("run_id"),
            "profile_id": run.get("profile_id") or manifest.get("profile_id"),
            "profile_name": run.get("profile_name") or manifest.get("profile_name"),
            "model_id": run.get("model_id") or manifest.get("model_id"),
            "model_path": run.get("model_path") or manifest.get("model_path"),
            "backend_family": run.get("backend_family") or manifest.get("backend_family"),
            "base_url": run.get("base_url") or manifest.get("base_url"),
            "health_url": run.get("health_url") or manifest.get("health_url"),
            "container_name": run.get("container_name") or manifest.get("container_name"),
            "log_file_path": log_file_path,
            "log_paths": {
                **dict(manifest.get("log_paths") or {}),
                "host_log_file_path": log_file_path,
            },
            "runtime_observability": runtime_observability,
            "primary_probe_hint": run.get("primary_probe_hint") or manifest.get("primary_probe_hint"),
            "semantic_expert_ids": run.get("semantic_expert_ids") or manifest.get("semantic_expert_ids"),
            "semantic_expert_ids_status": run.get("semantic_expert_ids") or manifest.get("semantic_expert_ids"),
            "latest_health_result": latest_health,
            "run_status": run.get("status"),
            "run_created_at": run.get("created_at"),
            "run_updated_at": run.get("updated_at"),
            "launch": {
                "command": run.get("launch_command"),
                "shell_command": run.get("launch_shell_command"),
                "returncode": run.get("launch_returncode"),
                "stdout": run.get("launch_stdout"),
                "stderr": run.get("launch_stderr"),
            },
        }
    )
    return manifest

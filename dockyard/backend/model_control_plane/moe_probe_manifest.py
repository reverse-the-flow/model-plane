from __future__ import annotations

from typing import Any


MOE_MANIFEST_SCHEMA_VERSION = "model-plane-moe-probe-manifest-v1"


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def endpoint_base_url(profile: dict[str, Any]) -> str:
    health_url = str(profile.get("health", {}).get("url", "")).strip()
    if health_url:
        for suffix in ("/health", "/v1/models", "/models"):
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
        "vllm": "vllm_openai_compatible",
        "ollama": "ollama_openai_compatible",
        "openai": "openai_compatible",
        "openai_compatible": "openai_compatible",
        "pytorch": "pytorch_transformers",
        "transformers": "pytorch_transformers",
    }
    return aliases.get(backend, backend or "unknown")


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
    explicit = moe_probe.get("primary_probe_hint")
    if isinstance(explicit, str) and explicit:
        return explicit
    if bool(moe_probe.get("passive_sidecar_requested")):
        return "passive_sidecar"
    if bool(moe_probe.get("hookable_runtime_available")):
        return "hookable_pytorch"
    return "runtime_baseline"


def build_moe_probe_manifest(profile: dict[str, Any]) -> dict[str, Any]:
    moe_probe = profile.get("moe_probe", {})
    model = profile.get("model", {})
    container = profile.get("container", {})
    semantic_status = semantic_expert_ids_status(profile)
    hookable = bool(moe_probe.get("hookable_runtime_available"))
    return {
        "schema_version": MOE_MANIFEST_SCHEMA_VERSION,
        "profile_id": profile.get("id"),
        "profile_name": profile.get("name"),
        "model_id": model.get("id"),
        "model_path": model.get("local_path"),
        "backend_family": backend_family(profile),
        "base_url": endpoint_base_url(profile),
        "health_url": profile.get("health", {}).get("url"),
        "log_file_path": moe_probe.get("log_file_path") or profile.get("logs", {}).get("file_path"),
        "container_name": container.get("name"),
        "primary_probe_hint": primary_probe_hint(profile),
        "semantic_expert_ids": semantic_status,
        "hookable_runtime_available": hookable,
        "passive_sidecar_requested": bool(moe_probe.get("passive_sidecar_requested")),
        "runtime_observability": {
            "kind": "runtime_evidence",
            "expected_paths": list(moe_probe.get("observability_paths", ["/props", "/metrics", "/slots"])),
        },
        "safety_notes": [
            "Manifest export does not start containers or model servers.",
            "Manifest export does not inspect tokens or download models.",
            "Stock llama.cpp, vLLM, Ollama, and OpenAI-compatible telemetry is runtime evidence, not semantic expert ids.",
            "Use the hookable PyTorch path only when hookable_runtime_available is true and router outputs are exposed locally.",
        ],
    }

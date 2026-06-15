from __future__ import annotations

from copy import deepcopy
from typing import Any


FunctionDescriptor = dict[str, Any]

COMMON_FORBIDDEN_ACTIONS = [
    "download_models",
    "use_tokens",
    "docker_prune",
    "broad_deletion",
    "unapproved_prompt_traffic",
    "unapproved_model_launch",
    "start_model_server",
]

SECRET_FORBIDDEN_ACTIONS = COMMON_FORBIDDEN_ACTIONS + [
    "log_secret_values",
    "persist_secret_values",
    "echo_secret_values",
    "return_secret_values",
    "include_secret_values_in_manifests",
    "include_secret_values_in_job_state",
    "include_secret_values_in_rendered_commands",
]

FUNCTION_CATALOG: dict[str, FunctionDescriptor] = {
    "secret.hf_token.clear": {
        "function_id": "secret.hf_token.clear",
        "description": "Clear HF_TOKEN from the backend process and remove any remembered local Hugging Face token file without returning its value.",
        "method": "DELETE",
        "path_template": "/secrets/hf-token",
        "side_effect": "process_env_and_local_secret_clear",
        "required_fields": [],
        "allowed_for_cron": False,
        "forbidden_actions": SECRET_FORBIDDEN_ACTIONS,
    },
    "secret.hf_token.set": {
        "function_id": "secret.hf_token.set",
        "description": "Set the Hugging Face token in HF_TOKEN, optionally remembering it in the local configured secret path, without returning its value.",
        "method": "POST",
        "path_template": "/secrets/hf-token",
        "side_effect": "process_env_secret_write_optional_local_persistence",
        "required_fields": [],
        "allowed_for_cron": False,
        "forbidden_actions": SECRET_FORBIDDEN_ACTIONS,
        "default_body": {"token": "", "remember": False},
    },
    "secret.hf_token.status": {
        "function_id": "secret.hf_token.status",
        "description": "Return cron-readable redacted Hugging Face token status for HF_TOKEN and local persistence.",
        "method": "GET",
        "path_template": "/secrets/hf-token",
        "side_effect": "read_only_secret_status",
        "required_fields": [],
        "allowed_for_cron": True,
        "forbidden_actions": SECRET_FORBIDDEN_ACTIONS,
    },
    "profile.validate": {
        "function_id": "profile.validate",
        "description": "Validate one saved profile and return warnings or errors.",
        "method": "POST",
        "path_template": "/profiles/{profile_id}/validate",
        "side_effect": "read_only_validation",
        "required_fields": ["profile_id"],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
    },
    "run.health_check": {
        "function_id": "run.health_check",
        "description": "Check one run's explicit health URL and persist the result.",
        "method": "POST",
        "path_template": "/runs/{run_id}/health",
        "side_effect": "network_health_probe_and_run_state_write",
        "required_fields": ["run_id"],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
    },
    "run.moe_probe_manifest.export": {
        "function_id": "run.moe_probe_manifest.export",
        "description": "Export a run-scoped MoE Run Anyway manifest.",
        "method": "GET",
        "path_template": "/runs/{run_id}/moe-probe-manifest",
        "side_effect": "read_only_manifest_export",
        "required_fields": ["run_id"],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS
        + [
            "send_probe_prompt_without_explicit_operator_approval",
            "start_probe_sidecar_without_explicit_operator_approval",
        ],
    },
    "cleanup.review": {
        "function_id": "cleanup.review",
        "description": "Record review metadata for one run without removing containers.",
        "method": "POST",
        "path_template": "/runs/{run_id}/cleanup",
        "side_effect": "run_cleanup_review_state_write",
        "required_fields": ["run_id"],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS
        + [
            "docker_rm_without_explicit_operator_approval",
            "delete_run_record",
        ],
        "default_body": {"remove_container": False, "notes": "cron review packet"},
    },
    "job.complete": {
        "function_id": "job.complete",
        "description": "Record completion metadata for one agent job.",
        "method": "POST",
        "path_template": "/agent-jobs/{job_id}/complete",
        "side_effect": "agent_job_state_write",
        "required_fields": ["job_id"],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
        "default_body": {"result": {}, "notes": None},
    },
    "cron.tick": {
        "function_id": "cron.tick",
        "description": "Create or reuse bounded cron job packets from current profile and run state.",
        "method": "POST",
        "path_template": "/cron/tick",
        "side_effect": "agent_job_state_write",
        "required_fields": [],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
        "default_body": {"health_stale_seconds": 900},
    },
}


def list_function_descriptors() -> list[FunctionDescriptor]:
    return [deepcopy(FUNCTION_CATALOG[key]) for key in sorted(FUNCTION_CATALOG)]


def get_function_descriptor(function_id: str) -> FunctionDescriptor | None:
    descriptor = FUNCTION_CATALOG.get(function_id)
    if descriptor is None:
        return None
    return deepcopy(descriptor)


def format_path(path_template: str, fields: dict[str, Any]) -> str:
    path = path_template
    for key, value in fields.items():
        path = path.replace("{" + key + "}", str(value))
    return path


def build_call_descriptor(function_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    descriptor = get_function_descriptor(function_id)
    if descriptor is None:
        raise KeyError(function_id)
    missing = [field for field in descriptor["required_fields"] if not fields.get(field)]
    if missing:
        raise ValueError(f"Missing required fields for {function_id}: {', '.join(missing)}")
    body = deepcopy(descriptor.get("default_body"))
    return {
        "method": descriptor["method"],
        "path": format_path(descriptor["path_template"], fields),
        "body": body,
        "side_effect": descriptor["side_effect"],
    }

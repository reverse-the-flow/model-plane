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

HARNESS_EXPORT_FORBIDDEN_ACTIONS = COMMON_FORBIDDEN_ACTIONS + [
    "write_harness_config_files",
    "modify_hermes_config",
    "modify_openclaw_config",
    "send_prompt_traffic",
    "change_unrelated_services",
]

MOE_SMOKE_FORBIDDEN_ACTIONS = COMMON_FORBIDDEN_ACTIONS + [
    "change_model_weights",
    "claim_semantic_expert_ids_from_ollama_runtime_baseline",
    "run_unbounded_prompt_suite",
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
    "network.modes": {
        "function_id": "network.modes",
        "description": "List supported Model Plane network modes and their default bind/auth/mDNS behavior.",
        "method": "GET",
        "path_template": "/network/modes",
        "side_effect": "read_only_network_mode_catalog",
        "required_fields": [],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
    },
    "moe.test_cards.list": {
        "function_id": "moe.test_cards.list",
        "description": "List bounded MoE Run Anyway test cards and their rendered commands.",
        "method": "GET",
        "path_template": "/moe-test-cards",
        "side_effect": "read_only_moe_test_card_catalog",
        "required_fields": [],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
    },
    "moe.test_card.preflight": {
        "function_id": "moe.test_card.preflight",
        "description": "Run endpoint readiness preflight for one MoE Run Anyway test card without prompt traffic.",
        "method": "POST",
        "path_template": "/moe-test-cards/{card_id}/preflight",
        "side_effect": "network_readiness_probe_only",
        "required_fields": ["card_id"],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
    },
    "moe.test_card.smoke": {
        "function_id": "moe.test_card.smoke",
        "description": "Run one explicitly approved one-prompt MoE Run Anyway runtime baseline smoke card.",
        "method": "POST",
        "path_template": "/moe-test-cards/{card_id}/smoke",
        "side_effect": "approved_single_prompt_runtime_probe",
        "required_fields": ["card_id"],
        "allowed_for_cron": False,
        "forbidden_actions": MOE_SMOKE_FORBIDDEN_ACTIONS,
        "default_body": {"approved_prompt_traffic": False},
    },
    "profile.integration_preview.export": {
        "function_id": "profile.integration_preview.export",
        "description": "Export a pre-launch Hermes/OpenClaw integration preview for one profile.",
        "method": "GET",
        "path_template": "/profiles/{profile_id}/integration-preview",
        "side_effect": "read_only_harness_integration_preview",
        "required_fields": ["profile_id"],
        "allowed_for_cron": True,
        "forbidden_actions": HARNESS_EXPORT_FORBIDDEN_ACTIONS,
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
    "run.status": {
        "function_id": "run.status",
        "description": "Return one run's lifecycle status, managed process status, health metadata, and client base URL.",
        "method": "GET",
        "path_template": "/runs/{run_id}/status",
        "side_effect": "read_only_run_status",
        "required_fields": ["run_id"],
        "allowed_for_cron": True,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS,
    },
    "run.stop": {
        "function_id": "run.stop",
        "description": "Stop one run-scoped managed process or Dockyard container when explicitly authorized.",
        "method": "POST",
        "path_template": "/runs/{run_id}/stop",
        "side_effect": "run_scoped_process_or_container_stop",
        "required_fields": ["run_id"],
        "allowed_for_cron": False,
        "forbidden_actions": COMMON_FORBIDDEN_ACTIONS
        + [
            "stop_without_explicit_operator_approval",
            "stop_unrelated_process",
            "stop_unrelated_container",
        ],
        "default_body": {"timeout_seconds": 10, "notes": None},
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
    "run.integration_bundle.export": {
        "function_id": "run.integration_bundle.export",
        "description": "Export a run-scoped Hermes/OpenClaw integration bundle.",
        "method": "GET",
        "path_template": "/runs/{run_id}/integration-bundle",
        "side_effect": "read_only_harness_integration_bundle_export",
        "required_fields": ["run_id"],
        "allowed_for_cron": True,
        "forbidden_actions": HARNESS_EXPORT_FORBIDDEN_ACTIONS,
    },
    "run.integration_bundle.check": {
        "function_id": "run.integration_bundle.check",
        "description": "Check host and Docker-context /v1/models reachability for one run integration bundle.",
        "method": "POST",
        "path_template": "/runs/{run_id}/integration-bundle/check",
        "side_effect": "network_connectivity_probe_only",
        "required_fields": ["run_id"],
        "allowed_for_cron": True,
        "forbidden_actions": HARNESS_EXPORT_FORBIDDEN_ACTIONS,
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

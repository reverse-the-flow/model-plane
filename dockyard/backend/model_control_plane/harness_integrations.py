from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

import yaml

from .moe_probe_manifest import backend_family, endpoint_base_url
from .network_policy import docker_harness_url, network_policy_summary, rewrite_local_url_for_policy, url_with_host
from .profile_types import is_capsule_gateway_profile, profile_health_url, profile_model_id


INTEGRATION_BUNDLE_SCHEMA_VERSION = "model-plane-harness-integration-bundle-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_alias_part(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "model"


def configured_integrations(profile: dict[str, Any]) -> dict[str, Any]:
    integrations = profile.get("integrations")
    return integrations if isinstance(integrations, dict) else {}


def stable_alias(profile: dict[str, Any], run: dict[str, Any] | None = None) -> str:
    integrations = configured_integrations(profile)
    explicit = integrations.get("alias")
    openclaw = integrations.get("openclaw") if isinstance(integrations.get("openclaw"), dict) else {}
    if not explicit:
        explicit = openclaw.get("alias")
    if explicit:
        return str(explicit)
    profile_id = str((run or {}).get("profile_id") or profile.get("id") or "")
    return f"{clean_alias_part(profile_id)}.local"


def display_name(profile: dict[str, Any], run: dict[str, Any] | None = None) -> str:
    return str((run or {}).get("profile_name") or profile.get("name") or profile.get("id") or "Model Plane endpoint")


def ensure_openai_v1_base_url(url: str) -> str:
    selected = str(url or "").strip().rstrip("/")
    if not selected:
        return ""
    if selected.endswith("/v1/models"):
        return selected[: -len("/models")]
    if selected.endswith("/models"):
        return selected[: -len("/models")]
    if selected.endswith("/v1"):
        return selected
    return f"{selected}/v1"


def models_url(base_url: str) -> str:
    selected = ensure_openai_v1_base_url(base_url)
    return f"{selected.rstrip('/')}/models" if selected else ""


def profile_base_url(profile: dict[str, Any]) -> str:
    integrations = configured_integrations(profile)
    hermes = integrations.get("hermes") if isinstance(integrations.get("hermes"), dict) else {}
    openclaw = integrations.get("openclaw") if isinstance(integrations.get("openclaw"), dict) else {}
    configured = hermes.get("base_url") or openclaw.get("base_url")
    if configured:
        return ensure_openai_v1_base_url(rewrite_local_url_for_policy(profile, str(configured)))
    return ensure_openai_v1_base_url(rewrite_local_url_for_policy(profile, endpoint_base_url(profile)))


def run_base_url(profile: dict[str, Any], run: dict[str, Any] | None) -> str:
    if not run:
        return profile_base_url(profile)
    return ensure_openai_v1_base_url(str(run.get("client_base_url") or run.get("base_url") or profile_base_url(profile)))


def run_is_healthy(run: dict[str, Any]) -> bool:
    latest = run.get("last_health_result")
    return run.get("status") == "healthy" and isinstance(latest, dict) and latest.get("ok") is True


def healthy_linked_capsule_run(
    base_run: dict[str, Any] | None,
    profiles: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if base_run is None or base_run.get("service_type") == "capsule_gateway":
        return None
    base_profile_id = str(base_run.get("profile_id") or "")
    if not base_profile_id:
        return None
    profiles_by_id = {str(profile.get("id") or ""): profile for profile in profiles}
    for candidate in runs:
        candidate_profile = profiles_by_id.get(str(candidate.get("profile_id") or ""))
        if not candidate_profile or not is_capsule_gateway_profile(candidate_profile):
            continue
        if not run_is_healthy(candidate):
            continue
        endpoint = candidate_profile.get("endpoint")
        if isinstance(endpoint, dict) and endpoint.get("runtime_profile_id") == base_profile_id:
            return candidate_profile, candidate
    return None


def preferred_endpoint(
    profile: dict[str, Any],
    run: dict[str, Any] | None,
    profiles: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_runtime_base_url = run_base_url(profile, run)
    if run and run.get("service_type") == "capsule_gateway":
        return {
            "source": "session_capsule_gateway",
            "run_id": run.get("run_id"),
            "profile_id": run.get("profile_id"),
            "base_url": raw_runtime_base_url,
            "raw_runtime_base_url": None,
        }
    linked = healthy_linked_capsule_run(run, profiles or [], runs or [])
    if linked is not None:
        capsule_profile, capsule_run = linked
        return {
            "source": "session_capsule_gateway",
            "run_id": capsule_run.get("run_id"),
            "profile_id": capsule_run.get("profile_id"),
            "base_url": run_base_url(capsule_profile, capsule_run),
            "raw_runtime_base_url": raw_runtime_base_url,
        }
    return {
        "source": "run" if run else "profile",
        "run_id": (run or {}).get("run_id"),
        "profile_id": (run or {}).get("profile_id") or profile.get("id"),
        "base_url": raw_runtime_base_url,
        "raw_runtime_base_url": raw_runtime_base_url,
    }


def base_url_variants(profile: dict[str, Any], base_url: str) -> dict[str, str]:
    selected = ensure_openai_v1_base_url(base_url)
    local = url_with_host(selected, "127.0.0.1")
    return {
        "host": selected,
        "docker_harness": docker_harness_url(profile, selected),
        "localhost": local,
    }


def connectivity_targets(profile: dict[str, Any], base_url: str) -> dict[str, str]:
    return {context: models_url(url) for context, url in base_url_variants(profile, base_url).items()}


def tag_list(profile: dict[str, Any], run: dict[str, Any] | None, selected_backend_family: str) -> list[str]:
    model = profile.get("model") if isinstance(profile.get("model"), dict) else {}
    tags = [
        f"backend:{selected_backend_family}",
        f"profile:{str((run or {}).get('profile_id') or profile.get('id') or 'unknown')}",
    ]
    quant = model.get("quant")
    if quant:
        tags.append(f"quant:{quant}")
    hardware = profile.get("hardware_profile") or profile.get("hardware")
    if hardware:
        tags.append(f"hardware:{hardware}")
    return tags


def snippets(alias: str, base_url: str, tags: list[str], profile: dict[str, Any]) -> dict[str, Any]:
    integrations = configured_integrations(profile)
    hermes_config = {
        "provider": "openai",
        "model": alias,
        "base_url": base_url,
        "api_key": str((integrations.get("hermes") or {}).get("api_key_label") or "local")
        if isinstance(integrations.get("hermes"), dict)
        else "local",
    }
    openclaw_route = {
        "alias": alias,
        "base_url": base_url,
        "tags": tags,
    }
    return {
        "hermes": {
            "json": hermes_config,
            "yaml": yaml.safe_dump(hermes_config, sort_keys=False),
            "text": "\n".join(f"{key}: {value}" for key, value in hermes_config.items()),
        },
        "openclaw": {
            "json": openclaw_route,
            "yaml": yaml.safe_dump(openclaw_route, sort_keys=False),
            "text": f"{alias} -> {base_url}",
        },
    }


def build_harness_integration_bundle(
    profile: dict[str, Any],
    run: dict[str, Any] | None = None,
    profiles: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
    connectivity_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_backend_family = str((run or {}).get("backend_family") or backend_family(profile))
    alias = stable_alias(profile, run)
    endpoint = preferred_endpoint(profile, run, profiles, runs)
    preferred_base_url = ensure_openai_v1_base_url(str(endpoint.get("base_url") or ""))
    variants = base_url_variants(profile, preferred_base_url)
    checks = list(connectivity_checks or [])
    return {
        "schema_version": INTEGRATION_BUNDLE_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "run_id": (run or {}).get("run_id"),
        "profile_id": (run or {}).get("profile_id") or profile.get("id"),
        "model_id": (run or {}).get("model_id") or profile_model_id(profile),
        "backend_family": selected_backend_family,
        "display_name": display_name(profile, run),
        "alias": alias,
        "provider_kind": "openai_compatible",
        "base_url": preferred_base_url,
        "preferred_base_url": preferred_base_url,
        "raw_runtime_base_url": endpoint.get("raw_runtime_base_url"),
        "alternate_base_urls": variants,
        "connectivity_targets": connectivity_targets(profile, preferred_base_url),
        "network": network_policy_summary(profile),
        "health_url": (run or {}).get("health_url") or profile_health_url(profile),
        "latest_health_result": (run or {}).get("last_health_result"),
        "preferred_endpoint": endpoint,
        "config_snippets": snippets(alias, preferred_base_url, tag_list(profile, run, selected_backend_family), profile),
        "connectivity_checks": checks,
        "connectivity_summary": connectivity_summary(checks),
        "provenance": {
            "profile": "profile-derived",
            "run": "run-derived" if run else "profile-preview",
            "alias": "user-entered" if configured_integrations(profile).get("alias") else "defaulted",
            "base_url": endpoint.get("source") or "profile",
            "snippets": "generated",
        },
        "safety_notes": [
            "This bundle is export-only and does not write Hermes or OpenClaw configuration files.",
            "Connectivity checks call /v1/models only and do not send prompt traffic.",
            "api_key is a local placeholder label, not a secret value.",
        ],
    }


def connectivity_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    by_context = {str(check.get("context")): check for check in checks if isinstance(check, dict)}
    host = by_context.get("host")
    docker = by_context.get("docker_harness")
    if not checks:
        return {"checked": False, "message": "Connectivity has not been checked."}
    if host and host.get("ok") is True and docker and docker.get("ok") is not True:
        return {"checked": True, "ok": True, "message": "Host reachable, Docker harness unreachable."}
    return {
        "checked": True,
        "ok": all(check.get("ok") is True for check in checks),
        "message": "All checked contexts are reachable."
        if all(check.get("ok") is True for check in checks)
        else "One or more checked contexts are unreachable.",
    }


UrlOpen = Callable[..., Any]


def check_url(context: str, url: str, urlopen: UrlOpen | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"context": context, "url": url, "checked_at": utc_now()}
    if not url:
        result.update({"ok": False, "error": "No connectivity target URL is available."})
        return result
    try:
        selected_urlopen = urlopen or urllib.request.urlopen
        with selected_urlopen(url, timeout=5) as response:
            result.update({"ok": 200 <= response.status < 300, "status": response.status})
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})
    return result


def check_harness_connectivity(bundle: dict[str, Any], urlopen: UrlOpen | None = None) -> dict[str, Any]:
    targets = bundle.get("connectivity_targets")
    if not isinstance(targets, dict):
        targets = {}
    checks = [
        check_url("host", str(targets.get("host") or ""), urlopen),
        check_url("docker_harness", str(targets.get("docker_harness") or ""), urlopen),
    ]
    updated = json.loads(json.dumps(bundle))
    updated["connectivity_checks"] = checks
    updated["connectivity_summary"] = connectivity_summary(checks)
    return updated

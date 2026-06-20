from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .opencode_harness import OPENCODE_ACTIVE_MODEL, OPENCODE_PROVIDER_ID


T3CODE_MODULE_ID = "t3code_harness"
T3CODE_OPENCODE_INSTANCE_ID = "opencode"
T3CODE_MODEL_PLANE_MODEL = f"{OPENCODE_PROVIDER_ID}/{OPENCODE_ACTIVE_MODEL}"


def default_t3code_settings_path() -> Path:
    configured = os.environ.get("T3CODE_SETTINGS_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".t3" / "userdata" / "settings.json"


def _clean_optional_string(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def t3code_settings_patch(
    *,
    opencode_binary_path: str | None = "opencode",
    opencode_server_url: str | None = None,
    opencode_server_password: str | None = None,
    select_model_plane: bool = True,
) -> dict[str, Any]:
    opencode_settings: dict[str, Any] = {
        "enabled": True,
        "customModels": [T3CODE_MODEL_PLANE_MODEL],
    }
    binary_path = _clean_optional_string(opencode_binary_path)
    if binary_path:
        opencode_settings["binaryPath"] = binary_path
    server_url = _clean_optional_string(opencode_server_url)
    if server_url:
        opencode_settings["serverUrl"] = server_url
    server_password = _clean_optional_string(opencode_server_password)
    if server_password:
        opencode_settings["serverPassword"] = server_password

    patch: dict[str, Any] = {"providers": {T3CODE_OPENCODE_INSTANCE_ID: opencode_settings}}
    if select_model_plane:
        patch["textGenerationModelSelection"] = {
            "instanceId": T3CODE_OPENCODE_INSTANCE_ID,
            "model": T3CODE_MODEL_PLANE_MODEL,
        }
    return patch


def _merge_model_lists(existing: Any, patch: list[str]) -> list[str]:
    merged: list[str] = []
    for value in (existing if isinstance(existing, list) else []):
        if isinstance(value, str) and value not in merged:
            merged.append(value)
    for value in patch:
        if value not in merged:
            merged.append(value)
    return merged


def merge_t3code_settings(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, dict):
        raise ValueError("T3 Code settings must be a JSON object.")
    if not isinstance(patch, dict):
        raise ValueError("T3 Code settings patch must be a JSON object.")

    merged = dict(existing)
    providers = merged.get("providers")
    if providers is None:
        providers = {}
    if not isinstance(providers, dict):
        raise ValueError("T3 Code settings providers field must be an object.")

    patch_providers = patch.get("providers")
    if patch_providers is not None and not isinstance(patch_providers, dict):
        raise ValueError("T3 Code settings patch providers field must be an object.")

    providers = dict(providers)
    for provider_id, provider_patch in (patch_providers or {}).items():
        if not isinstance(provider_patch, dict):
            raise ValueError("T3 Code provider patch entries must be objects.")
        current_provider = providers.get(provider_id)
        if current_provider is None:
            current_provider = {}
        if not isinstance(current_provider, dict):
            raise ValueError("T3 Code provider settings entries must be objects.")
        next_provider = dict(current_provider)
        for key, value in provider_patch.items():
            if key == "customModels":
                next_provider[key] = _merge_model_lists(next_provider.get(key), value if isinstance(value, list) else [])
            else:
                next_provider[key] = value
        providers[provider_id] = next_provider

    merged["providers"] = providers
    if "textGenerationModelSelection" in patch:
        merged["textGenerationModelSelection"] = patch["textGenerationModelSelection"]
    return merged


def t3code_config_payload(
    *,
    settings_path: Path | None = None,
    opencode_binary_path: str | None = "opencode",
    opencode_server_url: str | None = None,
    select_model_plane: bool = True,
) -> dict[str, Any]:
    patch = t3code_settings_patch(
        opencode_binary_path=opencode_binary_path,
        opencode_server_url=opencode_server_url,
        select_model_plane=select_model_plane,
    )
    return {
        "settings_path": str(settings_path or default_t3code_settings_path()),
        "provider": T3CODE_OPENCODE_INSTANCE_ID,
        "model": T3CODE_MODEL_PLANE_MODEL,
        "settings_patch": patch,
        "requirements": [
            "T3 Code uses its OpenCode provider, not a raw OpenAI /v1 endpoint.",
            "OpenCode must be installed or T3 Code must be pointed at an existing OpenCode server URL.",
            "OpenCode must be configured with provider model-plane and model active.",
        ],
    }


def apply_t3code_settings(
    settings_path: Path | None = None,
    *,
    opencode_binary_path: str | None = "opencode",
    opencode_server_url: str | None = None,
    opencode_server_password: str | None = None,
    select_model_plane: bool = True,
) -> dict[str, Any]:
    selected_path = (settings_path or default_t3code_settings_path()).expanduser()
    patch = t3code_settings_patch(
        opencode_binary_path=opencode_binary_path,
        opencode_server_url=opencode_server_url,
        opencode_server_password=opencode_server_password,
        select_model_plane=select_model_plane,
    )
    if selected_path.exists():
        try:
            existing = json.loads(selected_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("T3 Code settings must be strict JSON for safe helper-managed writes.") from exc
        backup_path = selected_path.with_name(
            selected_path.name + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bak"
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected_path, backup_path)
    else:
        existing = {}
        backup_path = None
        selected_path.parent.mkdir(parents=True, exist_ok=True)

    merged = merge_t3code_settings(existing, patch)
    selected_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "settings_path": str(selected_path),
        "backup_path": str(backup_path) if backup_path else None,
        "provider": T3CODE_OPENCODE_INSTANCE_ID,
        "model": T3CODE_MODEL_PLANE_MODEL,
    }

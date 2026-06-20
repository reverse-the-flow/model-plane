from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPENCODE_MODULE_ID = "opencode_harness"
OPENCODE_PROVIDER_ID = "model-plane"
OPENCODE_PROVIDER_NAME = "Model Plane Local Helper"
OPENCODE_ACTIVE_MODEL = "active"
OPENCODE_ACTIVE_MODEL_NAME = "Model Plane Active"
OPENCODE_CONFIG_SCHEMA = "https://opencode.ai/config.json"
OPENCODE_NPM_PACKAGE = "@ai-sdk/openai-compatible"
OPENCODE_HARNESS_PATH = "/harness/opencode/v1"
TARGET_STORE_SCHEMA_VERSION = "model-plane-opencode-target-store-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_helper_state_dir() -> Path:
    configured = os.environ.get("MODEL_PLANE_HELPER_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ModelPlane" / "LocalHelper"
    return Path.home() / ".local" / "share" / "model-plane" / "local-helper"


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


def clean_target_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "target"


def helper_base_url(host: str = "127.0.0.1", port: int = 19112) -> str:
    return f"http://{host}:{port}{OPENCODE_HARNESS_PATH}"


def provider_block(base_url: str) -> dict[str, Any]:
    return {
        "npm": OPENCODE_NPM_PACKAGE,
        "name": OPENCODE_PROVIDER_NAME,
        "options": {"baseURL": ensure_openai_v1_base_url(base_url)},
        "models": {
            OPENCODE_ACTIVE_MODEL: {
                "name": OPENCODE_ACTIVE_MODEL_NAME,
            },
        },
    }


def opencode_config(base_url: str) -> dict[str, Any]:
    return {
        "$schema": OPENCODE_CONFIG_SCHEMA,
        "provider": {
            OPENCODE_PROVIDER_ID: provider_block(base_url),
        },
    }


def target_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("Target import requires a JSON object.")
    base_url = ensure_openai_v1_base_url(str(bundle.get("preferred_base_url") or bundle.get("base_url") or ""))
    if not base_url:
        raise ValueError("Target bundle must include base_url or preferred_base_url.")
    provider_kind = str(bundle.get("provider_kind") or "openai_compatible")
    if provider_kind != "openai_compatible":
        raise ValueError("Target bundle must describe an OpenAI-compatible endpoint.")
    run_id = str(bundle.get("run_id") or "").strip()
    profile_id = str(bundle.get("profile_id") or "").strip()
    target_id = clean_target_id(run_id or profile_id or str(bundle.get("alias") or "target"))
    model_id = str(bundle.get("model_id") or bundle.get("alias") or OPENCODE_ACTIVE_MODEL).strip()
    now = utc_now()
    return {
        "target_id": target_id,
        "imported_at": now,
        "updated_at": now,
        "source": "model_plane_integration_bundle",
        "run_id": run_id or None,
        "profile_id": profile_id or None,
        "alias": str(bundle.get("alias") or target_id),
        "display_name": str(bundle.get("display_name") or bundle.get("alias") or target_id),
        "model_id": model_id,
        "provider_kind": provider_kind,
        "base_url": base_url,
        "raw_runtime_base_url": bundle.get("raw_runtime_base_url"),
        "preferred_endpoint": bundle.get("preferred_endpoint") if isinstance(bundle.get("preferred_endpoint"), dict) else {},
        "network": bundle.get("network") if isinstance(bundle.get("network"), dict) else {},
        "latest_health_result": bundle.get("latest_health_result") if isinstance(bundle.get("latest_health_result"), dict) else None,
    }


def empty_store() -> dict[str, Any]:
    return {
        "schema_version": TARGET_STORE_SCHEMA_VERSION,
        "selected_target_id": None,
        "targets": [],
    }


def read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_store()
    if not isinstance(data, dict):
        return empty_store()
    targets = data.get("targets")
    return {
        "schema_version": str(data.get("schema_version") or TARGET_STORE_SCHEMA_VERSION),
        "selected_target_id": data.get("selected_target_id"),
        "targets": [target for target in targets if isinstance(target, dict)] if isinstance(targets, list) else [],
    }


def write_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": str(data.get("schema_version") or TARGET_STORE_SCHEMA_VERSION),
        "selected_target_id": data.get("selected_target_id"),
        "targets": list(data.get("targets") or []),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


class OpenCodeTargetStore:
    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or default_helper_state_dir() / OPENCODE_MODULE_ID
        self.store_path = self.state_dir / "targets.json"

    def read(self) -> dict[str, Any]:
        return read_store(self.store_path)

    def list_targets(self) -> list[dict[str, Any]]:
        return self.read()["targets"]

    def get_target(self, target_id: str) -> dict[str, Any] | None:
        for target in self.list_targets():
            if target.get("target_id") == target_id:
                return target
        return None

    def selected_target(self) -> dict[str, Any] | None:
        selected_id = self.read().get("selected_target_id")
        return self.get_target(str(selected_id)) if selected_id else None

    def upsert_target(self, target: dict[str, Any], select: bool = False) -> dict[str, Any]:
        data = self.read()
        target_id = str(target["target_id"])
        targets = [existing for existing in data["targets"] if existing.get("target_id") != target_id]
        targets.append(target)
        data["targets"] = sorted(targets, key=lambda item: str(item.get("target_id") or ""))
        if select:
            data["selected_target_id"] = target_id
        write_store(self.store_path, data)
        return target

    def import_bundle(self, bundle: dict[str, Any], select: bool = False) -> dict[str, Any]:
        return self.upsert_target(target_from_bundle(bundle), select=select)

    def select_target(self, target_id: str) -> dict[str, Any]:
        target = self.get_target(target_id)
        if target is None:
            raise KeyError(target_id)
        data = self.read()
        data["selected_target_id"] = target_id
        write_store(self.store_path, data)
        return target


def strip_hop_by_hop_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def rewrite_chat_payload(payload: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    updated = dict(payload)
    if str(updated.get("model") or "") == OPENCODE_ACTIVE_MODEL:
        updated["model"] = target.get("model_id") or target.get("alias") or OPENCODE_ACTIVE_MODEL
    return updated


def openai_models_response(target: dict[str, Any] | None) -> dict[str, Any]:
    model = {
        "id": OPENCODE_ACTIVE_MODEL,
        "object": "model",
        "created": 0,
        "owned_by": OPENCODE_PROVIDER_ID,
    }
    if target:
        model["target"] = {
            "target_id": target.get("target_id"),
            "display_name": target.get("display_name"),
            "model_id": target.get("model_id"),
            "base_url": target.get("base_url"),
        }
    return {"object": "list", "data": [model]}


def merge_opencode_config(existing: dict[str, Any], base_url: str) -> dict[str, Any]:
    if not isinstance(existing, dict):
        raise ValueError("OpenCode config must be a JSON object.")
    merged = dict(existing)
    merged.setdefault("$schema", OPENCODE_CONFIG_SCHEMA)
    providers = merged.get("provider")
    if providers is None:
        providers = {}
    if not isinstance(providers, dict):
        raise ValueError("OpenCode config provider field must be an object.")
    providers = dict(providers)
    providers[OPENCODE_PROVIDER_ID] = provider_block(base_url)
    merged["provider"] = providers
    return merged


def apply_opencode_config(config_path: Path, base_url: str) -> dict[str, Any]:
    selected_path = config_path.expanduser()
    if selected_path.exists():
        try:
            existing = json.loads(selected_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("OpenCode config must be strict JSON for safe helper-managed writes.") from exc
        backup_path = selected_path.with_name(selected_path.name + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bak")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected_path, backup_path)
    else:
        existing = {}
        backup_path = None
        selected_path.parent.mkdir(parents=True, exist_ok=True)
    merged = merge_opencode_config(existing, base_url)
    selected_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "config_path": str(selected_path),
        "backup_path": str(backup_path) if backup_path else None,
        "provider_id": OPENCODE_PROVIDER_ID,
        "model": OPENCODE_ACTIVE_MODEL,
    }

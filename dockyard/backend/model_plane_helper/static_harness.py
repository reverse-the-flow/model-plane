from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from .opencode_harness import default_helper_state_dir, ensure_openai_v1_base_url


HERMES_MODULE_ID = "hermes_harness"
OPENCLAW_MODULE_ID = "openclaw_harness"
STATIC_HARNESS_STORE_SCHEMA_VERSION = "model-plane-static-harness-target-store-v1"
StaticHarnessId = Literal["hermes", "openclaw"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_target_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "target"


def empty_store() -> dict[str, Any]:
    return {
        "schema_version": STATIC_HARNESS_STORE_SCHEMA_VERSION,
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
        "schema_version": str(data.get("schema_version") or STATIC_HARNESS_STORE_SCHEMA_VERSION),
        "selected_target_id": data.get("selected_target_id"),
        "targets": [target for target in targets if isinstance(target, dict)] if isinstance(targets, list) else [],
    }


def write_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": str(data.get("schema_version") or STATIC_HARNESS_STORE_SCHEMA_VERSION),
        "selected_target_id": data.get("selected_target_id"),
        "targets": list(data.get("targets") or []),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def hermes_config_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    snippets = bundle.get("config_snippets") if isinstance(bundle.get("config_snippets"), dict) else {}
    hermes = snippets.get("hermes") if isinstance(snippets.get("hermes"), dict) else {}
    candidate = hermes.get("json")
    if isinstance(candidate, dict):
        return dict(candidate)
    alias = str(bundle.get("alias") or bundle.get("model_id") or "model-plane.local")
    base_url = ensure_openai_v1_base_url(str(bundle.get("preferred_base_url") or bundle.get("base_url") or ""))
    return {
        "provider": "openai",
        "model": alias,
        "base_url": base_url,
        "api_key": "local",
    }


def openclaw_route_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    snippets = bundle.get("config_snippets") if isinstance(bundle.get("config_snippets"), dict) else {}
    openclaw = snippets.get("openclaw") if isinstance(snippets.get("openclaw"), dict) else {}
    candidate = openclaw.get("json")
    if isinstance(candidate, dict):
        return dict(candidate)
    alias = str(bundle.get("alias") or bundle.get("model_id") or "model-plane.local")
    base_url = ensure_openai_v1_base_url(str(bundle.get("preferred_base_url") or bundle.get("base_url") or ""))
    return {
        "alias": alias,
        "base_url": base_url,
        "tags": [],
    }


def config_from_bundle(bundle: dict[str, Any], harness: StaticHarnessId) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("Harness target import requires a JSON object.")
    provider_kind = str(bundle.get("provider_kind") or "openai_compatible")
    if provider_kind != "openai_compatible":
        raise ValueError("Harness target bundle must describe an OpenAI-compatible endpoint.")
    base_url = ensure_openai_v1_base_url(str(bundle.get("preferred_base_url") or bundle.get("base_url") or ""))
    if not base_url:
        raise ValueError("Harness target bundle must include base_url or preferred_base_url.")
    return hermes_config_from_bundle(bundle) if harness == "hermes" else openclaw_route_from_bundle(bundle)


def target_from_bundle(bundle: dict[str, Any], harness: StaticHarnessId) -> dict[str, Any]:
    config = config_from_bundle(bundle, harness)
    run_id = str(bundle.get("run_id") or "").strip()
    profile_id = str(bundle.get("profile_id") or "").strip()
    target_id = clean_target_id(run_id or profile_id or str(bundle.get("alias") or "target"))
    now = utc_now()
    return {
        "target_id": target_id,
        "imported_at": now,
        "updated_at": now,
        "source": "model_plane_integration_bundle",
        "harness": harness,
        "run_id": run_id or None,
        "profile_id": profile_id or None,
        "alias": str(bundle.get("alias") or target_id),
        "display_name": str(bundle.get("display_name") or bundle.get("alias") or target_id),
        "model_id": str(bundle.get("model_id") or bundle.get("alias") or target_id),
        "provider_kind": "openai_compatible",
        "base_url": ensure_openai_v1_base_url(str(bundle.get("preferred_base_url") or bundle.get("base_url") or "")),
        "config": config,
        "latest_health_result": bundle.get("latest_health_result") if isinstance(bundle.get("latest_health_result"), dict) else None,
    }


class StaticHarnessTargetStore:
    def __init__(self, harness: StaticHarnessId, state_dir: Path | None = None) -> None:
        self.harness = harness
        module_id = HERMES_MODULE_ID if harness == "hermes" else OPENCLAW_MODULE_ID
        self.state_dir = state_dir or default_helper_state_dir() / module_id
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
        return self.upsert_target(target_from_bundle(bundle, self.harness), select=select)

    def select_target(self, target_id: str) -> dict[str, Any]:
        target = self.get_target(target_id)
        if target is None:
            raise KeyError(target_id)
        data = self.read()
        data["selected_target_id"] = target_id
        write_store(self.store_path, data)
        return target


def render_config(config: dict[str, Any], output_format: str = "yaml") -> str:
    selected_format = output_format.lower().strip()
    if selected_format == "json":
        return json.dumps(config, indent=2, sort_keys=True) + "\n"
    if selected_format == "yaml":
        return yaml.safe_dump(config, sort_keys=False)
    if selected_format == "text":
        return "\n".join(f"{key}: {value}" for key, value in config.items()) + "\n"
    raise ValueError("Harness config format must be one of: yaml, json, text.")


def write_config_file(config_path: Path, config: dict[str, Any], output_format: str = "yaml") -> dict[str, Any]:
    selected_path = config_path.expanduser()
    rendered = render_config(config, output_format)
    if selected_path.exists():
        backup_path = selected_path.with_name(
            selected_path.name + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bak"
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected_path, backup_path)
    else:
        backup_path = None
        selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_text(rendered, encoding="utf-8")
    return {
        "ok": True,
        "config_path": str(selected_path),
        "backup_path": str(backup_path) if backup_path else None,
        "format": output_format.lower().strip(),
    }

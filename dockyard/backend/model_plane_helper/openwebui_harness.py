from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from .opencode_harness import (
    OPENCODE_ACTIVE_MODEL,
    OpenCodeTargetStore,
    default_helper_state_dir,
    ensure_openai_v1_base_url,
)


OPENWEBUI_MODULE_ID = "openwebui_harness"
OPENWEBUI_HARNESS_PATH = "/harness/openwebui/v1"
OPENWEBUI_ACTIVE_MODEL = OPENCODE_ACTIVE_MODEL
OPENWEBUI_API_KEY_PLACEHOLDER = "local"


class OpenWebUITargetStore(OpenCodeTargetStore):
    def __init__(self, state_dir: Path | None = None) -> None:
        super().__init__(state_dir or default_helper_state_dir() / OPENWEBUI_MODULE_ID)


def openwebui_helper_base_url(host: str = "127.0.0.1", port: int = 19112, *, docker: bool = False) -> str:
    selected_host = "host.docker.internal" if docker and host in {"127.0.0.1", "localhost"} else host
    return f"http://{selected_host}:{port}{OPENWEBUI_HARNESS_PATH}"


def openwebui_env(
    base_url: str,
    *,
    api_key: str = OPENWEBUI_API_KEY_PLACEHOLDER,
    default_model: str = OPENWEBUI_ACTIVE_MODEL,
    include_default_model: bool = True,
) -> dict[str, str]:
    env = {
        "ENABLE_OPENAI_API": "True",
        "OPENAI_API_BASE_URLS": ensure_openai_v1_base_url(base_url),
        "OPENAI_API_KEYS": api_key,
    }
    if include_default_model:
        env["DEFAULT_MODELS"] = default_model
    return env


def render_env_file(env: dict[str, str]) -> str:
    lines = []
    for key in sorted(env):
        value = str(env[key])
        if any(char.isspace() for char in value) or "#" in value:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            value = f'"{escaped}"'
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def write_env_file(env_path: Path, env: dict[str, str]) -> dict[str, str | None | bool]:
    selected_path = env_path.expanduser()
    if selected_path.exists():
        backup_path = selected_path.with_name(
            selected_path.name + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bak"
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected_path, backup_path)
    else:
        backup_path = None
        selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_text(render_env_file(env), encoding="utf-8")
    return {
        "ok": True,
        "env_path": str(selected_path),
        "backup_path": str(backup_path) if backup_path else None,
    }

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .opencode_harness import (
    OPENCODE_ACTIVE_MODEL,
    OPENCODE_HARNESS_PATH,
    OPENCODE_MODULE_ID,
    OpenCodeTargetStore,
    apply_opencode_config,
    helper_base_url,
    opencode_config,
    openai_models_response,
    rewrite_chat_payload,
    strip_hop_by_hop_headers,
)
from .openwebui_harness import (
    OPENWEBUI_ACTIVE_MODEL,
    OPENWEBUI_HARNESS_PATH,
    OPENWEBUI_MODULE_ID,
    OpenWebUITargetStore,
    openwebui_env,
    openwebui_helper_base_url,
    render_env_file,
    write_env_file,
)
from .static_harness import (
    HERMES_MODULE_ID,
    OPENCLAW_MODULE_ID,
    StaticHarnessTargetStore,
    render_config,
    write_config_file,
)
from .t3code_harness import (
    T3CODE_MODULE_ID,
    apply_t3code_settings,
    default_t3code_settings_path,
    t3code_config_payload,
)


HELPER_SCHEMA_VERSION = "model-plane-local-helper-v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19112
CAPSULE_MODULE_ID = "capsule_handoff"
STATUS_MODULE_ID = "status"
SUPPORTED_MODULES = {
    CAPSULE_MODULE_ID,
    OPENCODE_MODULE_ID,
    OPENWEBUI_MODULE_ID,
    T3CODE_MODULE_ID,
    HERMES_MODULE_ID,
    OPENCLAW_MODULE_ID,
}
MODULE_ENV_VAR = "MODEL_PLANE_HELPER_MODULES"


class OpenCodeTargetImportRequest(BaseModel):
    bundle: dict[str, Any]
    select: bool = False


class OpenCodeConfigApplyRequest(BaseModel):
    config_path: str
    base_url: str | None = None


class StaticHarnessTargetImportRequest(BaseModel):
    bundle: dict[str, Any]
    select: bool = False


class StaticHarnessConfigApplyRequest(BaseModel):
    config_path: str
    format: str = "yaml"


class OpenWebUIEnvApplyRequest(BaseModel):
    env_path: str
    context: str = "docker"
    base_url: str | None = None
    api_key: str = "local"
    include_default_model: bool = True


class T3CodeConfigApplyRequest(BaseModel):
    settings_path: str | None = None
    opencode_binary_path: str | None = "opencode"
    opencode_server_url: str | None = None
    opencode_server_password: str | None = None
    select_model_plane: bool = True


def parse_enabled_modules(value: str | None = None) -> set[str]:
    raw = os.environ.get(MODULE_ENV_VAR, "") if value is None else value
    requested = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return {module for module in requested if module in SUPPORTED_MODULES}


def module_status(enabled_modules: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "id": STATUS_MODULE_ID,
            "enabled": True,
            "routes": ["/helper/status", "/helper/modules"],
            "job": "local helper status and module discovery",
        },
        {
            "id": CAPSULE_MODULE_ID,
            "enabled": CAPSULE_MODULE_ID in enabled_modules,
            "routes": ["/tray/status", "/tray/import", "/tray/pending", "/tray/attach"],
            "job": "receive, verify, stage, and hand off capsule bundles",
        },
        {
            "id": OPENCODE_MODULE_ID,
            "enabled": OPENCODE_MODULE_ID in enabled_modules,
            "routes": [
                "/helper/opencode/config",
                "/helper/opencode/targets/import",
                f"{OPENCODE_HARNESS_PATH}/models",
                f"{OPENCODE_HARNESS_PATH}/chat/completions",
            ],
            "job": "stable OpenAI-compatible endpoint for OpenCode",
        },
        {
            "id": OPENWEBUI_MODULE_ID,
            "enabled": OPENWEBUI_MODULE_ID in enabled_modules,
            "routes": [
                "/helper/openwebui/config",
                "/helper/openwebui/config/apply",
                "/helper/openwebui/targets/import",
                f"{OPENWEBUI_HARNESS_PATH}/models",
                f"{OPENWEBUI_HARNESS_PATH}/chat/completions",
            ],
            "job": "stable OpenAI-compatible endpoint and env snippets for Open WebUI",
        },
        {
            "id": HERMES_MODULE_ID,
            "enabled": HERMES_MODULE_ID in enabled_modules,
            "routes": [
                "/helper/hermes/config",
                "/helper/hermes/config/apply",
                "/helper/hermes/targets/import",
            ],
            "job": "Hermes config snippet import, selection, and drop-in file export",
        },
        {
            "id": OPENCLAW_MODULE_ID,
            "enabled": OPENCLAW_MODULE_ID in enabled_modules,
            "routes": [
                "/helper/openclaw/config",
                "/helper/openclaw/config/apply",
                "/helper/openclaw/targets/import",
            ],
            "job": "OpenClaw route snippet import, selection, and drop-in file export",
        },
        {
            "id": T3CODE_MODULE_ID,
            "enabled": T3CODE_MODULE_ID in enabled_modules,
            "routes": [
                "/helper/t3code/config",
                "/helper/t3code/config/apply",
            ],
            "job": "T3 Code settings patch for its OpenCode provider",
        },
    ]


def build_core_router(enabled_modules: set[str]) -> APIRouter:
    router = APIRouter()

    @router.get("/helper/status")
    def helper_status() -> dict[str, Any]:
        return {
            "schema_version": HELPER_SCHEMA_VERSION,
            "bind_host": DEFAULT_HOST,
            "default_port": DEFAULT_PORT,
            "enabled_modules": sorted(enabled_modules),
            "module_env_var": MODULE_ENV_VAR,
            "modules": module_status(enabled_modules),
            "boundaries": [
                "binds to localhost by default",
                "does not transfer model weights",
                "does not transfer live KV tensors",
                "does not enable capsule or harness modules unless configured at startup",
            ],
        }

    @router.get("/helper/modules")
    def helper_modules() -> dict[str, Any]:
        return {"schema_version": HELPER_SCHEMA_VERSION, "modules": module_status(enabled_modules)}

    return router


def build_static_harness_router(harness: str, module_id: str) -> APIRouter:
    router = APIRouter()
    target_store = StaticHarnessTargetStore("hermes" if harness == "hermes" else "openclaw")
    prefix = f"/helper/{harness}"

    def selected_or_404() -> dict[str, Any]:
        target = target_store.selected_target()
        if target is None:
            raise HTTPException(status_code=409, detail=f"No {harness} harness target is selected.")
        return target

    @router.get(f"{prefix}/targets")
    def list_targets() -> dict[str, Any]:
        data = target_store.read()
        return {
            "schema_version": data["schema_version"],
            "selected_target_id": data.get("selected_target_id"),
            "targets": data["targets"],
        }

    @router.post(f"{prefix}/targets/import")
    def import_target(request: StaticHarnessTargetImportRequest) -> dict[str, Any]:
        try:
            target = target_store.import_bundle(request.bundle, select=request.select)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "selected": request.select, "target": target}

    @router.post(f"{prefix}/targets/{{target_id}}/select")
    def select_target(target_id: str) -> dict[str, Any]:
        try:
            target = target_store.select_target(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"{harness} target not found.") from exc
        return {"ok": True, "selected_target_id": target_id, "target": target}

    @router.get(f"{prefix}/config")
    def get_config(format: str = "yaml") -> dict[str, Any]:
        target = selected_or_404()
        config = target.get("config")
        if not isinstance(config, dict):
            raise HTTPException(status_code=409, detail=f"Selected {harness} target has no config object.")
        try:
            rendered = render_config(config, format)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "schema_version": HELPER_SCHEMA_VERSION,
            "module": module_id,
            "harness": harness,
            "target": target,
            "format": format,
            "config": config,
            "rendered": rendered,
        }

    @router.post(f"{prefix}/config/apply")
    def apply_config(request: StaticHarnessConfigApplyRequest) -> dict[str, Any]:
        target = selected_or_404()
        config = target.get("config")
        if not isinstance(config, dict):
            raise HTTPException(status_code=409, detail=f"Selected {harness} target has no config object.")
        try:
            result = write_config_file(Path(request.config_path), config, request.format)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"schema_version": HELPER_SCHEMA_VERSION, "module": module_id, "harness": harness, **result}

    return router


def build_t3code_router() -> APIRouter:
    router = APIRouter()

    @router.get("/helper/t3code/config")
    def get_t3code_config(
        settings_path: str | None = None,
        opencode_binary_path: str | None = "opencode",
        opencode_server_url: str | None = None,
        select_model_plane: bool = True,
    ) -> dict[str, Any]:
        selected_path = Path(settings_path).expanduser() if settings_path else default_t3code_settings_path()
        return {
            "schema_version": HELPER_SCHEMA_VERSION,
            "module": T3CODE_MODULE_ID,
            **t3code_config_payload(
                settings_path=selected_path,
                opencode_binary_path=opencode_binary_path,
                opencode_server_url=opencode_server_url,
                select_model_plane=select_model_plane,
            ),
        }

    @router.post("/helper/t3code/config/apply")
    def apply_config(request: T3CodeConfigApplyRequest) -> dict[str, Any]:
        settings_path = Path(request.settings_path).expanduser() if request.settings_path else None
        try:
            result = apply_t3code_settings(
                settings_path,
                opencode_binary_path=request.opencode_binary_path,
                opencode_server_url=request.opencode_server_url,
                opencode_server_password=request.opencode_server_password,
                select_model_plane=request.select_model_plane,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"schema_version": HELPER_SCHEMA_VERSION, **result}

    return router


def build_openwebui_router(store: OpenWebUITargetStore | None = None) -> APIRouter:
    router = APIRouter()
    target_store = store or OpenWebUITargetStore()

    def selected_or_404() -> dict[str, Any]:
        target = target_store.selected_target()
        if target is None:
            raise HTTPException(status_code=409, detail="No Open WebUI harness target is selected.")
        return target

    def base_url_for_context(context: str, base_url: str | None = None) -> str:
        if base_url:
            return base_url
        return openwebui_helper_base_url(DEFAULT_HOST, DEFAULT_PORT, docker=context.lower().strip() == "docker")

    @router.get("/helper/openwebui/config")
    def get_openwebui_config(
        context: str = "docker",
        base_url: str | None = None,
        api_key: str = "local",
        include_default_model: bool = True,
    ) -> dict[str, Any]:
        selected_base_url = base_url_for_context(context, base_url)
        env = openwebui_env(selected_base_url, api_key=api_key, include_default_model=include_default_model)
        return {
            "schema_version": HELPER_SCHEMA_VERSION,
            "module": OPENWEBUI_MODULE_ID,
            "model": OPENWEBUI_ACTIVE_MODEL,
            "context": context,
            "base_url": selected_base_url,
            "local_base_url": openwebui_helper_base_url(DEFAULT_HOST, DEFAULT_PORT),
            "docker_base_url": openwebui_helper_base_url(DEFAULT_HOST, DEFAULT_PORT, docker=True),
            "env": env,
            "env_file": render_env_file(env),
            "docker_compose": {"environment": env},
            "notes": [
                "Use docker context when Open WebUI runs in Docker on the same machine.",
                "Open WebUI PersistentConfig values may override env vars after first launch; update Admin Settings or reset persistent config if needed.",
            ],
        }

    @router.post("/helper/openwebui/config/apply")
    def apply_openwebui_env_file(request: OpenWebUIEnvApplyRequest) -> dict[str, Any]:
        selected_base_url = base_url_for_context(request.context, request.base_url)
        env = openwebui_env(
            selected_base_url,
            api_key=request.api_key,
            include_default_model=request.include_default_model,
        )
        try:
            result = write_env_file(Path(request.env_path), env)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "schema_version": HELPER_SCHEMA_VERSION,
            "module": OPENWEBUI_MODULE_ID,
            "context": request.context,
            "base_url": selected_base_url,
            "env": env,
            **result,
        }

    @router.get("/helper/openwebui/targets")
    def list_targets() -> dict[str, Any]:
        data = target_store.read()
        return {
            "schema_version": data["schema_version"],
            "selected_target_id": data.get("selected_target_id"),
            "targets": data["targets"],
        }

    @router.post("/helper/openwebui/targets/import")
    def import_target(request: OpenCodeTargetImportRequest) -> dict[str, Any]:
        try:
            target = target_store.import_bundle(request.bundle, select=request.select)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "selected": request.select, "target": target}

    @router.post("/helper/openwebui/targets/{target_id}/select")
    def select_target(target_id: str) -> dict[str, Any]:
        try:
            target = target_store.select_target(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Open WebUI target not found.") from exc
        return {"ok": True, "selected_target_id": target_id, "target": target}

    @router.get(f"{OPENWEBUI_HARNESS_PATH}/models")
    def models() -> dict[str, Any]:
        return openai_models_response(target_store.selected_target())

    @router.post(f"{OPENWEBUI_HARNESS_PATH}/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> Any:
        target = selected_or_404()
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Chat completion request must be JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Chat completion request must be a JSON object.")
        upstream_payload = rewrite_chat_payload(payload, target)
        upstream_url = str(target["base_url"]).rstrip("/") + "/chat/completions"
        headers = strip_hop_by_hop_headers({key: value for key, value in request.headers.items()})
        headers["content-type"] = "application/json"
        try:
            if upstream_payload.get("stream") is True:
                client = httpx.AsyncClient(timeout=None)
                upstream = client.stream("POST", upstream_url, json=upstream_payload, headers=headers)
                response = await upstream.__aenter__()

                async def stream_body():
                    try:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                    finally:
                        await upstream.__aexit__(None, None, None)
                        await client.aclose()

                return StreamingResponse(
                    stream_body(),
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type", "text/event-stream"),
                    headers=strip_hop_by_hop_headers(dict(response.headers)),
                )
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(upstream_url, json=upstream_payload, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Open WebUI target request failed: {exc}") from exc
        return JSONResponse(
            content=response.json() if response.content else {},
            status_code=response.status_code,
            headers=strip_hop_by_hop_headers(dict(response.headers)),
        )

    return router


def build_opencode_router(store: OpenCodeTargetStore | None = None) -> APIRouter:
    router = APIRouter()
    target_store = store or OpenCodeTargetStore()

    def selected_or_404() -> dict[str, Any]:
        target = target_store.selected_target()
        if target is None:
            raise HTTPException(status_code=409, detail="No OpenCode harness target is selected.")
        return target

    @router.get("/helper/opencode/config")
    def get_opencode_config(base_url: str | None = None) -> dict[str, Any]:
        selected_base_url = base_url or helper_base_url(DEFAULT_HOST, DEFAULT_PORT)
        return {
            "schema_version": HELPER_SCHEMA_VERSION,
            "provider_id": "model-plane",
            "model": OPENCODE_ACTIVE_MODEL,
            "base_url": selected_base_url,
            "config": opencode_config(selected_base_url),
        }

    @router.post("/helper/opencode/config/apply")
    def apply_config(request: OpenCodeConfigApplyRequest) -> dict[str, Any]:
        selected_base_url = request.base_url or helper_base_url(DEFAULT_HOST, DEFAULT_PORT)
        try:
            return apply_opencode_config(Path(request.config_path), selected_base_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/helper/opencode/targets")
    def list_targets() -> dict[str, Any]:
        data = target_store.read()
        return {
            "schema_version": data["schema_version"],
            "selected_target_id": data.get("selected_target_id"),
            "targets": data["targets"],
        }

    @router.post("/helper/opencode/targets/import")
    def import_target(request: OpenCodeTargetImportRequest) -> dict[str, Any]:
        try:
            target = target_store.import_bundle(request.bundle, select=request.select)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "selected": request.select, "target": target}

    @router.post("/helper/opencode/targets/{target_id}/select")
    def select_target(target_id: str) -> dict[str, Any]:
        try:
            target = target_store.select_target(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="OpenCode target not found.") from exc
        return {"ok": True, "selected_target_id": target_id, "target": target}

    @router.get(f"{OPENCODE_HARNESS_PATH}/models")
    def models() -> dict[str, Any]:
        return openai_models_response(target_store.selected_target())

    @router.post(f"{OPENCODE_HARNESS_PATH}/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> Any:
        target = selected_or_404()
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Chat completion request must be JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Chat completion request must be a JSON object.")
        upstream_payload = rewrite_chat_payload(payload, target)
        upstream_url = str(target["base_url"]).rstrip("/") + "/chat/completions"
        headers = strip_hop_by_hop_headers({key: value for key, value in request.headers.items()})
        headers["content-type"] = "application/json"
        try:
            if upstream_payload.get("stream") is True:
                client = httpx.AsyncClient(timeout=None)
                upstream = client.stream("POST", upstream_url, json=upstream_payload, headers=headers)
                response = await upstream.__aenter__()

                async def stream_body():
                    try:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                    finally:
                        await upstream.__aexit__(None, None, None)
                        await client.aclose()

                return StreamingResponse(
                    stream_body(),
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type", "text/event-stream"),
                    headers=strip_hop_by_hop_headers(dict(response.headers)),
                )
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(upstream_url, json=upstream_payload, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"OpenCode target request failed: {exc}") from exc
        return JSONResponse(
            content=response.json() if response.content else {},
            status_code=response.status_code,
            headers=strip_hop_by_hop_headers(dict(response.headers)),
        )

    return router


def create_app(enabled_modules: set[str] | None = None) -> FastAPI:
    selected_modules = parse_enabled_modules() if enabled_modules is None else set(enabled_modules)
    app = FastAPI(title="Model Plane Local Helper", version="0.1.0")
    app.include_router(build_core_router(selected_modules))
    if CAPSULE_MODULE_ID in selected_modules:
        from capsule_handoff_tray.app import router as capsule_router

        app.include_router(capsule_router)
    if OPENCODE_MODULE_ID in selected_modules:
        app.include_router(build_opencode_router())
    if OPENWEBUI_MODULE_ID in selected_modules:
        app.include_router(build_openwebui_router())
    if HERMES_MODULE_ID in selected_modules:
        app.include_router(build_static_harness_router("hermes", HERMES_MODULE_ID))
    if OPENCLAW_MODULE_ID in selected_modules:
        app.include_router(build_static_harness_router("openclaw", OPENCLAW_MODULE_ID))
    if T3CODE_MODULE_ID in selected_modules:
        app.include_router(build_t3code_router())
    return app


app = create_app()

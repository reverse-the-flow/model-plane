from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from model_plane_helper.app import CAPSULE_MODULE_ID, OPENCODE_MODULE_ID, build_opencode_router, create_app
from model_plane_helper.opencode_harness import (
    OPENCODE_ACTIVE_MODEL,
    OPENCODE_PROVIDER_ID,
    OpenCodeTargetStore,
    apply_opencode_config,
    opencode_config,
)
from model_plane_helper.openwebui_harness import OPENWEBUI_ACTIVE_MODEL, OPENWEBUI_MODULE_ID, OpenWebUITargetStore
from model_plane_helper.static_harness import HERMES_MODULE_ID, OPENCLAW_MODULE_ID
from model_plane_helper.t3code_harness import (
    T3CODE_MODEL_PLANE_MODEL,
    T3CODE_MODULE_ID,
    apply_t3code_settings,
    merge_t3code_settings,
    t3code_settings_patch,
)


def integration_bundle() -> dict:
    return {
        "schema_version": "model-plane-harness-integration-bundle-v1",
        "run_id": "run-llama-local-1",
        "profile_id": "llama-local",
        "model_id": "local/example",
        "provider_kind": "openai_compatible",
        "preferred_base_url": "http://127.0.0.1:8765/v1",
        "alias": "llama-local.local",
        "display_name": "Local llama.cpp",
        "latest_health_result": {"ok": True, "status": 200},
        "config_snippets": {
            "hermes": {
                "json": {
                    "provider": "openai",
                    "model": "llama-local.local",
                    "base_url": "http://127.0.0.1:8765/v1",
                    "api_key": "local",
                },
            },
            "openclaw": {
                "json": {
                    "alias": "llama-local.local",
                    "base_url": "http://127.0.0.1:8765/v1",
                    "tags": ["backend:llama_cpp", "profile:llama-local"],
                },
            },
        },
    }


def opencode_app(store: OpenCodeTargetStore) -> FastAPI:
    app = FastAPI()
    app.include_router(build_opencode_router(store))
    return app


def openwebui_app(store: OpenWebUITargetStore) -> FastAPI:
    from model_plane_helper.app import build_openwebui_router

    app = FastAPI()
    app.include_router(build_openwebui_router(store))
    return app


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.status_code = 200
        self.headers = {"content-type": "application/json", "connection": "close"}
        self.content = json.dumps(payload).encode("utf-8")

    def json(self) -> dict:
        return self.payload


class FakeAsyncClient:
    calls: list[dict] = []

    def __init__(self, timeout=None) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, json: dict, headers: dict) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse({"ok": True, "upstream_model": json.get("model"), "url": url})


class ModelPlaneHelperTests(unittest.TestCase):
    def test_helper_defaults_to_status_only(self) -> None:
        client = TestClient(create_app(set()))

        status = client.get("/helper/status")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["enabled_modules"], [])
        self.assertEqual(client.get("/tray/status").status_code, 404)
        self.assertEqual(client.get("/harness/opencode/v1/models").status_code, 404)
        self.assertEqual(client.get("/helper/openwebui/config").status_code, 404)
        self.assertEqual(client.get("/harness/openwebui/v1/models").status_code, 404)
        self.assertEqual(client.get("/helper/hermes/config").status_code, 404)
        self.assertEqual(client.get("/helper/openclaw/config").status_code, 404)
        self.assertEqual(client.get("/helper/t3code/config").status_code, 404)

    def test_capsule_module_is_optional_under_helper_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("CAPSULE_HANDOFF_TRAY_STATE_DIR")
            os.environ["CAPSULE_HANDOFF_TRAY_STATE_DIR"] = temp_dir
            try:
                client = TestClient(create_app({CAPSULE_MODULE_ID}))
                response = client.get("/tray/status")
            finally:
                if previous is None:
                    os.environ.pop("CAPSULE_HANDOFF_TRAY_STATE_DIR", None)
                else:
                    os.environ["CAPSULE_HANDOFF_TRAY_STATE_DIR"] = previous

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["bind_host"], "127.0.0.1")
        self.assertIn("receive, verify, stage", response.json()["job"])

    def test_opencode_config_uses_stable_provider_and_active_model(self) -> None:
        config = opencode_config("http://127.0.0.1:19112/harness/opencode/v1")

        provider = config["provider"][OPENCODE_PROVIDER_ID]
        self.assertEqual(provider["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(provider["options"]["baseURL"], "http://127.0.0.1:19112/harness/opencode/v1")
        self.assertEqual(provider["models"], {OPENCODE_ACTIVE_MODEL: {"name": "Model Plane Active"}})
        self.assertNotIn("apiKey", provider["options"])

    def test_opencode_target_import_select_and_models_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OpenCodeTargetStore(Path(temp_dir))
            client = TestClient(opencode_app(store))

            imported = client.post("/helper/opencode/targets/import", json={"bundle": integration_bundle(), "select": True})
            models = client.get("/harness/opencode/v1/models")

        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["target"]["target_id"], "run-llama-local-1")
        self.assertTrue(imported.json()["selected"])
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["data"][0]["id"], OPENCODE_ACTIVE_MODEL)
        self.assertEqual(models.json()["data"][0]["target"]["model_id"], "local/example")

    def test_opencode_config_apply_preserves_existing_providers_and_writes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "opencode.json"
            config_path.write_text(
                json.dumps({"provider": {"ollama": {"name": "Ollama", "models": {"llama3": {"name": "Llama 3"}}}}}),
                encoding="utf-8",
            )

            result = apply_opencode_config(config_path, "http://127.0.0.1:19112/harness/opencode/v1")
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            backups = list(Path(temp_dir).glob("opencode.json.*.bak"))

        self.assertTrue(result["ok"])
        self.assertEqual(len(backups), 1)
        self.assertIn("ollama", updated["provider"])
        self.assertIn(OPENCODE_PROVIDER_ID, updated["provider"])
        self.assertEqual(updated["provider"][OPENCODE_PROVIDER_ID]["models"][OPENCODE_ACTIVE_MODEL]["name"], "Model Plane Active")

    def test_opencode_chat_proxy_requires_selected_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OpenCodeTargetStore(Path(temp_dir))
            client = TestClient(opencode_app(store))

            response = client.post("/harness/opencode/v1/chat/completions", json={"model": OPENCODE_ACTIVE_MODEL})

        self.assertEqual(response.status_code, 409)
        self.assertIn("No OpenCode harness target", response.json()["detail"])

    def test_opencode_chat_proxy_rewrites_active_model_to_selected_target(self) -> None:
        FakeAsyncClient.calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OpenCodeTargetStore(Path(temp_dir))
            store.import_bundle(integration_bundle(), select=True)
            client = TestClient(opencode_app(store))

            with mock.patch("model_plane_helper.app.httpx.AsyncClient", FakeAsyncClient):
                response = client.post(
                    "/harness/opencode/v1/chat/completions",
                    json={"model": OPENCODE_ACTIVE_MODEL, "messages": [{"role": "user", "content": "hi"}]},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["upstream_model"], "local/example")
        self.assertEqual(FakeAsyncClient.calls[0]["url"], "http://127.0.0.1:8765/v1/chat/completions")

    def test_openwebui_config_defaults_to_docker_host_env(self) -> None:
        client = TestClient(create_app({OPENWEBUI_MODULE_ID}))

        response = client.get("/helper/openwebui/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["model"], OPENWEBUI_ACTIVE_MODEL)
        self.assertEqual(payload["env"]["ENABLE_OPENAI_API"], "True")
        self.assertEqual(payload["env"]["OPENAI_API_BASE_URLS"], "http://host.docker.internal:19112/harness/openwebui/v1")
        self.assertEqual(payload["env"]["OPENAI_API_KEYS"], "local")
        self.assertEqual(payload["env"]["DEFAULT_MODELS"], OPENWEBUI_ACTIVE_MODEL)
        self.assertIn("OPENAI_API_BASE_URLS=http://host.docker.internal:19112/harness/openwebui/v1", payload["env_file"])

    def test_openwebui_target_import_models_and_chat_proxy(self) -> None:
        FakeAsyncClient.calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OpenWebUITargetStore(Path(temp_dir))
            client = TestClient(openwebui_app(store))

            imported = client.post("/helper/openwebui/targets/import", json={"bundle": integration_bundle(), "select": True})
            models = client.get("/harness/openwebui/v1/models")
            with mock.patch("model_plane_helper.app.httpx.AsyncClient", FakeAsyncClient):
                response = client.post(
                    "/harness/openwebui/v1/chat/completions",
                    json={"model": OPENWEBUI_ACTIVE_MODEL, "messages": [{"role": "user", "content": "hi"}]},
                )

        self.assertEqual(imported.status_code, 200)
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["data"][0]["id"], OPENWEBUI_ACTIVE_MODEL)
        self.assertEqual(models.json()["data"][0]["target"]["model_id"], "local/example")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["upstream_model"], "local/example")
        self.assertEqual(FakeAsyncClient.calls[0]["url"], "http://127.0.0.1:8765/v1/chat/completions")

    def test_openwebui_env_apply_writes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "model-plane-openwebui.env"
            env_path.write_text("OLD=true\n", encoding="utf-8")
            client = TestClient(create_app({OPENWEBUI_MODULE_ID}))

            result = client.post(
                "/helper/openwebui/config/apply",
                json={"env_path": str(env_path), "context": "local", "include_default_model": True},
            )
            env_text = env_path.read_text(encoding="utf-8")
            backups = list(Path(temp_dir).glob("model-plane-openwebui.env.*.bak"))

        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(backups), 1)
        self.assertIn("ENABLE_OPENAI_API=True", env_text)
        self.assertIn("OPENAI_API_BASE_URLS=http://127.0.0.1:19112/harness/openwebui/v1", env_text)
        self.assertIn(f"DEFAULT_MODELS={OPENWEBUI_ACTIVE_MODEL}", env_text)

    def test_t3code_config_patch_targets_opencode_provider(self) -> None:
        client = TestClient(create_app({T3CODE_MODULE_ID}))

        response = client.get("/helper/t3code/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "opencode")
        self.assertEqual(payload["model"], T3CODE_MODEL_PLANE_MODEL)
        self.assertEqual(payload["settings_patch"]["providers"]["opencode"]["customModels"], [T3CODE_MODEL_PLANE_MODEL])
        self.assertEqual(payload["settings_patch"]["textGenerationModelSelection"]["instanceId"], "opencode")

    def test_t3code_merge_preserves_existing_settings_and_models(self) -> None:
        existing = {
            "providers": {
                "opencode": {
                    "enabled": True,
                    "binaryPath": "C:/tools/opencode.cmd",
                    "customModels": ["openai/gpt-5"],
                },
                "codex": {"enabled": False},
            },
            "defaultThreadEnvMode": "local",
        }
        patch = t3code_settings_patch(opencode_binary_path="opencode", select_model_plane=True)

        merged = merge_t3code_settings(existing, patch)

        self.assertEqual(merged["defaultThreadEnvMode"], "local")
        self.assertEqual(merged["providers"]["codex"], {"enabled": False})
        self.assertEqual(merged["providers"]["opencode"]["customModels"], ["openai/gpt-5", T3CODE_MODEL_PLANE_MODEL])
        self.assertEqual(merged["textGenerationModelSelection"]["model"], T3CODE_MODEL_PLANE_MODEL)

    def test_t3code_apply_writes_backup_for_existing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps({"providers": {"opencode": {"customModels": ["openai/gpt-5"]}}}), encoding="utf-8")

            result = apply_t3code_settings(settings_path, opencode_server_url="http://127.0.0.1:4096")
            updated = json.loads(settings_path.read_text(encoding="utf-8"))
            backups = list(Path(temp_dir).glob("settings.json.*.bak"))

        self.assertTrue(result["ok"])
        self.assertEqual(len(backups), 1)
        self.assertEqual(updated["providers"]["opencode"]["serverUrl"], "http://127.0.0.1:4096")
        self.assertIn(T3CODE_MODEL_PLANE_MODEL, updated["providers"]["opencode"]["customModels"])

    def test_hermes_target_import_select_and_render_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("MODEL_PLANE_HELPER_STATE_DIR")
            os.environ["MODEL_PLANE_HELPER_STATE_DIR"] = temp_dir
            try:
                client = TestClient(create_app({HERMES_MODULE_ID}))

                imported = client.post("/helper/hermes/targets/import", json={"bundle": integration_bundle(), "select": True})
                config = client.get("/helper/hermes/config?format=json")
            finally:
                if previous is None:
                    os.environ.pop("MODEL_PLANE_HELPER_STATE_DIR", None)
                else:
                    os.environ["MODEL_PLANE_HELPER_STATE_DIR"] = previous

        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["target"]["target_id"], "run-llama-local-1")
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["config"]["provider"], "openai")
        self.assertEqual(config.json()["config"]["model"], "llama-local.local")
        self.assertIn('"base_url": "http://127.0.0.1:8765/v1"', config.json()["rendered"])

    def test_openclaw_target_apply_writes_backup_and_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("MODEL_PLANE_HELPER_STATE_DIR")
            os.environ["MODEL_PLANE_HELPER_STATE_DIR"] = temp_dir
            config_path = Path(temp_dir) / "openclaw-route.yaml"
            config_path.write_text("old: true\n", encoding="utf-8")
            try:
                client = TestClient(create_app({OPENCLAW_MODULE_ID}))

                imported = client.post("/helper/openclaw/targets/import", json={"bundle": integration_bundle(), "select": True})
                applied = client.post(
                    "/helper/openclaw/config/apply",
                    json={"config_path": str(config_path), "format": "yaml"},
                )
                updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                backups = list(Path(temp_dir).glob("openclaw-route.yaml.*.bak"))
            finally:
                if previous is None:
                    os.environ.pop("MODEL_PLANE_HELPER_STATE_DIR", None)
                else:
                    os.environ["MODEL_PLANE_HELPER_STATE_DIR"] = previous

        self.assertEqual(imported.status_code, 200)
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(len(backups), 1)
        self.assertEqual(updated["alias"], "llama-local.local")
        self.assertEqual(updated["base_url"], "http://127.0.0.1:8765/v1")
        self.assertIn("backend:llama_cpp", updated["tags"])


if __name__ == "__main__":
    unittest.main()

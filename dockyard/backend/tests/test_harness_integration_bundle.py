from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from model_control_plane import app as app_module
from model_control_plane import run_state


def llama_profile() -> dict:
    return {
        "id": "llama-local",
        "name": "Local llama.cpp",
        "model": {"id": "local/example", "local_path": "/models/example.gguf", "quant": "gguf"},
        "runtime": {"backend": "llama_cpp", "image": "example:1", "args": []},
        "network": {"mode": "local_only"},
        "container": {
            "name": "dockyard-llama-local",
            "host_port": 18080,
            "internal_port": 8080,
            "gpu": "all",
            "env": {},
            "volumes": [],
        },
        "health": {"url": "http://127.0.0.1:18080/health"},
    }


def capsule_profile() -> dict:
    return {
        "id": "session-capsule-local-llama",
        "name": "Session Capsule Gateway - local llama.cpp",
        "profile_type": "capsule_gateway",
        "runtime": {"backend": "capsule_gateway"},
        "network": {"mode": "local_only"},
        "endpoint": {
            "id": "local-llama-cpp",
            "kind": "openai_compatible",
            "runtime_profile_id": "llama-local",
            "base_url": "http://127.0.0.1:18080/v1",
        },
        "capsule_gateway": {
            "repo_path": "C:/session-capsule",
            "state_dir": "C:/session-capsule-state",
            "endpoint_id": "local-llama-cpp",
            "host": "127.0.0.1",
            "port": 8765,
            "checkpoint_mode": "soft",
            "slot": 0,
            "default_prefill": "user_default",
            "healthcheck_url": "http://127.0.0.1:8765/api/capsules/status",
            "client_base_url": "http://127.0.0.1:8765/v1",
            "fallback_replay": True,
        },
        "health": {"url": "http://127.0.0.1:8765/api/capsules/status"},
    }


class FakeHealthResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> "FakeHealthResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def selective_urlopen(url: str, timeout: int = 5) -> FakeHealthResponse:
    if "/chat/" in url or "/chat/completions" in url:
        raise AssertionError("Connectivity checks must not send prompt traffic.")
    if "host.docker.internal" in url:
        raise OSError("docker harness refused")
    return FakeHealthResponse(200)


class HarnessIntegrationBundleTests(unittest.TestCase):
    def test_profile_integration_preview_exports_minimal_harness_configs(self) -> None:
        with mock.patch.object(app_module, "load_profile", return_value=llama_profile()):
            bundle = app_module.export_profile_integration_preview("llama-local")

        self.assertEqual(bundle["schema_version"], "model-plane-harness-integration-bundle-v1")
        self.assertIsNone(bundle["run_id"])
        self.assertEqual(bundle["profile_id"], "llama-local")
        self.assertEqual(bundle["alias"], "llama-local.local")
        self.assertEqual(bundle["base_url"], "http://127.0.0.1:18080/v1")
        self.assertEqual(bundle["connectivity_targets"]["host"], "http://127.0.0.1:18080/v1/models")
        self.assertEqual(bundle["connectivity_targets"]["docker_harness"], "http://host.docker.internal:18080/v1/models")
        self.assertEqual(bundle["config_snippets"]["hermes"]["json"]["provider"], "openai")
        self.assertEqual(bundle["config_snippets"]["hermes"]["json"]["model"], "llama-local.local")
        self.assertEqual(bundle["config_snippets"]["hermes"]["json"]["api_key"], "local")
        self.assertEqual(bundle["config_snippets"]["openclaw"]["json"]["alias"], "llama-local.local")
        self.assertEqual(bundle["config_snippets"]["openclaw"]["json"]["base_url"], "http://127.0.0.1:18080/v1")
        self.assertIn("backend:llama_cpp", bundle["config_snippets"]["openclaw"]["json"]["tags"])

    def test_run_integration_bundle_uses_run_health_and_endpoint_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                selected = run_state.create_run(llama_profile(), ["docker", "run", "example"])
                run_state.record_launch_result(selected["run_id"], 0, "container-id", "")
                run_state.record_health_result(selected["run_id"], {"ok": True, "status": 200})
                with mock.patch.object(app_module, "load_profile", return_value=llama_profile()):
                    bundle = app_module.export_run_integration_bundle(selected["run_id"])

        self.assertEqual(bundle["run_id"], selected["run_id"])
        self.assertEqual(bundle["latest_health_result"]["status"], 200)
        self.assertEqual(bundle["preferred_endpoint"]["source"], "run")
        self.assertEqual(bundle["preferred_base_url"], "http://127.0.0.1:18080/v1")

    def test_run_integration_bundle_prefers_healthy_linked_capsule_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                base = run_state.create_run(llama_profile(), ["docker", "run", "example"])
                run_state.record_health_result(base["run_id"], {"ok": True, "status": 200})
                capsule = run_state.create_run(capsule_profile(), ["py", "-3", "capsule_gateway.py"])
                run_state.record_process_launch_result(capsule["run_id"], 4321, "started", None)
                run_state.record_health_result(capsule["run_id"], {"ok": True, "status": 200})
                profiles = [llama_profile(), capsule_profile()]

                with (
                    mock.patch.object(app_module, "load_profile", return_value=llama_profile()),
                    mock.patch.object(app_module, "all_profiles", return_value=profiles),
                ):
                    bundle = app_module.export_run_integration_bundle(base["run_id"])

        self.assertEqual(bundle["preferred_endpoint"]["source"], "session_capsule_gateway")
        self.assertEqual(bundle["preferred_endpoint"]["run_id"], capsule["run_id"])
        self.assertEqual(bundle["base_url"], "http://127.0.0.1:8765/v1")
        self.assertEqual(bundle["raw_runtime_base_url"], "http://127.0.0.1:18080/v1")
        self.assertEqual(bundle["config_snippets"]["hermes"]["json"]["base_url"], "http://127.0.0.1:8765/v1")

    def test_connectivity_check_hits_only_models_endpoints_and_keeps_partial_success_usable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                selected = run_state.create_run(llama_profile(), ["docker", "run", "example"])
                run_state.record_health_result(selected["run_id"], {"ok": True, "status": 200})

                with (
                    mock.patch.object(app_module, "load_profile", return_value=llama_profile()),
                    mock.patch.object(app_module.urllib.request, "urlopen", side_effect=AssertionError("wrong urlopen")),
                    mock.patch("model_control_plane.harness_integrations.urllib.request.urlopen", side_effect=selective_urlopen) as urlopen,
                ):
                    bundle = app_module.check_run_integration_bundle(selected["run_id"])

        called_urls = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(called_urls, [
            "http://127.0.0.1:18080/v1/models",
            "http://host.docker.internal:18080/v1/models",
        ])
        checks = {check["context"]: check for check in bundle["connectivity_checks"]}
        self.assertTrue(checks["host"]["ok"])
        self.assertFalse(checks["docker_harness"]["ok"])
        self.assertTrue(bundle["connectivity_summary"]["ok"])
        self.assertEqual(bundle["connectivity_summary"]["message"], "Host reachable, Docker harness unreachable.")


if __name__ == "__main__":
    unittest.main()

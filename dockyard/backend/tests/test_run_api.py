from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from model_control_plane import app as app_module
from model_control_plane import run_state


def profile_fixture() -> dict:
    return {
        "id": "llama-local",
        "name": "Local llama.cpp",
        "model": {"id": "local/example", "local_path": "/models/example.gguf"},
        "runtime": {"backend": "llama_cpp", "image": "example:1", "args": []},
        "container": {
            "name": "dockyard-llama-local",
            "host_port": 18080,
            "internal_port": 8080,
            "gpu": "all",
            "env": {},
            "volumes": [],
        },
        "health": {"url": "http://127.0.0.1:18080/health"},
        "moe_probe": {"primary_probe_hint": "runtime_baseline"},
    }


class FakeHealthResponse:
    status = 204

    def __enter__(self) -> "FakeHealthResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class RunApiTests(unittest.TestCase):
    def test_backend_routes_include_run_surface(self) -> None:
        routes = {route.path for route in app_module.app.routes}

        self.assertIn("/runs", routes)
        self.assertIn("/runs/{run_id}", routes)
        self.assertIn("/runs/{run_id}/health", routes)
        self.assertIn("/runs/{run_id}/moe-probe-manifest", routes)

    def test_launch_records_failed_docker_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            completed = subprocess.CompletedProcess(
                args=["docker"],
                returncode=125,
                stdout="created nothing",
                stderr="docker failed",
            )

            with (
                mock.patch.object(run_state, "RUNS_PATH", runs_path),
                mock.patch.object(app_module, "load_profile", return_value=profile_fixture()),
                mock.patch.object(app_module.subprocess, "run", return_value=completed),
            ):
                response = app_module.launch("llama-local")

            self.assertFalse(response["ok"])
            self.assertEqual(response["returncode"], 125)
            self.assertEqual(response["stderr"], "docker failed")
            self.assertEqual(response["run"]["status"], "launch_failed")
            self.assertEqual(response["run"]["launch_returncode"], 125)
            self.assertEqual(run_state.get_run(response["run_id"], runs_path)["launch_stderr"], "docker failed")

    def test_run_health_endpoint_records_latest_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                run = run_state.create_run(profile_fixture(), ["docker", "run", "example"])

                with mock.patch.object(app_module.urllib.request, "urlopen", return_value=FakeHealthResponse()):
                    result = app_module.check_run_health(run["run_id"])

                persisted = run_state.get_run(run["run_id"], runs_path)

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], 204)
            self.assertEqual(persisted["status"], "healthy")
            self.assertEqual(persisted["last_health_result"]["status"], 204)

    def test_run_scoped_manifest_includes_concrete_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                run = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_launch_result(run["run_id"], 0, "container-id", "")
                run_state.record_health_result(run["run_id"], {"ok": True, "status": 200})

                with mock.patch.object(app_module, "load_profile", return_value=profile_fixture()):
                    manifest = app_module.export_run_moe_probe_manifest(run["run_id"])

            self.assertEqual(manifest["run_id"], run["run_id"])
            self.assertEqual(manifest["profile_id"], "llama-local")
            self.assertEqual(manifest["model_id"], "local/example")
            self.assertEqual(manifest["backend_family"], "llama_cpp")
            self.assertEqual(manifest["base_url"], "http://127.0.0.1:18080")
            self.assertEqual(manifest["health_url"], "http://127.0.0.1:18080/health")
            self.assertEqual(manifest["container_name"], "dockyard-llama-local")
            self.assertEqual(manifest["latest_health_result"]["status"], 200)
            self.assertEqual(manifest["primary_probe_hint"], "runtime_baseline")
            self.assertEqual(manifest["semantic_expert_ids_status"], "not_exposed")
            self.assertIn("safety_notes", manifest)


if __name__ == "__main__":
    unittest.main()

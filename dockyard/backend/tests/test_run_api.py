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
        "logs": {
            "file_path": "/mnt/Calliope/logs/model-plane/llama-cpp/llama-local.log",
            "container_path": "/logs/llama-local.log",
        },
        "moe_probe": {
            "primary_probe_hint": "runtime_baseline",
            "semantic_expert_ids": "not_exposed",
            "observability_paths": ["/metrics", "/slots", "/props", "/perf"],
            "readiness_paths": ["/health"],
        },
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

        self.assertIn("/cleanup/plan", routes)
        self.assertIn("/runs", routes)
        self.assertIn("/runs/{run_id}", routes)
        self.assertIn("/runs/{run_id}/health", routes)
        self.assertIn("/runs/{run_id}/moe-probe-manifest", routes)
        self.assertIn("/runs/{run_id}/cleanup", routes)

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
            self.assertEqual(manifest["observability_paths"], ["/metrics", "/slots", "/props", "/perf"])
            self.assertEqual(manifest["runtime_observability"]["readiness_paths"], ["/health"])
            self.assertEqual(manifest["runtime_observability"]["log_file_path"], "/mnt/Calliope/logs/model-plane/llama-cpp/llama-local.log")
            self.assertEqual(manifest["log_paths"]["host_log_file_path"], "/mnt/Calliope/logs/model-plane/llama-cpp/llama-local.log")
            self.assertIn("safety_notes", manifest)

    def test_cleanup_plan_returns_failed_and_unhealthy_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                failed = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_launch_result(failed["run_id"], 125, "", "docker failed")
                unhealthy = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_health_result(unhealthy["run_id"], {"ok": False, "error": "refused"})
                healthy = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_health_result(healthy["run_id"], {"ok": True, "status": 200})

                plan = app_module.cleanup_plan(run_id=None)

            candidates = {candidate["run_id"]: candidate for candidate in plan["candidates"]}
            self.assertEqual(plan["schema_version"], "model-plane-cleanup-plan-v1")
            self.assertIn(failed["run_id"], candidates)
            self.assertIn(unhealthy["run_id"], candidates)
            self.assertNotIn(healthy["run_id"], candidates)
            self.assertEqual(candidates[failed["run_id"]]["profile_id"], "llama-local")
            self.assertEqual(candidates[failed["run_id"]]["container_name"], "dockyard-llama-local")
            self.assertEqual(candidates[failed["run_id"]]["health_url"], "http://127.0.0.1:18080/health")
            self.assertIn("log_path", candidates[failed["run_id"]])
            self.assertIn("launch_failed", candidates[failed["run_id"]]["candidate_reasons"])

    def test_cleanup_plan_has_no_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                failed = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_launch_result(failed["run_id"], 125, "", "docker failed")
                before = runs_path.read_text(encoding="utf-8")

                with mock.patch.object(run_state, "write_store") as write_store:
                    plan = app_module.cleanup_plan(run_id=None)

                after = runs_path.read_text(encoding="utf-8")

            self.assertEqual(plan["candidate_count"], 1)
            write_store.assert_not_called()
            self.assertEqual(after, before)

    def test_cleanup_endpoint_records_review_only_cleanup_without_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                selected = run_state.create_run(profile_fixture(), ["docker", "run", "example"])

                with mock.patch.object(app_module.subprocess, "run") as docker_run:
                    response = app_module.cleanup_run(
                        selected["run_id"],
                        app_module.CleanupRequest(notes="reviewed stale launch"),
                    )

                persisted = run_state.get_run(selected["run_id"], runs_path)

            docker_run.assert_not_called()
            self.assertEqual(response["cleanup"]["action"], "reviewed")
            self.assertFalse(response["cleanup"]["docker_called"])
            self.assertEqual(persisted["cleanup_status"], "reviewed")
            self.assertEqual(persisted["last_cleanup_result"]["notes"], "reviewed stale launch")

    def test_cleanup_endpoint_calls_docker_rm_only_for_explicit_dockyard_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            completed = subprocess.CompletedProcess(
                args=["docker", "rm", "-f", "dockyard-llama-local"],
                returncode=0,
                stdout="removed",
                stderr="",
            )
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                selected = run_state.create_run(profile_fixture(), ["docker", "run", "example"])

                with mock.patch.object(app_module.subprocess, "run", return_value=completed) as docker_run:
                    review = app_module.cleanup_run(selected["run_id"], app_module.CleanupRequest())
                    removal = app_module.cleanup_run(
                        selected["run_id"],
                        app_module.CleanupRequest(remove_container=True, notes="remove concrete run"),
                    )

                persisted = run_state.get_run(selected["run_id"], runs_path)

            self.assertFalse(review["cleanup"]["docker_called"])
            docker_run.assert_called_once_with(
                ["docker", "rm", "-f", "dockyard-llama-local"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(removal["cleanup"]["action"], "container_removed")
            self.assertTrue(removal["cleanup"]["docker_called"])
            self.assertEqual(persisted["last_cleanup_result"]["stdout"], "removed")

    def test_cleanup_endpoint_refuses_non_dockyard_container_names(self) -> None:
        unsafe_profile = profile_fixture()
        unsafe_profile["container"] = dict(unsafe_profile["container"], name="production-model")
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                selected = run_state.create_run(unsafe_profile, ["docker", "run", "example"])

                with mock.patch.object(app_module.subprocess, "run") as docker_run:
                    response = app_module.cleanup_run(
                        selected["run_id"],
                        app_module.CleanupRequest(remove_container=True, notes="try unsafe remove"),
                    )

                persisted = run_state.get_run(selected["run_id"], runs_path)

            docker_run.assert_not_called()
            self.assertEqual(response["cleanup"]["action"], "refused")
            self.assertFalse(response["cleanup"]["docker_called"])
            self.assertEqual(response["cleanup"]["container_name"], "production-model")
            self.assertEqual(persisted["cleanup_status"], "refused")
            self.assertIn("Refusing to remove", persisted["last_cleanup_result"]["reason"])


if __name__ == "__main__":
    unittest.main()

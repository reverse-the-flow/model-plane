from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from model_control_plane import app as app_module
from model_control_plane import orchestration_jobs, run_state


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


class CronTickTests(unittest.TestCase):
    def test_cron_tick_creates_cleanup_review_jobs_for_failed_unhealthy_and_stale_launching_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            jobs_path = Path(temp_dir) / "agent_jobs.json"
            with (
                mock.patch.object(run_state, "RUNS_PATH", runs_path),
                mock.patch.object(orchestration_jobs, "JOBS_PATH", jobs_path),
                mock.patch.object(app_module, "all_profiles", return_value=[profile_fixture()]),
            ):
                failed = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_launch_result(failed["run_id"], 125, "", "docker failed")
                unhealthy = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_health_result(unhealthy["run_id"], {"ok": False, "error": "refused"})
                stale = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                stale["created_at"] = "2000-01-01T00:00:00Z"
                stale["updated_at"] = "2000-01-01T00:00:00Z"
                run_state.save_run(stale)

                result = app_module.run_cron_tick()

            cleanup_jobs = [job for job in result["created_jobs"] if job["job_type"] == "cleanup_review"]
            cleanup_run_ids = {job["run_id"] for job in cleanup_jobs}
            self.assertEqual(result["schema_version"], "model-plane-cron-tick-v1")
            self.assertIn(failed["run_id"], cleanup_run_ids)
            self.assertIn(unhealthy["run_id"], cleanup_run_ids)
            self.assertIn(stale["run_id"], cleanup_run_ids)
            for job in cleanup_jobs:
                self.assertEqual(job["payload"]["request_body"]["remove_container"], False)
                self.assertIn("docker_prune", orchestration_jobs.get_job(job["job_id"], jobs_path)["forbidden_actions"])

    def test_cron_tick_creates_health_and_moe_probe_jobs_for_launched_and_healthy_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            jobs_path = Path(temp_dir) / "agent_jobs.json"
            with (
                mock.patch.object(run_state, "RUNS_PATH", runs_path),
                mock.patch.object(orchestration_jobs, "JOBS_PATH", jobs_path),
                mock.patch.object(app_module, "all_profiles", return_value=[profile_fixture()]),
            ):
                launched = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_launch_result(launched["run_id"], 0, "container-id", "")
                healthy = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                healthy = run_state.record_health_result(healthy["run_id"], {"ok": True, "status": 200})
                healthy["last_health_result"]["checked_at"] = "2000-01-01T00:00:00Z"
                healthy["updated_at"] = "2000-01-01T00:00:00Z"
                run_state.save_run(healthy)

                result = app_module.run_cron_tick(app_module.CronTickRequest(health_stale_seconds=1))

            by_type = {}
            for job in result["created_jobs"]:
                by_type.setdefault(job["job_type"], set()).add(job["run_id"])
            self.assertIn(launched["run_id"], by_type["run_health_check"])
            self.assertIn(healthy["run_id"], by_type["run_health_check"])
            self.assertIn(launched["run_id"], by_type["moe_probe_plan"])
            self.assertIn(healthy["run_id"], by_type["moe_probe_plan"])

    def test_cron_tick_is_idempotent_while_jobs_are_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_path = Path(temp_dir) / "runs.json"
            jobs_path = Path(temp_dir) / "agent_jobs.json"
            with (
                mock.patch.object(run_state, "RUNS_PATH", runs_path),
                mock.patch.object(orchestration_jobs, "JOBS_PATH", jobs_path),
                mock.patch.object(app_module, "all_profiles", return_value=[profile_fixture()]),
            ):
                launched = run_state.create_run(profile_fixture(), ["docker", "run", "example"])
                run_state.record_launch_result(launched["run_id"], 0, "container-id", "")

                first = app_module.run_cron_tick()
                second = app_module.run_cron_tick()

            self.assertGreater(len(first["created_jobs"]), 0)
            self.assertEqual(second["created_jobs"], [])
            self.assertEqual(
                {job["dedupe_key"] for job in first["created_jobs"]},
                {job["dedupe_key"] for job in second["reused_open_jobs"]},
            )
            self.assertEqual(len(orchestration_jobs.list_jobs(jobs_path)), len(first["created_jobs"]))

    def test_api_routes_exist_and_completion_records_metadata(self) -> None:
        routes = {route.path for route in app_module.app.routes}
        self.assertIn("/cron/tick", routes)
        self.assertIn("/agent-jobs", routes)
        self.assertIn("/agent-jobs/{job_id}", routes)
        self.assertIn("/agent-jobs/{job_id}/complete", routes)

        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_path = Path(temp_dir) / "agent_jobs.json"
            with mock.patch.object(orchestration_jobs, "JOBS_PATH", jobs_path):
                job = orchestration_jobs.create_job(
                    job_type="profile_validate",
                    source="cron_tick",
                    profile_id="llama-local",
                    allowed_actions=["read_profile"],
                    forbidden_actions=["download_models"],
                    payload={"api_path": "/profiles/llama-local/validate"},
                )

                completed = app_module.complete_agent_job(
                    job["job_id"],
                    app_module.JobCompletionRequest(result={"reviewed": True}, notes="looks ok"),
                )

            self.assertEqual(completed["status"], "completed")
            self.assertTrue(completed["result"]["reviewed"])
            self.assertEqual(completed["result"]["notes"], "looks ok")
            self.assertIn("recorded_at", completed["result"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from model_control_plane import orchestration_jobs


class OrchestrationJobsTests(unittest.TestCase):
    def test_job_state_create_list_get_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agent_jobs.json"

            job = orchestration_jobs.create_job(
                job_type="profile_validate",
                source="cron_tick",
                profile_id="llama-local",
                allowed_actions=["read_profile"],
                forbidden_actions=["download_models"],
                payload={"api_path": "/profiles/llama-local/validate"},
                dedupe_key="profile_validate:llama-local",
                path=path,
            )

            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], "model-plane-agent-jobs-v1")
            self.assertEqual(data["jobs"][0]["job_id"], job["job_id"])
            self.assertEqual(data["jobs"][0]["status"], "open")

            listed = orchestration_jobs.list_jobs(path)
            fetched = orchestration_jobs.get_job(job["job_id"], path)
            completed = orchestration_jobs.complete_job(job["job_id"], {"reviewed": True}, path)

            self.assertEqual(len(listed), 1)
            self.assertEqual(fetched["profile_id"], "llama-local")
            self.assertEqual(completed["status"], "completed")
            self.assertTrue(completed["result"]["reviewed"])
            self.assertIn("recorded_at", completed["result"])
            self.assertEqual(completed["history"][-1]["event"], "completed")

    def test_create_or_reuse_open_job_uses_dedupe_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agent_jobs.json"
            kwargs = {
                "job_type": "run_health_check",
                "source": "cron_tick",
                "run_id": "run-1",
                "allowed_actions": ["inspect_run_state"],
                "forbidden_actions": ["use_tokens"],
                "payload": {"api_path": "/runs/run-1/health"},
                "dedupe_key": "run_health_check:run-1",
                "path": path,
            }

            first, first_reused = orchestration_jobs.create_or_reuse_open_job(**kwargs)
            second, second_reused = orchestration_jobs.create_or_reuse_open_job(**kwargs)

            self.assertFalse(first_reused)
            self.assertTrue(second_reused)
            self.assertEqual(first["job_id"], second["job_id"])
            self.assertEqual(len(orchestration_jobs.list_jobs(path)), 1)


if __name__ == "__main__":
    unittest.main()

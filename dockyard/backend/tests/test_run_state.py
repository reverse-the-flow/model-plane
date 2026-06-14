from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from model_control_plane import run_state


def profile_fixture() -> dict:
    return {
        "id": "llama-local",
        "name": "Local llama.cpp",
        "model": {"id": "local/example", "local_path": "/models/example.gguf"},
        "runtime": {"backend": "llama_cpp"},
        "container": {
            "name": "dockyard-llama-local",
            "host_port": 18080,
            "internal_port": 8080,
        },
        "health": {"url": "http://127.0.0.1:18080/health"},
        "logs": {"file_path": "/tmp/dockyard-llama-local.log"},
    }


class RunStateTests(unittest.TestCase):
    def test_run_state_persists_to_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runs.json"

            run = run_state.create_run(profile_fixture(), ["docker", "run", "example"], path)

            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], "model-plane-run-state-v1")
            self.assertEqual(data["runs"][0]["run_id"], run["run_id"])
            self.assertEqual(data["runs"][0]["status"], "launching")
            self.assertEqual(data["runs"][0]["profile_id"], "llama-local")
            self.assertEqual(data["runs"][0]["log_file_path"], "/tmp/dockyard-llama-local.log")
            self.assertEqual(data["runs"][0]["launch_command"], ["docker", "run", "example"])

    def test_failed_launch_result_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runs.json"
            run = run_state.create_run(profile_fixture(), ["docker", "run", "example"], path)

            updated = run_state.record_launch_result(run["run_id"], 125, "out", "bad docker", path)

            self.assertIsNotNone(updated)
            persisted = run_state.get_run(run["run_id"], path)
            self.assertEqual(persisted["status"], "launch_failed")
            self.assertEqual(persisted["launch_returncode"], 125)
            self.assertEqual(persisted["launch_stdout"], "out")
            self.assertEqual(persisted["launch_stderr"], "bad docker")
            self.assertEqual(persisted["launch"]["returncode"], 125)

    def test_health_result_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runs.json"
            run = run_state.create_run(profile_fixture(), ["docker", "run", "example"], path)

            updated = run_state.record_health_result(run["run_id"], {"ok": False, "error": "refused"}, path)

            self.assertIsNotNone(updated)
            persisted = run_state.get_run(run["run_id"], path)
            self.assertEqual(persisted["status"], "unhealthy")
            self.assertEqual(persisted["last_health_result"]["ok"], False)
            self.assertEqual(persisted["last_health_result"]["error"], "refused")
            self.assertIn("checked_at", persisted["last_health_result"])


if __name__ == "__main__":
    unittest.main()

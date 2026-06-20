from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from model_control_plane import app as app_module
from model_control_plane import run_state


def capsule_profile(repo_path: Path, state_dir: Path) -> dict:
    gateway_script = repo_path / "scripts" / "capsule_gateway.py"
    gateway_script.parent.mkdir(parents=True, exist_ok=True)
    gateway_script.write_text("print('fake gateway')\n", encoding="utf-8")
    return {
        "id": "session-capsule-local-llama",
        "name": "Session Capsule Gateway - local llama.cpp",
        "profile_type": "capsule_gateway",
        "version": 1,
        "runtime": {"backend": "capsule_gateway"},
        "network": {"mode": "local_only"},
        "endpoint": {
            "id": "local-llama-cpp",
            "kind": "openai_compatible",
            "base_url": "http://127.0.0.1:18080/v1",
        },
        "capsule_gateway": {
            "repo_path": str(repo_path),
            "state_dir": str(state_dir),
            "endpoint_id": "local-llama-cpp",
            "host": "127.0.0.1",
            "port": 8765,
            "checkpoint_mode": "soft",
            "slot": 0,
            "default_prefill": "user_default",
            "healthcheck_url": "http://127.0.0.1:8765/api/capsules/status",
            "client_base_url": "http://127.0.0.1:8765/v1",
            "python_command": ["py", "-3"],
            "fallback_replay": True,
        },
        "health": {"url": "http://127.0.0.1:8765/api/capsules/status"},
    }


class FakeHealthResponse:
    status = 200

    def __enter__(self) -> "FakeHealthResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class FakePopen:
    pid = 4321


class CapsuleGatewayProfileTests(unittest.TestCase):
    def test_capsule_gateway_profile_renders_target_launch_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = capsule_profile(root / "session-capsule", root / "capsule-state")

            messages = app_module.validate_profile(profile)
            command = app_module.render_command(profile)

        self.assertFalse([message for message in messages if message["level"] == "error"])
        self.assertEqual(command[:3], ["py", "-3", str(Path(temp_dir) / "session-capsule" / "scripts" / "capsule_gateway.py")])
        self.assertIn("--state-dir", command)
        self.assertIn("--endpoint", command)
        self.assertIn("local-llama-cpp", command)
        self.assertIn("--checkpoint-mode", command)
        self.assertIn("soft", command)
        self.assertIn("--default-prefill", command)
        self.assertIn("user_default", command)

    def test_capsule_gateway_health_uses_status_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            selected = capsule_profile(root / "session-capsule", root / "capsule-state")
            with (
                mock.patch.object(app_module, "load_profile", return_value=selected),
                mock.patch.object(app_module.urllib.request, "urlopen", return_value=FakeHealthResponse()) as urlopen,
            ):
                result = app_module.check_profile_health("session-capsule-local-llama")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        urlopen.assert_called_once_with("http://127.0.0.1:8765/api/capsules/status", timeout=5)

    def test_capsule_gateway_launch_records_pid_and_client_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_path = root / "runs.json"
            selected = capsule_profile(root / "session-capsule", root / "capsule-state")
            with (
                mock.patch.object(run_state, "RUNS_PATH", runs_path),
                mock.patch.object(app_module, "load_profile", return_value=selected),
                mock.patch.object(app_module.subprocess, "Popen", return_value=FakePopen()) as popen,
            ):
                response = app_module.launch("session-capsule-local-llama")
                persisted = run_state.get_run(response["run_id"], runs_path)

        self.assertTrue(response["ok"])
        self.assertEqual(response["client_base_url"], "http://127.0.0.1:8765/v1")
        self.assertEqual(persisted["service_type"], "capsule_gateway")
        self.assertEqual(persisted["endpoint_id"], "local-llama-cpp")
        self.assertEqual(persisted["base_url"], "http://127.0.0.1:8765/v1")
        self.assertEqual(persisted["client_base_url"], "http://127.0.0.1:8765/v1")
        self.assertEqual(persisted["health_url"], "http://127.0.0.1:8765/api/capsules/status")
        self.assertEqual(persisted["process_pid"], 4321)
        self.assertEqual(persisted["status"], "launched")
        popen.assert_called_once()
        self.assertIn("capsule_gateway.py", popen.call_args.args[0][2])

    def test_capsule_gateway_status_and_stop_are_run_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_path = root / "runs.json"
            selected = capsule_profile(root / "session-capsule", root / "capsule-state")
            with mock.patch.object(run_state, "RUNS_PATH", runs_path):
                run = run_state.create_run(selected, app_module.render_command(selected))
                run_state.record_process_launch_result(run["run_id"], 4321, "started", None)

                with mock.patch.object(app_module, "process_is_running", return_value=True):
                    status = app_module.run_status(run["run_id"])

                with mock.patch.object(
                    app_module,
                    "terminate_process",
                    return_value={"ok": True, "action": "process_stopped", "pid": 4321, "returncode": 0},
                ) as terminate:
                    stopped = app_module.stop_run(run["run_id"], app_module.StopRunRequest(notes="test stop"))

                persisted = run_state.get_run(run["run_id"], runs_path)

        self.assertEqual(status["client_base_url"], "http://127.0.0.1:8765/v1")
        self.assertTrue(status["process"]["running"])
        terminate.assert_called_once_with(4321, 10)
        self.assertEqual(stopped["stop"]["action"], "process_stopped")
        self.assertEqual(stopped["stop"]["notes"], "test stop")
        self.assertEqual(persisted["status"], "stopped")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from model_control_plane import app as app_module


class HfTokenSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HF_TOKEN_PATH", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_path = Path(self.temp_dir.name) / "secrets" / "hf_token"
        os.environ["HF_TOKEN_PATH"] = str(self.token_path)

    def tearDown(self) -> None:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HF_TOKEN_PATH", None)
        self.temp_dir.cleanup()

    def test_status_endpoint_does_not_reveal_token(self) -> None:
        os.environ["HF_TOKEN"] = "test-secret-value"

        status = app_module.get_hf_token_status()

        self.assertEqual(status["env_var"], "HF_TOKEN")
        self.assertTrue(status["configured"])
        self.assertTrue(status["process_configured"])
        self.assertFalse(status["persistent_configured"])
        self.assertEqual(status["scope"], "process_env")
        self.assertEqual(status["redacted"], "set")
        self.assertEqual(status["token_path_source"], "HF_TOKEN_PATH")
        self.assertNotIn("test-secret-value", repr(status))

    def test_secret_routes_are_registered(self) -> None:
        routes = {route.path for route in app_module.app.routes}

        self.assertIn("/secrets/hf-token", routes)

    def test_set_endpoint_sets_process_env_and_returns_only_metadata(self) -> None:
        status = app_module.set_hf_token(app_module.HfTokenRequest(token="  test-secret-value  "))

        self.assertEqual(os.environ["HF_TOKEN"], "test-secret-value")
        self.assertTrue(status["configured"])
        self.assertTrue(status["process_configured"])
        self.assertFalse(status["persistent_configured"])
        self.assertEqual(status["redacted"], "set")
        self.assertFalse(self.token_path.exists())
        self.assertNotIn("test-secret-value", repr(status))
        self.assertNotIn("token", status)

    def test_set_endpoint_can_remember_token_without_returning_value(self) -> None:
        status = app_module.set_hf_token(app_module.HfTokenRequest(token="  test-secret-value  ", remember=True))

        self.assertEqual(os.environ["HF_TOKEN"], "test-secret-value")
        self.assertTrue(self.token_path.exists())
        self.assertEqual(self.token_path.read_text(encoding="utf-8"), "test-secret-value\n")
        self.assertTrue(status["configured"])
        self.assertTrue(status["process_configured"])
        self.assertTrue(status["persistent_configured"])
        self.assertEqual(status["scope"], "process_env+persistent_file")
        self.assertNotIn("test-secret-value", repr(status))
        if os.name == "posix":
            file_mode = stat.S_IMODE(self.token_path.stat().st_mode)
            parent_mode = stat.S_IMODE(self.token_path.parent.stat().st_mode)
            self.assertEqual(file_mode, 0o600)
            self.assertEqual(parent_mode, 0o700)

    def test_status_loads_persisted_token_when_process_env_is_unset(self) -> None:
        app_module.set_hf_token(app_module.HfTokenRequest(token="test-secret-value", remember=True))
        os.environ.pop("HF_TOKEN", None)

        status = app_module.get_hf_token_status()

        self.assertEqual(os.environ["HF_TOKEN"], "test-secret-value")
        self.assertTrue(status["configured"])
        self.assertTrue(status["process_configured"])
        self.assertTrue(status["persistent_configured"])
        self.assertNotIn("test-secret-value", repr(status))

    def test_session_only_set_does_not_overwrite_existing_persistent_token(self) -> None:
        app_module.set_hf_token(app_module.HfTokenRequest(token="persisted-secret", remember=True))

        status = app_module.set_hf_token(app_module.HfTokenRequest(token="session-secret", remember=False))

        self.assertEqual(os.environ["HF_TOKEN"], "session-secret")
        self.assertEqual(self.token_path.read_text(encoding="utf-8"), "persisted-secret\n")
        self.assertTrue(status["process_configured"])
        self.assertTrue(status["persistent_configured"])
        self.assertNotIn("persisted-secret", repr(status))
        self.assertNotIn("session-secret", repr(status))

    def test_persisted_token_does_not_override_existing_process_env(self) -> None:
        app_module.set_hf_token(app_module.HfTokenRequest(token="persisted-secret", remember=True))
        os.environ["HF_TOKEN"] = "process-secret"

        status = app_module.get_hf_token_status()

        self.assertEqual(os.environ["HF_TOKEN"], "process-secret")
        self.assertTrue(status["process_configured"])
        self.assertTrue(status["persistent_configured"])
        self.assertNotIn("persisted-secret", repr(status))
        self.assertNotIn("process-secret", repr(status))

    def test_delete_endpoint_clears_process_env_and_persistent_token(self) -> None:
        os.environ["HF_TOKEN"] = "test-secret-value"
        app_module.write_persistent_hf_token("test-secret-value")

        status = app_module.clear_hf_token()

        self.assertNotIn("HF_TOKEN", os.environ)
        self.assertFalse(self.token_path.exists())
        self.assertFalse(status["configured"])
        self.assertFalse(status["process_configured"])
        self.assertFalse(status["persistent_configured"])
        self.assertEqual(status["redacted"], "unset")
        self.assertNotIn("test-secret-value", repr(status))

    def test_empty_token_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.set_hf_token(app_module.HfTokenRequest(token=" \n\t "))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertNotIn("HF_TOKEN", os.environ)


if __name__ == "__main__":
    unittest.main()

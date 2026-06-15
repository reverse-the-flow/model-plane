from __future__ import annotations

import os
import unittest

from fastapi import HTTPException

from model_control_plane import app as app_module


class HfTokenSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("HF_TOKEN", None)

    def tearDown(self) -> None:
        os.environ.pop("HF_TOKEN", None)

    def test_status_endpoint_does_not_reveal_token(self) -> None:
        os.environ["HF_TOKEN"] = "test-secret-value"

        status = app_module.get_hf_token_status()

        self.assertEqual(status["env_var"], "HF_TOKEN")
        self.assertTrue(status["configured"])
        self.assertEqual(status["scope"], "process_env")
        self.assertEqual(status["redacted"], "set")
        self.assertNotIn("test-secret-value", repr(status))

    def test_secret_routes_are_registered(self) -> None:
        routes = {route.path for route in app_module.app.routes}

        self.assertIn("/secrets/hf-token", routes)

    def test_set_endpoint_sets_process_env_and_returns_only_metadata(self) -> None:
        status = app_module.set_hf_token(app_module.HfTokenRequest(token="  test-secret-value  "))

        self.assertEqual(os.environ["HF_TOKEN"], "test-secret-value")
        self.assertTrue(status["configured"])
        self.assertEqual(status["redacted"], "set")
        self.assertNotIn("test-secret-value", repr(status))
        self.assertNotIn("token", status)

    def test_delete_endpoint_clears_process_env(self) -> None:
        os.environ["HF_TOKEN"] = "test-secret-value"

        status = app_module.clear_hf_token()

        self.assertNotIn("HF_TOKEN", os.environ)
        self.assertFalse(status["configured"])
        self.assertEqual(status["redacted"], "unset")
        self.assertNotIn("test-secret-value", repr(status))

    def test_empty_token_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.set_hf_token(app_module.HfTokenRequest(token=" \n\t "))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertNotIn("HF_TOKEN", os.environ)


if __name__ == "__main__":
    unittest.main()

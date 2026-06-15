from __future__ import annotations

import unittest

from fastapi import HTTPException

from model_control_plane import app as app_module
from model_control_plane import callable_functions


class CallableFunctionsTests(unittest.TestCase):
    def test_function_catalog_lists_expected_callable_functions(self) -> None:
        descriptors = callable_functions.list_function_descriptors()
        by_id = {descriptor["function_id"]: descriptor for descriptor in descriptors}

        self.assertEqual(
            {
                "profile.validate",
                "run.health_check",
                "run.moe_probe_manifest.export",
                "cleanup.review",
                "job.complete",
                "cron.tick",
            },
            set(by_id),
        )
        self.assertTrue(all(descriptor["allowed_for_cron"] for descriptor in descriptors))
        self.assertEqual(by_id["profile.validate"]["path_template"], "/profiles/{profile_id}/validate")

    def test_build_call_descriptor_resolves_path_and_side_effect(self) -> None:
        call = callable_functions.build_call_descriptor("cleanup.review", {"run_id": "run-1"})

        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], "/runs/run-1/cleanup")
        self.assertEqual(call["body"]["remove_container"], False)
        self.assertEqual(call["side_effect"], "run_cleanup_review_state_write")

    def test_function_lookup_route_returns_descriptor(self) -> None:
        routes = {route.path for route in app_module.app.routes}
        self.assertIn("/functions", routes)
        self.assertIn("/functions/{function_id}", routes)

        descriptor = app_module.function("run.moe_probe_manifest.export")

        self.assertEqual(descriptor["function_id"], "run.moe_probe_manifest.export")
        self.assertEqual(descriptor["method"], "GET")
        self.assertEqual(descriptor["path_template"], "/runs/{run_id}/moe-probe-manifest")

    def test_function_lookup_route_returns_404_for_unknown_function(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.function("missing.function")

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

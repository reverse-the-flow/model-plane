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
                "profile.integration_preview.export",
                "run.health_check",
                "run.status",
                "run.stop",
                "run.moe_probe_manifest.export",
                "run.integration_bundle.export",
                "run.integration_bundle.check",
                "cleanup.review",
                "job.complete",
                "cron.tick",
                "secret.hf_token.status",
                "secret.hf_token.set",
                "secret.hf_token.clear",
                "network.modes",
                "moe.test_cards.list",
                "moe.test_card.preflight",
                "moe.test_card.smoke",
                "moe.test_card.phase3_capture",
            },
            set(by_id),
        )
        self.assertTrue(by_id["secret.hf_token.status"]["allowed_for_cron"])
        self.assertFalse(by_id["secret.hf_token.set"]["allowed_for_cron"])
        self.assertFalse(by_id["secret.hf_token.clear"]["allowed_for_cron"])
        self.assertTrue(by_id["run.status"]["allowed_for_cron"])
        self.assertFalse(by_id["run.stop"]["allowed_for_cron"])
        self.assertTrue(by_id["moe.test_card.preflight"]["allowed_for_cron"])
        self.assertFalse(by_id["moe.test_card.smoke"]["allowed_for_cron"])
        self.assertFalse(by_id["moe.test_card.phase3_capture"]["allowed_for_cron"])
        self.assertTrue(
            all(
                descriptor["allowed_for_cron"]
                for function_id, descriptor in by_id.items()
                if not function_id.startswith("secret.hf_token.")
                and function_id not in {"run.stop", "moe.test_card.smoke", "moe.test_card.phase3_capture"}
            )
        )
        self.assertEqual(by_id["profile.validate"]["path_template"], "/profiles/{profile_id}/validate")
        self.assertEqual(by_id["network.modes"]["path_template"], "/network/modes")
        self.assertEqual(by_id["moe.test_cards.list"]["path_template"], "/moe-test-cards")
        self.assertEqual(by_id["moe.test_card.preflight"]["path_template"], "/moe-test-cards/{card_id}/preflight")
        self.assertEqual(by_id["moe.test_card.smoke"]["path_template"], "/moe-test-cards/{card_id}/smoke")
        self.assertEqual(by_id["moe.test_card.phase3_capture"]["path_template"], "/moe-test-cards/{card_id}/phase3-capture")
        self.assertEqual(by_id["profile.integration_preview.export"]["path_template"], "/profiles/{profile_id}/integration-preview")
        self.assertEqual(by_id["run.integration_bundle.export"]["path_template"], "/runs/{run_id}/integration-bundle")
        self.assertEqual(by_id["run.integration_bundle.check"]["path_template"], "/runs/{run_id}/integration-bundle/check")
        self.assertIn("write_harness_config_files", by_id["run.integration_bundle.export"]["forbidden_actions"])
        self.assertIn("send_prompt_traffic", by_id["run.integration_bundle.check"]["forbidden_actions"])
        self.assertIn("run_unbounded_prompt_suite", by_id["moe.test_card.smoke"]["forbidden_actions"])
        self.assertIn("write_phase3_artifacts_without_exact_paths", by_id["moe.test_card.phase3_capture"]["forbidden_actions"])
        self.assertEqual(by_id["moe.test_card.smoke"]["default_body"], {"approved_prompt_traffic": False})
        self.assertFalse(by_id["moe.test_card.phase3_capture"]["default_body"]["approved_phase3_capture"])
        self.assertFalse(by_id["moe.test_card.phase3_capture"]["default_body"]["execute"])
        self.assertIsNone(by_id["moe.test_card.phase3_capture"]["default_body"]["request_max_tokens"])
        self.assertEqual(by_id["secret.hf_token.set"]["default_body"], {"token": "", "remember": False})
        self.assertIn("return_secret_values", by_id["secret.hf_token.set"]["forbidden_actions"])
        self.assertIn("persist_secret_values", by_id["secret.hf_token.status"]["forbidden_actions"])

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

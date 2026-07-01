from __future__ import annotations

import unittest

from model_control_plane.moe_probe_manifest import build_moe_probe_manifest


class MoeProbeManifestTests(unittest.TestCase):
    def test_llama_cpp_profile_exports_runtime_baseline_manifest(self) -> None:
        manifest = build_moe_probe_manifest(
            {
                "id": "llama-local",
                "name": "Local llama.cpp",
                "model": {"id": "local/example", "local_path": "/models/example.gguf"},
                "runtime": {"backend": "llama_cpp"},
                "container": {"name": "dockyard-llama-local", "host_port": 18080},
                "health": {"url": "http://127.0.0.1:18080/health"},
                "logs": {
                    "file_path": "/mnt/Calliope/logs/model-plane/llama-cpp/llama-local.log",
                    "container_path": "/logs/llama-local.log",
                },
                "moe_probe": {
                    "observability_paths": ["/metrics", "/slots", "/props", "/perf"],
                    "readiness_paths": ["/health"],
                },
            }
        )

        self.assertEqual(manifest["schema_version"], "model-plane-moe-probe-manifest-v1")
        self.assertEqual(manifest["profile_id"], "llama-local")
        self.assertEqual(manifest["backend_family"], "llama_cpp")
        self.assertEqual(manifest["base_url"], "http://127.0.0.1:18080")
        self.assertEqual(manifest["primary_probe_hint"], "runtime_baseline")
        self.assertEqual(manifest["semantic_expert_ids"], "not_exposed")
        self.assertFalse(manifest["hookable_runtime_available"])
        self.assertEqual(manifest["observability_paths"], ["/metrics", "/slots", "/props", "/perf"])
        self.assertEqual(manifest["readiness_paths"], ["/health"])
        self.assertEqual(manifest["log_file_path"], "/mnt/Calliope/logs/model-plane/llama-cpp/llama-local.log")
        self.assertEqual(manifest["log_paths"]["container_log_file_path"], "/logs/llama-local.log")
        self.assertEqual(manifest["runtime_observability"]["required_paths"], ["/metrics", "/slots", "/props", "/perf"])
        self.assertEqual(manifest["runtime_observability"]["log_file_path"], "/mnt/Calliope/logs/model-plane/llama-cpp/llama-local.log")

    def test_hookable_profile_can_request_semantic_probe_path(self) -> None:
        manifest = build_moe_probe_manifest(
            {
                "id": "mixtral-hookable",
                "name": "Hookable Mixtral",
                "model": {"id": "local/mixtral", "local_path": "/models/mixtral"},
                "runtime": {"backend": "transformers"},
                "container": {"name": "dockyard-mixtral", "host_port": 18081},
                "health": {"url": "http://127.0.0.1:18081/v1/models"},
                "moe_probe": {"hookable_runtime_available": True},
            }
        )

        self.assertEqual(manifest["backend_family"], "pytorch_transformers")
        self.assertEqual(manifest["base_url"], "http://127.0.0.1:18081")
        self.assertEqual(manifest["primary_probe_hint"], "hookable_pytorch")
        self.assertEqual(
            manifest["semantic_expert_ids"],
            "expected_when_router_outputs_are_exposed",
        )
        self.assertTrue(manifest["hookable_runtime_available"])

    def test_passive_sidecar_request_overrides_default_probe_hint(self) -> None:
        manifest = build_moe_probe_manifest(
            {
                "id": "opaque",
                "name": "Opaque endpoint",
                "model": {"id": "local/opaque"},
                "runtime": {"backend": "openai_compatible"},
                "container": {"name": "dockyard-opaque", "host_port": 18082},
                "health": {"url": "http://127.0.0.1:18082/v1/models"},
                "moe_probe": {"passive_sidecar_requested": True},
            }
        )

        self.assertEqual(manifest["primary_probe_hint"], "passive_sidecar")
        self.assertEqual(manifest["semantic_expert_ids"], "not_exposed")

    def test_android_edge_manifest_cannot_claim_hookable_expert_ids(self) -> None:
        manifest = build_moe_probe_manifest(
            {
                "id": "android-pocketpal",
                "name": "Android PocketPal",
                "model": {"id": "user-selected-gguf"},
                "runtime": {"backend": "android_pocketpal"},
                "moe_probe": {
                    "hookable_runtime_available": True,
                    "semantic_expert_ids": "claimed",
                    "observability_paths": [],
                },
            }
        )

        self.assertEqual(manifest["backend_family"], "android_pocketpal")
        self.assertEqual(manifest["primary_probe_hint"], "edge_runtime_baseline")
        self.assertEqual(manifest["semantic_expert_ids"], "not_exposed")
        self.assertFalse(manifest["hookable_runtime_available"])
        self.assertEqual(manifest["observability_paths"], [])
        self.assertEqual(manifest["runtime_observability"]["kind"], "manual_edge_evidence")


if __name__ == "__main__":
    unittest.main()

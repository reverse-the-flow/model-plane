from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from model_control_plane import app as app_module
from model_control_plane import moe_test_cards


def make_fake_moe_root(path: Path) -> None:
    (path / "scripts").mkdir(parents=True)
    (path / "memory-moe-mvp" / "data").mkdir(parents=True)
    (path / "scripts" / "run_live_baseline.py").write_text("# fixture\n", encoding="utf-8")
    (path / "memory-moe-mvp" / "data" / "mixtral_probe_prompts.json").write_text("{}\n", encoding="utf-8")


class MoeTestCardTests(unittest.TestCase):
    def test_card_catalog_uses_pc_or_configured_moe_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fake_moe_root(root)
            with mock.patch.dict(os.environ, {moe_test_cards.MOE_RUN_ANYWAY_ROOT_ENV: str(root)}):
                cards = app_module.moe_cards()

        self.assertEqual(len(cards), 9)
        self.assertEqual(cards[0]["card_id"], "llama-cpp-dolphin-mixtral-8x7b-sidecar")
        self.assertEqual(cards[0]["model"], "dolphin-mixtral-8x7b.gguf")
        self.assertEqual(cards[0]["backend_family"], "llama_cpp")
        self.assertEqual(cards[0]["card_type"], "launch_plus_probe")
        self.assertEqual(cards[0]["probe_tier"], "passive_external_plus_internal_runtime")
        self.assertIn("docker run", cards[0]["launch_command"]["shell_command"])
        self.assertIn("memory-moe-llama-sidecar:latest", cards[0]["launch_command"]["shell_command"])
        self.assertNotIn("--gpus", cards[0]["launch_command"]["argv"])
        self.assertTrue(cards[0]["moe_root"]["available"])
        self.assertIn("run_live_baseline.py", cards[0]["preflight_command"]["shell_command"])
        self.assertIn("--log-file-path", cards[0]["preflight_command"]["argv"])
        self.assertTrue(cards[0]["smoke_command"]["sends_prompt_traffic"])
        sidecar_ids = {card["card_id"] for card in cards if card["card_type"] == "launch_plus_probe"}
        self.assertIn("llama-cpp-qwen3-30b-sidecar", sidecar_ids)
        qwen3_sidecar = next(card for card in cards if card["card_id"] == "llama-cpp-qwen3-30b-sidecar")
        self.assertEqual(qwen3_sidecar["base_url"], "http://127.0.0.1:18081")
        self.assertEqual(qwen3_sidecar["model"], "qwen3-30b.gguf")
        self.assertNotIn("--gpus", qwen3_sidecar["launch_command"]["argv"])
        nemotron_sidecar = next(
            card for card in cards if card["card_id"] == "llama-cpp-nemotron-3-super-120b-sidecar"
        )
        self.assertEqual(nemotron_sidecar["base_url"], "http://127.0.0.1:18082")
        self.assertEqual(nemotron_sidecar["model_class"], "nemotron_h_moe")
        self.assertIn("--no-warmup", nemotron_sidecar["launch_command"]["argv"])
        self.assertNotIn("--no-mmap", nemotron_sidecar["launch_command"]["argv"])
        self.assertNotIn("--gpus", nemotron_sidecar["launch_command"]["argv"])
        nemotron_vllm = next(card for card in cards if card["card_id"] == "vllm-nemotron-omni-30b-a3b-nvfp4")
        self.assertEqual(nemotron_vllm["backend_family"], "vllm_openai_compatible")
        self.assertEqual(nemotron_vllm["base_url"], "http://127.0.0.1:18002")
        self.assertEqual(nemotron_vllm["profile_id"], "nemotron-omni-nvfp4-vllm")
        self.assertEqual(nemotron_vllm["card_type"], "openai_compatible_runtime_baseline")
        opaque_mixtral = next(card for card in cards if card["card_id"] == "ollama-dolphin-mixtral-8x7b")
        self.assertEqual(opaque_mixtral["card_type"], "opaque_runtime_baseline")
        self.assertIn("Ollama does not expose", " ".join(opaque_mixtral["limitations"]))

    def test_smoke_requires_explicit_prompt_traffic_approval(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_smoke(
                "ollama-dolphin-mixtral-8x7b",
                app_module.MoeTestSmokeRequest(approved_prompt_traffic=False),
            )

        self.assertEqual(raised.exception.status_code, 400)

    def test_preflight_runs_bounded_runner_without_prompt_traffic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fake_moe_root(root)
            completed = subprocess.CompletedProcess(
                args=["python"],
                returncode=0,
                stdout=json.dumps({"preflight": {"traffic_allowed": True}}),
                stderr="",
            )
            with (
                mock.patch.dict(os.environ, {moe_test_cards.MOE_RUN_ANYWAY_ROOT_ENV: str(root)}),
                mock.patch.object(moe_test_cards.subprocess, "run", return_value=completed) as run,
            ):
                result = app_module.moe_card_preflight("llama-cpp-dolphin-mixtral-8x7b-sidecar")

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "preflight")
        self.assertTrue(result["parsed_stdout"]["preflight"]["traffic_allowed"])
        command = run.call_args.args[0]
        self.assertIn("--preflight-only", command)
        self.assertIn("--json", command)
        self.assertIn("--backend-family", command)
        self.assertEqual(command[command.index("--backend-family") + 1], "llama_cpp")
        self.assertIn("--log-file-path", command)

    def test_approved_smoke_runs_single_prompt_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fake_moe_root(root)
            completed = subprocess.CompletedProcess(
                args=["python"],
                returncode=0,
                stdout=json.dumps({"run_dir": "/tmp/model-plane-moe-test-runs/run-1"}),
                stderr="",
            )
            with (
                mock.patch.dict(os.environ, {moe_test_cards.MOE_RUN_ANYWAY_ROOT_ENV: str(root)}),
                mock.patch.object(moe_test_cards.subprocess, "run", return_value=completed) as run,
            ):
                result = app_module.moe_card_smoke(
                    "ollama-qwen3-30b",
                    app_module.MoeTestSmokeRequest(approved_prompt_traffic=True),
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "smoke")
        self.assertEqual(result["parsed_stdout"]["run_dir"], "/tmp/model-plane-moe-test-runs/run-1")
        command = run.call_args.args[0]
        self.assertNotIn("--preflight-only", command)
        self.assertIn("--max-prompts", command)
        self.assertEqual(command[command.index("--max-prompts") + 1], "1")


if __name__ == "__main__":
    unittest.main()

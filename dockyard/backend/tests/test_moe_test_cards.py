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
    (path / "scripts" / "run_phase3_dense_output_capture.py").write_text("# fixture\n", encoding="utf-8")
    (path / "memory-moe-mvp" / "data" / "mixtral_probe_prompts.json").write_text("{}\n", encoding="utf-8")


def make_phase3_capture_request() -> app_module.MoePhase3CaptureRequest:
    return app_module.MoePhase3CaptureRequest(
        approved_phase3_capture=True,
        approved_prompt_traffic=True,
        runtime_capture_request_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle.runtime-capture-request.json",
        artifact_id="candidate_router_trace",
        capture_kind="llama_cpp_router_trace_jsonl",
        receipt_kind="trace_capture_receipt_json",
        prompt_set_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle.prompt-set.json",
        artifact_output_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle-policy-candidate/candidate-router-events.jsonl",
        receipt_output_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle-policy-candidate/candidate-router-events.capture-receipt.json",
        approval_keys=["runtime_prompt_traffic_approved", "router_trace_capture_approved"],
    )


def make_phase3_dense_capture_request() -> app_module.MoePhase3CaptureRequest:
    return app_module.MoePhase3CaptureRequest(
        approved_phase3_capture=True,
        approved_prompt_traffic=True,
        runtime_capture_request_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle.runtime-capture-request.json",
        artifact_id="dense_output_summary_fill",
        capture_kind="dense_output_summary_json",
        receipt_kind="embedded_output_capture_receipt",
        prompt_set_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle.prompt-set.json",
        artifact_output_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle-fallback/dense-output-summary.json",
        receipt_output_path="memory-moe-mvp/phase3-real-evidence/pc_mixtral_phase3_real_evidence_bundle-fallback/dense-output-summary.json",
        approval_keys=["runtime_prompt_traffic_approved", "dense_output_capture_approved"],
        request_max_tokens=32,
    )

class MoeTestCardTests(unittest.TestCase):
    def test_card_catalog_uses_pc_or_configured_moe_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fake_moe_root(root)
            with mock.patch.dict(os.environ, {moe_test_cards.MOE_RUN_ANYWAY_ROOT_ENV: str(root)}):
                cards = app_module.moe_cards()

        self.assertEqual(len(cards), 12)
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
        self.assertEqual(
            cards[0]["phase3_capture_endpoint"],
            "/moe-test-cards/llama-cpp-dolphin-mixtral-8x7b-sidecar/phase3-capture",
        )
        self.assertEqual(cards[0]["phase3_artifact_writer_contract_count"], 3)
        self.assertEqual(cards[0]["phase3_artifact_writer_ready_count"], 1)
        phase3_writer = next(
            writer for writer in cards[0]["phase3_artifact_writers"] if writer["artifact_id"] == "candidate_router_trace"
        )
        self.assertEqual(phase3_writer["function_id"], "moe.test_card.phase3_capture")
        self.assertFalse(phase3_writer["runtime_execution_ready"])
        self.assertTrue(phase3_writer["accepts_runtime_capture_request_path"])
        self.assertTrue(phase3_writer["writes_explicit_receipt_path"])
        self.assertIn("phase3_runtime_artifact_writer_execution_not_implemented", phase3_writer["blockers"])
        dense_writer = next(
            writer for writer in cards[0]["phase3_artifact_writers"] if writer["artifact_id"] == "dense_output_summary_fill"
        )
        self.assertTrue(dense_writer["runtime_execution_ready"])
        self.assertTrue(dense_writer["writes_runtime_artifacts"])
        self.assertEqual(dense_writer["implementation_status"], "dense_output_runtime_writer_ready")
        self.assertEqual(dense_writer["blockers"], [])
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
        pocketpal = next(card for card in cards if card["card_id"] == "android-pocketpal-baseline")
        self.assertEqual(pocketpal["target_class"], "android_physical_device")
        self.assertEqual(pocketpal["runtime_stack"], "pocketpal_gguf_llama_cpp_class")
        self.assertEqual(pocketpal["execution_mode"], "manual_evidence")
        self.assertFalse(pocketpal["requires_moe_checkout"])
        self.assertTrue(pocketpal["supports_manual_evidence"])
        self.assertIsNone(pocketpal["preflight_command"])
        self.assertIsNone(pocketpal["smoke_command"])
        self.assertEqual(pocketpal["manual_evidence_endpoint"], "/moe-test-cards/android-pocketpal-baseline/manual-evidence")
        self.assertIsNone(pocketpal["phase3_capture_endpoint"])
        self.assertEqual(pocketpal["phase3_artifact_writer_contract_count"], 0)
        self.assertEqual(pocketpal["phase3_artifact_writer_ready_count"], 0)
        self.assertIn("Physical-device measurements", " ".join(pocketpal["evidence_limits"]))
        emulator = next(card for card in cards if card["card_id"] == "android-emulator-ui-only")
        self.assertEqual(emulator["target_class"], "android_emulator")
        self.assertIn("UI/connectivity evidence only", " ".join(emulator["evidence_limits"]))

    def test_smoke_requires_explicit_prompt_traffic_approval(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_smoke(
                "ollama-dolphin-mixtral-8x7b",
                app_module.MoeTestSmokeRequest(approved_prompt_traffic=False),
            )

        self.assertEqual(raised.exception.status_code, 400)

    def test_phase3_capture_contract_validates_exact_paths_without_runtime_execution(self) -> None:
        result = app_module.moe_card_phase3_capture(
            "llama-cpp-dolphin-mixtral-8x7b-sidecar",
            make_phase3_capture_request(),
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["accepted_contract"])
        self.assertFalse(result["runtime_execution_ready"])
        self.assertFalse(result["write_performed"])
        self.assertFalse(result["prompt_traffic_sent_by_model_plane"])
        self.assertEqual(result["artifact_id"], "candidate_router_trace")
        self.assertEqual(result["capture_kind"], "llama_cpp_router_trace_jsonl")
        self.assertEqual(result["receipt_kind"], "trace_capture_receipt_json")
        self.assertIn("phase3_runtime_artifact_writer_execution_not_implemented", result["blockers"])

    def test_phase3_capture_requires_explicit_approval(self) -> None:
        request = make_phase3_capture_request()
        request.approved_phase3_capture = False

        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_phase3_capture("llama-cpp-dolphin-mixtral-8x7b-sidecar", request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("explicit approval", str(raised.exception.detail))

    def test_phase3_capture_rejects_unsafe_output_paths(self) -> None:
        request = make_phase3_capture_request()
        request.artifact_output_path = "/tmp/bad.jsonl"

        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_phase3_capture("llama-cpp-dolphin-mixtral-8x7b-sidecar", request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("repo-relative safe path", str(raised.exception.detail))

    def test_manual_edge_card_refuses_phase3_capture(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_phase3_capture("android-pocketpal-baseline", make_phase3_capture_request())

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("manual_evidence", str(raised.exception.detail))

    def test_phase3_dense_capture_contract_is_ready_but_plan_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fake_moe_root(root)
            with mock.patch.dict(os.environ, {moe_test_cards.MOE_RUN_ANYWAY_ROOT_ENV: str(root)}):
                result = app_module.moe_card_phase3_capture(
                    "llama-cpp-dolphin-mixtral-8x7b-sidecar",
                    make_phase3_dense_capture_request(),
                )

        self.assertFalse(result["ok"])
        self.assertTrue(result["runtime_execution_ready"])
        self.assertFalse(result["write_performed"])
        self.assertFalse(result["prompt_traffic_sent_by_model_plane"])
        self.assertEqual(result["artifact_id"], "dense_output_summary_fill")
        self.assertIn("phase3_capture_execute_flag_not_set", result["blockers"])

    def test_phase3_dense_capture_execute_runs_guarded_writer(self) -> None:
        request = make_phase3_dense_capture_request()
        request.execute = True
        completed = subprocess.CompletedProcess(
            args=["python"],
            returncode=0,
            stdout=json.dumps({"ok": True, "write_performed": True, "prompt_traffic_sent": True}),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fake_moe_root(root)
            with (
                mock.patch.dict(os.environ, {moe_test_cards.MOE_RUN_ANYWAY_ROOT_ENV: str(root)}),
                mock.patch.object(moe_test_cards.subprocess, "run", return_value=completed) as run,
            ):
                result = app_module.moe_card_phase3_capture(
                    "llama-cpp-dolphin-mixtral-8x7b-sidecar",
                    request,
                )

        self.assertTrue(result["ok"])
        self.assertTrue(result["write_performed"])
        self.assertTrue(result["prompt_traffic_sent_by_model_plane"])
        command = run.call_args.args[0]
        self.assertIn("run_phase3_dense_output_capture.py", command[1])
        self.assertIn("--approved-runtime-prompt-traffic", command)
        self.assertIn("--approved-dense-output-capture", command)
        self.assertIn("--request-max-tokens", command)
        self.assertEqual(run.call_args.kwargs["cwd"], root)

    def test_phase3_candidate_trace_execute_stays_blocked(self) -> None:
        request = make_phase3_capture_request()
        request.execute = True

        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_phase3_capture("llama-cpp-dolphin-mixtral-8x7b-sidecar", request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("does not have a runtime artifact writer", str(raised.exception.detail))

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

    def test_manual_edge_card_refuses_preflight_runner(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_preflight("android-pocketpal-baseline")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("manual_evidence", str(raised.exception.detail))

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

    def test_manual_edge_evidence_requires_explicit_approval(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_manual_evidence(
                "android-pocketpal-baseline",
                app_module.MoeManualEvidenceRequest(
                    approved_manual_evidence=False,
                    evidence={"device_label": "screen-repair-phone"},
                ),
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("explicit approval", str(raised.exception.detail))

    def test_manual_edge_evidence_writes_bounded_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {moe_test_cards.MOE_TEST_OUTPUT_DIR_ENV: temp_dir}):
                result = app_module.moe_card_manual_evidence(
                    "android-pocketpal-baseline",
                    app_module.MoeManualEvidenceRequest(
                        approved_manual_evidence=True,
                        evidence={
                            "device_label": "screen-repair-phone",
                            "app_runtime": "PocketPal",
                            "model_id": "tinyllama-q4.gguf",
                            "model_format": "GGUF",
                            "quant": "Q4",
                            "tokens_per_second": 5.2,
                            "thermal_note": "warm after one run",
                        },
                        notes="first physical baseline",
                    ),
                )

            run_dir = Path(result["run_dir"])
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            manual = json.loads((run_dir / "manual-evidence.json").read_text(encoding="utf-8"))
            event = json.loads((run_dir / "events.jsonl").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertFalse(result["sends_prompt_traffic"])
        self.assertFalse(result["device_commands_run"])
        self.assertEqual(manifest["semantic_expert_ids"], "not_exposed")
        self.assertFalse(manifest["hookable_runtime_available"])
        self.assertEqual(manifest["target_class"], "android_physical_device")
        self.assertEqual(summary["manual_evidence"]["app_runtime"], "PocketPal")
        self.assertEqual(manual["raw_manual_evidence"]["model_id"], "tinyllama-q4.gguf")
        self.assertEqual(event["event_type"], "edge_manual_evidence_recorded")

    def test_runner_card_refuses_manual_edge_evidence(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            app_module.moe_card_manual_evidence(
                "ollama-qwen3-30b",
                app_module.MoeManualEvidenceRequest(
                    approved_manual_evidence=True,
                    evidence={"device_label": "not-edge"},
                ),
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("does not accept manual evidence", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()

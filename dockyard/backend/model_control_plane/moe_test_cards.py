from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOCKYARD_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = DOCKYARD_ROOT.parent
MOE_RUN_ANYWAY_ROOT_ENV = "MOE_RUN_ANYWAY_ROOT"
MOE_TEST_OUTPUT_DIR_ENV = "MOE_RUN_ANYWAY_TEST_OUTPUT_DIR"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_BACKEND_FAMILY = "ollama_openai_compatible"
LLAMA_CPP_BASE_URL = "http://127.0.0.1:18080"
LLAMA_CPP_QWEN3_30B_BASE_URL = "http://127.0.0.1:18081"
LLAMA_CPP_NEMOTRON_SUPER_BASE_URL = "http://127.0.0.1:18082"
VLLM_NEMOTRON_OMNI_BASE_URL = "http://127.0.0.1:18002"
DEFAULT_SUITE_RELATIVE = Path("memory-moe-mvp/data/mixtral_probe_prompts.json")
RUNNER_RELATIVE = Path("scripts/run_live_baseline.py")
PHASE3_DENSE_CAPTURE_RELATIVE = Path("scripts/run_phase3_dense_output_capture.py")
DOLPHIN_MIXTRAL_LOG_FILE = Path("/home/codexlab/model-plane-runtime/logs/llama-cpp/dolphin-mixtral-8x7b.log")
QWEN3_30B_LOG_FILE = Path("/home/codexlab/model-plane-runtime/logs/llama-cpp/qwen3-30b.log")
NEMOTRON_SUPER_LOG_FILE = Path("/home/codexlab/model-plane-runtime/logs/llama-cpp/nemotron-3-super-120b.log")
MANUAL_EVIDENCE_SCHEMA_VERSION = "model-plane-moe-edge-manual-evidence-v1"
MANUAL_EVIDENCE_MAX_BYTES = 64 * 1024
PROJECT_LOCAL_OUTPUT_DIR = DOCKYARD_ROOT / "state" / "moe-run-anyway-runs"


@dataclass(frozen=True)
class MoeTestCard:
    card_id: str
    title: str
    model: str
    label: str
    model_class: str
    card_type: str
    evidence_level: str
    probe_tier: str
    hardware_note: str
    purpose: str
    base_url: str = DEFAULT_BASE_URL
    backend_family: str = DEFAULT_BACKEND_FAMILY
    max_prompts: int = 1
    repeats: int = 1
    profile_id: str | None = None
    log_file_path: Path | None = None
    launch_recipe: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    target_class: str = "local_runtime"
    runtime_stack: str = "openai_compatible_endpoint"
    execution_mode: str = "runner"
    evidence_limits: tuple[str, ...] = ()


OPAQUE_RUNTIME_LIMITATIONS = (
    "OpenAI-compatible/Ollama artifacts are runtime evidence, not semantic expert id evidence.",
    "Ollama does not expose llama.cpp hook telemetry through this card.",
)

VLLM_RUNTIME_LIMITATIONS = (
    "vLLM artifacts prove OpenAI-compatible runtime behavior, not llama.cpp hookability.",
    "Semantic expert ids are not exposed through this card; use it to compare runnable MoE backend behavior.",
)

LLAMA_CPP_RUNTIME_LIMITATIONS = (
    "Stock llama.cpp observability is runtime evidence, not semantic expert id evidence.",
    "Router logits, selected expert ids, and per-layer dispatch require a later hook or fork.",
)

ANDROID_EDGE_LIMITATIONS = (
    "There is no Docker path on Android for this lane.",
    "Android execution happens outside Model Plane through an operator-installed app or raw native device workflow.",
    "Android edge artifacts are runtime evidence only; they do not expose semantic expert ids.",
    "Physical-device measurements are required for inference, thermals, battery, and sustained throughput.",
    "Model Plane does not install apps, transfer weights, call ADB, or send prompt traffic for this card.",
)

ANDROID_EMULATOR_LIMITATIONS = (
    "Emulator artifacts are UI/connectivity evidence only, not inference performance evidence.",
    "Emulators do not provide reliable thermals, battery drain, mobile GPU/NPU behavior, or throttling data.",
    "Semantic expert ids are not exposed through emulator evidence.",
)

RUNTIME_ARTIFACTS = (
    "manifest.json",
    "summary.json",
    "events.jsonl",
)

LLAMA_CPP_ARTIFACTS = (
    "manifest.json",
    "summary.json",
    "events.jsonl",
    "llama.cpp /metrics, /slots, /props snapshots when available",
    "configured llama.cpp log growth when available",
)

EDGE_MANUAL_ARTIFACTS = (
    "manifest.json",
    "summary.json",
    "events.jsonl",
    "manual-evidence.json",
)

PHASE3_ARTIFACT_WRITER_SCHEMA_VERSION = "model-plane-moe-phase3-artifact-writer-v1"
PHASE3_CAPTURE_RESULT_SCHEMA_VERSION = "model-plane-moe-phase3-capture-result-v1"
PHASE3_CAPTURE_ARTIFACTS = (
    {
        "artifact_id": "candidate_router_trace",
        "capture_kind": "llama_cpp_router_trace_jsonl",
        "receipt_kind": "trace_capture_receipt_json",
        "approval_keys": ("runtime_prompt_traffic_approved", "router_trace_capture_approved"),
    },
    {
        "artifact_id": "managed_output_summary_fill",
        "capture_kind": "managed_output_summary_json",
        "receipt_kind": "embedded_output_capture_receipt",
        "approval_keys": ("runtime_prompt_traffic_approved", "managed_output_capture_approved"),
    },
    {
        "artifact_id": "dense_output_summary_fill",
        "capture_kind": "dense_output_summary_json",
        "receipt_kind": "embedded_output_capture_receipt",
        "approval_keys": ("runtime_prompt_traffic_approved", "dense_output_capture_approved"),
    },
)

MANUAL_EVIDENCE_FIELDS = (
    {
        "name": "device_label",
        "description": "Human-readable device name or lab label.",
        "required": True,
    },
    {"name": "android_version", "description": "Android release or build string.", "required": False},
    {"name": "chipset", "description": "SoC/chipset if known.", "required": False},
    {"name": "ram_gb", "description": "Approximate device RAM in GB.", "required": False},
    {"name": "storage_notes", "description": "Free space/model storage notes.", "required": False},
    {"name": "dev_path", "description": "external_app, adb_raw_binary, termux_raw, emulator_ui, or other path.", "required": False},
    {"name": "app_runtime", "description": "PocketPal, direct llama.cpp, MLC, MediaPipe, Termux, or other runtime.", "required": True},
    {"name": "model_id", "description": "Model name or file label tested.", "required": True},
    {"name": "model_format", "description": "GGUF, app bundle, TFLite, MLC package, or other format.", "required": False},
    {"name": "quant", "description": "Quantization label if known.", "required": False},
    {"name": "acceleration_path", "description": "CPU, GPU/Vulkan, NNAPI/NPU, or app-reported backend.", "required": False},
    {"name": "tokens_per_second", "description": "Observed decode throughput if available.", "required": False},
    {"name": "time_to_first_token_ms", "description": "Observed TTFT if available.", "required": False},
    {"name": "battery_note", "description": "Battery level, drain, charging state, or unavailable.", "required": False},
    {"name": "thermal_note", "description": "Thermal/throttling observation or unavailable.", "required": False},
    {"name": "observations", "description": "Short freeform result notes.", "required": False},
)


def sidecar_launch_recipe(
    *,
    container_name: str,
    host_port: str,
    sidecar_label: str,
    model_filename: str,
    log_filename: str,
    ctx_size: str = "512",
    no_mmap: bool = True,
    extra_runtime_args: tuple[str, ...] = (),
) -> tuple[str, ...]:
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "-p",
        f"0.0.0.0:{host_port}:8080",
        "--shm-size",
        "16gb",
        "-e",
        f"SIDECAR_LABEL={sidecar_label}",
        "-e",
        "SIDECAR_EXTRA_ARGS=--capture-upstream-observability",
        "-v",
        "/mnt/Calliope/models:/models:ro",
        "-v",
        "/home/codexlab/model-plane-runtime/logs/llama-cpp:/logs",
        "-v",
        "/home/codexlab/model-plane-runtime/logs/moe-sidecar:/var/log/memory-moe-sidecar",
        "memory-moe-llama-sidecar:latest",
        "-m",
        f"/models/{model_filename}",
        "--ctx-size",
        ctx_size,
        "--n-gpu-layers",
        "0",
    ]
    if no_mmap:
        command.append("--no-mmap")
    command.extend(
        [
            *extra_runtime_args,
            "--metrics",
            "--slots",
            "--props",
            "--perf",
            "--log-file",
            f"/logs/{log_filename}",
            "--log-prefix",
            "--log-timestamps",
            "--verbosity",
            "4",
        ]
    )
    return tuple(command)


MOE_TEST_CARDS = [
    MoeTestCard(
        card_id="llama-cpp-dolphin-mixtral-8x7b-sidecar",
        title="Dolphin Mixtral llama.cpp Sidecar",
        model="dolphin-mixtral-8x7b.gguf",
        label="dolphin-mixtral-8x7b-llama-sidecar",
        model_class="mixtral_style",
        card_type="launch_plus_probe",
        evidence_level="stock_llama_cpp_observability_with_passive_sidecar",
        probe_tier="passive_external_plus_internal_runtime",
        backend_family="llama_cpp",
        base_url=LLAMA_CPP_BASE_URL,
        profile_id="dolphin-mixtral-8x7b-llama-sidecar",
        log_file_path=DOLPHIN_MIXTRAL_LOG_FILE,
        launch_recipe=sidecar_launch_recipe(
            container_name="dockyard-moe-dolphin-mixtral-sidecar",
            host_port="18080",
            sidecar_label="dolphin-mixtral-8x7b-sidecar-cpu-baseline",
            model_filename="dolphin-mixtral-8x7b.gguf",
            log_filename="dolphin-mixtral-8x7b.log",
        ),
        expected_artifacts=LLAMA_CPP_ARTIFACTS,
        limitations=LLAMA_CPP_RUNTIME_LIMITATIONS,
        prerequisites=(
            "memory-moe-llama-sidecar:latest image is built on the target host",
            "/mnt/Calliope/models/dolphin-mixtral-8x7b.gguf exists or the profile is edited to a real GGUF path",
            "Docker access is available to the Model Plane launcher",
        ),
        hardware_note=(
            "Intended GX10 card for the MoE Run Anyway llama.cpp sidecar path; "
            "requires direct llama-server/sidecar launch, not Ollama."
        ),
        purpose="Primary Mixtral-family card for llama.cpp observability artifacts and later hook/fork comparison.",
    ),
    MoeTestCard(
        card_id="llama-cpp-dolphin-mixtral-8x7b-existing",
        title="Dolphin Mixtral Existing llama.cpp",
        model="dolphin-mixtral-8x7b.gguf",
        label="dolphin-mixtral-8x7b-llama-runtime",
        model_class="mixtral_style",
        card_type="probe_existing_endpoint",
        evidence_level="stock_llama_cpp_observability",
        probe_tier="internal_runtime",
        backend_family="llama_cpp",
        base_url=LLAMA_CPP_BASE_URL,
        log_file_path=DOLPHIN_MIXTRAL_LOG_FILE,
        expected_artifacts=LLAMA_CPP_ARTIFACTS,
        limitations=LLAMA_CPP_RUNTIME_LIMITATIONS,
        prerequisites=(
            "A llama-server-compatible endpoint is already listening on http://127.0.0.1:18080",
            "The endpoint exposes /metrics, /slots, and /props before prompt traffic is sent",
        ),
        hardware_note="Use this card when a llama.cpp server is already up outside Dockyard.",
        purpose="Bounded runtime probe for a pre-launched direct llama.cpp Mixtral endpoint.",
    ),
    MoeTestCard(
        card_id="llama-cpp-qwen3-30b-sidecar",
        title="Qwen3 30B llama.cpp Sidecar",
        model="qwen3-30b.gguf",
        label="qwen3-30b-llama-sidecar",
        model_class="qwen3_moe",
        card_type="launch_plus_probe",
        evidence_level="stock_llama_cpp_observability_with_passive_sidecar",
        probe_tier="passive_external_plus_internal_runtime",
        backend_family="llama_cpp",
        base_url=LLAMA_CPP_QWEN3_30B_BASE_URL,
        profile_id="qwen3-30b-llama-sidecar",
        log_file_path=QWEN3_30B_LOG_FILE,
        launch_recipe=sidecar_launch_recipe(
            container_name="dockyard-moe-qwen3-30b-sidecar",
            host_port="18081",
            sidecar_label="qwen3-30b-sidecar-cpu-baseline",
            model_filename="qwen3-30b.gguf",
            log_filename="qwen3-30b.log",
        ),
        expected_artifacts=LLAMA_CPP_ARTIFACTS,
        limitations=LLAMA_CPP_RUNTIME_LIMITATIONS,
        prerequisites=(
            "memory-moe-llama-sidecar:latest image is built on the target host",
            "/mnt/Calliope/models/qwen3-30b.gguf exists or the profile is edited to a real GGUF path",
            "Docker access is available to the Model Plane launcher",
        ),
        hardware_note=(
            "Second sparse-model sidecar target on GX10; uses a separate port from Mixtral "
            "so the architecture comparison can run without reusing the same endpoint."
        ),
        purpose="Qwen3 MoE-family card for comparing llama.cpp observability behavior against Mixtral.",
    ),
    MoeTestCard(
        card_id="llama-cpp-nemotron-3-super-120b-sidecar",
        title="Nemotron 3 Super 120B llama.cpp Sidecar",
        model="nemotron-3-super-120b.gguf",
        label="nemotron-3-super-120b-llama-sidecar",
        model_class="nemotron_h_moe",
        card_type="launch_plus_probe",
        evidence_level="stock_llama_cpp_observability_with_passive_sidecar_high_memory",
        probe_tier="passive_external_plus_internal_runtime",
        backend_family="llama_cpp",
        base_url=LLAMA_CPP_NEMOTRON_SUPER_BASE_URL,
        profile_id="nemotron-3-super-120b-llama-sidecar",
        log_file_path=NEMOTRON_SUPER_LOG_FILE,
        launch_recipe=sidecar_launch_recipe(
            container_name="dockyard-moe-nemotron-3-super-120b-sidecar",
            host_port="18082",
            sidecar_label="nemotron-3-super-120b-sidecar-cpu-mmap-baseline",
            model_filename="nemotron-3-super-120b.gguf",
            log_filename="nemotron-3-super-120b.log",
            ctx_size="256",
            no_mmap=False,
            extra_runtime_args=("--no-warmup",),
        ),
        expected_artifacts=LLAMA_CPP_ARTIFACTS,
        limitations=LLAMA_CPP_RUNTIME_LIMITATIONS
        + (
            "This is an 86.8 GB GGUF; run it sequentially after stopping smaller resident sidecars.",
            "The card uses mmap and no warmup to make startup evidence safer than a full prompt smoke.",
        ),
        prerequisites=(
            "memory-moe-llama-sidecar:latest image is built on the target host",
            "/mnt/Calliope/models/nemotron-3-super-120b.gguf exists or the profile is edited to a real GGUF path",
            "Stop other large sidecars before launch unless the host has enough available memory.",
        ),
        hardware_note=(
            "Largest copied true-MoE GGUF on Calliope: nemotron_h_moe with 512 experts and 22 routed experts. "
            "Use for startup/preflight evidence first; prompt smoke may require freeing resident models."
        ),
        purpose="High-memory Nemotron-H MoE card to test whether stock llama.cpp sidecar evidence scales beyond Mixtral/Qwen3.",
    ),
    MoeTestCard(
        card_id="vllm-nemotron-omni-30b-a3b-nvfp4",
        title="Nemotron Omni 30B A3B NVFP4 vLLM",
        model="nemotron_3_nano_omni",
        label="nemotron-omni-30b-a3b-vllm",
        model_class="nemotron_h_moe",
        card_type="openai_compatible_runtime_baseline",
        evidence_level="vllm_openai_compatible_runtime",
        probe_tier="request_boundary_plus_metrics",
        backend_family="vllm_openai_compatible",
        base_url=VLLM_NEMOTRON_OMNI_BASE_URL,
        profile_id="nemotron-omni-nvfp4-vllm",
        expected_artifacts=RUNTIME_ARTIFACTS + ("vLLM /metrics when available",),
        limitations=VLLM_RUNTIME_LIMITATIONS,
        prerequisites=(
            "The nemotron-omni-nvfp4-vllm profile is launched and healthy",
            "/mnt/Calliope/models/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4-hf is present",
            "GPU-backed vLLM Docker access is available on the target host",
        ),
        hardware_note=(
            "HF/NVFP4 MoE model with custom Nemotron-H code: 128 routed experts, 6 experts per token. "
            "This is the runnable backend comparison for Nemotron when stock llama.cpp cannot load nemotron_h_moe GGUF."
        ),
        purpose="Compare a runnable vLLM Nemotron-H MoE path against llama.cpp Mixtral/Qwen3 sidecar evidence.",
    ),
    MoeTestCard(
        card_id="ollama-dolphin-mixtral-8x7b",
        title="Dolphin Mixtral 8x7B Opaque Baseline",
        model="dolphin-mixtral:8x7b",
        label="dolphin-mixtral-8x7b-smoke",
        model_class="mixtral_style",
        card_type="opaque_runtime_baseline",
        evidence_level="opaque_openai_compatible_runtime",
        probe_tier="request_boundary",
        hardware_note="Freshly pulled on GX10 Ollama; use as the original Mixtral-family runtime baseline anchor.",
        purpose="Primary MoE Run Anyway opaque-runtime baseline for the Mixtral coverage prompt suite.",
        expected_artifacts=RUNTIME_ARTIFACTS,
        limitations=OPAQUE_RUNTIME_LIMITATIONS,
    ),
    MoeTestCard(
        card_id="ollama-qwen3-30b",
        title="Qwen3 30B MoE Opaque Smoke",
        model="qwen3:30b",
        label="qwen3-30b-smoke",
        model_class="qwen3moe",
        card_type="opaque_runtime_baseline",
        evidence_level="opaque_openai_compatible_runtime",
        probe_tier="request_boundary",
        hardware_note="Validated as an Ollama GGUF smoke target on the GX10 class host.",
        purpose="Fastest current sparse-model smoke for MoE Run Anyway runtime baseline artifacts.",
        expected_artifacts=RUNTIME_ARTIFACTS,
        limitations=OPAQUE_RUNTIME_LIMITATIONS,
    ),
    MoeTestCard(
        card_id="ollama-qwen36-27b",
        title="Qwen3.6 27B Opaque Runtime Smoke",
        model="qwen3.6:27b",
        label="qwen36-27b-smoke",
        model_class="qwen35",
        card_type="opaque_runtime_baseline",
        evidence_level="opaque_openai_compatible_runtime",
        probe_tier="request_boundary",
        hardware_note="Current Ollama GGUF target; useful dense/runtime comparison near the MoE size tier.",
        purpose="Second baseline to compare OpenAI-compatible runtime behavior across model families.",
        expected_artifacts=RUNTIME_ARTIFACTS,
        limitations=OPAQUE_RUNTIME_LIMITATIONS,
    ),
    MoeTestCard(
        card_id="ollama-gemma4-31b",
        title="Gemma 31B Opaque Runtime Smoke",
        model="gemma4:31b",
        label="gemma4-31b-smoke",
        model_class="gemma4",
        card_type="opaque_runtime_baseline",
        evidence_level="opaque_openai_compatible_runtime",
        probe_tier="request_boundary",
        hardware_note="Current Ollama GGUF target; known to load on available GPU memory after large vLLM jobs are stopped.",
        purpose="Third baseline to keep MoE Run Anyway tests honest against a non-Qwen runtime target.",
        expected_artifacts=RUNTIME_ARTIFACTS,
        limitations=OPAQUE_RUNTIME_LIMITATIONS,
    ),
    MoeTestCard(
        card_id="android-pocketpal-baseline",
        title="Android PocketPal Physical Baseline",
        model="user-selected-gguf",
        label="android-pocketpal-baseline",
        model_class="mobile_gguf",
        card_type="edge_manual_baseline",
        evidence_level="physical_android_runtime_baseline",
        probe_tier="manual_edge_runtime",
        backend_family="android_pocketpal",
        base_url="",
        target_class="android_physical_device",
        runtime_stack="pocketpal_gguf_llama_cpp_class",
        execution_mode="manual_evidence",
        expected_artifacts=EDGE_MANUAL_ARTIFACTS,
        limitations=ANDROID_EDGE_LIMITATIONS,
        evidence_limits=ANDROID_EDGE_LIMITATIONS,
        prerequisites=(
            "Screen-repaired physical Android phone is available",
            "PocketPal or equivalent on-device GGUF app is operator-installed outside Model Plane",
            "Exact model file, quant, and app/runtime version are recorded manually",
        ),
        hardware_note="Use this first after screen repair; do not substitute emulator measurements for throughput, battery, or thermals.",
        purpose="Physical-device baseline for an external PocketPal/GGUF runtime before any raw Android device workflow is planned.",
    ),
    MoeTestCard(
        card_id="android-adb-llama-cpp-baseline",
        title="Android ADB llama.cpp Baseline",
        model="user-selected-gguf",
        label="android-adb-llama-cpp-baseline",
        model_class="mobile_gguf",
        card_type="edge_reserved_baseline",
        evidence_level="physical_android_direct_llama_cpp_reserved",
        probe_tier="manual_edge_runtime",
        backend_family="android_llama_cpp",
        base_url="",
        target_class="android_physical_device",
        runtime_stack="adb_llama_cpp_direct_reserved",
        execution_mode="manual_evidence",
        expected_artifacts=EDGE_MANUAL_ARTIFACTS,
        limitations=ANDROID_EDGE_LIMITATIONS
        + ("Direct ADB execution is intentionally not automated by this pre-work card.",),
        evidence_limits=ANDROID_EDGE_LIMITATIONS,
        prerequisites=(
            "Physical Android phone is available and developer options can be enabled later",
            "ADB/direct llama.cpp benchmark plan has been reviewed before any device command is run",
            "Exact binary, model file, and command line are captured manually if tested",
        ),
        hardware_note="Reserved for a later raw Android llama.cpp/ADB path; this card only records manually gathered evidence.",
        purpose="Placeholder evidence tier for raw Android llama.cpp benchmarks after the physical baseline is understood.",
    ),
    MoeTestCard(
        card_id="android-emulator-ui-only",
        title="Android Emulator UI Only",
        model="not-applicable",
        label="android-emulator-ui-only",
        model_class="mobile_ui",
        card_type="edge_ui_only",
        evidence_level="android_emulator_ui_connectivity_only",
        probe_tier="manual_ui_connectivity",
        backend_family="android_emulator",
        base_url="",
        target_class="android_emulator",
        runtime_stack="android_emulator_ui_connectivity",
        execution_mode="manual_evidence",
        expected_artifacts=EDGE_MANUAL_ARTIFACTS,
        limitations=ANDROID_EMULATOR_LIMITATIONS,
        evidence_limits=ANDROID_EMULATOR_LIMITATIONS,
        prerequisites=(
            "Android emulator is used only for app UI, install-flow, permission, or network checks",
            "No inference performance claim is recorded from emulator data",
        ),
        hardware_note="Use this only for UI/connectivity rehearsal; it is not an edge inference benchmark.",
        purpose="Keep emulator work available without confusing it with physical edge inference evidence.",
    ),
]


def candidate_moe_roots() -> list[Path]:
    configured = os.environ.get(MOE_RUN_ANYWAY_ROOT_ENV, "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            REPO_ROOT.parent / "moe-run-anyway-github-upload",
            Path.home() / "moe-run-anyway-bridge-work",
            REPO_ROOT.parent / "moe-run-anyway-bridge-work",
            Path("/home/codexlab/moe-run-anyway-bridge-work"),
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def is_moe_root(path: Path) -> bool:
    return (path / RUNNER_RELATIVE).is_file() and (path / DEFAULT_SUITE_RELATIVE).is_file()


def find_moe_root() -> Path | None:
    for candidate in candidate_moe_roots():
        if is_moe_root(candidate):
            return candidate
    return None


def output_dir() -> Path:
    configured = os.environ.get(MOE_TEST_OUTPUT_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(tempfile.gettempdir()) / "model-plane-moe-test-runs"


def manual_evidence_output_dir() -> Path:
    configured = os.environ.get(MOE_TEST_OUTPUT_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return PROJECT_LOCAL_OUTPUT_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact_local_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "manual-edge"


def moe_root_status(root: Path | None = None) -> dict[str, Any]:
    selected = root or find_moe_root()
    return {
        "available": selected is not None,
        "path": str(selected) if selected else None,
        "env_var": MOE_RUN_ANYWAY_ROOT_ENV,
        "checked_candidates": [str(candidate) for candidate in candidate_moe_roots()],
    }


def card_by_id(card_id: str) -> MoeTestCard | None:
    for card in MOE_TEST_CARDS:
        if card.card_id == card_id:
            return card
    return None


def build_command(card: MoeTestCard, root: Path, mode: str) -> list[str]:
    command = [
        sys.executable,
        str(root / RUNNER_RELATIVE),
        "--base-url",
        card.base_url,
        "--backend-family",
        card.backend_family,
        "--model",
        card.model,
        "--output-dir",
        str(output_dir()),
        "--label",
        card.label,
        "--suite-path",
        str(root / DEFAULT_SUITE_RELATIVE),
        "--max-prompts",
        str(card.max_prompts),
        "--repeats",
        str(card.repeats),
        "--preflight-timeout-seconds",
        "3",
        "--timeout-seconds",
        "300",
        "--json",
    ]
    if card.log_file_path is not None:
        command.extend(["--log-file-path", str(card.log_file_path)])
    if mode == "preflight":
        command.append("--preflight-only")
    return command


def launch_command_record(card: MoeTestCard) -> dict[str, Any] | None:
    if not card.launch_recipe:
        return None
    command = list(card.launch_recipe)
    return {
        "argv": command,
        "shell_command": shlex.join(command),
        "mode": "launch",
        "sends_prompt_traffic": False,
        "executes_from_test_card": False,
    }


def command_record(card: MoeTestCard, root: Path | None, mode: str) -> dict[str, Any]:
    command = build_command(card, root, mode) if root else []
    return {
        "argv": command,
        "shell_command": shlex.join(command) if command else None,
        "mode": mode,
        "sends_prompt_traffic": mode == "smoke",
    }


def manual_evidence_schema(card: MoeTestCard) -> dict[str, Any] | None:
    if card.execution_mode != "manual_evidence":
        return None
    return {
        "schema_version": MANUAL_EVIDENCE_SCHEMA_VERSION,
        "fields": list(MANUAL_EVIDENCE_FIELDS),
        "approval_field": "approved_manual_evidence",
        "max_payload_bytes": MANUAL_EVIDENCE_MAX_BYTES,
        "writes_prompt_traffic": False,
        "writes_device_commands": False,
    }


def phase3_dense_capture_runner_available(root: Path | None) -> bool:
    return root is not None and (root / PHASE3_DENSE_CAPTURE_RELATIVE).is_file()


def phase3_artifact_writer_descriptors(card: MoeTestCard, root: Path | None = None) -> list[dict[str, Any]]:
    if card.execution_mode != "runner":
        return []
    dense_capture_ready = phase3_dense_capture_runner_available(root)
    descriptors: list[dict[str, Any]] = []
    for item in PHASE3_CAPTURE_ARTIFACTS:
        artifact_id = item["artifact_id"]
        runtime_ready = artifact_id == "dense_output_summary_fill" and dense_capture_ready
        descriptors.append(
            {
                "schema_version": PHASE3_ARTIFACT_WRITER_SCHEMA_VERSION,
                "function_id": "moe.test_card.phase3_capture",
                "endpoint": f"/moe-test-cards/{card.card_id}/phase3-capture",
                "card_id": card.card_id,
                "profile_id": card.profile_id,
                "artifact_id": artifact_id,
                "capture_kind": item["capture_kind"],
                "receipt_kind": item["receipt_kind"],
                "approval_keys": list(item["approval_keys"]),
                "accepts_runtime_capture_request_path": True,
                "accepts_prompt_set_path": True,
                "writes_explicit_artifact_path": True,
                "writes_explicit_receipt_path": True,
                "requires_explicit_user_approval": True,
                "may_send_prompt_traffic": True,
                "runtime_execution_ready": runtime_ready,
                "writes_runtime_artifacts": runtime_ready,
                "implementation_status": (
                    "dense_output_runtime_writer_ready"
                    if runtime_ready
                    else "contract_ready_runtime_execution_pending"
                ),
                "blockers": [] if runtime_ready else ["phase3_runtime_artifact_writer_execution_not_implemented"],
            }
        )
    return descriptors


def phase3_capture_descriptor_for(card: MoeTestCard, artifact_id: str, root: Path | None = None) -> dict[str, Any] | None:
    for descriptor in phase3_artifact_writer_descriptors(card, root):
        if descriptor["artifact_id"] == artifact_id:
            return descriptor
    return None


def phase3_path_is_safe(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace("\\", "/")
    if Path(value).is_absolute() or normalized.startswith("/"):
        return False
    if ":" in normalized.split("/", 1)[0]:
        return False
    return ".." not in Path(normalized).parts


def require_phase3_path(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required.")
    if not phase3_path_is_safe(value.strip()):
        raise ValueError(f"{name} must be a repo-relative safe path.")
    return value.strip()


def phase3_dense_capture_command(
    card: MoeTestCard,
    root: Path,
    *,
    runtime_capture_request_path: str,
    prompt_set_path: str,
    artifact_output_path: str,
    receipt_output_path: str,
    request_max_tokens: Any = None,
    notes: Any = None,
) -> list[str]:
    command = [
        sys.executable,
        str(root / PHASE3_DENSE_CAPTURE_RELATIVE),
        runtime_capture_request_path,
        "--prompt-set-path",
        prompt_set_path,
        "--artifact-output",
        artifact_output_path,
        "--receipt-output",
        receipt_output_path,
        "--base-url",
        card.base_url,
        "--backend-family",
        card.backend_family,
        "--model",
        card.model,
        "--approved-runtime-prompt-traffic",
        "--approved-dense-output-capture",
        "--json",
    ]
    if isinstance(request_max_tokens, int) and request_max_tokens > 0:
        command.extend(["--request-max-tokens", str(request_max_tokens)])
    if isinstance(notes, str) and notes.strip():
        command.extend(["--operator-notes", notes.strip()])
    return command


def run_phase3_dense_capture(
    card: MoeTestCard,
    root: Path,
    *,
    runtime_capture_request_path: str,
    prompt_set_path: str,
    artifact_output_path: str,
    receipt_output_path: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    command = phase3_dense_capture_command(
        card,
        root,
        runtime_capture_request_path=runtime_capture_request_path,
        prompt_set_path=prompt_set_path,
        artifact_output_path=artifact_output_path,
        receipt_output_path=receipt_output_path,
        request_max_tokens=request.get("request_max_tokens"),
        notes=request.get("notes"),
    )
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=1800, check=False)
    parsed_stdout: dict[str, Any] | None = None
    if completed.stdout.strip():
        try:
            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict):
                parsed_stdout = parsed
        except json.JSONDecodeError:
            parsed_stdout = None
    ok = completed.returncode == 0 and isinstance(parsed_stdout, dict) and parsed_stdout.get("ok") is True
    return {
        "ok": ok,
        "returncode": completed.returncode,
        "command": command,
        "shell_command": shlex.join(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed_stdout": parsed_stdout,
    }


def phase3_capture_plan_result(
    *,
    card: MoeTestCard,
    descriptor: dict[str, Any],
    artifact_id: str,
    runtime_capture_request_path: str,
    prompt_set_path: str,
    artifact_output_path: str,
    receipt_output_path: str,
    approval_key_set: set[str],
) -> dict[str, Any]:
    runtime_ready = descriptor.get("runtime_execution_ready") is True
    blockers = list(descriptor.get("blockers") or [])
    if runtime_ready:
        blockers = ["phase3_capture_execute_flag_not_set"]
    return {
        "ok": False,
        "schema_version": PHASE3_CAPTURE_RESULT_SCHEMA_VERSION,
        "card_id": card.card_id,
        "mode": "phase3_artifact_capture",
        "accepted_contract": True,
        "runtime_execution_ready": runtime_ready,
        "write_performed": False,
        "prompt_traffic_sent_by_model_plane": False,
        "approved_phase3_capture": True,
        "approved_prompt_traffic": True,
        "artifact_id": artifact_id,
        "capture_kind": descriptor["capture_kind"],
        "receipt_kind": descriptor["receipt_kind"],
        "runtime_capture_request_path": runtime_capture_request_path,
        "prompt_set_path": prompt_set_path,
        "artifact_output_path": artifact_output_path,
        "receipt_output_path": receipt_output_path,
        "approval_keys": sorted(approval_key_set),
        "descriptor": descriptor,
        "implementation_status": descriptor.get("implementation_status"),
        "blockers": blockers,
        "next_actions": (
            ["Call this endpoint with execute=true after explicit approval to write the dense output summary."]
            if runtime_ready
            else [
                "Implement runtime execution that sends the approved prompt set to the selected card runtime.",
                "Write exactly artifact_output_path and receipt_output_path from the request payload.",
                "Keep this artifact non-command-ready until runtime execution and receipt writing are tested.",
            ]
        ),
        "safety_contract": [
            "Phase 3 capture validation does not run unless execute=true is provided.",
            "Phase 3 dense execution sends prompt traffic only after explicit approval flags.",
            "Phase 3 dense execution writes exactly artifact_output_path, with the receipt embedded in the same file.",
            "Candidate router traces and managed-output summaries remain blocked until their runtime writers exist.",
        ],
    }


def plan_phase3_artifact_capture(card_id: str, request: dict[str, Any]) -> dict[str, Any]:
    card = card_by_id(card_id)
    if card is None:
        raise KeyError(card_id)
    if card.execution_mode != "runner":
        raise PermissionError(f"{card.card_id} is a {card.execution_mode} card and cannot run Phase 3 capture.")
    if request.get("approved_phase3_capture") is not True:
        raise PermissionError("Phase 3 artifact capture requires explicit approval.")
    if request.get("approved_prompt_traffic") is not True:
        raise PermissionError("Phase 3 artifact capture may send prompt traffic and requires explicit approval.")

    root = find_moe_root()
    artifact_id = str(request.get("artifact_id") or "")
    descriptor = phase3_capture_descriptor_for(card, artifact_id, root)
    if descriptor is None:
        raise ValueError(f"Unsupported Phase 3 artifact_id for {card.card_id}: {artifact_id or 'missing'}")
    if request.get("capture_kind") != descriptor["capture_kind"]:
        raise ValueError("capture_kind does not match the Phase 3 artifact descriptor.")
    if request.get("receipt_kind") != descriptor["receipt_kind"]:
        raise ValueError("receipt_kind does not match the Phase 3 artifact descriptor.")
    approval_keys = request.get("approval_keys") if isinstance(request.get("approval_keys"), list) else []
    approval_key_set = {str(item) for item in approval_keys if isinstance(item, str)}
    expected_keys = set(descriptor["approval_keys"])
    if not expected_keys.issubset(approval_key_set):
        raise ValueError("approval_keys must include the expected Phase 3 approval keys.")

    runtime_capture_request_path = require_phase3_path(
        "runtime_capture_request_path",
        request.get("runtime_capture_request_path"),
    )
    prompt_set_path = require_phase3_path("prompt_set_path", request.get("prompt_set_path"))
    artifact_output_path = require_phase3_path("artifact_output_path", request.get("artifact_output_path"))
    receipt_output_path = require_phase3_path("receipt_output_path", request.get("receipt_output_path"))

    plan = phase3_capture_plan_result(
        card=card,
        descriptor=descriptor,
        artifact_id=artifact_id,
        runtime_capture_request_path=runtime_capture_request_path,
        prompt_set_path=prompt_set_path,
        artifact_output_path=artifact_output_path,
        receipt_output_path=receipt_output_path,
        approval_key_set=approval_key_set,
    )
    if request.get("execute") is not True:
        return plan
    if descriptor.get("runtime_execution_ready") is not True:
        raise PermissionError(f"{artifact_id} does not have a runtime artifact writer yet.")
    if artifact_id != "dense_output_summary_fill":
        raise PermissionError(f"{artifact_id} cannot be executed by the dense output writer.")
    if root is None:
        raise PermissionError("MoE Run Anyway root is not available for Phase 3 execution.")

    execution = run_phase3_dense_capture(
        card,
        root,
        runtime_capture_request_path=runtime_capture_request_path,
        prompt_set_path=prompt_set_path,
        artifact_output_path=artifact_output_path,
        receipt_output_path=receipt_output_path,
        request=request,
    )
    parsed = execution.get("parsed_stdout") if isinstance(execution.get("parsed_stdout"), dict) else {}
    return {
        **plan,
        "ok": execution["ok"],
        "runtime_execution_ready": True,
        "write_performed": parsed.get("write_performed") is True,
        "prompt_traffic_sent_by_model_plane": parsed.get("prompt_traffic_sent") is True,
        "implementation_status": "dense_output_runtime_writer_executed" if execution["ok"] else "dense_output_runtime_writer_failed",
        "blockers": [] if execution["ok"] else ["phase3_dense_output_capture_failed"],
        "execution": execution,
        "dense_capture_summary": parsed,
    }


def list_moe_test_cards() -> list[dict[str, Any]]:
    root = find_moe_root()
    root_status = moe_root_status(root)
    cards = []
    for card in MOE_TEST_CARDS:
        phase3_writers = phase3_artifact_writer_descriptors(card, root)
        cards.append(
            {
                "card_id": card.card_id,
                "title": card.title,
                "model": card.model,
                "model_class": card.model_class,
                "card_type": card.card_type,
                "evidence_level": card.evidence_level,
                "probe_tier": card.probe_tier,
                "backend_family": card.backend_family,
                "base_url": card.base_url,
                "target_class": card.target_class,
                "runtime_stack": card.runtime_stack,
                "execution_mode": card.execution_mode,
                "requires_moe_checkout": card.execution_mode == "runner",
                "supports_manual_evidence": card.execution_mode == "manual_evidence",
                "label": card.label,
                "profile_id": card.profile_id,
                "purpose": card.purpose,
                "hardware_note": card.hardware_note,
                "suite": str(DEFAULT_SUITE_RELATIVE),
                "max_prompts": card.max_prompts,
                "repeats": card.repeats,
                "log_file_path": str(card.log_file_path) if card.log_file_path else None,
                "moe_root": root_status,
                "output_dir": str(output_dir() if card.execution_mode == "runner" else manual_evidence_output_dir()),
                "launch_command": launch_command_record(card),
                "preflight_command": command_record(card, root, "preflight") if card.execution_mode == "runner" else None,
                "smoke_command": command_record(card, root, "smoke") if card.execution_mode == "runner" else None,
                "manual_evidence_endpoint": (
                    f"/moe-test-cards/{card.card_id}/manual-evidence"
                    if card.execution_mode == "manual_evidence"
                    else None
                ),
                "manual_evidence_schema": manual_evidence_schema(card),
                "phase3_capture_endpoint": f"/moe-test-cards/{card.card_id}/phase3-capture" if phase3_writers else None,
                "phase3_artifact_writers": phase3_writers,
                "phase3_artifact_writer_contract_count": len(phase3_writers),
                "phase3_artifact_writer_ready_count": sum(1 for writer in phase3_writers if writer.get("runtime_execution_ready") is True),
                "expected_artifacts": list(card.expected_artifacts),
                "limitations": list(card.limitations),
                "prerequisites": list(card.prerequisites),
                "evidence_limits": list(card.evidence_limits),
                "safety_notes": [
                    (
                        "Manual evidence recording writes operator-provided measurements only."
                        if card.execution_mode == "manual_evidence"
                        else "Preflight checks endpoint readiness only."
                    ),
                    (
                        "Manual edge cards do not call Docker, ADB, install apps, move model weights, or send prompt traffic."
                        if card.execution_mode == "manual_evidence"
                        else "Smoke sends one prompt from the Mixtral coverage suite to the selected model."
                    ),
                    *card.limitations,
                ],
            }
        )
    return cards


def run_card(card_id: str, mode: str, approved_prompt_traffic: bool = False) -> dict[str, Any]:
    card = card_by_id(card_id)
    if card is None:
        raise KeyError(card_id)
    if card.execution_mode != "runner":
        raise PermissionError(f"{card.card_id} is a {card.execution_mode} card and cannot run {mode}.")
    if mode == "smoke" and not approved_prompt_traffic:
        raise PermissionError("Smoke tests send prompt traffic and require explicit approval.")
    root = find_moe_root()
    if root is None:
        raise FileNotFoundError("MoE Run Anyway checkout was not found.")
    command = build_command(card, root, mode)
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=360, check=False)
    parsed_stdout: Any = None
    if result.stdout.strip():
        try:
            parsed_stdout = json.loads(result.stdout)
        except json.JSONDecodeError:
            parsed_stdout = None
    return {
        "ok": result.returncode == 0,
        "card_id": card.card_id,
        "mode": mode,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "parsed_stdout": parsed_stdout,
        "command": command_record(card, root, mode),
        "moe_root": moe_root_status(root),
    }


def normalize_manual_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(evidence)
    normalized.setdefault("device_label", None)
    normalized.setdefault("app_runtime", None)
    normalized.setdefault("model_id", None)
    return normalized


def manual_evidence_payload_size(evidence: dict[str, Any], notes: str | None) -> int:
    payload = {"evidence": evidence, "notes": notes}
    return len(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))


def record_manual_evidence(
    card_id: str,
    evidence: dict[str, Any],
    *,
    approved_manual_evidence: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    card = card_by_id(card_id)
    if card is None:
        raise KeyError(card_id)
    if card.execution_mode != "manual_evidence":
        raise PermissionError(f"{card.card_id} does not accept manual evidence.")
    if not approved_manual_evidence:
        raise PermissionError("Manual edge evidence requires explicit approval.")
    if not isinstance(evidence, dict):
        raise ValueError("Manual evidence must be a JSON object.")
    payload_size = manual_evidence_payload_size(evidence, notes)
    if payload_size > MANUAL_EVIDENCE_MAX_BYTES:
        raise ValueError(f"Manual evidence payload exceeds {MANUAL_EVIDENCE_MAX_BYTES} bytes.")

    created_at = utc_now()
    run_id = f"{compact_local_timestamp()}-{slug(card.label)}-manual-edge-{uuid.uuid4().hex[:8]}"
    run_dir = manual_evidence_output_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    normalized = normalize_manual_evidence(evidence)
    common = {
        "schema_version": MANUAL_EVIDENCE_SCHEMA_VERSION,
        "created_at": created_at,
        "run_id": run_id,
        "card_id": card.card_id,
        "title": card.title,
        "mode": "edge_manual_evidence",
        "target_class": card.target_class,
        "runtime_stack": card.runtime_stack,
        "execution_mode": card.execution_mode,
        "backend_family": card.backend_family,
        "model": card.model,
        "model_class": card.model_class,
        "probe_tier": card.probe_tier,
        "evidence_level": card.evidence_level,
        "semantic_expert_ids": "not_exposed",
        "hookable_runtime_available": False,
        "prompt_traffic_sent_by_model_plane": False,
        "device_commands_run_by_model_plane": False,
        "evidence_limits": list(card.evidence_limits),
        "notes": notes,
    }
    manual_payload = {
        **common,
        "manual_evidence": normalized,
        "raw_manual_evidence": evidence,
        "payload_size_bytes": payload_size,
    }
    manifest = {
        **common,
        "config": {
            "label": card.label,
            "manual_evidence_schema": manual_evidence_schema(card),
            "expected_artifacts": list(card.expected_artifacts),
        },
    }
    summary = {
        **common,
        "manual_evidence": normalized,
        "totals": {
            "manual_measurement_count": 1,
            "tokens_per_second": normalized.get("tokens_per_second"),
            "time_to_first_token_ms": normalized.get("time_to_first_token_ms"),
        },
    }
    event = {
        "timestamp": created_at,
        "event_type": "edge_manual_evidence_recorded",
        "card_id": card.card_id,
        "target_class": card.target_class,
        "runtime_stack": card.runtime_stack,
        "manual_evidence": normalized,
        "semantic_expert_ids": "not_exposed",
        "hookable_runtime_available": False,
    }
    artifacts = {
        "manifest": run_dir / "manifest.json",
        "summary": run_dir / "summary.json",
        "events": run_dir / "events.jsonl",
        "manual_evidence": run_dir / "manual-evidence.json",
    }
    artifacts["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts["summary"].write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts["manual_evidence"].write_text(json.dumps(manual_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts["events"].write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "card_id": card.card_id,
        "mode": "manual_evidence",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "artifacts": {key: str(path) for key, path in artifacts.items()},
        "summary": summary,
        "sends_prompt_traffic": False,
        "device_commands_run": False,
    }

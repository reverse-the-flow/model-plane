from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
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
DOLPHIN_MIXTRAL_LOG_FILE = Path("/home/codexlab/model-plane-runtime/logs/llama-cpp/dolphin-mixtral-8x7b.log")
QWEN3_30B_LOG_FILE = Path("/home/codexlab/model-plane-runtime/logs/llama-cpp/qwen3-30b.log")
NEMOTRON_SUPER_LOG_FILE = Path("/home/codexlab/model-plane-runtime/logs/llama-cpp/nemotron-3-super-120b.log")


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


def list_moe_test_cards() -> list[dict[str, Any]]:
    root = find_moe_root()
    root_status = moe_root_status(root)
    cards = []
    for card in MOE_TEST_CARDS:
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
                "label": card.label,
                "profile_id": card.profile_id,
                "purpose": card.purpose,
                "hardware_note": card.hardware_note,
                "suite": str(DEFAULT_SUITE_RELATIVE),
                "max_prompts": card.max_prompts,
                "repeats": card.repeats,
                "log_file_path": str(card.log_file_path) if card.log_file_path else None,
                "moe_root": root_status,
                "output_dir": str(output_dir()),
                "launch_command": launch_command_record(card),
                "preflight_command": command_record(card, root, "preflight"),
                "smoke_command": command_record(card, root, "smoke"),
                "expected_artifacts": list(card.expected_artifacts),
                "limitations": list(card.limitations),
                "prerequisites": list(card.prerequisites),
                "safety_notes": [
                    "Preflight checks endpoint readiness only.",
                    "Smoke sends one prompt from the Mixtral coverage suite to the selected model.",
                    *card.limitations,
                ],
            }
        )
    return cards


def run_card(card_id: str, mode: str, approved_prompt_traffic: bool = False) -> dict[str, Any]:
    card = card_by_id(card_id)
    if card is None:
        raise KeyError(card_id)
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

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TextIO


SEARCH_ORDER = [
    "gpu_layers",
    "context_size",
    "batch_size",
    "micro_batch",
    "kv_cache_type",
    "flash_attn",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
]

LLAMA_CPP_SUPPORTED = {
    "gpu_layers",
    "context_size",
    "batch_size",
    "micro_batch",
    "kv_cache_type",
    "flash_attn",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
}

OLLAMA_SUPPORTED = {
    "context_size",
    "batch_size",
    "flash_attn",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
}

GENERATION_OPTION_KEYS = [
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
]

WORD_RE = re.compile(r"[A-Za-z0-9']+")
FORMAL_MARKERS = {
    "therefore",
    "however",
    "moreover",
    "furthermore",
    "indeed",
    "thus",
    "shall",
    "must",
    "consequently",
    "accordingly",
}
FIRST_PERSON_MARKERS = {"i", "i'm", "i've", "i'd", "me", "my", "mine", "we", "we're", "our", "ours"}


@dataclass
class PromptCase:
    name: str
    prompt: str
    max_tokens: int
    temperature: float | None = None
    repetitions: int = 1
    target_words: int | None = None
    generation_overrides: dict[str, Any] | None = None
    think: bool | str | None = None


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: pathlib.Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def slugify(value: str) -> str:
    allowed = []
    for char in value.lower():
        if char.isalnum():
            allowed.append(char)
        elif char in {" ", "-", "_", "."}:
            allowed.append("-")
    text = "".join(allowed).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return text or "run"


def split_command(command: str | list[str]) -> list[str]:
    if isinstance(command, list):
        return [str(part) for part in command]
    return shlex.split(command, posix=(os.name != "nt"))


def resolve_path(base_dir: pathlib.Path, raw_path: str | None) -> pathlib.Path | None:
    if not raw_path:
        return None
    path = pathlib.Path(raw_path)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def looks_like_oom(message: str) -> bool:
    lowered = message.lower()
    return "out of memory" in lowered or "cuda error 2" in lowered or "oom" in lowered


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalize_range(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return clamp01((value - low) / (high - low))


def safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def preview_text(text: str, limit: int = 220) -> str:
    squashed = " ".join(text.split())
    if len(squashed) <= limit:
        return squashed
    return f"{squashed[: limit - 3]}..."


def compute_style_metrics(text: str, target_words: int | None = None) -> dict[str, float | int]:
    words = WORD_RE.findall(text)
    lowered_words = [word.lower() for word in words]
    word_count = len(lowered_words)
    unique_ratio = safe_div(len(set(lowered_words)), word_count)
    long_word_ratio = safe_div(sum(1 for word in lowered_words if len(word) >= 9), word_count)
    punctuation_ratio = safe_div(sum(text.count(mark) for mark in "!?;:"), max(word_count, 1))
    newline_count = text.count("\n")
    formal_ratio = safe_div(sum(1 for word in lowered_words if word in FORMAL_MARKERS), word_count)
    first_person_ratio = safe_div(sum(1 for word in lowered_words if word in FIRST_PERSON_MARKERS), word_count)
    contraction_ratio = safe_div(sum(1 for word in words if "'" in word), word_count)
    sentence_lengths = [
        len(WORD_RE.findall(sentence))
        for sentence in re.split(r"[.!?]+", text)
        if WORD_RE.search(sentence)
    ]
    avg_sentence_words = safe_div(sum(sentence_lengths), len(sentence_lengths))

    weirdness = clamp01(
        0.45 * normalize_range(unique_ratio, 0.35, 0.75)
        + 0.25 * normalize_range(long_word_ratio, 0.03, 0.18)
        + 0.20 * normalize_range(punctuation_ratio, 0.0, 0.08)
        + 0.10 * normalize_range(float(newline_count), 0.0, 8.0)
    )

    if target_words is not None:
        verbosity = normalize_range(float(word_count), max(12.0, target_words * 0.4), max(80.0, target_words * 1.6))
    else:
        verbosity = normalize_range(float(word_count), 20.0, 260.0)

    stiffness = clamp01(
        0.35 * normalize_range(formal_ratio, 0.0, 0.06)
        + 0.25 * normalize_range(avg_sentence_words, 8.0, 28.0)
        + 0.20 * (1.0 - normalize_range(contraction_ratio, 0.0, 0.08))
        + 0.20 * (1.0 - normalize_range(first_person_ratio, 0.0, 0.08))
    )

    return {
        "word_count": word_count,
        "unique_ratio": round(unique_ratio, 4),
        "avg_sentence_words": round(avg_sentence_words, 2),
        "weirdness": round(weirdness, 4),
        "verbosity": round(verbosity, 4),
        "stiffness": round(stiffness, 4),
    }


def http_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            parsed = None
            if body:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = None
            return {
                "status": response.status,
                "text": body,
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request to {url} failed: {exc}") from exc


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    response = http_request(method, url, payload, timeout)
    if response["json"] is None:
        raise RuntimeError(f"Expected JSON from {url} but received non-JSON content.")
    return response["json"]


class NvidiaMemoryMonitor:
    def __init__(self, command: str | None, sample_interval_ms: int) -> None:
        self.command = command
        self.sample_interval = max(sample_interval_ms, 100) / 1000.0
        self.peak_mib: int | None = None
        self.available = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.command:
            return
        first_value = self._sample_once()
        if first_value is None:
            return
        self.available = True
        self.peak_mib = first_value
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.available:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            value = self._sample_once()
            if value is not None:
                if self.peak_mib is None:
                    self.peak_mib = value
                else:
                    self.peak_mib = max(self.peak_mib, value)
            self._stop.wait(self.sample_interval)

    def _sample_once(self) -> int | None:
        try:
            command = split_command(self.command or "")
            command.extend(
                [
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ]
            )
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        values = []
        for line in result.stdout.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            try:
                values.append(int(float(cleaned.split()[0])))
            except ValueError:
                continue
        if not values:
            return None
        return max(values)


class BackendBase:
    supported_dimensions: set[str] = set()

    def __init__(
        self,
        root_config: dict[str, Any],
        trial_config: dict[str, Any],
        run_dir: pathlib.Path,
        trial_index: int,
    ) -> None:
        self.root_config = root_config
        self.trial_config = trial_config
        self.run_dir = run_dir
        self.trial_index = trial_index

    def launch(self) -> None:
        return None

    def warmup(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def run_prompt(self, prompt_case: PromptCase) -> dict[str, Any]:
        raise NotImplementedError

    def generation_options(self, prompt_case: PromptCase) -> dict[str, Any]:
        options: dict[str, Any] = {}
        for key in GENERATION_OPTION_KEYS:
            value = self.trial_config.get(key)
            if value is not None:
                options[key] = value
        if prompt_case.temperature is not None:
            options["temperature"] = float(prompt_case.temperature)
        if prompt_case.generation_overrides:
            options.update(prompt_case.generation_overrides)
        return options


class LlamaCppServerBackend(BackendBase):
    supported_dimensions = LLAMA_CPP_SUPPORTED

    def __init__(
        self,
        root_config: dict[str, Any],
        trial_config: dict[str, Any],
        run_dir: pathlib.Path,
        trial_index: int,
    ) -> None:
        super().__init__(root_config, trial_config, run_dir, trial_index)
        backend = root_config["backend"]
        self.api_base = backend["api_base"].rstrip("/")
        self.health_path = backend.get("health_path", "/health")
        self.completion_path = backend.get("completion_path", "/v1/completions")
        self.chat_completion_path = backend.get("chat_completion_path", "/v1/chat/completions")
        self.api_mode = backend.get("api_mode", "completion")
        self.system_prompt = backend.get("system_prompt")
        self.request_timeout = float(backend.get("request_timeout_sec", 240))
        self.start_command_template = backend.get("start_command")
        self.stop_command_template = backend.get("stop_command")
        self.startup_timeout = float(backend.get("startup_timeout_sec", 120))
        self.model_id = root_config["model"].get("model_id")
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: TextIO | None = None
        self.log_path = run_dir / f"trial-{trial_index:04d}.log"

    def launch(self) -> None:
        if not self.start_command_template:
            return
        command = self._render_start_command()
        self.log_handle = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            command,
            cwd=self._working_dir_string(),
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._wait_until_ready()

    def warmup(self) -> None:
        benchmark = self.root_config.get("benchmark", {})
        warmup_prompt = benchmark.get("warmup_prompt")
        if not warmup_prompt:
            return
        warmup_case = PromptCase(
            name="warmup",
            prompt=warmup_prompt,
            max_tokens=int(benchmark.get("warmup_max_tokens", 8)),
            temperature=0.0,
        )
        if self.api_mode == "chat":
            payload = self._build_chat_payload(warmup_case)
            endpoint = self.chat_completion_path
        else:
            payload = self._build_completion_payload(warmup_case)
            endpoint = self.completion_path
        _ = http_json("POST", f"{self.api_base}{endpoint}", payload, self.request_timeout)

    def stop(self) -> None:
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            if self.stop_command_template:
                subprocess.run(
                    self._render_command(self.stop_command_template),
                    cwd=self._working_dir_string(),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
        finally:
            self.process = None
            if self.log_handle is not None:
                self.log_handle.close()
                self.log_handle = None

    def run_prompt(self, prompt_case: PromptCase) -> dict[str, Any]:
        if self.api_mode == "chat":
            payload = self._build_chat_payload(prompt_case)
            endpoint = self.chat_completion_path
        else:
            payload = self._build_completion_payload(prompt_case)
            endpoint = self.completion_path
        started = time.perf_counter()
        body = http_json(
            "POST",
            f"{self.api_base}{endpoint}",
            payload,
            self.request_timeout,
        )
        elapsed = time.perf_counter() - started
        usage = body.get("usage", {})
        timings = body.get("timings", {})
        prompt_tokens = timings.get("prompt_n") or usage.get("prompt_tokens")
        completion_tokens = timings.get("predicted_n") or usage.get("completion_tokens")
        prompt_ms = timings.get("prompt_ms")
        predicted_ms = timings.get("predicted_ms")
        decode_tps = timings.get("predicted_per_second")
        prompt_seconds = (float(prompt_ms) / 1000.0) if prompt_ms is not None else None
        decode_seconds = (float(predicted_ms) / 1000.0) if predicted_ms is not None else None
        response_text, reasoning_text = self._extract_response_text(body)
        if decode_tps is None and completion_tokens and decode_seconds:
            decode_tps = float(completion_tokens) / max(decode_seconds, 1e-6)
        if decode_seconds is None and prompt_seconds is not None:
            decode_seconds = max(elapsed - prompt_seconds, 0.0)
        return {
            "ok": True,
            "prompt_name": prompt_case.name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_seconds": prompt_seconds,
            "decode_seconds": decode_seconds,
            "total_seconds": elapsed,
            "decode_tokens_per_sec": decode_tps,
            "response_text": response_text,
            "reasoning_text": reasoning_text,
            "text_preview": preview_text(response_text),
            "style_metrics": compute_style_metrics(response_text, prompt_case.target_words),
            "raw": body,
        }

    def _build_completion_payload(self, prompt_case: PromptCase) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": prompt_case.prompt,
            "max_tokens": prompt_case.max_tokens,
            "stream": False,
        }
        payload.update(self.generation_options(prompt_case))
        if self.model_id:
            payload["model"] = self.model_id
        return payload

    def _build_chat_payload(self, prompt_case: PromptCase) -> dict[str, Any]:
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt_case.prompt})
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": prompt_case.max_tokens,
            "stream": False,
        }
        payload.update(self.generation_options(prompt_case))
        if self.model_id:
            payload["model"] = self.model_id
        return payload

    def _extract_response_text(self, body: dict[str, Any]) -> tuple[str, str | None]:
        response_text = ""
        reasoning_text = None
        choices = body.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return response_text, reasoning_text
        choice = choices[0]
        if self.api_mode == "chat":
            message = choice.get("message", {})
            if isinstance(message, dict):
                response_text = str(message.get("content", ""))
                if message.get("reasoning_content") is not None:
                    reasoning_text = str(message.get("reasoning_content"))
        else:
            response_text = str(choice.get("text", ""))
        return response_text, reasoning_text

    def _render_start_command(self) -> list[str]:
        return self._render_command(self.start_command_template)

    def _render_command(self, template: str | list[str] | None) -> list[str]:
        if template is None:
            return []
        values = {}
        values.update(self.root_config.get("backend", {}))
        values.update(self.root_config.get("model", {}))
        values.update(self.root_config.get("defaults", {}))
        values.update(self.trial_config)
        values["flash_attn_value"] = "on" if self.trial_config.get("flash_attn") else "off"
        values["flash_attn_arg"] = f"--flash-attn {values['flash_attn_value']}"
        command = []
        for part in split_command(template):
            rendered = str(part).format(**values).strip()
            if rendered:
                command.append(rendered)
        return command

    def _working_dir_string(self) -> str | None:
        working_dir = resolve_path(
            pathlib.Path(self.root_config["_config_dir"]),
            self.root_config["backend"].get("working_dir"),
        )
        return str(working_dir) if working_dir else None

    def _wait_until_ready(self) -> None:
        url = f"{self.api_base}{self.health_path}"
        deadline = time.time() + self.startup_timeout
        last_error = "Server did not become ready."
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(f"llama.cpp exited before becoming ready. See {self.log_path}")
            try:
                _ = http_request("GET", url, None, 5)
                return
            except RuntimeError as exc:
                last_error = str(exc)
                time.sleep(1.0)
        raise RuntimeError(last_error)


class OllamaBackend(BackendBase):
    supported_dimensions = OLLAMA_SUPPORTED

    def __init__(
        self,
        root_config: dict[str, Any],
        trial_config: dict[str, Any],
        run_dir: pathlib.Path,
        trial_index: int,
    ) -> None:
        super().__init__(root_config, trial_config, run_dir, trial_index)
        backend = root_config["backend"]
        self.api_base = backend.get("api_base", "http://127.0.0.1:11434").rstrip("/")
        self.request_timeout = float(backend.get("request_timeout_sec", 240))
        self.keep_alive = backend.get("keep_alive", "0s")
        self.model_id = root_config["model"]["model_id"]

    def warmup(self) -> None:
        benchmark = self.root_config.get("benchmark", {})
        warmup_prompt = benchmark.get("warmup_prompt")
        if not warmup_prompt:
            return
        payload = {
            "model": self.model_id,
            "prompt": warmup_prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "num_ctx": int(self.trial_config["context_size"]),
                "num_batch": int(self.trial_config["batch_size"]),
                "flash_attn": bool(self.trial_config.get("flash_attn", False)),
                "num_predict": int(benchmark.get("warmup_max_tokens", 8)),
                "temperature": 0.0,
            },
        }
        _ = http_json("POST", f"{self.api_base}/api/generate", payload, self.request_timeout)

    def run_prompt(self, prompt_case: PromptCase) -> dict[str, Any]:
        options = {
            "num_ctx": int(self.trial_config["context_size"]),
            "num_batch": int(self.trial_config["batch_size"]),
            "flash_attn": bool(self.trial_config.get("flash_attn", False)),
            "num_predict": int(prompt_case.max_tokens),
        }
        options.update(self.generation_options(prompt_case))
        payload = {
            "model": self.model_id,
            "prompt": prompt_case.prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": options,
        }
        if prompt_case.think is not None:
            payload["think"] = prompt_case.think
        started = time.perf_counter()
        body = http_json("POST", f"{self.api_base}/api/generate", payload, self.request_timeout)
        elapsed = time.perf_counter() - started
        prompt_eval_duration = body.get("prompt_eval_duration", 0)
        eval_duration = body.get("eval_duration", 0)
        prompt_tokens = body.get("prompt_eval_count")
        completion_tokens = body.get("eval_count")
        prompt_seconds = (float(prompt_eval_duration) / 1_000_000_000.0) if prompt_eval_duration else None
        decode_seconds = (float(eval_duration) / 1_000_000_000.0) if eval_duration else None
        response_text = str(body.get("response", ""))
        decode_tps = None
        if completion_tokens and decode_seconds:
            decode_tps = float(completion_tokens) / max(decode_seconds, 1e-6)
        return {
            "ok": True,
            "prompt_name": prompt_case.name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_seconds": prompt_seconds,
            "decode_seconds": decode_seconds,
            "total_seconds": elapsed,
            "decode_tokens_per_sec": decode_tps,
            "response_text": response_text,
            "reasoning_text": body.get("thinking"),
            "text_preview": preview_text(response_text),
            "style_metrics": compute_style_metrics(response_text, prompt_case.target_words),
            "raw": body,
        }


BACKENDS = {
    "llama_cpp_server": LlamaCppServerBackend,
    "ollama": OllamaBackend,
}


class Tuner:
    def __init__(self, config_path: pathlib.Path) -> None:
        self.config_path = config_path.resolve()
        self.base_dir = self.config_path.parent
        self.config = load_json(self.config_path)
        self.config["_config_dir"] = str(self.base_dir)
        self.prompts = self._load_prompts()
        self.backend_type = self.config["backend"]["type"]
        if self.backend_type not in BACKENDS:
            raise ValueError(f"Unsupported backend type: {self.backend_type}")
        self.backend_class = BACKENDS[self.backend_type]
        self.defaults = dict(self.config.get("defaults", {}))
        self.supported_dimensions = [
            dimension
            for dimension in SEARCH_ORDER
            if dimension in self.config.get("search", {})
            and dimension in self.backend_class.supported_dimensions
        ]
        self.output_dir: pathlib.Path | None = None
        self.results_path: pathlib.Path | None = None
        self.progress_path: pathlib.Path | None = None
        self.status_path: pathlib.Path | None = None
        self.cache: dict[str, dict[str, Any]] = {}
        self.all_results: list[dict[str, Any]] = []
        self.trial_counter = 0

    def plan(self) -> dict[str, Any]:
        strategy = self.config.get("strategy", {})
        return {
            "model": self.config["model"]["name"],
            "backend": self.backend_type,
            "supported_dimensions": self.supported_dimensions,
            "mode": strategy.get("mode", "coordinate"),
            "defaults": self.defaults,
            "search": {name: self.config["search"][name] for name in self.supported_dimensions},
            "prompt_count": len(self.prompts),
        }

    def run(self) -> dict[str, Any]:
        self._ensure_output_dir()
        self._append_progress(
            f"Run started for model={self.config['model']['name']} backend={self.backend_type}."
        )
        self._write_status(
            {
                "phase": "running",
                "model": self.config["model"]["name"],
                "backend": self.backend_type,
                "run_dir": str(self.output_dir),
                "trial": None,
                "prompt_name": None,
            }
        )
        mode = self.config.get("strategy", {}).get("mode", "coordinate")
        if mode == "grid":
            best = self._run_grid()
        else:
            best = self._run_coordinate()
        stability = self._stability_pass(best["config"])
        ranked = sorted(self.all_results, key=lambda item: item["score"], reverse=True)
        summary = {
            "model": self.config["model"]["name"],
            "backend": self.backend_type,
            "best": {
                "config": best["config"],
                "score": best["score"],
                "summary": best["summary"],
            },
            "stability": stability,
            "top_results": ranked[: int(self.config.get("strategy", {}).get("top_results", 5))],
            "run_dir": str(self.output_dir),
        }
        save_json(self.output_dir / "summary.json", summary)
        self._append_progress(
            "Run completed with "
            f"best_config={json.dumps(best['config'], sort_keys=True)} "
            f"best_score={best['score']:.3f}."
        )
        self._write_status(
            {
                "phase": "completed",
                "model": self.config["model"]["name"],
                "backend": self.backend_type,
                "run_dir": str(self.output_dir),
                "best_config": best["config"],
                "best_score": best["score"],
            }
        )
        return summary

    def _run_coordinate(self) -> dict[str, Any]:
        best = self.evaluate(self.defaults, reason="baseline")
        for dimension in self.supported_dimensions:
            best = self._sweep_dimension(best["config"], dimension, self.config["search"][dimension], "coarse")
        refinement_rounds = int(self.config.get("strategy", {}).get("refinement_rounds", 1))
        for _ in range(refinement_rounds):
            for dimension in self.supported_dimensions:
                neighborhood = self._neighbor_values(dimension, best["config"].get(dimension))
                if neighborhood:
                    best = self._sweep_dimension(best["config"], dimension, neighborhood, "refine")
        return best

    def _run_grid(self) -> dict[str, Any]:
        candidates = [dict(self.defaults)]
        for dimension in self.supported_dimensions:
            next_candidates: list[dict[str, Any]] = []
            for candidate in candidates:
                for value in self.config["search"][dimension]:
                    updated = dict(candidate)
                    updated[dimension] = value
                    next_candidates.append(updated)
            candidates = next_candidates
        best: dict[str, Any] | None = None
        for candidate in candidates:
            result = self.evaluate(candidate, reason="grid")
            if best is None or result["score"] > best["score"]:
                best = result
        if best is None:
            raise RuntimeError("No candidate configurations were generated.")
        return best

    def _sweep_dimension(
        self,
        base_config: dict[str, Any],
        dimension: str,
        values: list[Any],
        reason: str,
    ) -> dict[str, Any]:
        best: dict[str, Any] | None = None
        for value in values:
            trial = dict(base_config)
            trial[dimension] = value
            result = self.evaluate(trial, reason=f"{reason}:{dimension}")
            if best is None or result["score"] > best["score"]:
                best = result
        if best is None:
            raise RuntimeError(f"No results were produced for {dimension}.")
        return best

    def _stability_pass(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        repeats = int(self.config.get("strategy", {}).get("stability_runs", 0))
        runs = []
        for index in range(repeats):
            runs.append(self.evaluate(dict(config), reason=f"stability:{index + 1}", allow_cache=False))
        return runs

    def evaluate(
        self,
        candidate: dict[str, Any],
        reason: str,
        allow_cache: bool = True,
    ) -> dict[str, Any]:
        config = dict(self.defaults)
        config.update(candidate)
        cache_key = json.dumps(config, sort_keys=True)
        if allow_cache and cache_key in self.cache:
            return self.cache[cache_key]

        self._ensure_output_dir()
        assert self.output_dir is not None
        assert self.results_path is not None
        self.trial_counter += 1
        self._append_progress(
            f"Trial {self.trial_counter} started reason={reason} config={json.dumps(config, sort_keys=True)}."
        )
        self._write_status(
            {
                "phase": "trial",
                "model": self.config["model"]["name"],
                "backend": self.backend_type,
                "run_dir": str(self.output_dir),
                "trial": self.trial_counter,
                "reason": reason,
                "config": config,
                "prompt_name": None,
            }
        )
        monitor_config = self.config.get("monitoring", {})
        monitor = NvidiaMemoryMonitor(
            monitor_config.get("nvidia_smi"),
            int(monitor_config.get("sample_interval_ms", 500)),
        )
        backend = self.backend_class(self.config, config, self.output_dir, self.trial_counter)
        prompt_runs: list[dict[str, Any]] = []
        failure: str | None = None

        monitor.start()
        try:
            backend.launch()
            backend.warmup()
            for prompt in self.prompts:
                for _ in range(max(prompt.repetitions, 1)):
                    self._append_progress(
                        f"Trial {self.trial_counter} prompt={prompt.name} started."
                    )
                    self._write_status(
                        {
                            "phase": "prompt",
                            "model": self.config["model"]["name"],
                            "backend": self.backend_type,
                            "run_dir": str(self.output_dir),
                            "trial": self.trial_counter,
                            "reason": reason,
                            "config": config,
                            "prompt_name": prompt.name,
                        }
                    )
                    prompt_result = backend.run_prompt(prompt)
                    prompt_runs.append(prompt_result)
                    decode_tps = prompt_result.get("decode_tokens_per_sec")
                    total_seconds = prompt_result.get("total_seconds")
                    decode_text = f"{float(decode_tps):.2f}" if decode_tps is not None else "n/a"
                    total_text = f"{float(total_seconds):.2f}" if total_seconds is not None else "n/a"
                    self._append_progress(
                        f"Trial {self.trial_counter} prompt={prompt.name} finished "
                        f"total_seconds={total_text} decode_tokens_per_sec={decode_text}."
                    )
        except Exception as exc:  # noqa: BLE001
            failure = str(exc)
            self._append_progress(
                f"Trial {self.trial_counter} failed error={failure!r}."
            )
        finally:
            try:
                backend.stop()
            finally:
                monitor.stop()

        summary = self._summarize(prompt_runs, monitor.peak_mib)
        result = {
            "trial": self.trial_counter,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "reason": reason,
            "config": config,
            "summary": summary,
            "score": self._score(summary, failure),
            "failure": failure,
        }
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result) + "\n")
        self.all_results.append(result)
        decode_text = (
            f"{float(summary['median_decode_tokens_per_sec']):.2f}"
            if summary["median_decode_tokens_per_sec"] is not None
            else "n/a"
        )
        self._append_progress(
            f"Trial {self.trial_counter} completed score={result['score']:.3f} "
            f"median_decode_tokens_per_sec={decode_text} "
            f"peak_gpu_memory_mib={summary['peak_gpu_memory_mib']}."
        )
        if allow_cache:
            self.cache[cache_key] = result
        return result

    def _score(self, summary: dict[str, Any], failure: str | None) -> float:
        scoring = self.config.get("scoring", {})
        mode = scoring.get("mode", "performance")
        if mode == "style":
            return self._score_style(summary, failure)
        return self._score_performance(summary, failure)

    def _score_performance(self, summary: dict[str, Any], failure: str | None) -> float:
        scoring = self.config.get("scoring", {})
        score = 0.0
        if summary["median_decode_tokens_per_sec"] is not None:
            score += float(scoring.get("decode_weight", 1.0)) * float(summary["median_decode_tokens_per_sec"])
        if summary["median_prompt_seconds"] is not None:
            score -= float(scoring.get("prompt_weight", 0.2)) * float(summary["median_prompt_seconds"])
        if summary["median_total_seconds"] is not None:
            score -= float(scoring.get("total_weight", 0.05)) * float(summary["median_total_seconds"])
        soft_limit = self.config.get("monitoring", {}).get("gpu_memory_soft_limit_mib")
        if soft_limit is not None and summary["peak_gpu_memory_mib"] is not None:
            excess = max(float(summary["peak_gpu_memory_mib"]) - float(soft_limit), 0.0)
            score -= float(scoring.get("memory_pressure_penalty", 0.0)) * (excess / 1024.0)
        if failure:
            score -= float(scoring.get("failure_penalty", 1000.0))
            if looks_like_oom(failure):
                score -= float(scoring.get("oom_penalty", 250.0))
        return score

    def _score_style(self, summary: dict[str, Any], failure: str | None) -> float:
        scoring = self.config.get("scoring", {})
        style_summary = summary.get("style_metrics")
        if style_summary is None:
            score = -float(scoring.get("failure_penalty", 1000.0))
            if failure and looks_like_oom(failure):
                score -= float(scoring.get("oom_penalty", 250.0))
            return score

        targets = scoring.get("style_targets", {})
        weights = scoring.get("style_weights", {})
        score = 100.0
        for metric in ("weirdness", "verbosity", "stiffness"):
            actual = float(style_summary.get(metric, 0.0))
            target = float(targets.get(metric, 0.5))
            weight = float(weights.get(metric, 1.0))
            score -= abs(actual - target) * 100.0 * weight

        if summary["median_total_seconds"] is not None:
            score -= float(scoring.get("latency_weight", 0.0)) * float(summary["median_total_seconds"])

        if failure:
            score -= float(scoring.get("failure_penalty", 1000.0))
            if looks_like_oom(failure):
                score -= float(scoring.get("oom_penalty", 250.0))
        return score

    def _summarize(self, prompt_runs: list[dict[str, Any]], peak_gpu_memory_mib: int | None) -> dict[str, Any]:
        decode_values = [
            float(run["decode_tokens_per_sec"])
            for run in prompt_runs
            if run.get("decode_tokens_per_sec") is not None
        ]
        prompt_seconds = [
            float(run["prompt_seconds"])
            for run in prompt_runs
            if run.get("prompt_seconds") is not None
        ]
        total_seconds = [
            float(run["total_seconds"])
            for run in prompt_runs
            if run.get("total_seconds") is not None
        ]
        weirdness_values = [
            float(run["style_metrics"]["weirdness"])
            for run in prompt_runs
            if run.get("style_metrics") is not None
        ]
        verbosity_values = [
            float(run["style_metrics"]["verbosity"])
            for run in prompt_runs
            if run.get("style_metrics") is not None
        ]
        stiffness_values = [
            float(run["style_metrics"]["stiffness"])
            for run in prompt_runs
            if run.get("style_metrics") is not None
        ]
        sample_previews = [
            {
                "prompt_name": run.get("prompt_name"),
                "preview": run.get("text_preview", ""),
            }
            for run in prompt_runs
            if run.get("text_preview")
        ]
        return {
            "prompt_runs": prompt_runs,
            "successful_runs": len(prompt_runs),
            "median_decode_tokens_per_sec": median(decode_values),
            "median_prompt_seconds": median(prompt_seconds),
            "median_total_seconds": median(total_seconds),
            "peak_gpu_memory_mib": peak_gpu_memory_mib,
            "style_metrics": {
                "weirdness": median(weirdness_values),
                "verbosity": median(verbosity_values),
                "stiffness": median(stiffness_values),
                "samples": sample_previews[:3],
            }
            if weirdness_values or verbosity_values or stiffness_values
            else None,
        }

    def _neighbor_values(self, dimension: str, current_value: Any) -> list[Any]:
        values = list(self.config.get("search", {}).get(dimension, []))
        if current_value not in values:
            return values
        index = values.index(current_value)
        start = max(index - 1, 0)
        end = min(index + 2, len(values))
        return values[start:end]

    def _load_prompts(self) -> list[PromptCase]:
        prompt_path = resolve_path(self.base_dir, self.config["benchmark"]["prompts_file"])
        if prompt_path is None:
            raise ValueError("benchmark.prompts_file is required.")
        raw_prompts = load_json(prompt_path)
        prompts = []
        for item in raw_prompts:
            prompt_text = item.get("prompt")
            prompt_file = item.get("prompt_file")
            if prompt_file:
                resolved_prompt_file = resolve_path(prompt_path.parent, prompt_file)
                if resolved_prompt_file is None:
                    raise ValueError(f"Could not resolve prompt_file for {item['name']}")
                file_text = resolved_prompt_file.read_text(encoding="utf-8")
                prompt_text = f"{item.get('prompt_prefix', '')}{file_text}{item.get('prompt_suffix', '')}"
            if prompt_text is None:
                raise ValueError(f"Prompt {item['name']} must define prompt or prompt_file.")
            prompts.append(
                PromptCase(
                    name=item["name"],
                    prompt=prompt_text,
                    max_tokens=int(item["max_tokens"]),
                    temperature=float(item["temperature"]) if item.get("temperature") is not None else None,
                    repetitions=int(item.get("repetitions", 1)),
                    target_words=int(item["target_words"]) if item.get("target_words") is not None else None,
                    think=item.get("think"),
                    generation_overrides={
                        key: item[key]
                        for key in GENERATION_OPTION_KEYS
                        if key != "temperature" and item.get(key) is not None
                    }
                    or None,
                )
            )
        return prompts

    def _prepare_output_dir(self) -> pathlib.Path:
        output_root = resolve_path(
            self.base_dir,
            self.config.get("output", {}).get("results_dir", "results"),
        )
        if output_root is None:
            output_root = (self.base_dir / "results").resolve()
        run_dir = output_root / f"{slugify(self.config['model']['name'])}-{timestamp()}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _ensure_output_dir(self) -> None:
        if (
            self.output_dir is not None
            and self.results_path is not None
            and self.progress_path is not None
            and self.status_path is not None
        ):
            return
        self.output_dir = self._prepare_output_dir()
        self.results_path = self.output_dir / "results.jsonl"
        self.progress_path = self.output_dir / "progress.log"
        self.status_path = self.output_dir / "status.json"

    def _append_progress(self, message: str) -> None:
        if self.progress_path is None:
            return
        timestamp_text = datetime.now().isoformat(timespec="seconds")
        with self.progress_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp_text}] {message}\n")

    def _write_status(self, payload: dict[str, Any]) -> None:
        if self.status_path is None:
            return
        status_payload = dict(payload)
        status_payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_json(self.status_path, status_payload)


def console_summary(summary: dict[str, Any]) -> dict[str, Any]:
    best_summary = summary["best"]["summary"]
    return {
        "model": summary["model"],
        "backend": summary["backend"],
        "best_config": summary["best"]["config"],
        "best_score": summary["best"]["score"],
        "median_decode_tokens_per_sec": best_summary.get("median_decode_tokens_per_sec"),
        "median_prompt_seconds": best_summary.get("median_prompt_seconds"),
        "median_total_seconds": best_summary.get("median_total_seconds"),
        "peak_gpu_memory_mib": best_summary.get("peak_gpu_memory_mib"),
        "run_dir": summary["run_dir"],
    }


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def load_optional_json(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def resolve_report_run_dir(
    base_dir: pathlib.Path,
    run_dir: pathlib.Path | None,
    results_dir: pathlib.Path | None,
    contains: str | None,
) -> pathlib.Path:
    if run_dir is not None:
        raw_run_dir = run_dir if run_dir.is_absolute() else (base_dir / run_dir)
        return raw_run_dir.resolve()

    raw_results_dir = results_dir if results_dir is not None else pathlib.Path("results")
    results_root = raw_results_dir if raw_results_dir.is_absolute() else (base_dir / raw_results_dir)
    results_root = results_root.resolve()
    if not results_root.exists():
        raise FileNotFoundError(f"Results directory not found: {results_root}")

    search = contains.lower() if contains else None
    candidates = [
        path
        for path in sorted(results_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
        if path.is_dir() and (search is None or search in path.name.lower())
    ]
    if not candidates:
        filter_text = f" matching '{contains}'" if contains else ""
        raise FileNotFoundError(f"No run directories found under {results_root}{filter_text}.")
    return candidates[0]


def format_metric(value: float | int | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{decimals}f}"


def value_label(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if value is None:
        return "null"
    return str(value)


def compact_config(config: dict[str, Any]) -> str:
    labels = {
        "gpu_layers": "gl",
        "context_size": "ctx",
        "batch_size": "batch",
        "micro_batch": "ub",
        "kv_cache_type": "kv",
        "flash_attn": "fa",
        "temperature": "temp",
        "top_p": "top_p",
        "top_k": "top_k",
        "min_p": "min_p",
        "repeat_penalty": "rep",
    }
    parts = []
    for key in SEARCH_ORDER:
        if key not in config:
            continue
        parts.append(f"{labels.get(key, key)}={value_label(config[key])}")
    extras = sorted(key for key in config if key not in SEARCH_ORDER)
    for key in extras:
        parts.append(f"{key}={value_label(config[key])}")
    return " ".join(parts)


def make_bar(value: float | None, minimum: float, maximum: float, width: int) -> str:
    if value is None or width <= 0:
        return ""
    if maximum <= minimum:
        return "#" * max(1, width // 2)
    normalized = clamp01((value - minimum) / (maximum - minimum))
    filled = int(round(normalized * width))
    if value > minimum and filled == 0:
        filled = 1
    return "#" * filled


def render_series(
    title: str,
    rows: list[tuple[str, float | None, str]],
    width: int,
) -> list[str]:
    numeric_values = [value for _, value, _ in rows if value is not None]
    lines = [title]
    if not numeric_values:
        lines.append("  no data")
        return lines
    minimum = min(numeric_values)
    maximum = max(numeric_values)
    for label, value, extra in rows:
        metric_text = format_metric(value)
        bar = make_bar(value, minimum, maximum, width)
        suffix = f" {extra}" if extra else ""
        lines.append(f"  {label:>8} {metric_text:>9} {bar}{suffix}")
    return lines


def build_histogram(values: list[float], bins: int = 8) -> list[tuple[str, int]]:
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if maximum <= minimum:
        return [(f"{minimum:.2f}", len(values))]
    bucket_count = max(1, bins)
    width = (maximum - minimum) / bucket_count
    counts = [0 for _ in range(bucket_count)]
    for value in values:
        index = int((value - minimum) / width)
        if index >= bucket_count:
            index = bucket_count - 1
        counts[index] += 1
    histogram = []
    for index, count in enumerate(counts):
        start = minimum + (index * width)
        end = start + width
        histogram.append((f"{start:.1f}..{end:.1f}", count))
    return histogram


def render_histogram(title: str, values: list[float], width: int) -> list[str]:
    buckets = build_histogram(values)
    lines = [title]
    if not buckets:
        lines.append("  no data")
        return lines
    max_count = max(count for _, count in buckets) or 1
    for label, count in buckets:
        bar = "#" * max(1, int(round((count / max_count) * width))) if count else ""
        lines.append(f"  {label:>16} {count:>3} {bar}")
    return lines


def dimension_impact(records: list[dict[str, Any]]) -> list[tuple[str, list[tuple[str, float, int]]]]:
    if not records:
        return []
    config_keys: list[str] = []
    seen = set()
    for key in SEARCH_ORDER:
        seen.add(key)
        config_keys.append(key)
    for record in records:
        config = record.get("config", {})
        if not isinstance(config, dict):
            continue
        for key in config:
            if key not in seen:
                seen.add(key)
                config_keys.append(key)

    summaries: list[tuple[str, list[tuple[str, float, int]]]] = []
    for key in config_keys:
        grouped: dict[str, dict[str, Any]] = {}
        for record in records:
            config = record.get("config", {})
            if not isinstance(config, dict) or key not in config:
                continue
            score = record.get("score")
            if score is None:
                continue
            raw_value = config.get(key)
            label = value_label(raw_value)
            if label not in grouped:
                grouped[label] = {"scores": [], "sort_value": raw_value}
            grouped[label]["scores"].append(float(score))
        if len(grouped) < 2 or len(grouped) > 12:
            continue

        def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, Any]:
            raw_value = item[1]["sort_value"]
            if isinstance(raw_value, bool):
                return (0, int(raw_value))
            if isinstance(raw_value, (int, float)):
                return (1, float(raw_value))
            return (2, str(raw_value))

        rows = []
        for label, payload in sorted(grouped.items(), key=sort_key):
            scores = payload["scores"]
            rows.append((label, float(statistics.median(scores)), len(scores)))
        summaries.append((key, rows))
    return summaries


def tail_lines(path: pathlib.Path, count: int) -> list[str]:
    if count <= 0 or not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]


def render_top_results(records: list[dict[str, Any]], limit: int = 5) -> list[str]:
    lines = ["Top results"]
    if not records:
        lines.append("  no data")
        return lines
    ranked = sorted(records, key=lambda item: float(item.get("score", -1e9)), reverse=True)[:limit]
    for record in ranked:
        summary = record.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        decode = summary.get("median_decode_tokens_per_sec")
        memory = summary.get("peak_gpu_memory_mib")
        failure = record.get("failure")
        failure_text = " failed" if failure else ""
        lines.append(
            "  "
            f"trial={record.get('trial')} "
            f"score={format_metric(record.get('score'))} "
            f"decode={format_metric(decode)} tok/s "
            f"mem={value_label(memory)} MiB "
            f"{compact_config(record.get('config', {}))}{failure_text}"
        )
    return lines


def render_run_report(run_dir: pathlib.Path, recent_progress_lines: int = 6) -> str:
    summary = load_optional_json(run_dir / "summary.json")
    status = load_optional_json(run_dir / "status.json")
    records = load_jsonl(run_dir / "results.jsonl")
    terminal_width = shutil.get_terminal_size((120, 40)).columns
    chart_width = max(12, min(48, terminal_width - 34))

    model = None
    backend = None
    if summary is not None:
        model = summary.get("model")
        backend = summary.get("backend")
    if model is None and status is not None:
        model = status.get("model")
    if backend is None and status is not None:
        backend = status.get("backend")
    if model is None and records:
        model = records[0].get("model")
    if backend is None and records:
        backend = records[0].get("backend")

    successes = sum(1 for record in records if not record.get("failure"))
    failures = len(records) - successes
    best_record = None
    if records:
        best_record = max(records, key=lambda item: float(item.get("score", -1e9)))

    lines = [
        "Local Model Tuner Report",
        f"Run dir: {run_dir}",
        f"Model: {model or 'unknown'}",
        f"Backend: {backend or 'unknown'}",
        f"Trials: {len(records)} total | {successes} successful | {failures} failed",
    ]

    if best_record is not None:
        best_summary = best_record.get("summary", {})
        if not isinstance(best_summary, dict):
            best_summary = {}
        lines.append(
            "Best: "
            f"trial={best_record.get('trial')} "
            f"score={format_metric(best_record.get('score'))} "
            f"decode={format_metric(best_summary.get('median_decode_tokens_per_sec'))} tok/s "
            f"prompt={format_metric(best_summary.get('median_prompt_seconds'))} s "
            f"mem={value_label(best_summary.get('peak_gpu_memory_mib'))} MiB"
        )
        lines.append(f"Best config: {compact_config(best_record.get('config', {}))}")

    if status is not None:
        current_bits = [f"phase={status.get('phase', 'unknown')}"]
        if status.get("trial") is not None:
            current_bits.append(f"trial={status['trial']}")
        if status.get("prompt_name"):
            current_bits.append(f"prompt={status['prompt_name']}")
        if status.get("reason"):
            current_bits.append(f"reason={status['reason']}")
        if status.get("updated_at"):
            current_bits.append(f"updated={status['updated_at']}")
        lines.append("Current: " + " | ".join(current_bits))
        status_config = status.get("config")
        if isinstance(status_config, dict):
            lines.append(f"Current config: {compact_config(status_config)}")

    score_rows = [
        (f"trial {record.get('trial')}", float(record["score"]), record.get("reason", ""))
        for record in records
        if record.get("score") is not None
    ]
    decode_rows = []
    for record in records:
        summary_payload = record.get("summary", {})
        if not isinstance(summary_payload, dict):
            summary_payload = {}
        decode_rows.append(
            (
                f"trial {record.get('trial')}",
                float(summary_payload["median_decode_tokens_per_sec"])
                if summary_payload.get("median_decode_tokens_per_sec") is not None
                else None,
                record.get("reason", ""),
            )
        )

    if score_rows:
        lines.append("")
        lines.extend(render_series("Score by trial", score_rows, chart_width))
    if decode_rows:
        lines.append("")
        lines.extend(render_series("Decode tok/s by trial", decode_rows, chart_width))

    score_values = [float(record["score"]) for record in records if record.get("score") is not None]
    decode_values = []
    for record in records:
        summary_payload = record.get("summary", {})
        if isinstance(summary_payload, dict) and summary_payload.get("median_decode_tokens_per_sec") is not None:
            decode_values.append(float(summary_payload["median_decode_tokens_per_sec"]))

    if score_values:
        lines.append("")
        lines.extend(render_histogram("Score distribution", score_values, chart_width))
    if decode_values:
        lines.append("")
        lines.extend(render_histogram("Decode tok/s distribution", decode_values, chart_width))

    impacts = dimension_impact(records)
    if impacts:
        lines.append("")
        lines.append("Per-dimension median score")
        for dimension, rows in impacts:
            lines.append(f"  {dimension}")
            values = [value for _, value, _ in rows]
            minimum = min(values)
            maximum = max(values)
            for label, value, count in rows:
                bar = make_bar(value, minimum, maximum, chart_width)
                lines.append(
                    f"    {label:>10} {value:>9.2f} {bar} n={count}"
                )

    lines.append("")
    lines.extend(render_top_results(records))

    recent_progress = tail_lines(run_dir / "progress.log", recent_progress_lines)
    if recent_progress:
        lines.append("")
        lines.append("Recent progress")
        for line in recent_progress:
            lines.append(f"  {line}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tune one local model at a time.")
    config_common = argparse.ArgumentParser(add_help=False)
    config_common.add_argument(
        "--config",
        required=True,
        type=pathlib.Path,
        help="Path to the tuning config JSON file.",
    )
    report_common = argparse.ArgumentParser(add_help=False)
    report_common.add_argument(
        "--run-dir",
        type=pathlib.Path,
        help="Specific run directory to inspect. Defaults to the newest matching run.",
    )
    report_common.add_argument(
        "--results-dir",
        type=pathlib.Path,
        default=pathlib.Path("results"),
        help="Root directory containing run folders when --run-dir is omitted.",
    )
    report_common.add_argument(
        "--contains",
        help="Only consider run directory names containing this substring.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan", parents=[config_common], help="Print the resolved tuning plan.")
    subparsers.add_parser("run", parents=[config_common], help="Execute the tuning run.")
    subparsers.add_parser("report", parents=[report_common], help="Render charts for a completed or active run.")
    watch_parser = subparsers.add_parser(
        "watch",
        parents=[report_common],
        help="Refresh a live terminal report for an active run.",
    )
    watch_parser.add_argument(
        "--refresh-sec",
        type=float,
        default=2.0,
        help="Refresh interval in seconds.",
    )
    watch_parser.add_argument(
        "--tail-lines",
        type=int,
        default=8,
        help="Number of recent progress lines to show.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "plan":
        tuner = Tuner(args.config)
        print(json.dumps(tuner.plan(), indent=2))
        return 0
    if args.command == "run":
        tuner = Tuner(args.config)
        summary = tuner.run()
        print(json.dumps(console_summary(summary), indent=2))
        return 0

    base_dir = pathlib.Path(__file__).resolve().parent
    run_dir = resolve_report_run_dir(base_dir, args.run_dir, args.results_dir, args.contains)
    if args.command == "report":
        print(render_run_report(run_dir))
        return 0

    try:
        while True:
            report = render_run_report(run_dir, recent_progress_lines=int(args.tail_lines))
            print("\x1b[2J\x1b[H", end="")
            print(report)
            status = load_optional_json(run_dir / "status.json") or {}
            if status.get("phase") == "completed":
                return 0
            time.sleep(max(float(args.refresh_sec), 0.2))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

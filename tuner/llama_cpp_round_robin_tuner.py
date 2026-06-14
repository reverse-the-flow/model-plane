from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import pathlib
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TrialConfig:
    instances: int
    parallel: int
    concurrency: int
    context_size: int
    batch_size: int
    micro_batch: int
    gpu_layers: int
    kv_cache_type: str
    flash_attn: bool


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def http_json(method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def wait_ready(port: int, timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    url = f"http://127.0.0.1:{port}/health"
    last_error = "server did not become healthy"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=5).read()
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
            time.sleep(1.0)
    raise RuntimeError(last_error)


def run_command(command: list[str], log_path: pathlib.Path | None = None) -> subprocess.Popen[str]:
    if log_path is None:
        return subprocess.Popen(command, text=True)
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, text=True)
    process._llama_log_handle = handle  # type: ignore[attr-defined]
    return process


def close_process(process: subprocess.Popen[str]) -> None:
    with contextlib.suppress(Exception):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    handle = getattr(process, "_llama_log_handle", None)
    if handle is not None:
        handle.close()


class LlamaRoundRobinTuner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.model_path = pathlib.Path(args.model_path).resolve()
        self.run_dir = pathlib.Path(args.results_dir).resolve() / f"{args.name}-{timestamp()}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.results_path = self.run_dir / "results.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.progress_path = self.run_dir / "progress.log"

    def run(self) -> dict[str, Any]:
        trials = self.build_trials()
        self.log(f"Run started for {self.args.name}; {len(trials)} trial(s).")
        records = []
        for index, config in enumerate(trials, start=1):
            try:
                record = self.run_trial(index, config)
            except Exception as exc:
                record = {
                    "trial": index,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "config": config.__dict__,
                    "ok": False,
                    "error": str(exc),
                    "throughput_tps": 0.0,
                }
                self.log(f"Trial {index} failed: {exc}")
            with self.results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            records.append(record)

        ok_records = [record for record in records if record.get("ok")]
        best = max(ok_records, key=lambda record: record["throughput_tps"]) if ok_records else None
        summary = {
            "model": self.args.name,
            "model_path": str(self.model_path),
            "run_dir": str(self.run_dir),
            "best": best,
            "top_results": sorted(ok_records, key=lambda record: record["throughput_tps"], reverse=True)[:10],
            "failures": [record for record in records if not record.get("ok")],
        }
        self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        self.log("Run completed.")
        return summary

    def build_trials(self) -> list[TrialConfig]:
        trials = []
        for instances in self.args.instances:
            for parallel in self.args.parallel:
                for concurrency in self.args.concurrency:
                    if concurrency < instances:
                        continue
                    trials.append(
                        TrialConfig(
                            instances=instances,
                            parallel=parallel,
                            concurrency=concurrency,
                            context_size=self.args.context_size,
                            batch_size=self.args.batch_size,
                            micro_batch=self.args.micro_batch,
                            gpu_layers=self.args.gpu_layers,
                            kv_cache_type=self.args.kv_cache_type,
                            flash_attn=self.args.flash_attn,
                        )
                    )
        return trials

    def run_trial(self, index: int, config: TrialConfig) -> dict[str, Any]:
        self.log(f"Trial {index} starting config={config.__dict__}")
        base_port = self.args.base_port + (index * 100)
        containers = []
        processes = []
        try:
            for instance in range(config.instances):
                name = f"{self.args.name}-rr-{index}-{instance}"
                port = base_port + instance
                containers.append(name)
                log_path = self.run_dir / f"trial-{index:04d}-server-{instance}.log"
                command = self.build_server_command(name, port, config)
                process = run_command(command, log_path)
                processes.append(process)
            for instance in range(config.instances):
                wait_ready(base_port + instance, self.args.startup_timeout_sec)
            for instance in range(config.instances):
                self.send_request(base_port + instance, max_tokens=min(16, self.args.n_predict))

            started = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as pool:
                futures = [
                    pool.submit(self.send_request, base_port + (request_index % config.instances))
                    for request_index in range(self.args.requests)
                ]
                results = [future.result() for future in concurrent.futures.as_completed(futures)]
            elapsed = time.perf_counter() - started
            total_tokens = sum(result["tokens"] for result in results)
            avg_request_tps = sum(result["request_tps"] for result in results) / max(len(results), 1)
            throughput_tps = total_tokens / max(elapsed, 1e-6)
            record = {
                "trial": index,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "config": config.__dict__,
                "ok": True,
                "requests": len(results),
                "total_tokens": total_tokens,
                "elapsed_seconds": elapsed,
                "avg_request_tps": avg_request_tps,
                "throughput_tps": throughput_tps,
            }
            self.log(
                f"Trial {index} completed throughput_tps={throughput_tps:.2f} "
                f"avg_request_tps={avg_request_tps:.2f}"
            )
            return record
        finally:
            for process in processes:
                close_process(process)
            for name in containers:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30)

    def build_server_command(self, container_name: str, host_port: int, config: TrialConfig) -> list[str]:
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--gpus",
            "all",
            "-p",
            f"127.0.0.1:{host_port}:8080",
            "-v",
            f"{self.model_path.parent}:/models:ro",
            self.args.image,
            "-m",
            f"/models/{self.model_path.name}",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "--ctx-size",
            str(config.context_size),
            "--n-gpu-layers",
            str(config.gpu_layers),
            "--batch-size",
            str(config.batch_size),
            "--ubatch-size",
            str(config.micro_batch),
            "--parallel",
            str(config.parallel),
            "--cache-type-k",
            config.kv_cache_type,
            "--cache-type-v",
            config.kv_cache_type,
            "--flash-attn",
            "on" if config.flash_attn else "off",
        ]
        return command

    def send_request(self, port: int, max_tokens: int | None = None) -> dict[str, float]:
        payload = {
            "prompt": self.args.prompt,
            "max_tokens": max_tokens or self.args.n_predict,
            "temperature": 0,
            "stream": False,
        }
        started = time.perf_counter()
        body = http_json("POST", f"http://127.0.0.1:{port}/v1/completions", payload, self.args.request_timeout_sec)
        elapsed = time.perf_counter() - started
        timings = body.get("timings", {})
        tokens = int(timings.get("predicted_n") or body.get("usage", {}).get("completion_tokens") or payload["max_tokens"])
        predicted_per_second = timings.get("predicted_per_second")
        request_tps = float(predicted_per_second) if predicted_per_second is not None else tokens / max(elapsed, 1e-6)
        return {"tokens": float(tokens), "request_tps": request_tps}

    def log(self, message: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
        print(line, flush=True)
        with self.progress_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def parse_int_list(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sweep llama.cpp multi-instance round-robin throughput.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--image", default="ghcr.io/ggml-org/llama.cpp:server-cuda")
    parser.add_argument("--base-port", type=int, default=28080)
    parser.add_argument("--instances", type=parse_int_list, default=[1, 2, 4])
    parser.add_argument("--parallel", type=parse_int_list, default=[1, 2, 4])
    parser.add_argument("--concurrency", type=parse_int_list, default=[1, 4, 8, 16])
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--n-predict", type=int, default=128)
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--micro-batch", type=int, default=256)
    parser.add_argument("--gpu-layers", type=int, default=999)
    parser.add_argument("--kv-cache-type", default="q4_0")
    parser.add_argument("--flash-attn", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--startup-timeout-sec", type=float, default=240)
    parser.add_argument("--request-timeout-sec", type=float, default=300)
    parser.add_argument(
        "--prompt",
        default="List five concrete ways to improve local inference server throughput for coding agents.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = LlamaRoundRobinTuner(args).run()
    best = summary.get("best")
    if best:
        print(json.dumps({"run_dir": summary["run_dir"], "best": best}, indent=2))
    else:
        print(json.dumps({"run_dir": summary["run_dir"], "failures": summary["failures"]}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

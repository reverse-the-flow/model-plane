# Black-Box Creation Template

Use this when the model should be benchmarked through Ollama as a black box.

Canonical idea:

- the real `ollama` container stays running
- the model library stays where it is
- do not restart, recreate, or make Ollama ephemeral
- the ephemeral thing is the client container that runs `tuner.py`
- that client joins `open-webui_default` and connects to `http://ollama:11434`

## When To Use This

Use black-box when you want to measure the model through Ollama without controlling low-level runner internals.

Typical exposed knobs:

- `context_size`
- `batch_size`
- `flash_attn`

## Canonical Config Skeleton

Save as `configs/<model-slug>.ollama.open-webui-network.json`.

```json
{
  "model": {
    "name": "<model-slug>-ollama",
    "backend": "ollama",
    "model_id": "<ollama-model-name>"
  },
  "backend": {
    "type": "ollama",
    "api_base": "http://ollama:11434",
    "request_timeout_sec": 300,
    "keep_alive": "10m"
  },
  "defaults": {
    "context_size": 8192,
    "batch_size": 512,
    "flash_attn": true
  },
  "search": {
    "context_size": [4096, 8192, 16384],
    "batch_size": [128, 256, 512, 1024],
    "flash_attn": [true, false]
  },
  "strategy": {
    "mode": "coordinate",
    "refinement_rounds": 1,
    "stability_runs": 2,
    "top_results": 5
  },
  "benchmark": {
    "prompts_file": "../prompts/<prompt-file>.json",
    "warmup_prompt": "Say 'ready' and nothing else.",
    "warmup_max_tokens": 8
  },
  "monitoring": {
    "nvidia_smi": null,
    "sample_interval_ms": 500,
    "gpu_memory_soft_limit_mib": 15000
  },
  "scoring": {
    "decode_weight": 1.0,
    "prompt_weight": 0.2,
    "total_weight": 0.05,
    "failure_penalty": 1000.0,
    "oom_penalty": 250.0,
    "memory_pressure_penalty": 0.25
  },
  "output": {
    "results_dir": "../results"
  }
}
```

## Canonical Ephemeral Client Compose

Use `docker-compose.ollama-blackbox.yaml` as the canonical runner wrapper.

```yaml
services:
  blackbox-runner:
    image: python:3.11-slim
    working_dir: /workspace
    entrypoint:
      - python
      - /workspace/tuner.py
    volumes:
      - ./:/workspace
    networks:
      - open-webui_default
    environment:
      PYTHONDONTWRITEBYTECODE: "1"
    restart: "no"

networks:
  open-webui_default:
    external: true
```

## Run It

```powershell
docker compose -f "X:\Experiments\AI data\Local Model tuner\docker-compose.ollama-blackbox.yaml" run --rm `
  blackbox-runner `
  plan --config /workspace/configs/<model-slug>.ollama.open-webui-network.json
```

```powershell
docker compose -f "X:\Experiments\AI data\Local Model tuner\docker-compose.ollama-blackbox.yaml" run --rm `
  blackbox-runner `
  run --config /workspace/configs/<model-slug>.ollama.open-webui-network.json
```

## Invariants

- Ollama is not ephemeral.
- The black-box client container is ephemeral.
- The client must join `open-webui_default`.
- The client must connect to `http://ollama:11434`, not `127.0.0.1:11434`, when following this canonical pattern.
- One model at a time.
- Do not mount or move model blobs for black-box runs.

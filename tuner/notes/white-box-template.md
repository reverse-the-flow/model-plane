# White-Box Creation Template

Use this when the model should be benchmarked through an ephemeral `llama.cpp` server that you control directly.

Canonical idea:

- the model already exists in the persistent Ollama volume
- the benchmark server is ephemeral
- the server mounts the Ollama volume read-only
- the tuner talks to the ephemeral `llama.cpp` HTTP endpoint on a host port

## When To Use This

Use white-box when you want to tune runner knobs that Ollama does not expose directly:

- `gpu_layers`
- `context_size`
- `batch_size`
- `micro_batch`
- `kv_cache_type`
- `flash_attn`

## Inputs You Need

- Ollama model name as shown by `ollama list`
- a unique host port for the ephemeral `llama.cpp` server
- a unique container name
- a prompt file

## Resolve The Blob Path

```powershell
py -3 "X:\Experiments\AI data\Local Model tuner\resolve_ollama_model.py" `
  --container ollama `
  --volume open-webui_ollama `
  --model "<ollama-model-name>"
```

Copy `primary_model.path_in_volume` into `model_path`.

## Canonical Config Skeleton

Save as `configs/<model-slug>.open-webui-ollama-volume.json`.

```json
{
  "model": {
    "name": "<model-slug>",
    "backend": "llama_cpp_server",
    "model_id": "<ollama-model-name>"
  },
  "backend": {
    "type": "llama_cpp_server",
    "api_base": "http://127.0.0.1:<host-port>",
    "health_path": "/health",
    "completion_path": "/v1/completions",
    "chat_completion_path": "/v1/chat/completions",
    "api_mode": "chat",
    "system_prompt": "Provide only the final answer. Do not reveal hidden reasoning or think aloud.",
    "startup_timeout_sec": 180,
    "request_timeout_sec": 300,
    "container_name": "llama-cpp-tuner-<model-slug>",
    "network_name": "open-webui_default",
    "image": "ghcr.io/ggml-org/llama.cpp:server-cuda",
    "host_port": "<host-port>",
    "ollama_volume": "open-webui_ollama",
    "model_path": "/root/.ollama/models/blobs/sha256-<resolved-digest>",
    "start_command": [
      "docker",
      "run",
      "--rm",
      "--name",
      "{container_name}",
      "--runtime",
      "nvidia",
      "--gpus",
      "all",
      "--network",
      "{network_name}",
      "-p",
      "127.0.0.1:{host_port}:8080",
      "-v",
      "{ollama_volume}:/root/.ollama:ro",
      "{image}",
      "-m",
      "{model_path}",
      "--host",
      "0.0.0.0",
      "--port",
      "8080",
      "--ctx-size",
      "{context_size}",
      "--n-gpu-layers",
      "{gpu_layers}",
      "--batch-size",
      "{batch_size}",
      "--ubatch-size",
      "{micro_batch}",
      "--cache-type-k",
      "{kv_cache_type}",
      "--cache-type-v",
      "{kv_cache_type}",
      "--flash-attn",
      "{flash_attn_value}"
    ],
    "stop_command": [
      "docker",
      "rm",
      "-f",
      "{container_name}"
    ]
  },
  "defaults": {
    "gpu_layers": 24,
    "context_size": 8192,
    "batch_size": 512,
    "micro_batch": 128,
    "kv_cache_type": "q4_0",
    "flash_attn": true
  },
  "search": {
    "gpu_layers": [8, 16, 24, 32, 40],
    "context_size": [4096, 8192, 16384],
    "batch_size": [128, 256, 512, 1024],
    "micro_batch": [32, 64, 128, 256],
    "kv_cache_type": ["q4_0", "f16", "q8_0"],
    "flash_attn": [true]
  },
  "strategy": {
    "mode": "coordinate",
    "refinement_rounds": 1,
    "stability_runs": 2,
    "top_results": 5
  },
  "benchmark": {
    "prompts_file": "..\\prompts\\<prompt-file>.json",
    "warmup_prompt": "Say 'ready' and nothing else.",
    "warmup_max_tokens": 8
  },
  "monitoring": {
    "nvidia_smi": "nvidia-smi",
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
    "results_dir": "..\\results"
  }
}
```

## Run It

```powershell
py -3 "X:\Experiments\AI data\Local Model tuner\tuner.py" plan `
  --config "X:\Experiments\AI data\Local Model tuner\configs\<model-slug>.open-webui-ollama-volume.json"
```

```powershell
py -3 "X:\Experiments\AI data\Local Model tuner\tuner.py" run `
  --config "X:\Experiments\AI data\Local Model tuner\configs\<model-slug>.open-webui-ollama-volume.json"
```

## Invariants

- The `llama.cpp` container is ephemeral.
- The Ollama model library is mounted read-only.
- One model at a time.
- Use a unique host port and unique container name per model config.

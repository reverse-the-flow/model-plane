# Local Model Tuner

This folder now has a starter tuner for one model at a time.

The design matches the constraints in your note:

- One active model per tuning run.
- `fp4` is the default KV cache baseline in the original idea. For the current `llama.cpp` Docker image here, the nearest practical default is `q4_0`.
- The highest-value knobs are `gpu_layers`, `context_size`, `batch_size`, and `micro_batch`.
- `kv_cache_type` and `flash_attn` are treated as second-pass toggles.
- Search is staged so it does not explode into a full Cartesian grid unless you explicitly ask for it.

## What is here

- `tuner.py`: the tuner CLI.
- `resolve_ollama_model.py`: helper to map an Ollama model name to its blob path inside the Docker volume.
- `configs/llama-cpp.example.json`: sample config for a `llama.cpp` server process.
- `configs/llama-cpp.docker.example.json`: sample config for a Docker-managed `llama.cpp` server.
- `configs/llama-cpp.open-webui.example.json`: sample config for an ephemeral `llama.cpp` container on `open-webui_default`.
- `configs/llama-cpp.open-webui-ollama-volume.example.json`: sample config for mounting the `open-webui_ollama` volume directly.
- `configs/ollama.example.json`: sample config for Ollama.
- `notes/aitune-evaluation.md`: why NVIDIA AITune is not the main path for this GGUF runner tuner, plus a side experiment plan.
- `docker-compose.aitune-lab.yaml`: ephemeral AITune lab wrapper for a separate lower-level PyTorch tuning track.
- `run-aitune.ps1`: host launcher for the AITune lab wrapper and backend-specific ephemeral containers.
- `aitune-lab/scripts/aitune_probe.py`: lightweight backend inventory and environment probe that runs inside the AITune container.
- `notes/white-box-template.md`: canonical white-box model creation template.
- `notes/black-box-template.md`: canonical black-box model creation template.
- `prompts/benchmark-prompts.example.json`: benchmark prompt set template.
- `prompts/qwen3-performance-prompts.json`: a stronger performance-oriented prompt set for `qwen3:14b`.
- `prompts/qwen3-style-prompts.json`: a separate prompt set for style-tolerance sweeps.

## Backends

The tuner currently supports:

- `llama_cpp_server`
- `ollama`

Practical difference:

- `llama.cpp` exposes the full search space and is the best first backend for real tuning.
- Ollama is still useful, but it exposes fewer tuning knobs. In practice the tuner only sweeps the options Ollama can control directly.

Container note:

- If the model server is already running in Docker, point the config at its mapped `api_base` URL and omit `start_command`.
- If the server must be restarted to apply a setting, use `start_command` with an attached `docker run ...` command and optional `stop_command` for cleanup.
- The Open WebUI-specific example binds the benchmark server to `127.0.0.1` on the host while still joining the shared Docker network.
- If your models live in Ollama's named volume, mount that volume read-only into the ephemeral `llama.cpp` container instead of copying files out first.
- Canonical black-box Ollama pattern: keep the real `ollama` container running, and run the tuner from an ephemeral client container that joins `open-webui_default` and connects to `http://ollama:11434`.

Canonical black-box command:

```powershell
docker compose -f "X:\Experiments\AI data\Local Model tuner\docker-compose.ollama-blackbox.yaml" run --rm `
  blackbox-runner `
  run --config /workspace/configs/cogito-14b.ollama.open-webui-network.json
```

If DMR exposes an OpenAI-compatible or `llama.cpp`-style endpoint, the `llama_cpp_server` path is the closest fit. If DMR has a different API, the adapter points in `tuner.py` are isolated and easy to extend.

## AITune side lab

AITune stays separate from the main `llama.cpp` and Ollama tuner because it operates one layer lower: PyTorch `nn.Module` and pipeline compilation rather than served GGUF endpoints.

Current AITune backend count from the upstream README:

- 4 backend families: `TensorRT`, `Torch-TensorRT`, `TorchAO`, and `Torch Inductor`
- 5 selectable backend targets if you want to inspect them one-by-one:
  `tensorrt`, `torch_tensorrt_jit`, `torch_tensorrt_aot`, `torchao`, `torch_inductor`

The local wrapper keeps those as ephemeral Docker runs too. You can list or probe them without folding them into the main tuner:

```powershell
& 'X:\Experiments\AI data\Local Model tuner\run-aitune.ps1' `
  -Action list-backends
```

Probe a single backend in its own disposable container:

```powershell
& 'X:\Experiments\AI data\Local Model tuner\run-aitune.ps1' `
  -Action probe-imports `
  -Backends torch_inductor
```

Drop into an interactive shell inside the AITune lab container:

```powershell
& 'X:\Experiments\AI data\Local Model tuner\run-aitune.ps1' `
  -Action shell `
  -Backends tensorrt
```

Notes:

- This path assumes a Linux NVIDIA container, matching AITune's stated prerequisites.
- The default image is `nvcr.io/nvidia/pytorch:25.02-py3`, but `run-aitune.ps1` lets you override it.
- The compose file installs AITune from `git+https://github.com/ai-dynamo/aitune.git` by default so the lab tracks the repo you pointed at.

## Search strategy

Default mode is `coordinate`.

That means:

1. Start from one baseline config.
2. Sweep one knob at a time in this order:
   `gpu_layers -> context_size -> batch_size -> micro_batch -> kv_cache_type -> flash_attn`
3. Keep the best winner after each sweep.
4. Run a small neighborhood refinement pass around the current winner.
5. Re-run the final winner a few times for stability.

This works well for your machine because it finds the cliff edges without brute-forcing thousands of combinations.

If you really want a full grid, set `"mode": "grid"` in the config.

## Performance vs style

Use separate configs for separate jobs:

- Performance tuning: search hardware and runtime knobs like `gpu_layers`, `context_size`, `batch_size`, `micro_batch`, and KV cache type.
- Style tuning: hold the hardware profile steady and search decode knobs like `temperature`, `top_p`, `top_k`, `min_p`, and `repeat_penalty`.

The `qwen3:14b` files in `configs/` now demonstrate both flows.

## Score

The default score favors sustained decode speed and penalizes:

- slow prompt processing
- failed runs
- likely OOM runs
- configs that push GPU memory above a soft limit

You can tune the weights in the config.

For style sweeps, the score switches to tolerance matching. It uses simple text heuristics as proxies for:

- `weirdness`
- `verbosity`
- `stiffness`

These are not universal truths. They are just a lightweight way to rank outputs against your preferred range.

## Quick start

Copy one of the example configs and edit the paths, model name, image, and API settings.

For a Docker-managed `llama.cpp` server using your Ollama model volume:

```powershell
Copy-Item `
  'X:\Experiments\AI data\Local Model tuner\configs\llama-cpp.open-webui-ollama-volume.example.json' `
  'X:\Experiments\AI data\Local Model tuner\configs\my-model.json'
```

Resolve the actual blob path from an Ollama model name:

```powershell
py -3 'X:\Experiments\AI data\Local Model tuner\resolve_ollama_model.py' `
  --container ollama `
  --volume open-webui_ollama `
  --model 'qwen3:14b'
```

Then copy the reported `primary_model.path_in_volume` value into `model_path` in your config.

For `qwen3:14b`, that has already been done in:

- `configs/qwen3-14b.open-webui-ollama-volume.json`
- `configs/qwen3-14b.style-tolerance.json`

Then plan the run:

```powershell
py -3 'X:\Experiments\AI data\Local Model tuner\tuner.py' plan `
  --config 'X:\Experiments\AI data\Local Model tuner\configs\my-model.json'
```

And execute it:

```powershell
py -3 'X:\Experiments\AI data\Local Model tuner\tuner.py' run `
  --config 'X:\Experiments\AI data\Local Model tuner\configs\my-model.json'
```

## Notes for your hardware

For an RTX 4070 Ti Super with 16 GB VRAM and 64 GB RAM, these are sensible defaults:

- `gpu_layers`: `8..40`
- `context_size`: `4096, 8192, 16384`
- `batch_size`: `128, 256, 512, 1024`
- `micro_batch`: `32, 64, 128, 256`
- `kv_cache_type`: `q4_0, f16, q8_0`
- `flash_attn`: `true, false`

The sample `llama.cpp` config uses exactly that shape.

If you want flash attention always on, pin the search dimension to `[true]`. The live `qwen3:14b` configs already do that, and the current Docker image expects `--flash-attn on|off`.

## Outputs

Each run creates a timestamped directory under `results/` with:

- `results.jsonl`: every trial, one line per config.
- `summary.json`: best config and top-ranked trials.
- `status.json`: current phase, trial, prompt, and config for live monitoring.
- `progress.log`: append-only status log for live monitoring and post-run review.
- `trial-*.log`: launcher stdout/stderr for managed `llama.cpp` runs.

Each prompt run also saves the backend response payload, so you are not limited to numbers only. That means you can inspect the actual generated text later during style sweeps.

You can also render terminal charts for any existing run:

```powershell
py -3 'X:\Experiments\AI data\Local Model tuner\tuner.py' report `
  --contains 'llama31-8b'
```

That report shows:

- score by trial
- decode tokens/sec by trial
- score and decode distributions
- per-dimension median score bars
- top-ranked configs
- recent progress log lines

For a live terminal view while a run is active:

```powershell
py -3 'X:\Experiments\AI data\Local Model tuner\tuner.py' watch `
  --run-dir 'X:\Experiments\AI data\Local Model tuner\results\your-run-dir'
```

## Important limitations

- The tuner expects one model per config file and one tuning run at a time.
- Ollama does not expose every low-level `llama.cpp` control, so unsupported knobs are skipped automatically.
- This script uses only the Python standard library. No extra packages are required.

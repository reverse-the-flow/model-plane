# Model Control Plane Roadmap

## Project Shape

Model Control Plane is one local project with two modes:

- **Launch Control**: launch, stop, inspect, validate, and export local inference endpoints.
- **Tuning Lab**: benchmark and tune runtime settings, then promote known-good results back into launch profiles.

Tuning must remain optional. The normal path is launch first, tune later.

## Core Principle

The stable object is the full stack profile:

```text
model + runtime image + launch args + hardware assumptions + ports + healthcheck + integration exports + known results
```

This avoids one-off launch scripts and preserves the exact recipe that made a model usable on this machine.

## Directory Layout

```text
Model Control Plane/
  README.md
  ROADMAP.md
  dockyard/
    README.md
    profiles/
    state/
    generated/
    notes/
  tuner/
    tuner.py
    llama_cpp_round_robin_tuner.py
    configs/
    prompts/
    notes/
    results/
```

## Phase 0: Stabilize The Workspace

Goal: make the new project layout explicit and avoid path confusion.

Deliverables:

- Parent `README.md` explains the two-mode architecture.
- `dockyard/README.md` explains launch-control ownership.
- Existing tuner remains intact under `tuner/`.
- Old `Local Model tuner` path remains usable through a symlink.
- Planning note lives under `dockyard/notes/`.

Acceptance checks:

- Existing tuner files are still reachable from `Model Control Plane/tuner`.
- Existing old path resolves to the moved tuner.
- Planning note is physically stored in `dockyard/notes`.

Status: complete.

## Phase 1: Stack Profile Schema

Goal: define the reusable recipe format shared by Launch Control and Tuning Lab.

Create `dockyard/profiles/schema.json` or an equivalent Python/Pydantic schema.

Minimum profile fields:

```yaml
id: gemma4-vllm
name: Gemma 4 vLLM
version: 1

model:
  id: google/gemma-...
  local_path: /models/gemma
  source: huggingface
  card_url: https://huggingface.co/...
  quant: nvfp4
  modality:
    - text

runtime:
  backend: vllm
  image: exact/image:tag
  command: []
  args: []
  image_policy:
    allow_latest: false
    pin_exact_tag: true

container:
  name: gemma4-vllm
  host_port: 18001
  internal_port: 8000
  gpu: all
  volumes: []
  env: {}
  shm_size: null

health:
  url: http://127.0.0.1:18001/v1/models
  expected_api: openai-compatible

integrations:
  hermes:
    provider: openai
    model: gemma4-local
    base_url: http://127.0.0.1:18001/v1
    api_key: local
  openclaw:
    alias: gemma4.local
    base_url: http://127.0.0.1:18001/v1

compatibility:
  hardware: []
  cuda: null
  notes: []
  known_good: []
  known_broken: []
```

Backend types for MVP:

- `llama_cpp`
- `vllm`
- `ollama_endpoint`
- `openai_compatible_endpoint`

Validation rules:

- Reject missing `id`, `model`, `runtime`, `container`, or `health`.
- Warn when image tag is `latest`.
- Warn when no healthcheck exists.
- Warn when host port is already reserved or active.
- Warn when a local model path does not exist.
- Warn when GPU is requested but Docker/NVIDIA runtime is unavailable.
- Require exact image tag unless `image_policy.allow_latest` is true.

Acceptance checks:

- At least one valid sample profile for `llama_cpp`.
- At least one valid sample profile for `vllm`.
- At least one valid sample profile for existing Ollama endpoint.
- Invalid profiles produce actionable validation messages.

## Phase 2: Dockyard Backend MVP

Goal: expose Launch Control as a local API while reusing Python as the project base.

Preferred stack:

- Python
- FastAPI
- Pydantic
- Docker CLI wrapper first
- Optional Docker SDK later if useful

Create:

```text
dockyard/backend/
  app.py
  profiles.py
  docker_runner.py
  validators.py
  state.py
  integrations.py
```

Minimum API:

- `GET /profiles`
- `GET /profiles/{id}`
- `POST /profiles/{id}/validate`
- `POST /profiles/{id}/render`
- `POST /profiles/{id}/launch`
- `POST /runs/{id}/stop`
- `POST /runs/{id}/restart`
- `GET /runs`
- `GET /runs/{id}`
- `GET /runs/{id}/logs`
- `POST /runs/{id}/health`
- `GET /profiles/{id}/integrations/hermes`
- `GET /profiles/{id}/integrations/openclaw`

Backend behavior:

- Read profiles from `dockyard/profiles`.
- Store runtime state in `dockyard/state`.
- Generate Docker Compose files in `dockyard/generated/docker-compose`.
- Support direct `docker run` first.
- Render command before launch.
- Never silently overwrite a running container.
- Keep logs discoverable by run ID.

Acceptance checks:

- A profile can be validated without launching.
- A Docker command can be rendered without launching.
- A llama.cpp profile can launch and stop.
- A vLLM profile can render correctly even before a real model is configured.
- An existing Ollama endpoint profile can healthcheck without Docker launch.
- Port collision is detected before launch.

## Phase 3: State Model

Goal: track local runtime state durably without introducing a database too early.

Initial files:

```text
dockyard/state/runs.json
dockyard/state/ports.json
dockyard/state/events.jsonl
dockyard/generated/docker-compose/*.yaml
tuner/results/
```

Concepts:

- **Profile**: reusable launch recipe.
- **Run**: active or historical container/endpoint instance.
- **Tune**: benchmark/tuning result from the tuner.
- **Preset**: known-good profile created from profile plus tuning result.

Run record fields:

```yaml
id: run-...
profile_id: gemma4-vllm
status: running
container_name: gemma4-vllm
host_port: 18001
endpoint: http://127.0.0.1:18001/v1
started_at: ...
stopped_at: null
health:
  status: unknown
  last_checked_at: null
logs:
  path: dockyard/state/logs/run-....log
rendered:
  docker_command: []
  compose_file: dockyard/generated/docker-compose/...
```

Acceptance checks:

- State survives backend restart.
- Stale state can be reconciled against `docker ps`.
- Port reservations are released when a run stops.
- Failed launches are recorded with error text.

## Phase 4: Frontend MVP

Goal: provide one dense local desktop-window app for launch control.

Preferred stack:

- React
- Vite
- TypeScript
- Tauri desktop shell
- Python FastAPI sidecar
- Local-only app window

Fallback stack:

- Electron shell
- React frontend
- Python backend subprocess

Create:

```text
dockyard/frontend/
  src/
  package.json
dockyard/APP_SPEC.md
```

Primary views:

- Profiles table
- Profile detail
- Runs table
- Logs panel
- Health/warnings panel
- Integration export panel

Profiles table columns:

- name
- backend
- model
- image
- host port
- status
- health
- warnings

Profile actions:

- Validate
- Render
- Launch
- Stop
- Restart
- Logs
- Health
- Copy endpoint
- Copy Hermes config
- Copy OpenClaw config

Warnings panel must show:

- `latest` image tag
- missing local model path
- occupied host port
- missing Docker
- missing NVIDIA runtime/GPU visibility
- missing healthcheck
- profile schema errors

Acceptance checks:

- User can open Dockyard as an app window.
- User can launch and stop from UI.
- User can see rendered command before launch.
- User can copy endpoint and integration config.
- UI does not require tuning setup.
- Saved profiles are readable as files on disk.

## Phase 5: Tuner Integration

Goal: make the existing tuner consume Dockyard profiles instead of only ad hoc tuning configs.

Keep `tuner.py` intact initially. Add adapters rather than rewriting it.

Add:

```text
tuner/profile_adapter.py
```

Responsibilities:

- Convert a Dockyard `llama_cpp` profile into the current `llama_cpp_server` tuner config shape.
- Convert an `ollama_endpoint` profile into the current `ollama` tuner config shape.
- Allow explicit tuning overrides for search dimensions.
- Write tuning summary back into a Dockyard-compatible result record.

Promotion flow:

```text
Dockyard profile
  -> tuner adapter
  -> tuning run
  -> result summary
  -> promoted known-good profile or preset
```

Tuning result should capture:

- throughput
- prompt latency
- decode latency
- context size
- batch size
- micro batch
- GPU layers
- GPU memory peak
- KV cache type
- flash attention setting
- failure notes
- promoted profile ID, if any

Acceptance checks:

- Existing JSON tuner configs still work.
- A Dockyard profile can generate a tuner config.
- A tuning result can be linked back to the source profile.
- A promoted preset can be saved under `dockyard/profiles`.

## Phase 6: vLLM First-Class Support

Goal: handle the main pain point: model-specific vLLM images and args.

Add backend-specific rendering for:

- image provenance
- `--model`
- `--served-model-name`
- `--host`
- `--port`
- `--max-model-len`
- `--gpu-memory-utilization`
- tensor parallel flags
- quantization flags
- volume mounts
- Hugging Face cache/token env vars
- shared memory sizing

vLLM-specific validation:

- Warn on unpinned image.
- Warn if image is generic `vllm/vllm-openai:latest`.
- Require model path or HF model ID.
- Warn when multimodal/quant/hardware notes are missing for special recipes.

Acceptance checks:

- A vLLM profile renders a sane Docker command.
- Healthcheck targets `/v1/models`.
- Hermes/OpenClaw export uses `/v1` base URL.
- Profile can record known-good and known-broken image notes.

## Phase 7: Recipe Sources And Model Cards

Goal: preserve upstream recipe provenance without making ingestion mandatory.

Start with manual fields:

- Hugging Face model card URL
- recipe URL
- recommended Docker image
- date checked
- hardware target
- quantization
- notes

Later add optional assisted ingestion:

- scrape or fetch HF model card metadata
- detect Docker image mentions
- detect model files
- detect quantization labels
- create draft profile, never auto-launch

Acceptance checks:

- Manual provenance can be stored in profile.
- Draft imported profiles require validation before launch.
- Imported profiles do not assume `latest` is safe.

## Phase 8: Later Backends

Add only after MVP works:

- TensorRT-LLM
- NVIDIA NIM-style containers, if useful
- Unsloth recipe notes/imports
- existing OpenAI-compatible endpoint registry
- proxy/router mode for stable aliases

Acceptance checks:

- New backends use the same Profile/Run/Health/Integration model.
- Backend-specific complexity stays behind backend adapters.

## Implementation Order

1. Define profile schema and examples.
2. Build validation and render-only backend.
3. Add direct Docker launch/stop for llama.cpp.
4. Add existing endpoint support for Ollama.
5. Add state files and reconciliation.
6. Add React dashboard.
7. Add vLLM render and validation.
8. Add vLLM launch.
9. Add tuner profile adapter.
10. Add promotion from tuning result to known-good profile.
11. Add model-card provenance helpers.

## Non-Goals For MVP

- Kubernetes.
- Remote multi-user auth.
- Cloud deployment.
- Automatic model downloading.
- Full model-card scraping.
- Perfect Docker SDK abstraction.
- Tuning every backend equally.
- Replacing the existing tuner.

## MVP Definition

The MVP is complete when:

- `dockyard` can list, validate, render, launch, stop, log, and healthcheck a `llama.cpp` container profile.
- `dockyard` can validate and healthcheck an existing Ollama endpoint profile.
- `dockyard` can render a vLLM profile with warnings for risky image choices.
- The UI exposes the above without requiring tuning.
- The existing tuner still runs from old configs.
- At least one tuning result can be associated with a Dockyard profile manually or through an adapter.

## Quality Bar

- Local-only by default.
- No hidden cloud dependency.
- Render command before launch.
- Do not use `latest` silently.
- Do not overwrite or kill unrelated containers.
- Preserve logs and failure messages.
- Keep backend-specific behavior isolated.
- Make every warning actionable.

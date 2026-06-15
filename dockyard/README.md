# Dockyard

Launch-control workspace for local model runtimes.

This is the sibling project to `../tuner`. It should manage model stack profiles for Dockerized or existing endpoints across backends such as:

- `llama.cpp`
- `vllm`
- `ollama`
- `tensorrt-llm` later
- existing OpenAI-compatible endpoints

## Directories

- `profiles/`: reusable stack profiles.
- `state/`: runtime state such as active runs and reserved ports.
- `generated/`: generated Docker Compose files or rendered launch artifacts.
- `notes/`: design notes and implementation planning.
- `docs/`: durable operator and agent contracts.

The exported planning note is `notes/model launcher control plane`.

## Agent Orchestration Role

Model Plane should be treated as a local orchestration/control layer for agents,
with the human UI serving as a readable console for status and inspection. The
durable bridge is machine-readable: profiles, validation messages, rendered
launch commands, health checks, log references, and integration manifests should
be available through backend endpoints or manifest fields so an agent does not
ask the user to copy ports or endpoints between tools.

The intended bridge flow is:

```text
profile -> launch -> run id -> health/logs/state -> export MoE probe manifest -> cleanup plan -> cleanup action
```

The cron-friendly orchestration flow is:

```text
cron tick -> agent job packets -> subagent/tool execution -> completion metadata -> cleanup/retry
```

Read [docs/agent-orchestration-contract.md](docs/agent-orchestration-contract.md)
for the current endpoint contract and safety boundaries.

## Cron Agent Jobs

Cron should call one deterministic tick entrypoint:

```bash
cd /home/codexlab/model-plane-bridge-work/dockyard/backend
python3 scripts/cron_tick.py
```

The same behavior is exposed as `POST /cron/tick` when the backend is running.
The tick reads profiles, run state, and the cleanup plan, then creates or reuses
small job packets in `dockyard/state/agent_jobs.json`.

Default jobs are plan/review oriented: `profile_validate`, `run_health_check`,
`moe_probe_plan`, and `cleanup_review`. Each packet includes a `function_id` and
a concrete `call` object with method, path, body, and side-effect description.
These are callable Model Plane API descriptors for external schedulers, Hermes,
OpenClaw, local cron wrappers, or skills; they are not shell commands. The tick
does not call Docker, download models, use tokens, launch model servers, send
prompts, call health endpoints, export manifests, or perform cleanup. Consumers
call the recorded function and then complete the job with metadata.

## Hugging Face Token Entry

The console has an `HF Token` button for setting `HF_TOKEN` in the running
backend process environment. The dialog uses a password input and has an
explicit `Remember on this machine` checkbox. By default, the token is
session/process scoped. When remember is selected, the backend also stores the
token in a local secret file: `HF_TOKEN_PATH` if configured, otherwise
`dockyard/state/secrets/hf_token`. The backend creates the app-owned secret
directory/file with user-only permissions where the platform supports it.
Existing custom parent directories are not tightened automatically.

The backend returns only safe metadata:

- `env_var: HF_TOKEN`
- `configured: true` or `false`
- `process_configured: true` or `false`
- `persistent_configured: true` or `false`
- `scope: process_env`, `persistent_file`, `process_env+persistent_file`, or `unset`
- `redacted: set` or `unset`
- `token_path_source: HF_TOKEN_PATH` or `dockyard_state`

If a remembered token exists and `HF_TOKEN` is not already set, the backend loads
the remembered value into `os.environ["HF_TOKEN"]` on startup or the next status
check so future pull/launch subprocesses can inherit it. A process-level
`HF_TOKEN` already supplied by the shell wins over the remembered file. Setting a
new token with remember unchecked updates only the current backend process; any
existing remembered file is left unchanged until `Clear` removes it or a later
remembered set overwrites it.

Model Plane does not echo the raw value from any API, store it in browser
storage, write it to profiles, manifests, run state, agent job state, logs, docs
examples, or Git, or include it in rendered Docker commands. Use `Clear` in the
dialog or `DELETE /secrets/hf-token` to remove it from both the current backend
process and the remembered local token file.

Hugging Face Hub libraries and subprocesses read `HF_TOKEN` from environment
variables, often at import or startup time. Set the token before starting a model
pull subprocess or importing code that needs Hub authentication.

## MoE Probe Manifest

Model Plane can export a compact JSON manifest for agents that need to plan a
MoE Run Anyway probe without understanding the full Dockyard profile schema.
The profile-level endpoint remains available for planning before launch:

```bash
curl http://127.0.0.1:19110/profiles/llama-cpp-example/moe-probe-manifest \
  -o moe-probe-manifest.json
```

After launch, agents should prefer the run-scoped manifest so downstream tools
receive the exact run id, launch result, health result, container name, endpoint,
and log reference:

```bash
curl http://127.0.0.1:19110/runs/run-.../moe-probe-manifest \
  -o moe-probe-manifest.json
```

Run state is persisted in `dockyard/state/runs.json`. Launch attempts are
recorded before Docker is called, so failed Docker commands still produce an
inspectable run id. The manifest includes the selected profile id, run id, model
id/path, backend family, base URL, health URL, optional log file path, container
name, latest health result, a primary probe hint, runtime observability paths,
readiness paths, log path metadata, and safety notes. Stock `llama.cpp`, vLLM,
Ollama, and other OpenAI-compatible backends are reported as
runtime-observability paths; they do not expose semantic expert ids unless a
profile explicitly declares a hookable local runtime.

For `llama.cpp`, MoE Run Anyway expects the profile to advertise `--metrics`,
`--slots`, `--props`, `--perf`, and log-file configuration. The example profile
maps a host log directory under `/mnt/Calliope/logs/model-plane/llama-cpp` into
the container as `/logs`, records the host log path for MoE `--log-file-path`,
and declares `/metrics`, `/slots`, `/props`, `/perf`, plus `/health` readiness
metadata under `moe_probe`. Validation warns, rather than errors, when a
`llama.cpp` profile is missing those observability fields.

## Cleanup Planning

Agents should ask for a dry-run cleanup plan before taking cleanup action:

```bash
curl http://127.0.0.1:19110/cleanup/plan
```

The plan lists failed, errored, unhealthy, stale launching, and explicitly
requested runs with proposed actions. It has no side effects. Run-scoped cleanup
records review notes by default:

```bash
curl -X POST http://127.0.0.1:19110/runs/run-.../cleanup \
  -H 'content-type: application/json' \
  -d '{"notes":"reviewed failed launch"}'
```

Container removal must be explicit and remains limited to the concrete container
name recorded on the run when that name starts with `dockyard-`:

```bash
curl -X POST http://127.0.0.1:19110/runs/run-.../cleanup \
  -H 'content-type: application/json' \
  -d '{"remove_container":true,"notes":"remove failed launch container"}'
```

## Manual Boundaries

Agents can list runs, inspect run state, check an explicit health URL, and export
run manifests. Starting a local runtime remains an intentional launch action, and
Model Plane still only stops containers whose names start with `dockyard-`.
Model Plane does not download models, inspect private tokens, start unrelated
services, or send prompt traffic as part of manifest export or health checks.

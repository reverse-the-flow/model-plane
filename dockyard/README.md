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
Session Capsule Gateway launch profiles are documented in
`docs/session-capsule-gateway.md`.
The local Model Plane helper is documented in
`docs/model-plane-local-helper.md`. The Capsule Handoff module is documented in
`docs/capsule-handoff-tray.md`.

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

For Hermes, OpenClaw, OpenCode, and similar harnesses, healthy runs can also
export a Harness Integration Bundle:

```text
profile/run -> integration bundle -> helper target or copyable harness config
```

The bundle includes the preferred OpenAI-compatible `/v1` URL, host and Docker
harness URL variants, minimal Hermes/OpenClaw snippets, and optional
connectivity checks against `/v1/models`. For OpenCode, the Model Plane Local
Helper can expose one stable `model-plane/active` endpoint so users do not hand
edit every model and endpoint into OpenCode. Open WebUI can use its own stable
helper `/v1` endpoint with generated env snippets for local or Docker installs.
The same helper can hold selected Hermes/OpenClaw bundle snippets and write
explicit drop-in config files. T3 Code is supported through its OpenCode
provider settings rather than as a direct `/v1` consumer.

By default, launch profiles are meant for private trusted home LANs: bind to
`0.0.0.0`, use no auth, and advertise a `.local` endpoint for nearby clients and
agents. Set `network.mode: local_only` when the model should stay on the current
machine or behind an SSH tunnel. `network.mode: secured_remote` is kept as a
future hardening target rather than pretending v1 has enterprise security.

The useful middle tier is not an auth gateway. It is a repo-visible version of
the low-friction node companion pattern: discover a home NVIDIA/local-runtime
node, launch or supervise the runtime, advertise the LAN endpoint, export
OpenAI-compatible harness bundles, and hand off capsules. That is intentionally
easy to run on a private network. People who want a hardened gateway can add one
around these endpoints without Model Plane owning that complexity in v1.

## NVIDIA Sync Custom App

For a GX10 or DGX Spark-style node, add Model Plane as a custom NVIDIA Sync app
using the web UI port:

```text
Name: Model Plane
Port: 19111
Auto open in browser: checked
URL Path: /
Launch in Terminal: on for first run/debugging, off normally
```

Launch Script:

```bash
export MODEL_PLANE_ROOT="$HOME/model-plane-github-upload"
bash "$MODEL_PLANE_ROOT/dockyard/scripts/nvidia-sync-model-plane.sh"
```

The script installs missing backend and frontend dependencies on first launch.
To prewarm that manually:

```bash
export MODEL_PLANE_ROOT="$HOME/model-plane-github-upload"
cd "$MODEL_PLANE_ROOT/dockyard/backend" && ./install-deps.sh
cd "$MODEL_PLANE_ROOT/dockyard/frontend" && npm ci
```

The custom app port must be just `19111`. The launcher starts the backend on
`127.0.0.1:19110`, starts the frontend on `0.0.0.0:19111`, and uses the Vite
proxy path `/model-plane-api` so the browser only needs the one Sync-opened web
port.

For unattended GX10 use, install the user-level systemd service instead of
leaving the launcher in a tmux session:

```bash
cd /home/codexlab/model-plane-bridge-work
bash dockyard/scripts/install-model-plane-user-service.sh
```

The service runs the same launcher, restarts it when the backend or frontend
exits, and inherits `DOCKER_HOST=unix:///run/user/%U/docker.sock` for rootless
Docker launches. Inspect it with:

```bash
systemctl --user status model-plane.service
journalctl --user -u model-plane.service -f
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

## MoE Test Launch Cards

The console exposes MoE Run Anyway launch/probe cards in separate evidence
tiers. The direct Mixtral cards are the ones intended for llama.cpp sidecar and
stock llama.cpp observability work:

- `llama-cpp-dolphin-mixtral-8x7b-sidecar`
- `llama-cpp-dolphin-mixtral-8x7b-existing`

The Ollama cards are deliberately labeled as opaque runtime baselines:

- `ollama-dolphin-mixtral-8x7b`
- `ollama-qwen3-30b`
- `ollama-qwen36-27b`
- `ollama-gemma4-31b`

The backend endpoint is:

```bash
curl http://127.0.0.1:19110/moe-test-cards
```

Model Plane finds the MoE Run Anyway checkout from `MOE_RUN_ANYWAY_ROOT` first,
then from known sibling/worker checkout paths such as
`../moe-run-anyway-github-upload` on the PC or
`~/moe-run-anyway-bridge-work` on codexlab. Each card renders the exact
`scripts/run_live_baseline.py` command it will call. Cards may also expose a
non-executed launch recipe, such as the Docker command for the
`memory-moe-llama-sidecar:latest` Mixtral sidecar image.

Preflight is endpoint-readiness only:

```bash
curl -X POST http://127.0.0.1:19110/moe-test-cards/llama-cpp-dolphin-mixtral-8x7b-sidecar/preflight
```

Smoke tests require explicit prompt-traffic approval and are bounded to one
prompt and one repeat:

```bash
curl -X POST http://127.0.0.1:19110/moe-test-cards/llama-cpp-dolphin-mixtral-8x7b-sidecar/smoke \
  -H 'content-type: application/json' \
  -d '{"approved_prompt_traffic": true}'
```

These cards produce bounded runtime artifacts. Stock llama.cpp cards can capture
`/metrics`, `/slots`, `/props`, optional log growth, and passive sidecar request
events when launched through the sidecar image. Ollama/OpenAI-compatible cards
remain request-boundary baselines. None of these tiers claim semantic expert ids;
that still requires a hookable local runtime or a llama.cpp/libllama fork.

## Harness Integration Bundles

Agents and users can export harness-ready endpoint details without copying ports
by hand:

```bash
curl http://127.0.0.1:19110/runs/run-.../integration-bundle
```

Before launch, profiles expose a preview:

```bash
curl http://127.0.0.1:19110/profiles/llama-cpp-example/integration-preview
```

To verify reachability without sending prompt traffic:

```bash
curl -X POST http://127.0.0.1:19110/runs/run-.../integration-bundle/check
```

The check probes only `/v1/models` for the host URL and Docker harness URL
(`host.docker.internal`). If a linked Session Capsule Gateway is healthy, its
`/v1` URL is preferred; the raw runtime URL is still included as fallback.

Bundles include the selected network mode and URL variants for LAN, localhost,
and Docker harness contexts. In the default `private_trusted_lan` mode, the
preferred endpoint is the advertised `.local` URL. In `local_only`, the preferred
endpoint remains `127.0.0.1` and the bundle carries the SSH tunnel hint.

## Capsule Handoff Tray

The Capsule Handoff Tray is a small local helper for the receiving PC. It binds
to `127.0.0.1` by default, starts at login when installed, and has one job:
receive a capsule bundle, verify it, hold it pending, and hand it to a Session
Capsule Gateway through `/api/capsules/handoff`.

```powershell
dockyard\backend\scripts\install_capsule_tray_startup.ps1
```

The tray rejects unsigned imports unless `allow_unsigned=true` is explicitly set
for a trusted-LAN experiment. It does not move model weights, transport live KV
tensors, browse remote capsule stores, or send prompt traffic.

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
Session Capsule Gateway profiles are supervised as local processes: Model Plane
can render, launch, health-check, report, and stop the gateway process, but the
gateway keeps ownership of capsule restore/checkpoint, transcript diffs, and
fallback replay. The model runtime keeps ownership of weights, tokenizer/runtime
internals, live KV cache, slots, and generation. Model Plane does not download
models, inspect private tokens, start unrelated services, move model weights,
store live KV tensors, manipulate runtime slots, or send prompt traffic as part
of manifest export or health checks.

# Agent Orchestration Contract

Model Plane is the local control layer for model runtime profiles. It should be
usable by a human in the desktop console, but the durable contract is the backend
API an agent can call without moving endpoint details by hand.

The control loop is:

```text
profile -> launch -> run id -> health/logs/state -> export MoE probe manifest -> cleanup plan -> cleanup action
```

The scheduled orchestration loop is:

```text
cron tick -> agent job packets -> subagent/tool execution -> completion metadata -> cleanup/retry
```

The console should make this loop readable. The API should make it operable.

## Agent Responsibilities

An agent may:

- list candidate profiles and choose one by `profile_id`
- validate the selected profile before launch
- render a launch command for review or execution by the control layer
- call launch only when the user has authorized starting a local runtime
- inspect the returned `run_id` and persisted run record
- check run health through the run's explicit health URL
- pass along run log references when they exist
- export a run-scoped MoE probe manifest for MoE Run Anyway
- hand the manifest to the MoE Run Anyway planner instead of asking the user to
  copy base URLs, ports, model ids, or log paths manually
- ask Model Plane for a dry-run cleanup plan before changing any runtime state
- record run-scoped cleanup review notes, or explicitly request removal of only
  the concrete `dockyard-*` container recorded on a run
- call the cron tick entrypoint to create bounded review packets from current
  profile and run state
- inspect agent jobs, call only the Model Plane function descriptor recorded on
  the packet, and complete the job with metadata
- inspect the redacted `HF_TOKEN` configured/unconfigured status
- set or clear `HF_TOKEN` only through the explicit human-facing secret endpoint
  when the user has provided the value for the current backend session

An agent must not treat the human UI as the source of truth. The UI is a status
and inspection surface over the profile and runtime state.

## Current Backend Surface

The backend surface is enough for the run-state MoE bridge:

| Endpoint | Purpose | Runtime effect |
| --- | --- | --- |
| `GET /profiles` | List saved profile summaries, validation counts, ports, health URLs. | Reads local profile files. |
| `GET /profiles/{profile_id}` | Read the full profile. | Reads one local profile. |
| `POST /profiles/{profile_id}/validate` | Return profile errors and warnings. | Reads local paths for validation only. |
| `POST /profiles/{profile_id}/render` | Return Docker command as an argv list and shell string. | Does not start Docker. |
| `POST /profiles/{profile_id}/launch` | Create a persisted run record, then launch the rendered runtime command. | Starts Docker only when explicitly called. Failed Docker commands still leave an inspectable run id. |
| `POST /profiles/{profile_id}/health` | Probe the configured health URL. | Sends a health request only. |
| `GET /profiles/{profile_id}/moe-probe-manifest` | Export a pre-launch MoE Run Anyway bridge manifest. | Reads profile data only. |
| `GET /functions` | List callable Model Plane function descriptors. | Reads the static function catalog. |
| `GET /functions/{function_id}` | Inspect one callable function descriptor. | Reads the static function catalog. |
| `GET /secrets/hf-token` | Return redacted `HF_TOKEN` status metadata. | Reads the current backend process environment only. |
| `POST /secrets/hf-token` | Strip and set `HF_TOKEN` in the running backend process environment. | Writes only to process environment; does not persist or return the token. |
| `DELETE /secrets/hf-token` | Clear `HF_TOKEN` from the running backend process environment. | Removes only the process environment value. |
| `GET /cleanup/plan` | List cleanup candidates and proposed run-scoped actions. | Reads `state/runs.json`; no Docker calls or state writes. |
| `POST /cron/tick` | Create or reuse bounded agent job packets from profiles, runs, and cleanup plan state. | Writes `state/agent_jobs.json`; no Docker, downloads, token use, prompt traffic, model launch, or cleanup execution. |
| `GET /agent-jobs` | List persisted agent job packets. | Reads `state/agent_jobs.json`. |
| `GET /agent-jobs/{job_id}` | Inspect one agent job packet. | Reads `state/agent_jobs.json`. |
| `POST /agent-jobs/{job_id}/complete` | Record completion metadata for one job. | Writes result metadata only. |
| `GET /runs` | List persisted run records. | Reads `state/runs.json`. |
| `GET /runs/{run_id}` | Inspect one persisted run record. | Reads `state/runs.json`. |
| `POST /runs/{run_id}/health` | Probe the run's explicit health URL and persist the result. | Sends a health request only. |
| `GET /runs/{run_id}/moe-probe-manifest` | Export the MoE Run Anyway bridge manifest for one concrete run. | Reads run state and profile data only. |
| `POST /runs/{run_id}/cleanup` | Persist cleanup review metadata and optionally remove the run's concrete container. | Calls `docker rm -f` only when `remove_container=true` and the recorded container name starts with `dockyard-`. |
| `POST /containers/{container_name}/stop` | Stop a Dockyard-managed container. | Stops containers whose names start with `dockyard-`. |

The run-scoped manifest endpoint is the preferred integration point for MoE Run
Anyway after launch. Manifest export does not launch containers, download
models, inspect tokens, start model servers, or send prompt traffic.

## Hugging Face Token Boundary

Model Plane supports a small session-scoped Hugging Face token flow. The UI
dialog accepts `HF_TOKEN` through a password input and posts it to the backend.
The backend strips surrounding whitespace, rejects an empty value, and stores the
token only in `os.environ["HF_TOKEN"]` for the running backend process.

The secret endpoints return only metadata: `env_var`, `configured`, `scope`, a
redacted set/unset marker, and a restart notice. They never echo or return the
raw token. The frontend does not use `localStorage` or `sessionStorage` for this
value and clears the input after a successful set.

The token is not persisted to disk and must be entered again after the backend
restarts. It must not appear in profiles, manifests, rendered Docker commands,
run state, agent job state, cron packets, logs, docs examples, test output, or
Git. Hugging Face Hub libraries and model pull subprocesses read `HF_TOKEN` from
environment variables, often at import or subprocess startup time, so the token
should be present before those subprocesses or imports begin.

The function catalog exposes:

- `secret.hf_token.status`: cron-readable redacted status only.
- `secret.hf_token.set`: not allowed for cron.
- `secret.hf_token.clear`: not allowed for cron.

All secret descriptors forbid logging, persisting, echoing, returning, or
including secret values in manifests, job state, or rendered commands.

## Run State

Run state is persisted in `dockyard/state/runs.json` with schema version
`model-plane-run-state-v1`. A run record includes:

- `run_id`, `profile_id`, and `profile_name`
- `created_at`, `updated_at`, and `status`
- `container_name`, `base_url`, `health_url`, and `log_file_path` when available
- `model_id`, `model_path`, and `backend_family`
- `launch_command`, `launch_shell_command`, `launch_returncode`,
  `launch_stdout`, and `launch_stderr`
- grouped `launch` details for human inspection
- `last_health_result` after `POST /runs/{run_id}/health`
- `last_cleanup_result`, `cleanup_status`, `cleanup_reviewed_at`, and
  `cleanup_history` after `POST /runs/{run_id}/cleanup`

`POST /profiles/{profile_id}/launch` creates the run record before Docker is
called. If Docker exits nonzero, the run status becomes `launch_failed` and the
return code/stdout/stderr remain durable. If the health endpoint is checked, the
run status becomes `healthy` or `unhealthy` based on that explicit result.

## Agent Job State

Agent jobs are persisted in `dockyard/state/agent_jobs.json` with schema version
`model-plane-agent-jobs-v1`. A job record includes:

- `job_id`, `job_type`, `status`, `source`, `created_at`, and `updated_at`
- optional `profile_id`, `run_id`, `model_id`, and `backend_family`
- `allowed_actions` and `forbidden_actions`
- `payload` with `function_id`, a concrete `call` descriptor, and legacy
  `api_path` or `command_class` fields where useful
- `result` and `history` for completion or review metadata

Open jobs are deduplicated by a stable key such as
`profile_validate:{profile_id}` or `moe_probe_plan:{run_id}`. Repeated cron
ticks reuse open jobs instead of creating duplicate packets. Completing a job
records metadata only; it does not run Docker, call health endpoints, export
manifests, or execute external commands.

Cron-created packets point at safe Model Plane functions, not shell commands.
The function catalog is available through `GET /functions`; each descriptor
includes `function_id`, `description`, `method`, `path_template`, `side_effect`,
`required_fields`, `allowed_for_cron`, `forbidden_actions`, and optional
`default_body`. External schedulers, Hermes, OpenClaw, local cron wrappers, or
skills should treat the packet as an API/function call request:

```json
{
  "function_id": "run.moe_probe_manifest.export",
  "call": {
    "method": "GET",
    "path": "/runs/run-.../moe-probe-manifest",
    "body": null,
    "side_effect": "read_only_manifest_export"
  }
}
```

The scheduler is responsible for deciding whether to make the HTTP call and then
recording completion metadata. Model Plane job creation itself still does not
execute those actions.

Current job types are:

- `profile_validate`: review validation messages for a profile through
  `POST /profiles/{profile_id}/validate`.
- `run_health_check`: an approved caller may check one run health URL through
  `POST /runs/{run_id}/health`.
- `moe_probe_plan`: export `GET /runs/{run_id}/moe-probe-manifest` and pass it
  to MoE Run Anyway in plan/review mode.
- `cleanup_review`: record run-scoped cleanup review metadata through
  `POST /runs/{run_id}/cleanup` with `remove_container=false`.

Every cron-created job forbids model downloads, token use, Docker prune, broad
deletion, unapproved prompt traffic, unapproved model launches, and model server
startup. Cleanup packets additionally forbid unapproved `docker rm` and run
record deletion. MoE probe packets additionally forbid probe prompt traffic and
sidecar startup without explicit operator approval.

## Cron Heartbeat

Cron or a systemd timer is the heartbeat. Model Plane remains the guardrail and
state layer; a local model may later consume job packets, but it does not need to
stay awake as a continuous orchestrator.

Example cron entry:

```cron
*/5 * * * * cd /home/codexlab/model-plane-bridge-work/dockyard/backend && /usr/bin/env python3 scripts/cron_tick.py >> /home/codexlab/model-plane-bridge-work/dockyard/state/cron_tick.log 2>&1
```

Example systemd service command:

```bash
cd /home/codexlab/model-plane-bridge-work/dockyard/backend
python3 scripts/cron_tick.py
```

The script directly calls the backend planner and writes job packets. If the API
server is already running, callers can use the equivalent endpoint:

```bash
curl -X POST http://127.0.0.1:19110/cron/tick
```

The tick reads profiles and runs, asks the existing cleanup planner for
candidates, then creates small packets with callable function descriptors.
Cron, Hermes, OpenClaw, local schedulers, or skills consume those packets by
calling the recorded Model Plane API function and then `job.complete`. The tick
does not call Docker, download models, use tokens, launch model servers, send
prompts, call health endpoints, export manifests, or execute cleanup.

## Cleanup Planning And Action

Cleanup is a safe orchestration primitive, not a broad Docker maintenance
operation. Agents should call `GET /cleanup/plan` before requesting any cleanup
action. The plan is a dry run: it lists candidate runs, candidate reasons, the
run id, profile id, status, container name, health URL, log path, proposed
actions, and action notes without writing state or calling Docker.

Current cleanup candidates include:

- `launch_failed`
- `launch_error`
- `unhealthy`
- stale `launching`
- run ids explicitly passed to `GET /cleanup/plan?run_id=...`

`POST /runs/{run_id}/cleanup` is the run-scoped execution surface. Without
`remove_container=true`, it records review metadata and notes only. With
`remove_container=true`, it may call `docker rm -f` for the single container name
stored on that run, and only if that name starts with `dockyard-`. Non-Dockyard
container names are recorded as refused cleanup results. Cleanup never calls
Docker prune and does not delete run records.

## MoE Probe Manifest Fields

`GET /profiles/{profile_id}/moe-probe-manifest` returns
`schema_version: model-plane-moe-probe-manifest-v1` and the small set of fields
MoE Run Anyway needs:

- `profile_id` and `profile_name`
- `model_id` and optional `model_path`
- `backend_family`
- `base_url` and `health_url`
- optional `log_file_path`
- `container_name`
- `primary_probe_hint`
- `semantic_expert_ids`
- `hookable_runtime_available`
- `passive_sidecar_requested`
- `observability_paths`, `readiness_paths`, and `log_paths`
- `runtime_observability.expected_paths`
- `runtime_observability.required_paths`
- `runtime_observability.readiness_paths`
- `runtime_observability.log_file_path`
- `safety_notes`

`GET /runs/{run_id}/moe-probe-manifest` returns the same bridge fields plus
run-specific fields:

- `run_id`
- `latest_health_result`
- `run_status`, `run_created_at`, and `run_updated_at`
- launch command, shell command, return code, stdout, and stderr

`primary_probe_hint` tells the downstream planner which safe path to prefer:

- `runtime_baseline`: active runtime baseline planning for a stock endpoint.
- `passive_sidecar`: non-invasive sidecar/proxy telemetry around the endpoint.
- `hookable_pytorch`: semantic hook path only when
  `hookable_runtime_available=true`.

`semantic_expert_ids` is an honesty field. Stock `llama.cpp`, vLLM, Ollama, and
OpenAI-compatible endpoints provide runtime evidence, not semantic expert ids.
Semantic expert ids require a hookable local runtime or future backend patch
that exposes router outputs.

### llama.cpp MoE Observability

For MoE Run Anyway compatibility, a `llama.cpp` profile should expose the stock
runtime evidence paths the harness can inspect:

- runtime args include `--metrics`, `--slots`, `--props`, and `--perf`
- runtime args configure a log file, usually container-side `--log-file
  /logs/<name>.log`
- the host log file path is present in `logs.file_path` or
  `moe_probe.log_file_path` so the MoE harness can pass it as
  `--log-file-path`
- `moe_probe.primary_probe_hint` is `runtime_baseline`
- `moe_probe.semantic_expert_ids` is `not_exposed`
- `moe_probe.observability_paths` lists `/metrics`, `/slots`, `/props`, and
  `/perf`
- `moe_probe.readiness_paths` lists the readiness endpoint, usually `/health`

Model Plane validation warns when a llama.cpp profile is missing these MoE
observability flags or log metadata. These are warnings, not hard errors,
because an example profile must remain inspectable even when local host paths do
not exist.

## Handoff To MoE Run Anyway

An agent should save the manifest as JSON and invoke the MoE planner:

```bash
python3 scripts/plan_moe_probe_manifest.py /path/to/moe-probe-manifest.json
```

That planner decides whether the manifest maps to the runtime baseline, passive
sidecar, or hookable semantic path. It can print command plans without starting
servers, downloading models, running Docker, authenticating, inspecting private
tokens, or sending prompt traffic.

## What Remains Manual

Model Plane is now durable enough for an agent to launch with user authorization,
inspect a run id, check health, and hand an exact run manifest to MoE Run
Anyway. The remaining manual surface is intentional authorization and local
operator judgment: selecting which profile to launch, confirming that starting a
container is acceptable, reading logs outside known log paths, and deciding
whether to stop a `dockyard-` container. Deeper UI customization can layer on top
of this contract without changing the agent run-state loop.

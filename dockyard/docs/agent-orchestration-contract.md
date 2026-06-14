# Agent Orchestration Contract

Model Plane is the local control layer for model runtime profiles. It should be
usable by a human in the desktop console, but the durable contract is the backend
API an agent can call without moving endpoint details by hand.

The control loop is:

```text
profile -> launch -> run id -> health/logs/state -> export MoE probe manifest -> MoE Run Anyway planner/probe
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
| `GET /runs` | List persisted run records. | Reads `state/runs.json`. |
| `GET /runs/{run_id}` | Inspect one persisted run record. | Reads `state/runs.json`. |
| `POST /runs/{run_id}/health` | Probe the run's explicit health URL and persist the result. | Sends a health request only. |
| `GET /runs/{run_id}/moe-probe-manifest` | Export the MoE Run Anyway bridge manifest for one concrete run. | Reads run state and profile data only. |
| `POST /containers/{container_name}/stop` | Stop a Dockyard-managed container. | Stops containers whose names start with `dockyard-`. |

The run-scoped manifest endpoint is the preferred integration point for MoE Run
Anyway after launch. Manifest export does not launch containers, download
models, inspect tokens, start model servers, or send prompt traffic.

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

`POST /profiles/{profile_id}/launch` creates the run record before Docker is
called. If Docker exits nonzero, the run status becomes `launch_failed` and the
return code/stdout/stderr remain durable. If the health endpoint is checked, the
run status becomes `healthy` or `unhealthy` based on that explicit result.

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
- `runtime_observability.expected_paths`
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

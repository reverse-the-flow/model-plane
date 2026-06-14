# Agent Orchestration Contract

Model Plane is the local control layer for model runtime profiles. It should be
usable by a human in the desktop console, but the durable contract is the backend
API an agent can call without moving endpoint details by hand.

The control loop is:

```text
profile -> validate -> launch/health/logs -> export MoE probe manifest -> MoE Run Anyway planner/probe
```

The console should make this loop readable. The API should make it operable.

## Agent Responsibilities

An agent may:

- list candidate profiles and choose one by `profile_id`
- validate the selected profile before launch
- render a launch command for review or execution by the control layer
- call launch only when the user has authorized starting a local runtime
- check profile health through the configured health URL
- pass along profile log references when they exist
- export a MoE probe manifest for MoE Run Anyway
- hand the manifest to the MoE Run Anyway planner instead of asking the user to
  copy base URLs, ports, model ids, or log paths manually

An agent must not treat the human UI as the source of truth. The UI is a status
and inspection surface over the profile and runtime state.

## Current Backend Surface

The current backend surface is enough for the MoE bridge:

| Endpoint | Purpose | Runtime effect |
| --- | --- | --- |
| `GET /profiles` | List saved profile summaries, validation counts, ports, health URLs. | Reads local profile files. |
| `GET /profiles/{profile_id}` | Read the full profile. | Reads one local profile. |
| `POST /profiles/{profile_id}/validate` | Return profile errors and warnings. | Reads local paths for validation only. |
| `POST /profiles/{profile_id}/render` | Return Docker command as an argv list and shell string. | Does not start Docker. |
| `POST /profiles/{profile_id}/launch` | Launch the rendered runtime command. | Starts Docker only when explicitly called. |
| `POST /profiles/{profile_id}/health` | Probe the configured health URL. | Sends a health request only. |
| `GET /profiles/{profile_id}/moe-probe-manifest` | Export the MoE Run Anyway bridge manifest. | Reads profile data only. |
| `POST /containers/{container_name}/stop` | Stop a Dockyard-managed container. | Stops containers whose names start with `dockyard-`. |

The manifest endpoint is the preferred integration point for MoE Run Anyway. It
does not launch containers, download models, inspect tokens, start model
servers, or send prompt traffic.

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

## Missing Future Surface

The next useful bridge work is not another manual UI panel. It is a run-state
API that records launches, log locations, and health results by run id, then
lets the manifest reference a concrete run instead of only the source profile.
That would make `launch -> health -> logs -> manifest` fully durable across
agent turns.

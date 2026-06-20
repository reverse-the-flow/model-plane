# Session Capsule Gateway Service

Model Plane can supervise a Session Capsule Gateway as a launchable local
service. It starts and stops the gateway process, health-checks the configured
status endpoint, records run state, and reports the OpenAI-compatible client
base URL for tools such as Open WebUI or opencode.

Model Plane does not become the gateway. It must not transport model weights,
store live KV tensors, inspect runtime slots, or manipulate hard capsule files
directly.

The gateway source remains the standalone Session Capsule repository:
`https://github.com/reverse-the-flow/session-capsule.git`. A Model Plane profile
points at a local checkout or gateway executable path; it does not vendor the
gateway code.

## Ownership Boundary

Model Plane owns:

- endpoint registry metadata
- launch profiles
- process or container lifecycle
- health checks
- policy and routing metadata
- job packets such as checkpoint, resume, export, and validate requests

Session Capsule Gateway owns:

- the OpenAI-compatible request path
- thread ledger lookup
- capsule restore and checkpoint behavior
- transcript diffs
- transcript replay fallback
- `/api/capsules/status`, `/api/capsules/threads`, and
  `/api/capsules/checkpoint`

The model runtime owns:

- model weights
- tokenizer and runtime internals
- live KV cache
- runtime slots
- actual generation

Hard capsules are runtime-specific local snapshots. They are useful for fast
local resume, but they are not portable model artifacts. Transcript replay
fallback must stay available so a capsule can still resume when a hard snapshot
is missing, stale, or incompatible with the active runtime.

## Profile Shape

Use `runtime.backend: capsule_gateway` or `profile_type: capsule_gateway`.
The profile renders to:

```powershell
py -3 <session-capsule-repo>\scripts\capsule_gateway.py `
  --state-dir <capsule-state-dir> `
  --endpoint <endpoint-id> `
  --host 0.0.0.0 `
  --port 8765 `
  --checkpoint-mode soft `
  --slot 0 `
  --default-prefill user_default
```

Required `capsule_gateway` fields:

- `name`
- `repo_path` or `executable_path`
- `state_dir`
- `endpoint_id`
- `host`
- `port`
- `checkpoint_mode`
- `slot`
- `default_prefill`
- `healthcheck_url`
- `client_base_url`

Profiles also accept a top-level `network` block. The default posture is
`network.mode: private_trusted_lan`, which binds the gateway on `0.0.0.0`, uses
no auth, and advertises a `.local` OpenAI-compatible client URL for home LAN
clients. Use `network.mode: local_only` to bind to `127.0.0.1` for single-PC or
SSH-tunnel use. `network.mode: secured_remote` is reserved for future
token/Tailscale/TLS-style hardening.

The health check should point at `/api/capsules/status`, and `client_base_url`
should point at the OpenAI-compatible `/v1` base URL. Health checks stay on the
explicit configured URL so Model Plane can probe the local supervisor path.
Client URLs such as `http://127.0.0.1:8765/v1` are rewritten to the advertised
LAN URL in private LAN mode while the localhost variant remains available in
integration bundles.

See `dockyard/profiles/session-capsule-llama-cpp.example.yaml` for a local
llama.cpp-backed gateway profile.

## Lifecycle

The usual Model Plane flow still applies:

```text
profile -> render -> launch -> run id -> health -> status -> stop
```

Useful backend calls:

- `POST /profiles/{profile_id}/render` returns the exact process command and
  `client_base_url`.
- `POST /profiles/{profile_id}/launch` starts the gateway and records a run id,
  PID, health URL, and client base URL.
- `POST /runs/{run_id}/health` calls the run's explicit
  `/api/capsules/status` URL and records the result.
- `GET /runs/{run_id}/status` returns lifecycle state, process status, health
  metadata, and the client base URL.
- `POST /runs/{run_id}/stop` stops only the recorded process PID for capsule
  gateway runs.

Model Plane health checks are readiness probes only. They do not send prompt
traffic, checkpoint state, resume state, export capsules, or inspect runtime
cache.

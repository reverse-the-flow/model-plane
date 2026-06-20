# Model Control Plane

Home-lab launch control for local OpenAI-compatible model runtimes: profile,
start, health-check, advertise, and hand off endpoints without turning the
project into an enterprise auth gateway.

Parent workspace for local model launch and tuning work.

## Layout

- `tuner/`: existing Local Model Tuner project. This owns benchmark runs, tuning sweeps, scoring, prompts, and known tuning configs.
- `dockyard/`: launch control workspace. This will own reusable model/runtime/container profiles, generated Docker Compose files, runtime state, health checks, logs, and integration exports.
- `dockyard/APP_SPEC.md`: desktop app and UI direction, including saved profiles and metallic control-panel aesthetic.

## Intended Flow

1. Create or import a launch profile in `dockyard/profiles`.
2. Validate and launch the model runtime from Dockyard.
3. Run benchmarks or tuning sweeps from `tuner`.
4. Promote good tuning results back into reusable Dockyard profiles.

Dockyard answers whether a model stack runs reliably. Tuner answers which settings run best.

Dockyard also exposes a cron-friendly orchestration surface. A scheduled
`dockyard/backend/scripts/cron_tick.py` call creates bounded agent job packets
for profile validation, run health review, MoE probe planning, and cleanup
review. Packets contain callable Model Plane function descriptors, not shell
commands, so external schedulers or skills can call the explicit API function and
then record completion metadata. Job creation itself does not start Docker,
download models, use tokens, launch model servers, send prompts, or perform
cleanup.

Dockyard includes a small Hugging Face token entry flow for local model pulls.
The UI's `HF Token` control posts a token to the running backend, which strips
surrounding whitespace and stores it in that backend process environment as
`HF_TOKEN`. The dialog also has an explicit `Remember on this machine` option.
When selected, the backend writes the token only to the configured local secret
file: `HF_TOKEN_PATH` when set, otherwise Dockyard's git-ignored
`dockyard/state/secrets/hf_token` path. Status endpoints and the UI show only
metadata such as process/persistent configured state, a redacted marker, and the
path source. The raw value is not written to profiles, manifests, run state,
agent jobs, logs, docs examples, rendered commands, browser storage, or Git.

Hugging Face Hub tooling uses `HF_TOKEN` from environment variables, and some
libraries read environment state at import or subprocess startup time. Set the
token before starting model pull subprocesses or imports that need Hub access.

Dockyard can also supervise a Session Capsule Gateway as a launchable local
service profile. In that mode Model Plane owns only the profile, process
lifecycle, health checks, and client URL reporting; the gateway owns capsule
restore/checkpoint and transcript replay, while the model runtime owns weights,
live KV cache, slots, and generation. See
`dockyard/docs/session-capsule-gateway.md`.

Healthy runs can export Harness Integration Bundles for Hermes, OpenClaw,
OpenCode, and similar tools. Bundles include the preferred `/v1` endpoint,
host/Docker URL variants, copyable config snippets, and `/v1/models`
connectivity evidence. The Model Plane Local Helper can consume those bundles
on the end-user machine and expose a stable OpenCode `model-plane/active`
endpoint. It can expose a separate stable Open WebUI `/v1` endpoint with
generated env snippets, and can also hold selected Hermes/OpenClaw snippets and
write explicit drop-in config files. T3 Code can then use the OpenCode path
through its provider settings; it is not treated as a direct OpenAI `/v1`
harness in this helper.

Launch profiles default to a home-network posture: bind on `0.0.0.0`, no auth,
and advertise a `.local` client URL for private trusted LANs. Profiles can opt
into `local_only` for single-machine or SSH-tunnel use, while `secured_remote`
is reserved for later hardened token/Tailscale/TLS-style deployments.

The intended middle tier is a low-friction home-lab node experience, in the same
spirit as NVIDIA Sync for DGX Spark-style PC-to-node workflows: discover the
node, launch the runtime, expose the local OpenAI-compatible endpoint, and hand
off capsules or harness config without making the user build an auth gateway
first. Hardened remote access is deliberately left as an extension point for
people who want to add tokens, reverse proxies, Tailscale, TLS, or policy
gateways.

## NVIDIA Sync / DGX Spark Custom App

For a GX10 or DGX Spark-style home node, Model Plane can be launched from NVIDIA
Sync as a custom web app. The custom app should open the frontend port; the
frontend proxies API calls back to the backend on the node.

NVIDIA Sync custom app fields:

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

The `Port` field must be only the numeric web UI port. Do not put `127.0.0.1`
there. The launcher starts the backend on `127.0.0.1:19110`, starts the web UI
on `0.0.0.0:19111`, and routes browser API calls through `/model-plane-api` so
NVIDIA Sync only needs to open one port.

On first launch, the script installs missing backend and frontend dependencies.
To prewarm that manually:

```bash
export MODEL_PLANE_ROOT="$HOME/model-plane-github-upload"
cd "$MODEL_PLANE_ROOT/dockyard/backend" && ./install-deps.sh
cd "$MODEL_PLANE_ROOT/dockyard/frontend" && npm ci
```

The Capsule Handoff Tray is a separate local PC helper for receiving, verifying,
staging, and handing capsule bundles to a Session Capsule Gateway. It is
documented in `dockyard/docs/capsule-handoff-tray.md` and is intentionally not a
general file sync or remote capsule management service.

## Repository Boundary

This repository is source and configuration only. Local model files, generated
Docker Compose output, runtime state, dependency folders, and benchmark result
runs are intentionally excluded from git.

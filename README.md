# Model Control Plane

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

## Repository Boundary

This repository is source and configuration only. Local model files, generated
Docker Compose output, runtime state, dependency folders, and benchmark result
runs are intentionally excluded from git.

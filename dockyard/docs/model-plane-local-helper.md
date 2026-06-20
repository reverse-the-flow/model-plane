# Model Plane Local Helper

The Model Plane Local Helper is the end-user machine companion for local model
handoff. It is one localhost service with optional modules, not a remote control
plane.

Default startup is status-only:

```powershell
dockyard\backend\scripts\run_model_plane_helper.ps1
```

Status-only mode exposes:

```text
http://127.0.0.1:19112/helper/status
http://127.0.0.1:19112/helper/modules
```

Enable modules explicitly at startup:

```powershell
dockyard\backend\scripts\run_model_plane_helper.ps1 -Modules capsule_handoff
dockyard\backend\scripts\run_model_plane_helper.ps1 -Modules opencode_harness
dockyard\backend\scripts\run_model_plane_helper.ps1 -Modules openwebui_harness
dockyard\backend\scripts\run_model_plane_helper.ps1 -Modules hermes_harness,openclaw_harness
dockyard\backend\scripts\run_model_plane_helper.ps1 -Modules opencode_harness,openwebui_harness,t3code_harness
dockyard\backend\scripts\run_model_plane_helper.ps1 -Modules capsule_handoff,opencode_harness,openwebui_harness,hermes_harness,openclaw_harness,t3code_harness
```

Or set:

```text
MODEL_PLANE_HELPER_MODULES=capsule_handoff,opencode_harness,openwebui_harness,hermes_harness,openclaw_harness,t3code_harness
```

## Modules

| Module | Default | Purpose |
| --- | --- | --- |
| `status` | on | Local helper status and module discovery. |
| `capsule_handoff` | off | Receive, verify, stage, and hand off capsule bundles. |
| `opencode_harness` | off | Stable OpenAI-compatible endpoint for OpenCode. |
| `openwebui_harness` | off | Stable OpenAI-compatible endpoint and env snippets for Open WebUI. |
| `hermes_harness` | off | Hermes config snippet import, selection, and drop-in file export. |
| `openclaw_harness` | off | OpenClaw route snippet import, selection, and drop-in file export. |
| `t3code_harness` | off | T3 Code settings patch for its OpenCode provider. |

The helper binds to `127.0.0.1` by default. It does not transfer model weights,
transport live KV tensors, or enable capsule or harness behavior unless that
module is configured at startup.

## OpenCode Harness

The OpenCode harness module gives OpenCode one stable provider:

```text
provider: model-plane
model: active
baseURL: http://127.0.0.1:19112/harness/opencode/v1
```

Copy the generated config:

```text
GET http://127.0.0.1:19112/helper/opencode/config
```

The generated provider block contains no API key. OpenCode points at
`model-plane/active`; the helper rewrites `active` to the selected upstream
runtime model id.

Import a Model Plane integration bundle:

```text
POST /helper/opencode/targets/import
POST /helper/opencode/targets/{target_id}/select
```

The helper stores endpoint metadata only. Prompt traffic happens only when
OpenCode calls:

```text
POST /harness/opencode/v1/chat/completions
```

The module supports `/v1/chat/completions` for v1. `/v1/responses` is not part
of this helper version.

## Open WebUI Harness

Open WebUI can consume OpenAI-compatible endpoints directly. The helper exposes
one stable endpoint:

```text
model: active
baseURL: http://127.0.0.1:19112/harness/openwebui/v1
Docker baseURL: http://host.docker.internal:19112/harness/openwebui/v1
```

Import and select a Model Plane integration bundle:

```text
POST /helper/openwebui/targets/import
POST /helper/openwebui/targets/{target_id}/select
```

Open WebUI then lists:

```text
GET /harness/openwebui/v1/models
```

and sends prompt traffic to:

```text
POST /harness/openwebui/v1/chat/completions
```

Generated environment configuration:

```text
GET /helper/openwebui/config
GET /helper/openwebui/config?context=local
GET /helper/openwebui/config?context=docker
```

The default `docker` context emits:

```text
ENABLE_OPENAI_API=True
OPENAI_API_BASE_URLS=http://host.docker.internal:19112/harness/openwebui/v1
OPENAI_API_KEYS=local
DEFAULT_MODELS=active
```

Apply those variables to an explicit env-file path with backup:

```text
POST /helper/openwebui/config/apply
```

Open WebUI treats these connection variables as persistent configuration after
startup. If an existing Open WebUI database already has saved connection
settings, update them in Admin Settings or reset/disable persistent config so
the env vars take effect.

## Hermes And OpenClaw Harnesses

Hermes and OpenClaw do not auto-discover arbitrary OpenAI-compatible endpoints
from the helper. They still need config, but the helper can hold the selected
Model Plane integration bundle and produce the correct snippet on the receiving
machine.

Import and select a bundle:

```text
POST /helper/hermes/targets/import
POST /helper/openclaw/targets/import

POST /helper/hermes/targets/{target_id}/select
POST /helper/openclaw/targets/{target_id}/select
```

Render the selected snippet:

```text
GET /helper/hermes/config?format=yaml
GET /helper/openclaw/config?format=yaml
```

Apply the selected snippet to an explicit drop-in path with backup:

```text
POST /helper/hermes/config/apply
POST /helper/openclaw/config/apply
```

The apply endpoints write only the selected snippet to the requested file path.
They do not silently edit unknown global Hermes/OpenClaw config layouts. Formats
are `yaml`, `json`, and `text`.

## T3 Code Harness

T3 Code 0.0.27 does not consume a raw OpenAI-compatible `/v1` endpoint directly
for this path. Its local integration point is the built-in OpenCode provider:

```text
T3 Code -> OpenCode server/CLI -> OpenCode provider model-plane/active -> helper -> selected local /v1 target
```

The T3 Code helper module emits a patch for:

```text
~\.t3\userdata\settings.json
```

Generated patch:

```text
GET http://127.0.0.1:19112/helper/t3code/config
```

Apply it with backup:

```text
POST http://127.0.0.1:19112/helper/t3code/config/apply
```

The patch enables T3 Code's `opencode` provider, adds `model-plane/active` as a
custom model, and can set that model as the active text generation selection.
It does not install OpenCode. If `serverUrl` is left blank, T3 Code attempts to
spawn `opencode serve`; otherwise, `serverUrl` must point at an OpenCode app
server, not at the helper's `/harness/opencode/v1` endpoint.

## Startup Task

Install a current-user startup task:

```powershell
dockyard\backend\scripts\install_model_plane_helper_startup.ps1
dockyard\backend\scripts\install_model_plane_helper_startup.ps1 -Modules capsule_handoff,opencode_harness
dockyard\backend\scripts\install_model_plane_helper_startup.ps1 -Modules openwebui_harness
dockyard\backend\scripts\install_model_plane_helper_startup.ps1 -Modules hermes_harness,openclaw_harness
dockyard\backend\scripts\install_model_plane_helper_startup.ps1 -Modules opencode_harness,openwebui_harness,t3code_harness
```

Remove it:

```powershell
dockyard\backend\scripts\uninstall_model_plane_helper_startup.ps1
```

The older Capsule Handoff Tray scripts remain as compatibility wrappers. They
launch this helper with only `capsule_handoff` enabled.

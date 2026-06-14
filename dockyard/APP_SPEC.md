# Dockyard App Spec

## Product Direction

Dockyard should feel like a local control-panel app, not a loose collection of scripts or a documentation-heavy web page.

The user should open a window, see saved model profiles, understand which runtimes are available, and launch or inspect a model without remembering Docker commands.

## App Form

Preferred shape:

- Desktop app window.
- Local-only backend.
- Saved profiles on disk.
- No cloud account.
- No browser tab required for normal use.

Recommended implementation:

- React + Vite + TypeScript for the UI.
- Tauri desktop shell for the app window.
- Python FastAPI sidecar for profile validation, Docker control, health checks, logs, and tuner integration.

Fallback implementation if Tauri sidecars become annoying:

- Electron + React frontend.
- Python backend launched as a subprocess.

The backend should remain Python-first because the existing tuner is Python and should not be rewritten just to create a window.

## Primary Views

### Profiles

The default view should be a dense, readable table of saved launch profiles.

Columns:

- Profile name
- Model
- Backend
- Runtime image
- Quant
- Host port
- Status
- Health
- Warnings

Primary actions:

- Validate
- Launch
- Stop
- Restart
- Logs
- Health
- Copy endpoint
- Copy Hermes config
- Copy OpenClaw config

### Profile Detail

Profile detail should make the full stack recipe interpretable.

Sections:

- Model
- Runtime
- Container
- Healthcheck
- Integrations
- Compatibility notes
- Known-good results
- Known-broken notes
- Rendered Docker command

The rendered command should be visible before launch.

### Runs

The runs view should show active and recent launches.

Fields:

- Run ID
- Profile
- Container name
- Endpoint
- Started at
- Stopped at
- Status
- Last health result
- Log path

### Logs

Logs should be readable in-app with:

- live tail
- pause/resume
- copy
- clear filter
- severity/warning search if easy

### Tuning

Tuning should appear as a second mode, not part of the launch path.

The tuning view should:

- list tuning runs
- show best config
- show throughput and latency
- show GPU memory peak
- link back to source profile
- allow "promote to known-good profile"

## Saved Profiles

Profiles are saved under:

```text
dockyard/profiles/
```

Profile files should be easy to read and edit manually. YAML is preferred for human readability, with JSON schema or Pydantic validation underneath.

Profile examples should be named clearly:

```text
profiles/
  llama-cpp.example.yaml
  vllm.example.yaml
  ollama-endpoint.example.yaml
```

Profiles should be cloneable from the UI. The normal workflow should be:

```text
Clone known-good profile
Edit model/image/path/port
Validate
Launch
Tune later if needed
```

## Visual Direction

The aesthetic should be metallic, technical, and readable.

Useful cues:

- brushed metal / graphite surfaces
- subtle bevels and borders
- cool neutral palette
- restrained accent colors for status
- compact control-panel density
- monospaced text for commands, ports, image tags, and paths
- clear status lights for stopped/running/unhealthy

Avoid:

- marketing-style hero layouts
- large decorative cards
- playful colors
- excessive gradients
- one-note dark blue or purple palette
- hiding important config behind vague labels

Suggested palette:

```text
background: #17191b
panel:      #222629
panel-2:    #2e3337
edge:       #6f777d
text:       #e6e1d8
muted:      #a8a39b
accent:     #c9a968
success:    #78b892
warning:    #d8a24a
danger:     #d46a5f
info:       #80a7c7
```

This should read as graphite, steel, and brass rather than a generic dark theme.

## Interaction Principles

- The first screen is the usable profile dashboard.
- Every risky action shows what will happen before it runs.
- Launching a container must never hide the rendered Docker command.
- Warnings should be specific and actionable.
- Profiles should remain understandable as files on disk.
- Tuning should enhance profiles, not block launches.
- The app should make ports, images, paths, and health obvious at a glance.

## MVP Window Definition

The app-window MVP is complete when:

- The user can open Dockyard as a desktop window.
- Saved profiles are listed from `dockyard/profiles`.
- A selected profile can be validated.
- A rendered Docker command is visible.
- A supported profile can be launched and stopped.
- Logs and health status are visible in the app.
- Endpoint and integration configs can be copied.
- Profiles remain human-readable on disk.

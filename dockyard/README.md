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
profile -> validate -> launch/health/logs -> export MoE probe manifest -> MoE Run Anyway planner/probe
```

Read [docs/agent-orchestration-contract.md](docs/agent-orchestration-contract.md)
for the current endpoint contract and safety boundaries.

## MoE Probe Manifest

Model Plane can export a compact JSON manifest for agents that need to plan a
MoE Run Anyway probe without understanding the full Dockyard profile schema:

```bash
curl http://127.0.0.1:19110/profiles/llama-cpp-example/moe-probe-manifest \
  -o moe-probe-manifest.json
```

The manifest includes the selected profile id, model id/path, backend family,
base URL, health URL, optional log file path, container name, a primary probe
hint, and safety notes. Stock `llama.cpp`, vLLM, Ollama, and other
OpenAI-compatible backends are reported as runtime-observability paths; they do
not expose semantic expert ids unless a profile explicitly declares a hookable
local runtime.

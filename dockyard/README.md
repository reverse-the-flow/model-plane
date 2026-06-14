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

The exported planning note is `notes/model launcher control plane`.

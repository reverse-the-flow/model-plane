# AITune Evaluation For The Local Model Tuner

## Decision

NVIDIA AITune is not the main path for this project.

It solves a different problem:

- AITune tunes raw PyTorch models and pipelines by trying graph and compiler backends such as TensorRT, Torch-TensorRT, TorchAO, and Torch Inductor.
- This tuner is built around already-served local models, especially `llama.cpp` and Ollama, where the dominant knobs are runner settings such as `gpu_layers`, `context_size`, `batch_size`, `micro_batch`, KV cache type, and decode options.

For the current workflow, AITune should be treated as a separate side experiment rather than a replacement.

## Why More Layers Is Not Always Better

In theory, optimizing every layer of the stack can produce the best end-to-end result.

In practice, that is only true when all of the following hold:

- each layer is actually under your control
- the layers expose stable and meaningful knobs
- the optimizers are not fighting each other
- the comparison remains empirical rather than black-box

For this project, the stack is already split:

- GGUF quantization and model conversion already happened upstream
- `llama.cpp` exposes runner-level controls directly
- Ollama hides some implementation details and may apply its own behavior internally

That means AITune does not currently reach the same layer as our benchmarked knobs. For a GGUF served through `llama.cpp` or Ollama, the best gains usually come from the runner layer you can observe and control directly.

## Why It Does Not Fit The Mainline Workflow

- The current tuner works on one local model at a time through `llama.cpp` and Ollama HTTP endpoints.
- AITune targets Python model code, not GGUF server processes.
- AITune is Linux and NVIDIA oriented, while this workflow is Windows-hosted with Docker-managed local runners.
- The current tuner is designed to compare white-box and black-box serving behavior. AITune would introduce a third mode that is not apples-to-apples with either.

## Where AITune Could Still Be Useful

AITune becomes interesting if we create a separate test track for raw Hugging Face or PyTorch models running in a Linux NVIDIA environment.

That would answer a different question:

- "How fast can this architecture run when compiled and tuned as a PyTorch model?"

instead of the current question:

- "What is the best way to run this already-quantized local model through the runners I actually use?"

## Side Experiment Plan

If we test AITune, keep it separate from the main tuner results.

The local wrapper now reflects that decision:

- `docker-compose.aitune-lab.yaml` runs AITune as a disposable side lab.
- `run-aitune.ps1` launches the side lab or a single backend-specific container.
- backend inspection is ephemeral too, so you can look at one backend without turning all of them on at once.

Upstream backend count, based on the AITune README:

- 4 backend families:
  `TensorRT`, `Torch-TensorRT`, `TorchAO`, `Torch Inductor`
- 5 selectable backend targets for inspection:
  `tensorrt`, `torch_tensorrt_jit`, `torch_tensorrt_aot`, `torchao`, `torch_inductor`

Recommended pilot model:

- `llama3.1:8b`

Why this one:

- it already has white-box and black-box results in this project
- it is text-only and simpler than multimodal or reasoning-specialized models
- it is easier to align across prompt templates than models with internal think controls

Experiment outline:

1. Use a Linux NVIDIA container that satisfies AITune prerequisites.
2. Load the raw Hugging Face model and tokenizer rather than the GGUF artifact.
3. Reuse the same benchmark prompt set from this project.
4. Match generation settings as closely as possible:
   `temperature`, `top_p`, `max_tokens`, and prompt template.
5. Disable CPU fallback and reject any run that spills into an unintended execution mode.
6. Measure:
   time-to-first-token if available,
   decode tokens/sec,
   end-to-end latency,
   peak GPU memory,
   compile/tune time,
   output sanity.
7. Compare that result set to:
   the existing `llama.cpp` run,
   the existing Ollama run,
   and the tuned best config from this project.

## Rules For Interpreting The Comparison

- Do not treat AITune vs `llama.cpp` as a winner-take-all comparison.
- Treat it as "compiled raw model path" vs "served GGUF path".
- Keep the experiment separate from the main leaderboard because the artifact format, runtime stack, and hidden optimizations differ.
- If the AITune path cannot match prompt template behavior or stable decoding, stop the experiment rather than force a misleading chart.

## Current Recommendation

- Keep the local model tuner as the main tool.
- Add better visualization to the tuner so completed runs show distributions and not only a winner.
- If curiosity remains high, run one contained AITune pilot on `llama3.1:8b` as a separate benchmark track.
- Use the new AITune lab wrapper to validate backend availability first, then narrow the backend set before attempting a real pilot.

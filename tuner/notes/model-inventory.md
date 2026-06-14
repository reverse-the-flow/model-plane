# Local Model Inventory

This note preserves the model list captured in the tuning thread so we do not lose the shortlist between sessions.

It is a conversation snapshot, not a live `ollama list` export. If the container changes later, treat this as a working note and refresh it.

## Provided Model List

- `hf.co/unsloth/Qwen3.5-27B-GGUF:Q4_K_M`
- `llama4:17b-scout-16e-instruct-q4_K_M`
- `glm-4.7-flash:Q8_0`
- `dolphin-mixtral:8x7b`
- `qwen3-coder-next:Q4_K_M`
- `llama3.1:70b`
- `mistral-nemo:12b`
- `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q3_K_S`
- `qwen3-coder:30b`
- `nemotron-3-nano:30b-a3b-q4_K_M`
- `olmo-3:latest`
- `hf.co/unsloth/DeepSeek-V3.1-GGUF:TQ1_0`
- `gpt-oss:20b`
- `sunzhiyuan/suntray-qwen3:1.5b`
- `hf.co/tiiuae/Falcon3-7B-Instruct-GGUF:Q8_0`
- `hf.co/Mungert/MiMo-VL-7B-SFT-GGUF:Q4_K_M`
- `hf.co/microsoft/bitnet-b1.58-2B-4T-gguf:latest`
- `deepseek-r1:8b-0528-qwen3-q8_0`
- `hf.co/bartowski/PrimeIntellect_INTELLECT-2-GGUF:Q4_K_M`
- `hf.co/bartowski/nvidia_AceReason-Nemotron-7B-GGUF:Q4_K_M`
- `huihui_ai/nemotron-v1-abliterated:8b`
- `phi4-reasoning:plus`
- `exaone-deep:7.8b`
- `granite3.3:8b`
- `qwen3:14b`
- `deepcoder:latest`
- `phi4:latest`
- `olmo2:13b`
- `llama3.1:8b`
- `deepseek-r1:14b`
- `cogito:14b`
- `gemma3:12b`
- `hf.co/mradermacher/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-i1-GGUF:IQ3_XS`

## Tested In This Project

Tested in both white-box (`llama.cpp`) and black-box (`ollama`) runs:

- `qwen3:14b`
- `mistral-nemo:12b`
- `olmo2:13b`
- `llama3.1:8b`
- `deepseek-r1:14b`
- `exaone-deep:7.8b`

Tested only in black-box form:

- none recorded

Tested only in white-box form:

- `cogito:14b`

## Partial Or Problematic

- `gemma3:12b`
  white-box `llama.cpp` run failed because Gemma 3 support and/or model compatibility was not in a usable state for the image we were using at the time.
  black-box `ollama` run exists.

- `hf.co/mradermacher/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-i1-GGUF:IQ3_XS`
  white-box run started but was not a clean benchmark.
  It became CPU-heavy because too few layers were offloaded for a model that large, and the run was stopped.

- `cogito:14b`
  white-box `llama.cpp` run completed cleanly.
  black-box `ollama` run could not benchmark because the published host-side Ollama API at `127.0.0.1:11434` returned `model not found` even for known models, so the failure was environmental rather than model-specific.

## Mentioned As Strong Candidates

These came up as likely next models or ranked well in your local arena discussion:

- `gemma3:12b`
- `qwen3:14b`
- `olmo2:13b`
- `llama3.1:8b`
- `exaone-deep:7.8b`
- `deepseek-r1:14b`
- `cogito:14b`
- `phi4:latest`

## Not Yet Run In This Tuner

From the preserved list above, these do not have recorded run directories yet:

- `hf.co/unsloth/Qwen3.5-27B-GGUF:Q4_K_M`
- `llama4:17b-scout-16e-instruct-q4_K_M`
- `glm-4.7-flash:Q8_0`
- `dolphin-mixtral:8x7b`
- `qwen3-coder-next:Q4_K_M`
- `llama3.1:70b`
- `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q3_K_S`
- `qwen3-coder:30b`
- `nemotron-3-nano:30b-a3b-q4_K_M`
- `olmo-3:latest`
- `hf.co/unsloth/DeepSeek-V3.1-GGUF:TQ1_0`
- `gpt-oss:20b`
- `sunzhiyuan/suntray-qwen3:1.5b`
- `hf.co/tiiuae/Falcon3-7B-Instruct-GGUF:Q8_0`
- `hf.co/Mungert/MiMo-VL-7B-SFT-GGUF:Q4_K_M`
- `hf.co/microsoft/bitnet-b1.58-2B-4T-gguf:latest`
- `deepseek-r1:8b-0528-qwen3-q8_0`
- `hf.co/bartowski/PrimeIntellect_INTELLECT-2-GGUF:Q4_K_M`
- `hf.co/bartowski/nvidia_AceReason-Nemotron-7B-GGUF:Q4_K_M`
- `huihui_ai/nemotron-v1-abliterated:8b`
- `phi4-reasoning:plus`
- `granite3.3:8b`
- `deepcoder:latest`
- `phi4:latest`
- `cogito:14b`

## Good Next Picks

If we want practical next runs from this preserved list, these are still good candidates:

- `cogito:14b`
  close in size to models that already ran well on your hardware, so it is a clean comparison candidate.

- `phi4:latest`
  likely to be easy to run and useful as a smaller, strong baseline.

- `granite3.3:8b`
  another manageable-size comparison point that should be easier to benchmark cleanly.

- `dolphin-mixtral:8x7b`
  interesting if you want to see how a MoE-style model behaves under the same tuner logic.

- `gpt-oss:20b`
  worthwhile if you want a larger reasoning-oriented checkpoint without jumping straight back into the problematic 27B distill run.

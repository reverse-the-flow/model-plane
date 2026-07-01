# Android Edge Pre-Work

This lane records Android edge evidence before MoE Run Anyway touches devices
directly. Physical phones are the only valid targets for inference throughput,
thermals, battery drain, sustained throttling, mobile GPU/NPU behavior, and app
runtime memory limits. Emulators are UI and connectivity rehearsal only.

There is no Docker path on Android in this project. Model Plane does not build
Android apps, install Android apps, call ADB, push model weights, or supervise
phone runtimes. It only records evidence gathered outside Dockyard.

Android work should happen in one of two external lanes:

- External app baseline: use an operator-installed app such as PocketPal to
  learn what actually runs on the repaired phone.
- Raw device dev: use a separate ADB, Termux, or native-binary workflow for
  direct llama.cpp-style testing when that is worth doing.

## Card Tiers

`GET /moe-test-cards` includes three Android cards:

- `android-pocketpal-baseline`: first physical-device baseline for an
  operator-installed PocketPal or similar on-device GGUF runtime.
- `android-adb-llama-cpp-baseline`: reserved for a later direct ADB/llama.cpp
  or other raw native-device path; this pre-work only records manual evidence.
- `android-emulator-ui-only`: emulator UI, install-flow, permissions, or network
  rehearsal. It is not inference-performance evidence.

These cards use `execution_mode: manual_evidence`. They do not render runnable
preflight or smoke commands, do not require a MoE Run Anyway checkout, and do not
send prompt traffic. They also do not imply that Model Plane owns Android app
development. They always report `semantic_expert_ids: not_exposed` and
`hookable_runtime_available: false`.

## Manual Evidence

Manual evidence is recorded with:

```bash
curl -X POST http://127.0.0.1:19110/moe-test-cards/android-pocketpal-baseline/manual-evidence \
  -H 'content-type: application/json' \
  -d '{
    "approved_manual_evidence": true,
    "evidence": {
      "device_label": "screen-repair-phone",
      "android_version": "unknown",
      "chipset": "unknown",
      "ram_gb": null,
      "dev_path": "external_app",
      "app_runtime": "PocketPal",
      "model_id": "example-model-q4.gguf",
      "model_format": "GGUF",
      "quant": "Q4",
      "acceleration_path": "app-reported",
      "tokens_per_second": null,
      "time_to_first_token_ms": null,
      "battery_note": "not recorded",
      "thermal_note": "not recorded",
      "observations": "baseline install/load check"
    },
    "notes": "first physical baseline"
  }'
```

The backend writes a bounded artifact directory under
`dockyard/state/moe-run-anyway-runs` by default, or under
`MOE_RUN_ANYWAY_TEST_OUTPUT_DIR` when configured. Each manual run writes:

- `manifest.json`
- `summary.json`
- `events.jsonl`
- `manual-evidence.json`

## First Physical Pass

Use this sequence after screen repair:

1. Record the exact phone label, Android version, chipset if known, RAM/storage
   notes, dev path, and runtime version.
2. Outside Model Plane, run PocketPal or another on-device GGUF runtime with one
   small model that clearly fits device memory.
3. Record model id, format, quant, acceleration path if the app reports it,
   tokens/sec and TTFT if available, plus battery and thermal notes.
4. Save the result through the `android-pocketpal-baseline` manual evidence
   endpoint.
5. Only after this baseline exists, decide whether a direct ADB/llama.cpp pass is
   worth planning.

If the next step is raw device development, keep that workflow separate from
Dockyard: a standalone script, notes packet, or separate repo can own ADB,
Termux, native binary builds, and device file placement. Model Plane should only
receive the resulting measurements.

Do not infer expert routing, semantic expert ids, router logits, or paging policy
behavior from this lane. Those remain part of the hookable runtime path.

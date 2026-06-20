# Capsule Handoff Tray

The Capsule Handoff Tray is now an optional module inside the Model Plane Local
Helper. Its job is deliberately narrow:

```text
receive a capsule bundle -> verify it -> hold it pending -> handshake with gateway -> hand it off
```

Capsules are handed off, not remotely managed. The tray does not browse remote
capsule stores, sync arbitrary folders, move model weights, transport live KV
tensors, or decide runtime restore behavior.

## Why It Exists

`0.0.0.0` and mDNS make a gateway reachable on the LAN, but they do not control
where downloaded files land on the PC. The tray gives the PC a local, explicit
place to receive and stage capsule artifacts before a thread restore.

The helper binds to `127.0.0.1` by default. Remote machines should not write
directly into the tray. A browser, local agent, or local UI imports capsule
bundles into the tray, then the tray performs an explicit handoff to the
gateway. See `model-plane-local-helper.md` for the shared helper shell.

## Startup

The tray should start at login so capsule handoff does not become a hidden
troubleshooting step.

Run manually through the compatibility wrapper:

```powershell
dockyard\backend\scripts\run_capsule_handoff_tray.ps1
```

The wrapper launches `model_plane_helper.app` with `capsule_handoff` enabled.

Install the current-user startup task:

```powershell
dockyard\backend\scripts\install_capsule_tray_startup.ps1
```

Remove it:

```powershell
dockyard\backend\scripts\uninstall_capsule_tray_startup.ps1
```

Default status URL:

```text
http://127.0.0.1:19112/tray/status
```

## API

The local tray API is intentionally small:

| Endpoint | Purpose |
| --- | --- |
| `GET /tray/status` | Show helper status and local state directory. |
| `POST /tray/import` | Import a capsule bundle from inline JSON or an HTTP URL. |
| `GET /tray/pending` | List pending capsule bundles. |
| `GET /tray/items/{bundle_id}` | Inspect one staged capsule. |
| `POST /tray/attach` | Send one staged capsule to a gateway `/api/capsules/handoff`. |
| `POST /tray/items/{bundle_id}/reject` | Reject a staged capsule. |
| `POST /tray/items/{bundle_id}/expire` | Expire a staged capsule. |

Example inline import for trusted LAN testing:

```json
{
  "allow_unsigned": true,
  "bundle": {
    "capsule_id": "capsule-123",
    "thread_id": "thread-abc",
    "capsule_kind": "soft",
    "restore_requirements": {
      "hard_snapshot": {
        "requires_launch_card_digest": "launch-card-sha256"
      },
      "soft_replay": {
        "source_model_id": "nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4",
        "min_context_tokens": 12000
      }
    },
    "fallback": {
      "transcript_replay": true
    }
  }
}
```

Example attach:

```json
{
  "bundle_id": "capsule-123",
  "gateway_url": "http://gx10-81e8.local:8765",
  "thread_id": "thread-abc",
  "target_launch": {
    "launch_card_digest": "launch-card-sha256",
    "context_size": 32768
  }
}
```

Attach is a two-step gateway handshake:

1. `prepare`: the tray sends bundle id, thread id, size, SHA-256, and target
   launch hints.
2. `commit`: only after the gateway accepts, the tray sends the staged artifact
   with the returned handoff id.

Both calls go only to:

```text
/api/capsules/handoff
```

It does not call `/v1/chat/completions` or send prompt traffic.

## Verification

The tray supports two verification layers:

- bundle checksums through `checksums.json`
- manifest signatures through `hmac-sha256` trusted keys

Unsigned imports are rejected unless the import request explicitly sets
`allow_unsigned: true`. That keeps private-LAN experimentation possible without
making unsigned import look normal.

The signed manifest shape is:

```json
{
  "capsule_id": "capsule-123",
  "thread_id": "thread-abc",
  "capsule_kind": "soft",
  "restore_requirements": {},
  "fallback": { "transcript_replay": true },
  "signature": {
    "algorithm": "hmac-sha256",
    "key_id": "gx10",
    "value": "hex-hmac"
  }
}
```

Trusted keys can be supplied with:

```text
CAPSULE_HANDOFF_TRAY_TRUSTED_KEYS_JSON
CAPSULE_HANDOFF_TRAY_TRUSTED_KEYS
```

## Compatibility Boundary

The tray does not invent cross-model compatibility. It compares facts and sends
them to the gateway:

- hard restore requires an exact launch-card/runtime match
- non-exact targets use transcript replay only
- no transcript replay fallback means the bundle should stay pending or be
  rejected

The Session Capsule Gateway owns parsing, ledger attach, restore/checkpoint, and
fallback replay. Model Plane owns launch supervision and endpoint reporting. The
model runtime owns weights, tokenizer/runtime internals, slots, live KV cache,
and generation.

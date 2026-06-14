#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${MODEL_CONTROL_PLANE_VENDOR:-${HOME}/.local/share/model-control-plane/backend-vendor}"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${VENDOR_DIR}:${SCRIPT_DIR}:${PYTHONPATH:-}"

exec python3 -m uvicorn model_control_plane.app:app --host 127.0.0.1 --port 19110 "$@"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${MODEL_CONTROL_PLANE_VENDOR:-${HOME}/.local/share/model-control-plane/backend-vendor}"
MODEL_PLANE_BIND_HOST="${MODEL_PLANE_BIND_HOST:-0.0.0.0}"
MODEL_PLANE_PORT="${MODEL_PLANE_PORT:-19110}"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${VENDOR_DIR}:${SCRIPT_DIR}:${PYTHONPATH:-}"

exec python3 -m uvicorn model_control_plane.app:app --host "${MODEL_PLANE_BIND_HOST}" --port "${MODEL_PLANE_PORT}" "$@"

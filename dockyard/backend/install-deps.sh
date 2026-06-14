#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${MODEL_CONTROL_PLANE_VENDOR:-${HOME}/.local/share/model-control-plane/backend-vendor}"

mkdir -p "${VENDOR_DIR}"
python3 -m pip install --target "${VENDOR_DIR}" -r "${SCRIPT_DIR}/requirements.txt"

echo "Installed backend dependencies into ${VENDOR_DIR}"

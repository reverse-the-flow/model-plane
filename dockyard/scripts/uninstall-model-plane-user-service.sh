#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${MODEL_PLANE_SERVICE_NAME:-model-plane.service}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
SERVICE_PATH="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"

systemctl --user disable --now "${SERVICE_NAME}" 2>/dev/null || true
rm -f "${SERVICE_PATH}"
systemctl --user daemon-reload

echo "Model Plane user service removed: ${SERVICE_NAME}"

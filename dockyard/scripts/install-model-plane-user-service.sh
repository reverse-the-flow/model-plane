#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_PLANE_ROOT="${MODEL_PLANE_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
SERVICE_NAME="${MODEL_PLANE_SERVICE_NAME:-model-plane.service}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
SERVICE_PATH="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
WEB_PORT="${MODEL_PLANE_WEB_PORT:-19111}"
BACKEND_PORT="${MODEL_PLANE_PORT:-19110}"

mkdir -p "${SYSTEMD_USER_DIR}"

cat >"${SERVICE_PATH}" <<SERVICE
[Unit]
Description=Model Plane Dockyard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${MODEL_PLANE_ROOT}/dockyard
Environment=MODEL_PLANE_ROOT=${MODEL_PLANE_ROOT}
Environment=MODEL_PLANE_BIND_HOST=127.0.0.1
Environment=MODEL_PLANE_WEB_BIND_HOST=0.0.0.0
Environment=MODEL_PLANE_PORT=${BACKEND_PORT}
Environment=MODEL_PLANE_WEB_PORT=${WEB_PORT}
Environment=DOCKER_HOST=unix:///run/user/%U/docker.sock
ExecStart=/usr/bin/env bash ${MODEL_PLANE_ROOT}/dockyard/scripts/nvidia-sync-model-plane.sh
Restart=always
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}"
systemctl --user --no-pager --full status "${SERVICE_NAME}"

echo
echo "Model Plane user service installed: ${SERVICE_NAME}"
echo "Web UI: http://127.0.0.1:${WEB_PORT} or the node LAN address on port ${WEB_PORT}"
echo "Logs: journalctl --user -u ${SERVICE_NAME} -f"

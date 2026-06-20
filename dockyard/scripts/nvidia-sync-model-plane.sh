#!/usr/bin/env bash
set -euo pipefail

NAME="model-plane"
MODEL_PLANE_ROOT="${MODEL_PLANE_ROOT:-${HOME}/model-plane-github-upload}"
DOCKYARD_DIR="${MODEL_PLANE_ROOT}/dockyard"
BACKEND_PORT="${MODEL_PLANE_PORT:-19110}"
WEB_PORT="${MODEL_PLANE_WEB_PORT:-19111}"
BACKEND_BIND="${MODEL_PLANE_BIND_HOST:-127.0.0.1}"
WEB_BIND="${MODEL_PLANE_WEB_BIND_HOST:-0.0.0.0}"
LOG_DIR="${DOCKYARD_DIR}/state/logs"
PID_DIR="${DOCKYARD_DIR}/state/pids"
BACKEND_PID_FILE="${PID_DIR}/${NAME}-backend.pid"
WEB_PID_FILE="${PID_DIR}/${NAME}-web.pid"
BACKEND_VENDOR="${MODEL_CONTROL_PLANE_VENDOR:-${HOME}/.local/share/model-control-plane/backend-vendor}"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

is_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null
}

stop_pid_file() {
  local label="$1"
  local pid_file="$2"
  if is_running "${pid_file}"; then
    local pid
    pid="$(cat "${pid_file}")"
    echo "Stopping ${label} (${pid})..."
    kill "${pid}" >/dev/null 2>&1 || true
    wait "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${pid_file}"
}

cleanup() {
  echo "Signal received; stopping ${NAME}..."
  stop_pid_file "Model Plane web UI" "${WEB_PID_FILE}"
  stop_pid_file "Model Plane backend" "${BACKEND_PID_FILE}"
  exit 0
}
trap cleanup INT TERM HUP QUIT EXIT

require_command() {
  local command="$1"
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Error: ${command} is required on the node." >&2
    exit 1
  fi
}

load_node_env() {
  if command -v npm >/dev/null 2>&1; then
    return
  fi

  export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"
  if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
    # shellcheck source=/dev/null
    . "${NVM_DIR}/nvm.sh"
    nvm use --silent default >/dev/null 2>&1 || nvm use --silent node >/dev/null 2>&1 || true
  fi
}

if [[ ! -d "${DOCKYARD_DIR}" ]]; then
  echo "Error: MODEL_PLANE_ROOT does not point at the repo: ${MODEL_PLANE_ROOT}" >&2
  exit 1
fi

require_command python3
load_node_env
require_command npm

ensure_backend_deps() {
  if PYTHONPATH="${BACKEND_VENDOR}" python3 -c "import fastapi, uvicorn, yaml" >/dev/null 2>&1; then
    return
  fi

  echo "Installing backend dependencies..."
  (
    cd "${DOCKYARD_DIR}/backend"
    MODEL_CONTROL_PLANE_VENDOR="${BACKEND_VENDOR}" bash ./install-deps.sh
  )
}

start_backend() {
  if is_running "${BACKEND_PID_FILE}"; then
    echo "Model Plane backend already running on ${BACKEND_BIND}:${BACKEND_PORT}"
    return
  fi

  ensure_backend_deps

  echo "Starting Model Plane backend on ${BACKEND_BIND}:${BACKEND_PORT}"
  (
    cd "${DOCKYARD_DIR}/backend"
    MODEL_CONTROL_PLANE_VENDOR="${BACKEND_VENDOR}" MODEL_PLANE_BIND_HOST="${BACKEND_BIND}" MODEL_PLANE_PORT="${BACKEND_PORT}" bash ./run-backend.sh
  ) >"${LOG_DIR}/model-plane-backend.log" 2>&1 &
  echo "$!" >"${BACKEND_PID_FILE}"
}

start_frontend() {
  if is_running "${WEB_PID_FILE}"; then
    echo "Model Plane web UI already running on ${WEB_BIND}:${WEB_PORT}"
    return
  fi

  if [[ ! -d "${DOCKYARD_DIR}/frontend/node_modules" ]]; then
    echo "Installing frontend dependencies..."
    (cd "${DOCKYARD_DIR}/frontend" && npm ci)
  fi

  echo "Starting Model Plane web UI on ${WEB_BIND}:${WEB_PORT}"
  (
    cd "${DOCKYARD_DIR}/frontend"
    VITE_MODEL_PLANE_API="${VITE_MODEL_PLANE_API:-/model-plane-api}" \
      VITE_MODEL_PLANE_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}" \
      npm run dev -- --host "${WEB_BIND}" --port "${WEB_PORT}"
  ) >"${LOG_DIR}/model-plane-frontend.log" 2>&1 &
  echo "$!" >"${WEB_PID_FILE}"
}

start_backend
start_frontend

echo "Model Plane is ready for NVIDIA Sync on port ${WEB_PORT}"
echo "Backend log: ${LOG_DIR}/model-plane-backend.log"
echo "Frontend log: ${LOG_DIR}/model-plane-frontend.log"
echo "Running. Press Ctrl+C to stop ${NAME}."

while true; do
  if ! is_running "${BACKEND_PID_FILE}"; then
    echo "Model Plane backend stopped. Last log lines:"
    tail -n 80 "${LOG_DIR}/model-plane-backend.log" || true
    exit 1
  fi
  if ! is_running "${WEB_PID_FILE}"; then
    echo "Model Plane web UI stopped. Last log lines:"
    tail -n 80 "${LOG_DIR}/model-plane-frontend.log" || true
    exit 1
  fi
  sleep 5
done

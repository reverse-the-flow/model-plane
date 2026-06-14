#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-list-backends}"
shift || true

INSTALL_TARGET="${AITUNE_INSTALL_TARGET:-git+https://github.com/ai-dynamo/aitune.git}"
EXTRA_PACKAGES="${AITUNE_EXTRA_PIP_PACKAGES:-}"
WORKSPACE="${AITUNE_WORKSPACE:-/workspace}"
PROBE_SCRIPT="${WORKSPACE}/aitune-lab/scripts/aitune_probe.py"

ensure_command() {
  local command_name="$1"
  local package_name="${2:-$1}"
  if command -v "${command_name}" >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends "${package_name}"
    rm -rf /var/lib/apt/lists/*
    return 0
  fi
  echo "Unable to install missing command '${command_name}' automatically." >&2
  return 1
}

ensure_aitune() {
  if python -c "import aitune" >/dev/null 2>&1; then
    return 0
  fi

  python -m pip install --upgrade pip

  if [[ "${INSTALL_TARGET}" == git+* ]]; then
    ensure_command git git
  fi

  python -m pip install --extra-index-url https://pypi.nvidia.com "${INSTALL_TARGET}"

  if [[ -n "${EXTRA_PACKAGES}" ]]; then
    # Intentional word splitting so callers can pass a normal pip-style package list.
    python -m pip install ${EXTRA_PACKAGES}
  fi
}

case "${ACTION}" in
  shell)
    ensure_aitune
    exec /bin/bash "$@"
    ;;
  list-backends|probe-imports|env-report)
    ensure_aitune
    exec python "${PROBE_SCRIPT}" "${ACTION}" "$@"
    ;;
  *)
    ensure_aitune
    exec "$ACTION" "$@"
    ;;
esac

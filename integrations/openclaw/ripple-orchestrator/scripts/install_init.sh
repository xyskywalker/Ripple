#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

INSTALL_SCRIPT=""
if [[ -n "${RIPPLE_INSTALL_SCRIPT:-}" && -f "${RIPPLE_INSTALL_SCRIPT}" ]]; then
  INSTALL_SCRIPT="${RIPPLE_INSTALL_SCRIPT}"
fi

if [[ -z "${INSTALL_SCRIPT}" ]]; then
  CANDIDATE="${HOME}/.ripple/src/Ripple/install.sh"
  if [[ -f "${CANDIDATE}" ]]; then
    INSTALL_SCRIPT="${CANDIDATE}"
  else
    CANDIDATE="${SCRIPT_DIR}/../../../../install.sh"
    if [[ -f "${CANDIDATE}" ]]; then
      INSTALL_SCRIPT="${CANDIDATE}"
    fi
  fi
fi

if [[ -z "${INSTALL_SCRIPT}" || ! -f "${INSTALL_SCRIPT}" ]]; then
  printf '%s\n' '{"ok":false,"error":"install script not found"}'
  exit 1
fi

bash "${INSTALL_SCRIPT}" >/dev/null

doctor_json="$(run_ripple_cli doctor)"
llm_json="$(run_ripple_cli llm show || true)"
if [[ -z "${llm_json}" ]]; then
  llm_json='{"ok":false,"error":"llm show failed"}'
fi

printf '{"ok":true,"doctor":%s,"llm_show":%s}\n' "${doctor_json}" "${llm_json}"

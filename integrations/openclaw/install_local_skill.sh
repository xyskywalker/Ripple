#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/ripple-orchestrator"
TARGET_ROOT="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}"
TARGET_DIR="${TARGET_ROOT}/ripple-orchestrator"
STAGING_DIR=""
BACKUP_DIR=""

if [[ ! -d "${SRC_DIR}" ]]; then
  printf 'source skill directory not found: %s\n' "${SRC_DIR}" >&2
  exit 1
fi

mkdir -p "${TARGET_ROOT}"
STAGING_DIR="$(mktemp -d "${TARGET_ROOT}/.ripple-orchestrator.staging.XXXXXX")"

cleanup() {
  rm -rf "${STAGING_DIR}"
  if [[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]]; then
    rmdir "${BACKUP_DIR}" 2>/dev/null || true
  fi
}

trap cleanup EXIT

json_escape() {
  local value="$1"
  local out=""
  local char=""
  local code=0
  local i=0

  for ((i = 0; i < ${#value}; i++)); do
    char="${value:i:1}"
    case "${char}" in
      '"') out+='\"' ;;
      '\\') out+='\\\\' ;;
      $'\b') out+='\b' ;;
      $'\f') out+='\f' ;;
      $'\n') out+='\n' ;;
      $'\r') out+='\r' ;;
      $'\t') out+='\t' ;;
      *)
        LC_CTYPE=C printf -v code '%d' "'${char}"
        if ((code >= 0 && code < 32)); then
          printf -v char '\\u%04x' "${code}"
          out+="${char}"
        else
          out+="${char}"
        fi
        ;;
    esac
  done

  printf '%s' "${out}"
}

cp -R "${SRC_DIR}" "${STAGING_DIR}/ripple-orchestrator"

if [[ -e "${TARGET_DIR}" ]]; then
  BACKUP_DIR="$(mktemp -d "${TARGET_ROOT}/.ripple-orchestrator.backup.XXXXXX")"
  mv "${TARGET_DIR}" "${BACKUP_DIR}/ripple-orchestrator"
fi

if ! mv "${STAGING_DIR}/ripple-orchestrator" "${TARGET_DIR}"; then
  if [[ -n "${BACKUP_DIR}" && -e "${BACKUP_DIR}/ripple-orchestrator" ]]; then
    if ! /bin/mv "${BACKUP_DIR}/ripple-orchestrator" "${TARGET_DIR}"; then
      printf 'failed to restore original skill to target: %s\n' "${TARGET_DIR}" >&2
    fi
  fi
  printf 'failed to install skill to target: %s\n' "${TARGET_DIR}" >&2
  exit 1
fi

if [[ -n "${BACKUP_DIR}" && -e "${BACKUP_DIR}/ripple-orchestrator" ]]; then
  rm -rf "${BACKUP_DIR}/ripple-orchestrator"
fi
printf '{"ok":true,"target":"%s"}\n' "$(json_escape "${TARGET_DIR}")"

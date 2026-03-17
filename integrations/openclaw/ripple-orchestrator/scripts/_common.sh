#!/usr/bin/env bash
set -euo pipefail

resolve_ripple_cli_bin() {
  if [[ -n "${RIPPLE_CLI_BIN:-}" ]]; then
    printf '%s\n' "${RIPPLE_CLI_BIN}"
    return 0
  fi

  if [[ -x "${HOME}/.ripple/bin/ripple-cli" ]]; then
    printf '%s\n' "${HOME}/.ripple/bin/ripple-cli"
    return 0
  fi

  if command -v ripple-cli >/dev/null 2>&1; then
    command -v ripple-cli
    return 0
  fi

  printf '%s\n' "ripple-cli"
}

append_flag_if_missing() {
  local flag="$1"
  shift

  local -a filtered=()
  local arg
  for arg in "$@"; do
    if [[ "${arg}" != "${flag}" ]]; then
      filtered+=("${arg}")
    fi
  done

  printf '%s\0' "${filtered[@]}" "${flag}"
}

append_option_if_missing() {
  local option="$1"
  local value="$2"
  shift 2

  local -a args=("$@")
  local index
  for ((index = 0; index < ${#args[@]}; index += 1)); do
    if [[ "${args[index]}" == "${option}" ]]; then
      printf '%s\0' "${args[@]}"
      return 0
    fi
  done

  printf '%s\0' "${args[@]}" "${option}" "${value}"
}

run_ripple_cli() {
  local cli_bin
  local -a args=()
  local arg

  cli_bin="$(resolve_ripple_cli_bin)"

  if (($# == 0)); then
    args=(--json)
  else
    while IFS= read -r -d '' arg; do
      args+=("${arg}")
    done < <(append_flag_if_missing "--json" "$@")
  fi

  "${cli_bin}" "${args[@]}"
}

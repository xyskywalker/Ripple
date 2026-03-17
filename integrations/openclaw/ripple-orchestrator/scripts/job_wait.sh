#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

args=()
while IFS= read -r -d '' arg; do
  args+=("${arg}")
done < <(append_option_if_missing "--poll-interval" "30" "$@")

run_ripple_cli job wait "${args[@]}"

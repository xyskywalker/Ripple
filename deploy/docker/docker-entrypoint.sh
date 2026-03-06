#!/bin/sh
set -eu

python -m ripple.service.llm_config_bootstrap

exec "$@"


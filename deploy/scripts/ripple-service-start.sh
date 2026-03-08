#!/usr/bin/env bash
set -euo pipefail

mkdir -p data/ripple-service/ripple_outputs
docker compose -f deploy/docker/docker-compose.yml up -d --build
echo "ripple-service started"

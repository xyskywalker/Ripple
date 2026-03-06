#!/usr/bin/env bash
set -euo pipefail

docker compose -f deploy/docker/docker-compose.yml up -d --build
echo "ripple-service started"

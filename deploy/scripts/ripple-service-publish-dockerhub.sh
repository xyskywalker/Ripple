#!/usr/bin/env bash
set -euo pipefail

REPO="${DOCKERHUB_REPO:-xyplusxy/ripple}"
TAG="${1:-$(date +%Y%m%d-%H%M%S)}"
PUSH_LATEST="${PUSH_LATEST:-1}"
DOCKERFILE="${DOCKERFILE:-deploy/docker/Dockerfile}"
CONTEXT_DIR="${CONTEXT_DIR:-.}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
BUILDER_NAME="${BUILDER_NAME:-ripple-multi}"

IMAGE_TAGGED="${REPO}:${TAG}"
IMAGE_LATEST="${REPO}:latest"

if ! docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
  docker buildx create --name "${BUILDER_NAME}" --use >/dev/null
else
  docker buildx use "${BUILDER_NAME}" >/dev/null
fi
docker buildx inspect --bootstrap >/dev/null

echo "Building and pushing multi-arch image: ${IMAGE_TAGGED}"
echo "Platforms: ${PLATFORMS}"

TAGS=(-t "${IMAGE_TAGGED}")
if [[ "${PUSH_LATEST}" == "1" ]]; then
  TAGS+=(-t "${IMAGE_LATEST}")
fi

docker buildx build \
  --platform "${PLATFORMS}" \
  -f "${DOCKERFILE}" \
  "${TAGS[@]}" \
  --push \
  "${CONTEXT_DIR}"

echo "Done."
echo "Pushed:"
echo "  - ${IMAGE_TAGGED}"
if [[ "${PUSH_LATEST}" == "1" ]]; then
  echo "  - ${IMAGE_LATEST}"
fi

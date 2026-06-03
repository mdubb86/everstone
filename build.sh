#!/bin/bash
set -e

IMAGE="mdubb86/everstone:latest"

echo "Building and pushing ${IMAGE} for linux/amd64 and linux/arm64..."
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --tag "${IMAGE}" \
    --push \
    .

echo "Done! Image pushed to ${IMAGE}"

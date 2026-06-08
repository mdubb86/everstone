#!/bin/bash
set -e

IMAGE="everstone:latest"
CONTAINER="everstone"

echo "Building image for local system..."
docker build -t "${IMAGE}" .

echo "Stopping and removing existing container (if any)..."
docker rm -f "${CONTAINER}" 2>/dev/null || true

echo "Starting new container..."
mkdir -p ~/everstone-test

# Create local config if not present
if [ ! -f ~/everstone-test/config.yaml ]; then
    cat > ~/everstone-test/config.yaml << 'EOF'
couchdb:
  password: password

git:
  password: git
EOF
fi

docker run -d \
    --name "${CONTAINER}" \
    -v ~/everstone-test:/opt/data \
    -v ~/everstone-test/config.yaml:/opt/config.yaml \
    -p 9876:80 \
    "${IMAGE}"

echo "Container started. Logs:"
docker logs -f "${CONTAINER}"

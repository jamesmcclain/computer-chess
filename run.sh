#!/usr/bin/env bash
#
# run.sh — Build (if needed) and run the gnuchess-api container.
#
# Usage:
#   ./run.sh                             # build (if needed) and run
#   API_PORT=8003 VIEWER_PORT=8004 ./run.sh   # override host ports
#
# Once running:
#   REST API:  http://localhost:${API_PORT:-5003}/api
#   Viewer:    http://localhost:${VIEWER_PORT:-5004}/
#
# Try it from another terminal:
#   curl -X POST http://localhost:5003/api/game \
#     -H 'Content-Type: application/json' \
#     -d '{"white": "human", "black": "engine"}'
#
#   curl http://localhost:5003/api/game
#
#   curl http://localhost:5003/api/game/legal-moves
#
#   curl -X POST http://localhost:5003/api/game/move \
#     -H 'Content-Type: application/json' \
#     -d '{"move": "e2e4"}'
#
# Then open http://localhost:5004/ in a browser to watch the board.

set -euo pipefail

IMAGE_NAME="gnuchess-api"
CONTAINER_NAME="gnuchess-api"
API_PORT="${API_PORT:-5003}"
VIEWER_PORT="${VIEWER_PORT:-5004}"

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    echo "Image ${IMAGE_NAME} not found locally — building it now..."
    docker build -t "${IMAGE_NAME}" .
fi

# Remove any previous container with the same name so re-running is idempotent.
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "Starting container '${CONTAINER_NAME}' in the foreground (Ctrl-C to stop)."
echo "REST API:  http://localhost:${API_PORT}/api"
echo "Viewer:    http://localhost:${VIEWER_PORT}/"
echo

docker run -it --rm \
    --name "${CONTAINER_NAME}" \
    -p "${API_PORT}:5003" \
    -p "${VIEWER_PORT}:5004" \
    "${IMAGE_NAME}"

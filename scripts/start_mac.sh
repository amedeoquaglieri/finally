#!/usr/bin/env bash
# Start FinAlly in Docker (macOS/Linux). Safe to run repeatedly.
#
# Usage: scripts/start_mac.sh [--build] [--no-open]
#   --build    rebuild the image even if it already exists
#   --no-open  don't open the browser

set -euo pipefail

IMAGE="finally"
CONTAINER="finally"
VOLUME="finally-data"
PORT="${FINALLY_PORT:-8000}"
URL="http://localhost:${PORT}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

build=false
open_browser=true
for arg in "$@"; do
  case "$arg" in
    --build) build=true ;;
    --no-open) open_browser=false ;;
    -h|--help)
      sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (use --build, --no-open)" >&2
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi

if [ "$build" = true ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building image '${IMAGE}'..."
  docker build -t "$IMAGE" "$ROOT_DIR"
fi

# Replace any existing container (running or stopped); data lives in the volume.
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Removing existing container '${CONTAINER}'..."
  docker rm -f "$CONTAINER" >/dev/null
fi

run_args=(
  -d
  --name "$CONTAINER"
  -p "${PORT}:8000"
  -v "${VOLUME}:/app/db"
)
if [ -f "$ENV_FILE" ]; then
  run_args+=(--env-file "$ENV_FILE")
else
  echo "Warning: ${ENV_FILE} not found; running without it (AI chat needs OPENROUTER_API_KEY)." >&2
  echo "         Copy .env.example to .env and add your keys." >&2
fi

echo "Starting container '${CONTAINER}'..."
docker run "${run_args[@]}" "$IMAGE" >/dev/null

# Wait for the health endpoint (up to ~30 s).
ready=false
for _ in $(seq 1 30); do
  if curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
    ready=true
    break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]; then
    echo "Error: container exited during startup. Logs:" >&2
    docker logs "$CONTAINER" >&2 || true
    exit 1
  fi
  sleep 1
done

if [ "$ready" = true ]; then
  echo "FinAlly is running at ${URL}"
else
  echo "FinAlly is starting at ${URL} (not healthy yet; check: docker logs ${CONTAINER})"
fi

if [ "$open_browser" = true ]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi

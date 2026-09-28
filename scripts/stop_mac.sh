#!/usr/bin/env bash
# Stop FinAlly (macOS/Linux). Removes the container but keeps the finally-data volume,
# so portfolio data persists. Safe to run repeatedly.

set -euo pipefail

CONTAINER="finally"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  docker rm -f "$CONTAINER" >/dev/null
  echo "FinAlly stopped (data kept in volume 'finally-data')."
else
  echo "FinAlly is not running."
fi

#!/usr/bin/env bash
# Build and run the recon module on its own. The broker desk uses compose instead.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RECON_MODULE_DIR="${RECON_MODULE_DIR:-$ROOT}"
NETWORK="${QUANT_NETWORK:-quant_net}"

: "${REDIS_URL:?Set REDIS_URL}"
: "${MONGO_URI:?Set MONGO_URI}"
MONGO_DB_NAME="${MONGO_DB_NAME:-quant_db}"
RECON_BROKER_MODE="${RECON_BROKER_MODE:-kotak}"

docker network create "$NETWORK" 2>/dev/null || true

if [[ ! -f "$RECON_MODULE_DIR/Dockerfile" ]]; then
  echo "Missing Recon Module Dockerfile at: $RECON_MODULE_DIR" >&2
  exit 1
fi

echo "Building recon-module from $RECON_MODULE_DIR"
docker build -t recon-module:local "$RECON_MODULE_DIR"

docker rm -f recon 2>/dev/null || true
docker run -d \
  --name recon \
  --network "$NETWORK" \
  --add-host=host.docker.internal:host-gateway \
  -p "${RECON_SERVICE_PORT:-8013}:8013" \
  -e REDIS_URL="$REDIS_URL" \
  -e MONGO_URI="$MONGO_URI" \
  -e MONGO_DB_NAME="$MONGO_DB_NAME" \
  -e RECON_BROKER_MODE="$RECON_BROKER_MODE" \
  -e RECON_INTERVAL_SEC="${RECON_INTERVAL_SEC:-1}" \
  -e POSITION_STATE_DIR="${POSITION_STATE_DIR:-/state}" \
  -e LOG_LEVEL="${LOG_LEVEL:-INFO}" \
  recon-module:local

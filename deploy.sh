#!/usr/bin/env bash
#
# deploy.sh - Pull GarminBot, rebuild and restart garminbot
#
# Usage: bash deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../homeserver/docker-compose.yml"

cd "$SCRIPT_DIR"

echo "=== GarminBot Deploy ==="

echo "[1/3] Syncing to origin/main..."
git fetch origin
git reset --hard origin/main

echo "[2/3] Rebuilding and restarting garminbot..."
docker compose -f "$COMPOSE_FILE" up -d --build garminbot

echo "[3/3] Showing logs (Ctrl+C to stop watching)..."
docker compose -f "$COMPOSE_FILE" logs -f --tail=30 garminbot

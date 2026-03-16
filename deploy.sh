#!/usr/bin/env bash
#
# deploy.sh - Pull GarminBot, rebuild and restart garminbot
#
# Usage: bash deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$SCRIPT_DIR"

echo "=== GarminBot Deploy ==="

echo "[1/3] Syncing to origin/main..."
git fetch origin
git reset --hard origin/main

echo "[2/3] Rebuilding and restarting garminbot..."
docker compose up -d --build garminbot

echo "[3/3] Showing logs (Ctrl+C to stop watching)..."
docker compose logs -f --tail=30 garminbot

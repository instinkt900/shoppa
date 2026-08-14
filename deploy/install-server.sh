#!/usr/bin/env bash
# Pull the latest code and (re)build + start the Shoppa container on the serve host.
# Run this from inside a clone of the repo on that host.
#
# Prereqs (one-time on the host):
#   - docker/config.toml  (copy from ../config.example.toml; set database.path = "/data/shoppa.db")
#   - docker/.env         (optional: SHOPPA_PORT=...)
set -euo pipefail

cd "$(dirname "$0")/.."

git pull --ff-only
cd docker
docker compose up -d --build

echo "waiting for health..."
sleep 4
port="${SHOPPA_PORT:-8091}"
curl -fsS "http://localhost:${port}/healthz" && echo && echo "shoppa is up."

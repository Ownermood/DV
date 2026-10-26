#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as your normal sudo-capable user, not root." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl ffmpeg python3 python3-venv python3-pip

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Engine and the Docker Compose plugin from the official Docker documentation, then rerun." >&2
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is missing. Install it, then rerun." >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env. Edit it and set BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS, and a strong DASHBOARD_PASSWORD before launching."
else
  echo ".env already exists; leaving it unchanged."
fi

echo "Preparation complete. Edit .env, then run: docker compose up -d --build"

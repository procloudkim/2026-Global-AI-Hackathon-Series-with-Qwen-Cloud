#!/usr/bin/env bash
set -euo pipefail

# Incremental deployment script for Alibaba Cloud ECS host
# Usage:
#   bash deploy/deploy.sh /opt/librarian

APP_DIR="${1:-/opt/librarian}"
SERVICE_NAME="librarian"
REPO_URL="https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud.git"
BRANCH="main"

if [ ! -d "${APP_DIR}/.git" ]; then
  echo "[1/6] cloning repository..."
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
else
  echo "[1/6] updating repository..."
  git -C "${APP_DIR}" fetch origin
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
fi

echo "[2/6] syncing dependencies..."
cd "${APP_DIR}"
uv sync

if [ ! -f "${APP_DIR}/.env" ]; then
  echo "ERROR: ${APP_DIR}/.env not found"
  exit 1
fi

echo "[3/6] running tests..."
uv run pytest -q

echo "[4/6] restarting service..."
systemctl restart "${SERVICE_NAME}"

echo "[5/6] service status..."
systemctl --no-pager status "${SERVICE_NAME}" | head -n 20

echo "[6/6] health check..."
curl -fsS http://127.0.0.1:8080/health


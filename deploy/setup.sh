#!/usr/bin/env bash
set -euo pipefail

# One-time setup on Alibaba Cloud ECS (Ubuntu 22.04+)
# Usage:
#   sudo bash deploy/setup.sh /opt/librarian

APP_DIR="${1:-/opt/librarian}"
SERVICE_NAME="librarian"

echo "[1/6] installing system deps..."
apt-get update -y
apt-get install -y curl git python3.12 python3.12-venv

echo "[2/6] installing uv..."
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "[3/6] creating app dir..."
mkdir -p "${APP_DIR}"
chown -R "${SUDO_USER:-root}:${SUDO_USER:-root}" "${APP_DIR}"

echo "[4/6] writing systemd unit..."
cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Librarian FastAPI service
After=network.target

[Service]
Type=simple
User=${SUDO_USER:-root}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${HOME}/.local/bin/uv run uvicorn librarian.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

echo "[5/6] reloading systemd..."
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

echo "[6/6] done. Next run deploy/deploy.sh on this host."


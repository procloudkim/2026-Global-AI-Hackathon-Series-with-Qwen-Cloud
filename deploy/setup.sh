#!/usr/bin/env bash
set -Eeuo pipefail

# One-time host setup for Ubuntu 22.04+ on an approved Alibaba ECS/SAS instance.
# This script installs host dependencies and service definitions only. It does
# not activate a trial, create a cloud resource, deploy application source, or
# start a service.
#
# Usage (after explicit infrastructure approval):
#   sudo bash deploy/setup.sh

readonly RELEASE_ROOT="/opt/librarian/releases"
readonly CURRENT_LINK="/opt/librarian/current"
readonly STATE_ROOT="/var/lib/librarian"
readonly MEMORY_ROOT="${STATE_ROOT}/memory"
readonly DEPLOYMENT_ROOT="${STATE_ROOT}/deployments"
readonly CONFIG_ROOT="/etc/librarian"
readonly SERVICE_USER="librarian"
readonly SERVICE_GROUP="librarian"
readonly SERVICE_NAME="librarian"
readonly UV_VERSION="0.11.28"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: setup.sh must run as root" >&2
  exit 2
fi

if [[ ! -r /etc/os-release ]]; then
  echo "ERROR: /etc/os-release is unavailable" >&2
  exit 2
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "ERROR: this setup contract supports Ubuntu only (found ${ID:-unknown})" >&2
  exit 2
fi
if [[ "${VERSION_ID:-0}" != "22.04" && "${VERSION_ID:-0}" != "24.04" ]]; then
  echo "ERROR: supported Ubuntu versions are 22.04 and 24.04" >&2
  exit 2
fi

echo "[1/7] Installing pinned host prerequisites"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates curl debian-archive-keyring debian-keyring git gnupg \
  iproute2 jq python3 rsync tar util-linux

echo "[2/7] Installing uv ${UV_VERSION}"
if ! command -v uv >/dev/null 2>&1 || [[ "$(uv --version)" != "uv ${UV_VERSION}"* ]]; then
  case "$(uname -m)" in
    x86_64)
      uv_target="x86_64-unknown-linux-gnu"
      uv_sha256="e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224"
      ;;
    aarch64|arm64)
      uv_target="aarch64-unknown-linux-gnu"
      uv_sha256="03e9fe0a81b0718d0bc84625de3885df6cc3f89a8b6af6121d6b9f6113fb6533"
      ;;
    *)
      echo "ERROR: no pinned uv artifact for architecture $(uname -m)" >&2
      exit 2
      ;;
  esac
  uv_archive="$(mktemp)"
  uv_extract="$(mktemp -d)"
  trap 'rm -rf -- "${uv_archive:-}" "${uv_extract:-}"' EXIT
  curl --fail --silent --show-error --location \
    "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${uv_target}.tar.gz" \
    --output "${uv_archive}"
  printf '%s  %s\n' "${uv_sha256}" "${uv_archive}" | sha256sum --check --status
  tar --extract --gzip --file "${uv_archive}" --directory "${uv_extract}" \
    --strip-components=1
  install -m 0755 -o root -g root "${uv_extract}/uv" "${uv_extract}/uvx" \
    /usr/local/bin/
  rm -rf -- "${uv_archive}" "${uv_extract}"
  trap - EXIT
fi
uv --version

echo "[3/7] Installing Caddy from its signed package repository"
if ! command -v caddy >/dev/null 2>&1; then
  key_tmp="$(mktemp)"
  trap 'rm -f "${key_tmp:-}"; if [[ "${policy_created:-0}" -eq 1 ]]; then rm -f /usr/sbin/policy-rc.d; fi' EXIT
  curl --fail --silent --show-error --location \
    https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    -o "${key_tmp}"
  gpg --dearmor --yes \
    --output /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    "${key_tmp}"
  curl --fail --silent --show-error --location \
    https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    -o /etc/apt/sources.list.d/caddy-stable.list
  chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  policy_created=0
  if [[ ! -e /usr/sbin/policy-rc.d ]]; then
    printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
    chmod 0755 /usr/sbin/policy-rc.d
    policy_created=1
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends caddy
  systemctl stop caddy.service >/dev/null 2>&1 || true
  if [[ "${policy_created}" -eq 1 ]]; then
    rm -f /usr/sbin/policy-rc.d
  fi
  rm -f "${key_tmp}"
  trap - EXIT
fi

echo "[4/7] Creating the non-login runtime identity and persistent paths"
if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd --system "${SERVICE_GROUP}"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${SERVICE_GROUP}" --home-dir "${STATE_ROOT}" \
    --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
install -d -m 0755 -o root -g root "${RELEASE_ROOT}"
install -d -m 0750 -o root -g "${SERVICE_GROUP}" "${CONFIG_ROOT}"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
  "${STATE_ROOT}" "${MEMORY_ROOT}"
install -d -m 0750 -o root -g "${SERVICE_GROUP}" "${DEPLOYMENT_ROOT}"

if [[ ! -e "${CONFIG_ROOT}/librarian.env" ]]; then
  install -m 0640 -o root -g "${SERVICE_GROUP}" /dev/null \
    "${CONFIG_ROOT}/librarian.env"
fi
if [[ ! -e "${CONFIG_ROOT}/caddy.env" ]]; then
  install -m 0640 -o root -g caddy /dev/null "${CONFIG_ROOT}/caddy.env"
fi

echo "[5/7] Installing the managed Python 3.12 runtime"
install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
  "${STATE_ROOT}/python"
runuser -u "${SERVICE_USER}" -- \
  sh -c 'cd "$1" && exec env UV_PYTHON_INSTALL_DIR="$1/python" /usr/local/bin/uv python install 3.12' \
  sh "${STATE_ROOT}"

echo "[6/7] Writing hardened systemd and Caddy definitions"
install -m 0644 -o root -g root "${SCRIPT_DIR}/Caddyfile" /etc/caddy/Caddyfile
install -d -m 0755 -o root -g root /etc/systemd/system/caddy.service.d
cat >/etc/systemd/system/caddy.service.d/librarian.conf <<EOF
[Service]
EnvironmentFile=${CONFIG_ROOT}/caddy.env
EOF

cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Librarian Track 1 MemoryAgent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${CURRENT_LINK}
EnvironmentFile=${CONFIG_ROOT}/librarian.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
Environment=LIBRARIAN_MEMORY_ROOT=${MEMORY_ROOT}
ExecStart=/usr/bin/bash -c 'export LIBRARIAN_DEPLOYED_SHA="\$(cat ${CURRENT_LINK}/.deployed-sha)"; exec ${CURRENT_LINK}/.venv/bin/uvicorn librarian.main:app --host 127.0.0.1 --port 8080 --no-access-log'
Restart=on-failure
RestartSec=3
TimeoutStartSec=60
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=${MEMORY_ROOT}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service" caddy.service

echo "[7/7] Host setup complete; services intentionally not started"
cat <<EOF
NEXT_APPROVAL_GATED_STEPS:
1. Populate ${CONFIG_ROOT}/librarian.env (mode 0640) with the Qwen secret and bounded runtime settings.
2. Populate ${CONFIG_ROOT}/caddy.env with LIBRARIAN_DOMAIN, LIBRARIAN_BASIC_AUTH_USER,
   and a bcrypt LIBRARIAN_BASIC_AUTH_HASH from 'caddy hash-password'.
3. Validate the Alibaba security group exposes only SSH and HTTPS.
4. Run the exact-SHA deploy workflow. Do not start the app from this setup wave.
EOF

#!/usr/bin/env bash
set -Eeuo pipefail

# Install one already-validated Git commit as an immutable release.
# No branch checkout, git reset, secret mutation, or memory deletion occurs.
#
# Usage (on the approved host):
#   sudo bash deploy/deploy.sh \
#     --sha <40-hex-commit> \
#     --archive /tmp/librarian-<sha>.tar.gz \
#     --archive-sha256 <64-hex-sha256> \
#     --release-gate-receipt /tmp/release-gate.json \
#     --release-gate-sha256 <64-hex-sha256>

readonly RELEASE_ROOT="/opt/librarian/releases"
readonly CURRENT_LINK="/opt/librarian/current"
readonly STATE_ROOT="/var/lib/librarian"
readonly MEMORY_ROOT="${STATE_ROOT}/memory"
readonly DEPLOYMENT_ROOT="${STATE_ROOT}/deployments"
readonly SERVICE_NAME="librarian"
readonly SERVICE_USER="librarian"
readonly SERVICE_GROUP="librarian"
readonly HEALTH_URL="http://127.0.0.1:8080/health"
readonly PYTHON_ROOT="${STATE_ROOT}/python"

CANDIDATE_SHA=""
ARCHIVE=""
EXPECTED_ARCHIVE_SHA256=""
RELEASE_GATE_RECEIPT=""
EXPECTED_RELEASE_GATE_SHA256=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)
      CANDIDATE_SHA="${2:-}"
      shift 2
      ;;
    --archive)
      ARCHIVE="${2:-}"
      shift 2
      ;;
    --archive-sha256)
      EXPECTED_ARCHIVE_SHA256="${2:-}"
      shift 2
      ;;
    --release-gate-receipt)
      RELEASE_GATE_RECEIPT="${2:-}"
      shift 2
      ;;
    --release-gate-sha256)
      EXPECTED_RELEASE_GATE_SHA256="${2:-}"
      shift 2
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: deploy.sh must run as root" >&2
  exit 2
fi
if [[ ! "${CANDIDATE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: --sha must be a full lowercase 40-hex commit" >&2
  exit 2
fi
if [[ ! "${EXPECTED_ARCHIVE_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: --archive-sha256 must be a lowercase 64-hex digest" >&2
  exit 2
fi
if [[ ! "${EXPECTED_RELEASE_GATE_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: --release-gate-sha256 must be a lowercase 64-hex digest" >&2
  exit 2
fi
if [[ ! -f "${ARCHIVE}" || -L "${ARCHIVE}" ]]; then
  echo "ERROR: --archive must identify a regular, non-symlink file" >&2
  exit 2
fi
if [[ ! -f "${RELEASE_GATE_RECEIPT}" || -L "${RELEASE_GATE_RECEIPT}" ]]; then
  echo "ERROR: --release-gate-receipt must identify a regular, non-symlink file" >&2
  exit 2
fi
for command_name in curl git gzip python3 runuser sha256sum systemctl tar uv; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: ${command_name}" >&2
    exit 2
  fi
done

readonly RELEASE_PATH="${RELEASE_ROOT}/${CANDIDATE_SHA}"
readonly EVENT_ID="$(date -u +%Y%m%dT%H%M%SZ)-${CANDIDATE_SHA:0:12}"
readonly MANIFEST_PATH="${DEPLOYMENT_ROOT}/${EVENT_ID}.json"
readonly STAGING_PATH="${RELEASE_ROOT}/.${CANDIDATE_SHA}.staging.$$"
readonly HEALTH_BODY="${DEPLOYMENT_ROOT}/.${EVENT_ID}.health.json"
readonly STORED_RELEASE_GATE="${DEPLOYMENT_ROOT}/${EVENT_ID}.release-gate.json"
readonly SMOKE_MEMORY_ROOT="/tmp/librarian-smoke-memory-${EVENT_ID}-$$"
readonly STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

PREVIOUS_SHA=""
PREVIOUS_PATH=""
MEMORY_BEFORE=""
MEMORY_AFTER=""
MEMORY_PRE_CANDIDATE=""
BOOTSTRAP_APPLIED=0
HEALTH_STATUS="NOT_RUN"
SWITCHED=0
SERVICE_STOPPED=0

memory_digest() {
  find "${MEMORY_ROOT}" -type f \
    ! -name '.memory.lock' \
    ! -name 'runs.jsonl' \
    -print0 \
    | sort -z \
    | xargs -0 -r sha256sum \
    | sha256sum \
    | awk '{print $1}'
}

memory_file_count() {
  find "${MEMORY_ROOT}" -type f \
    ! -name '.memory.lock' \
    ! -name 'runs.jsonl' \
    -printf '.' | wc -c
}

atomic_link() {
  local target="$1"
  local temporary="/opt/librarian/.current-${EVENT_ID}-$$"
  ln -s "${target}" "${temporary}"
  mv -Tf "${temporary}" "${CURRENT_LINK}"
}

wait_for_health() {
  local expected_sha="$1"
  local attempt
  for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 3 \
      "${HEALTH_URL}" -o "${HEALTH_BODY}" 2>/dev/null \
      && EXPECTED_SHA="${expected_sha}" python3 - "${HEALTH_BODY}" <<'PY'
import json
import os
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
if body.get("status") != "ok":
    raise SystemExit(1)
if body.get("deployed_sha") != os.environ["EXPECTED_SHA"]:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 2
  done
  return 1
}

write_manifest() {
  local status="$1"
  local failure_line="${2:-}"
  local failure_code="${3:-}"
  local health_hash=""
  local memory_status="NOT_RUN"
  if [[ -s "${HEALTH_BODY}" ]]; then
    health_hash="$(sha256sum "${HEALTH_BODY}" | awk '{print $1}')"
  fi
  if [[ -n "${MEMORY_BEFORE}" && -n "${MEMORY_AFTER}" ]]; then
    if [[ "${MEMORY_BEFORE}" == "${MEMORY_AFTER}" ]]; then
      memory_status="PASS"
    else
      memory_status="FAIL"
    fi
  fi
  STATUS="${status}" \
  FAILURE_LINE="${failure_line}" \
  FAILURE_CODE="${failure_code}" \
  PREVIOUS_SHA_VALUE="${PREVIOUS_SHA}" \
  ARCHIVE_SHA_VALUE="${EXPECTED_ARCHIVE_SHA256}" \
  HEALTH_STATUS_VALUE="${HEALTH_STATUS}" \
  HEALTH_HASH_VALUE="${health_hash}" \
  MEMORY_STATUS_VALUE="${memory_status}" \
  MEMORY_BEFORE_VALUE="${MEMORY_BEFORE}" \
  MEMORY_AFTER_VALUE="${MEMORY_AFTER}" \
  MEMORY_PRE_CANDIDATE_VALUE="${MEMORY_PRE_CANDIDATE}" \
  BOOTSTRAP_APPLIED_VALUE="${BOOTSTRAP_APPLIED}" \
  python3 - "${MANIFEST_PATH}" <<PY
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile

path = Path("${MANIFEST_PATH}")
previous = os.environ["PREVIOUS_SHA_VALUE"] or None
health_hash = os.environ["HEALTH_HASH_VALUE"] or None
before = os.environ["MEMORY_BEFORE_VALUE"] or None
after = os.environ["MEMORY_AFTER_VALUE"] or None
failure = None
if os.environ["FAILURE_CODE"]:
    failure = {
        "line": int(os.environ["FAILURE_LINE"]),
        "exit_code": int(os.environ["FAILURE_CODE"]),
    }
payload = {
    "schema_version": "librarian-release-event/v1",
    "event_id": "${EVENT_ID}",
    "event_type": "deploy",
    "status": os.environ["STATUS"],
    "candidate_sha": "${CANDIDATE_SHA}",
    "previous_sha": previous,
    "archive_sha256": os.environ["ARCHIVE_SHA_VALUE"],
    "release_path": "${RELEASE_PATH}",
    "memory_path": "${MEMORY_ROOT}",
    "manifest_path": str(path),
    "release_gate_receipt": {
        "path": "${STORED_RELEASE_GATE}",
        "sha256": "${EXPECTED_RELEASE_GATE_SHA256}",
    },
    "started_at": "${STARTED_AT}",
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "health": {
        "url": "${HEALTH_URL}",
        "status": os.environ["HEALTH_STATUS_VALUE"],
        "response_sha256": health_hash,
    },
    "memory_integrity": {
        "status": os.environ["MEMORY_STATUS_VALUE"],
        "before_sha256": before,
        "after_sha256": after,
        "pre_candidate_sha256": os.environ["MEMORY_PRE_CANDIDATE_VALUE"] or None,
        "bootstrap_applied": os.environ["BOOTSTRAP_APPLIED_VALUE"] == "1",
    },
    "restart_persistence_proof": {
        "status": "PENDING_WAVE_5",
        "receipt_path": None,
    },
    "failure": failure,
}
path.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
    temporary = Path(handle.name)
temporary.replace(path)
PY
  chmod 0640 "${MANIFEST_PATH}"
  chown root:"${SERVICE_GROUP}" "${MANIFEST_PATH}"
}

on_error() {
  local exit_code="$?"
  local failure_line="${1:-1}"
  local rollback_ok=0
  local memory_safe=1
  trap - ERR
  set +e
  echo "ERROR: deployment failed at line ${failure_line}; attempting containment" >&2
  if [[ "${SWITCHED}" -eq 1 ]]; then
    systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1
  fi
  MEMORY_AFTER="$(memory_digest)"
  if [[ -n "${MEMORY_BEFORE}" && "${MEMORY_BEFORE}" != "${MEMORY_AFTER}" ]]; then
    memory_safe=0
    HEALTH_STATUS="FAIL"
    systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1
    echo "ERROR: persistent memory changed; previous release will not be started" >&2
  fi
  if [[ ("${SWITCHED}" -eq 1 || "${SERVICE_STOPPED}" -eq 1) \
        && "${memory_safe}" -eq 1 \
        && -n "${PREVIOUS_PATH}" && -d "${PREVIOUS_PATH}" ]]; then
    if [[ "${SWITCHED}" -eq 1 ]]; then
      atomic_link "${PREVIOUS_PATH}"
    fi
    systemctl start "${SERVICE_NAME}.service"
    if wait_for_health "${PREVIOUS_SHA}"; then
      HEALTH_STATUS="PASS"
      MEMORY_AFTER="$(memory_digest)"
      if [[ -n "${MEMORY_BEFORE}" && "${MEMORY_BEFORE}" != "${MEMORY_AFTER}" ]]; then
        systemctl stop "${SERVICE_NAME}.service"
        HEALTH_STATUS="FAIL"
      else
        rollback_ok=1
      fi
    else
      HEALTH_STATUS="FAIL"
      systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1
    fi
    if [[ "${rollback_ok}" -eq 1 ]]; then
      write_manifest "ROLLED_BACK" "${failure_line}" "${exit_code}"
    else
      write_manifest "FAILED" "${failure_line}" "${exit_code}"
    fi
  else
    if [[ "${SWITCHED}" -eq 1 || "${SERVICE_STOPPED}" -eq 1 ]]; then
      systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1
      HEALTH_STATUS="FAIL"
    else
      HEALTH_STATUS="NOT_RUN"
    fi
    write_manifest "FAILED" "${failure_line}" "${exit_code}"
  fi
  if [[ -d "${STAGING_PATH}" && "${STAGING_PATH}" == "${RELEASE_ROOT}/."*".staging."* ]]; then
    rm -rf -- "${STAGING_PATH}"
  fi
  if [[ -d "${SMOKE_MEMORY_ROOT}" && "${SMOKE_MEMORY_ROOT}" == /tmp/librarian-smoke-memory-* ]]; then
    rm -rf -- "${SMOKE_MEMORY_ROOT}"
  fi
  rm -f -- "${HEALTH_BODY}"
  echo "deployment_manifest=${MANIFEST_PATH}" >&2
  exit "${exit_code}"
}
trap 'on_error ${LINENO}' ERR

install -d -m 0755 -o root -g root "${RELEASE_ROOT}"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${MEMORY_ROOT}"
install -d -m 0750 -o root -g "${SERVICE_GROUP}" "${DEPLOYMENT_ROOT}"

actual_archive_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_archive_sha256}" != "${EXPECTED_ARCHIVE_SHA256}" ]]; then
  echo "ERROR: archive SHA-256 does not match the approved artifact" >&2
  false
fi
actual_release_gate_sha256="$(sha256sum "${RELEASE_GATE_RECEIPT}" | awk '{print $1}')"
if [[ "${actual_release_gate_sha256}" != "${EXPECTED_RELEASE_GATE_SHA256}" ]]; then
  echo "ERROR: release-gate receipt SHA-256 does not match the approved artifact" >&2
  false
fi
CANDIDATE_SHA_VALUE="${CANDIDATE_SHA}" python3 - "${RELEASE_GATE_RECEIPT}" <<'PY'
import json
import os
import sys

receipt = json.load(open(sys.argv[1], encoding="utf-8"))
if receipt.get("schema_version") != "librarian-release-gate/v1":
    raise SystemExit("unsupported release-gate receipt schema")
if receipt.get("status") != "PASS":
    raise SystemExit("release-gate receipt did not pass")
if receipt.get("candidate_sha") != os.environ["CANDIDATE_SHA_VALUE"]:
    raise SystemExit("release-gate receipt belongs to another candidate")
PY
install -m 0640 -o root -g "${SERVICE_GROUP}" \
  "${RELEASE_GATE_RECEIPT}" "${STORED_RELEASE_GATE}"
embedded_commit="$(gzip -dc -- "${ARCHIVE}" | git get-tar-commit-id)"
if [[ "${embedded_commit}" != "${CANDIDATE_SHA}" ]]; then
  echo "ERROR: git archive commit ${embedded_commit:-missing} != ${CANDIDATE_SHA}" >&2
  false
fi

if [[ -L "${CURRENT_LINK}" ]]; then
  PREVIOUS_PATH="$(readlink -f "${CURRENT_LINK}")"
  if [[ "${PREVIOUS_PATH}" =~ ^${RELEASE_ROOT}/([0-9a-f]{40})$ ]]; then
    PREVIOUS_SHA="${BASH_REMATCH[1]}"
  else
    echo "ERROR: current symlink points outside the managed release root" >&2
    false
  fi
fi

if [[ -d "${RELEASE_PATH}" ]]; then
  if [[ "$(<"${RELEASE_PATH}/.deployed-sha")" != "${CANDIDATE_SHA}" \
        || "$(<"${RELEASE_PATH}/.archive-sha256")" != "${EXPECTED_ARCHIVE_SHA256}" \
        || "$(<"${RELEASE_PATH}/.release-gate-sha256")" != "${EXPECTED_RELEASE_GATE_SHA256}" \
        || "$(sha256sum "${RELEASE_PATH}/.release-gate.json" | awk '{print $1}')" != "${EXPECTED_RELEASE_GATE_SHA256}" ]]; then
    echo "ERROR: immutable release path already exists with different metadata" >&2
    false
  fi
else
  mkdir -m 0755 "${STAGING_PATH}"
  tar --extract --gzip --file "${ARCHIVE}" --directory "${STAGING_PATH}" \
    --no-same-owner --no-same-permissions
  printf '%s\n' "${CANDIDATE_SHA}" >"${STAGING_PATH}/.deployed-sha"
  printf '%s\n' "${EXPECTED_ARCHIVE_SHA256}" >"${STAGING_PATH}/.archive-sha256"
  install -m 0644 "${RELEASE_GATE_RECEIPT}" "${STAGING_PATH}/.release-gate.json"
  printf '%s\n' "${EXPECTED_RELEASE_GATE_SHA256}" >"${STAGING_PATH}/.release-gate-sha256"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${STAGING_PATH}"
  runuser -u "${SERVICE_USER}" -- env \
    UV_PYTHON_INSTALL_DIR="${PYTHON_ROOT}" \
    /usr/local/bin/uv --directory "${STAGING_PATH}" sync --frozen --no-dev --python 3.12
  install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${SMOKE_MEMORY_ROOT}"
  runuser -u "${SERVICE_USER}" -- env \
    PYTHONPATH="${STAGING_PATH}/src" \
    LIBRARIAN_MEMORY_ROOT="${SMOKE_MEMORY_ROOT}" \
    RELEASE_WORKING_DIRECTORY="${STAGING_PATH}" \
    "${STAGING_PATH}/.venv/bin/python" -c \
    'import os; os.chdir(os.environ["RELEASE_WORKING_DIRECTORY"]); import librarian; import librarian.main'
  rm -rf -- "${SMOKE_MEMORY_ROOT}"
  ln -s "${MEMORY_ROOT}" "${STAGING_PATH}/memory"
  chown -R root:root "${STAGING_PATH}"
  chmod -R a-w "${STAGING_PATH}"
  mv "${STAGING_PATH}" "${RELEASE_PATH}"
fi

# Stop the old process before the canonical-state digest and symlink swap. The
# release directory is replaced; the memory directory is never replaced.
systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
SERVICE_STOPPED=1
MEMORY_PRE_CANDIDATE="$(memory_digest)"
MEMORY_BEFORE="${MEMORY_PRE_CANDIDATE}"
PRE_CANDIDATE_FILE_COUNT="$(memory_file_count)"
runuser -u "${SERVICE_USER}" -- env \
  PYTHONPATH="${RELEASE_PATH}/src" \
  LIBRARIAN_MEMORY_ROOT="${MEMORY_ROOT}" \
  "${RELEASE_PATH}/.venv/bin/python" -c \
  'import os; from librarian.store import MemoryStore; MemoryStore(os.environ["LIBRARIAN_MEMORY_ROOT"])'
MEMORY_AFTER_INIT="$(memory_digest)"
if [[ "${MEMORY_PRE_CANDIDATE}" != "${MEMORY_AFTER_INIT}" ]]; then
  if [[ "${PRE_CANDIDATE_FILE_COUNT}" -eq 0 ]] \
    && MEMORY_ROOT_VALUE="${MEMORY_ROOT}" python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["MEMORY_ROOT_VALUE"])
files = {
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file() and path.name not in {".memory.lock", "runs.jsonl"}
}
if files != {"wiki/graph.json", "wiki/index.md", "wiki/log.md"}:
    raise SystemExit("first-store bootstrap created unexpected canonical files")
PY
  then
    BOOTSTRAP_APPLIED=1
    MEMORY_BEFORE="${MEMORY_AFTER_INIT}"
  else
    echo "ERROR: candidate initialization changed non-empty production memory" >&2
    false
  fi
fi

atomic_link "${RELEASE_PATH}"
SWITCHED=1
systemctl start "${SERVICE_NAME}.service"
wait_for_health "${CANDIDATE_SHA}"
HEALTH_STATUS="PASS"
MEMORY_AFTER="$(memory_digest)"
if [[ "${MEMORY_BEFORE}" != "${MEMORY_AFTER}" ]]; then
  echo "ERROR: process restart changed the persistent memory digest" >&2
  false
fi

write_manifest "DEPLOYED"
rm -f -- "${HEALTH_BODY}"
trap - ERR

echo "candidate_sha=${CANDIDATE_SHA}"
echo "previous_sha=${PREVIOUS_SHA:-NONE}"
echo "deployment_manifest=${MANIFEST_PATH}"
echo "restart_persistence_proof=PENDING_WAVE_5"

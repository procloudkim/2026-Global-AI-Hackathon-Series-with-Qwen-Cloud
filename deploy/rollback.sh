#!/usr/bin/env bash
set -Eeuo pipefail

# Atomically restore one existing immutable release without changing persistent
# memory. The target must already exist under /opt/librarian/releases/<sha>.
#
# Usage:
#   sudo bash deploy/rollback.sh --sha <previous-40-hex-commit>

readonly RELEASE_ROOT="/opt/librarian/releases"
readonly CURRENT_LINK="/opt/librarian/current"
readonly MEMORY_ROOT="/var/lib/librarian/memory"
readonly DEPLOYMENT_ROOT="/var/lib/librarian/deployments"
readonly SERVICE_NAME="librarian"
readonly SERVICE_GROUP="librarian"
readonly HEALTH_URL="http://127.0.0.1:8080/health"

TARGET_SHA=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)
      TARGET_SHA="${2:-}"
      shift 2
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: rollback.sh must run as root" >&2
  exit 2
fi
if [[ ! "${TARGET_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: --sha must be a full lowercase 40-hex commit" >&2
  exit 2
fi

readonly TARGET_PATH="${RELEASE_ROOT}/${TARGET_SHA}"
if [[ ! -d "${TARGET_PATH}" \
      || "$(<"${TARGET_PATH}/.deployed-sha")" != "${TARGET_SHA}" ]]; then
  echo "ERROR: rollback target is not a verified immutable release" >&2
  exit 2
fi
if [[ ! -L "${CURRENT_LINK}" ]]; then
  echo "ERROR: no managed current release exists" >&2
  exit 2
fi

ORIGINAL_PATH="$(readlink -f "${CURRENT_LINK}")"
if [[ "${ORIGINAL_PATH}" =~ ^${RELEASE_ROOT}/([0-9a-f]{40})$ ]]; then
  ORIGINAL_SHA="${BASH_REMATCH[1]}"
else
  echo "ERROR: current symlink points outside the managed release root" >&2
  exit 2
fi

readonly EVENT_ID="$(date -u +%Y%m%dT%H%M%SZ)-${TARGET_SHA:0:12}-rollback"
readonly MANIFEST_PATH="${DEPLOYMENT_ROOT}/${EVENT_ID}.json"
readonly HEALTH_BODY="${DEPLOYMENT_ROOT}/.${EVENT_ID}.health.json"
readonly STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
readonly ARCHIVE_SHA256="$(<"${TARGET_PATH}/.archive-sha256")"
if [[ ! "${ARCHIVE_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: rollback target has invalid archive metadata" >&2
  exit 2
fi
readonly RELEASE_GATE_SHA256="$(<"${TARGET_PATH}/.release-gate-sha256")"
readonly EMBEDDED_RELEASE_GATE="${TARGET_PATH}/.release-gate.json"
if [[ ! "${RELEASE_GATE_SHA256}" =~ ^[0-9a-f]{64}$ \
      || ! -f "${EMBEDDED_RELEASE_GATE}" \
      || "$(sha256sum "${EMBEDDED_RELEASE_GATE}" | awk '{print $1}')" != "${RELEASE_GATE_SHA256}" ]]; then
  echo "ERROR: rollback target has invalid release-gate metadata" >&2
  exit 2
fi
readonly STORED_RELEASE_GATE="${DEPLOYMENT_ROOT}/${EVENT_ID}.release-gate.json"

MEMORY_BEFORE=""
MEMORY_AFTER=""
HEALTH_STATUS="NOT_RUN"
SWITCHED=0

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

release_is_finalized() {
  local target_sha="$1"
  TARGET_SHA_VALUE="${target_sha}" \
  DEPLOYMENT_ROOT_VALUE="${DEPLOYMENT_ROOT}" \
  python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ["DEPLOYMENT_ROOT_VALUE"]).resolve()
target = os.environ["TARGET_SHA_VALUE"]

def load(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}

def valid_reference(reference, *, schema, status):
    if not isinstance(reference, dict) or reference.get("status") != status:
        return False
    path = Path(str(reference.get("path", "")))
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    if path.is_symlink() or not path.is_file():
        return False
    if hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256"):
        return False
    payload = load(path)
    return (
        payload.get("schema_version") == schema
        and payload.get("status") == status
        and payload.get("candidate_sha") == target
    )

for receipt_path in sorted(root.glob(f"*-{target[:12]}.finalized.json")):
    if receipt_path.is_symlink() or not receipt_path.is_file():
        continue
    receipt = load(receipt_path)
    if (
        receipt.get("schema_version") == "librarian-release-finalization/v1"
        and receipt.get("status") == "RELEASE_VERIFIED"
        and receipt.get("candidate_sha") == target
        and valid_reference(
            receipt.get("deployment_manifest"),
            schema="librarian-release-event/v1",
            status="DEPLOYED",
        )
        and valid_reference(
            receipt.get("restart_persistence_proof"),
            schema="librarian-restart-persistence-proof/v1",
            status="PASS",
        )
    ):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

if ! release_is_finalized "${TARGET_SHA}"; then
  echo "ERROR: rollback target lacks a valid RELEASE_VERIFIED finalization receipt" >&2
  exit 2
fi

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
if body.get("status") != "ok" or body.get("deployed_sha") != os.environ["EXPECTED_SHA"]:
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
  HEALTH_STATUS_VALUE="${HEALTH_STATUS}" \
  HEALTH_HASH_VALUE="${health_hash}" \
  MEMORY_STATUS_VALUE="${memory_status}" \
  MEMORY_BEFORE_VALUE="${MEMORY_BEFORE}" \
  MEMORY_AFTER_VALUE="${MEMORY_AFTER}" \
  python3 - "${MANIFEST_PATH}" <<PY
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile

path = Path("${MANIFEST_PATH}")
failure = None
if os.environ["FAILURE_CODE"]:
    failure = {
        "line": int(os.environ["FAILURE_LINE"]),
        "exit_code": int(os.environ["FAILURE_CODE"]),
    }
payload = {
    "schema_version": "librarian-release-event/v1",
    "event_id": "${EVENT_ID}",
    "event_type": "rollback",
    "status": os.environ["STATUS"],
    "candidate_sha": "${TARGET_SHA}",
    "previous_sha": "${ORIGINAL_SHA}",
    "archive_sha256": "${ARCHIVE_SHA256}",
    "release_path": "${TARGET_PATH}",
    "memory_path": "${MEMORY_ROOT}",
    "manifest_path": str(path),
    "release_gate_receipt": {
        "path": "${STORED_RELEASE_GATE}",
        "sha256": "${RELEASE_GATE_SHA256}",
    },
    "started_at": "${STARTED_AT}",
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "health": {
        "url": "${HEALTH_URL}",
        "status": os.environ["HEALTH_STATUS_VALUE"],
        "response_sha256": os.environ["HEALTH_HASH_VALUE"] or None,
    },
    "memory_integrity": {
        "status": os.environ["MEMORY_STATUS_VALUE"],
        "before_sha256": os.environ["MEMORY_BEFORE_VALUE"] or None,
        "after_sha256": os.environ["MEMORY_AFTER_VALUE"] or None,
        "pre_candidate_sha256": os.environ["MEMORY_BEFORE_VALUE"] or None,
        "bootstrap_applied": False,
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
  local memory_safe=1
  trap - ERR
  set +e
  echo "ERROR: rollback failed at line ${failure_line}; restoring original release" >&2
  systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1
  if [[ "${SWITCHED}" -eq 1 ]]; then
    atomic_link "${ORIGINAL_PATH}"
  fi
  MEMORY_AFTER="$(memory_digest)"
  if [[ -n "${MEMORY_BEFORE}" && "${MEMORY_BEFORE}" != "${MEMORY_AFTER}" ]]; then
    memory_safe=0
    HEALTH_STATUS="FAIL"
    echo "ERROR: persistent memory changed; original release will not be started" >&2
  fi
  if [[ "${memory_safe}" -eq 1 ]]; then
    systemctl start "${SERVICE_NAME}.service"
    if wait_for_health "${ORIGINAL_SHA}"; then
      HEALTH_STATUS="PASS"
      MEMORY_AFTER="$(memory_digest)"
      if [[ -n "${MEMORY_BEFORE}" && "${MEMORY_BEFORE}" != "${MEMORY_AFTER}" ]]; then
        HEALTH_STATUS="FAIL"
        systemctl stop "${SERVICE_NAME}.service"
      fi
    else
      HEALTH_STATUS="FAIL"
      systemctl stop "${SERVICE_NAME}.service"
    fi
  fi
  write_manifest "FAILED" "${failure_line}" "${exit_code}"
  rm -f -- "${HEALTH_BODY}"
  echo "rollback_manifest=${MANIFEST_PATH}" >&2
  exit "${exit_code}"
}
trap 'on_error ${LINENO}' ERR

install -d -m 0750 -o root -g "${SERVICE_GROUP}" "${DEPLOYMENT_ROOT}"
install -m 0640 -o root -g "${SERVICE_GROUP}" \
  "${EMBEDDED_RELEASE_GATE}" "${STORED_RELEASE_GATE}"
systemctl stop "${SERVICE_NAME}.service"
MEMORY_BEFORE="$(memory_digest)"
atomic_link "${TARGET_PATH}"
SWITCHED=1
systemctl start "${SERVICE_NAME}.service"
wait_for_health "${TARGET_SHA}"
HEALTH_STATUS="PASS"
MEMORY_AFTER="$(memory_digest)"
if [[ "${MEMORY_BEFORE}" != "${MEMORY_AFTER}" ]]; then
  echo "ERROR: rollback restart changed the persistent memory digest" >&2
  false
fi

write_manifest "ROLLED_BACK"
rm -f -- "${HEALTH_BODY}"
trap - ERR

echo "rollback_sha=${TARGET_SHA}"
echo "previous_sha=${ORIGINAL_SHA}"
echo "rollback_manifest=${MANIFEST_PATH}"

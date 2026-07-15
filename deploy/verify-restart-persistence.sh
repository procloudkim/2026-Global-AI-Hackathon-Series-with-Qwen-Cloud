#!/usr/bin/env bash
set -Eeuo pipefail

# Wave 5 fail-closed proof. Run only after explicit approval for a live Alibaba
# deployment and live Qwen calls. This writes a unique proof namespace into the
# existing persistent store and never deletes or rewinds that store.
#
# Usage on the host:
#   sudo bash deploy/verify-restart-persistence.sh \
#     --base-url https://<host> \
#     --auth-user <basic-auth-user> \
#     --auth-password-file /run/secrets/librarian-proof-password \
#     --expected-sha <40-hex-commit> \
#     --proof-namespace release-proof-<unique-suffix> \
#     --output-dir /var/lib/librarian/deployments/proofs/<run-id>

readonly CURRENT_LINK="/opt/librarian/current"
readonly MEMORY_ROOT="/var/lib/librarian/memory"
readonly PROOF_ROOT="/var/lib/librarian/deployments/proofs"
readonly SERVICE_NAME="librarian"
readonly VERIFIER_SHA256="$(sha256sum "${BASH_SOURCE[0]}" | awk '{print $1}')"

BASE_URL=""
AUTH_USER=""
AUTH_PASSWORD_FILE=""
EXPECTED_SHA=""
PROOF_NAMESPACE=""
OUTPUT_DIR=""
CURL_CONFIG=""
REQUEST_FILE=""
AUTH_PASSWORD=""
PROOF_STARTED=0

cleanup() {
  local exit_code="$?"
  if [[ "${PROOF_STARTED}" -eq 1 ]]; then
    systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
  fi
  if [[ -n "${CURL_CONFIG}" ]]; then
    rm -f -- "${CURL_CONFIG}"
  fi
  if [[ -n "${REQUEST_FILE}" ]]; then
    rm -f -- "${REQUEST_FILE}"
  fi
  unset AUTH_PASSWORD
  return "${exit_code}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="${2:-}"; shift 2 ;;
    --auth-user) AUTH_USER="${2:-}"; shift 2 ;;
    --auth-password-file) AUTH_PASSWORD_FILE="${2:-}"; shift 2 ;;
    --expected-sha) EXPECTED_SHA="${2:-}"; shift 2 ;;
    --proof-namespace) PROOF_NAMESPACE="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: verification must run as root so it can restart the service" >&2
  exit 2
fi
if [[ ! "${BASE_URL}" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]]; then
  echo "ERROR: --base-url must be a host-only HTTPS URL" >&2
  exit 2
fi
if [[ ! "${AUTH_USER}" =~ ^[A-Za-z0-9._~-]{1,64}$ ]]; then
  echo "ERROR: --auth-user contains unsupported characters" >&2
  exit 2
fi
if [[ ! "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: --expected-sha must be a full lowercase commit SHA" >&2
  exit 2
fi
if [[ ! -f "${CURRENT_LINK}/.deployed-sha" \
      || "$(<"${CURRENT_LINK}/.deployed-sha")" != "${EXPECTED_SHA}" ]]; then
  echo "ERROR: current release does not match --expected-sha" >&2
  exit 2
fi
# The exact candidate is now known to be serving but has not earned its
# restart-persistence finalization. The EXIT and signal traps stop it unless
# the complete proof explicitly clears this flag.
PROOF_STARTED=1
if [[ ! "${PROOF_NAMESPACE}" =~ ^release-proof-[a-z0-9-]{12,48}$ ]]; then
  echo "ERROR: --proof-namespace must be unique and match release-proof-[a-z0-9-]{12,48}" >&2
  exit 2
fi
if [[ "${OUTPUT_DIR}" != "${PROOF_ROOT}/"* || -L "${OUTPUT_DIR}" ]]; then
  echo "ERROR: --output-dir must be a new path below ${PROOF_ROOT}" >&2
  exit 2
fi
if [[ -e "${OUTPUT_DIR}" ]]; then
  echo "ERROR: proof output already exists; receipts are append-only by directory" >&2
  exit 2
fi
if [[ ! -f "${AUTH_PASSWORD_FILE}" || -L "${AUTH_PASSWORD_FILE}" ]]; then
  echo "ERROR: auth password file must be a regular, non-symlink file" >&2
  exit 2
fi
password_mode="$(stat -c '%a' "${AUTH_PASSWORD_FILE}")"
if (( (8#${password_mode}) & 8#077 )); then
  echo "ERROR: auth password file must not be group/world accessible" >&2
  exit 2
fi
AUTH_PASSWORD="$(<"${AUTH_PASSWORD_FILE}")"
if [[ -z "${AUTH_PASSWORD}" || "${AUTH_PASSWORD}" == *$'\n'* \
      || "${AUTH_PASSWORD}" == *$'\r'* ]]; then
  echo "ERROR: auth password file must contain one non-empty line" >&2
  exit 2
fi

install -d -m 0750 -o root -g librarian "${PROOF_ROOT}"
install -d -m 0700 -o root -g root "${OUTPUT_DIR}"

CURL_CONFIG="$(mktemp)"
REQUEST_FILE="$(mktemp)"

on_error() {
  local caught_exit="$?"
  local failure_line="${1:-1}"
  local exit_code="${2:-${caught_exit}}"
  trap - ERR INT TERM HUP
  set +e
  if [[ "${PROOF_STARTED}" -eq 1 ]]; then
    systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1
    FAILURE_LINE_VALUE="${failure_line}" \
    FAILURE_CODE_VALUE="${exit_code}" \
    CANDIDATE_SHA_VALUE="${EXPECTED_SHA}" \
    VERIFIER_SHA256_VALUE="${VERIFIER_SHA256}" \
    NAMESPACE_VALUE="${PROOF_NAMESPACE}" \
    SOURCE_A_VALUE="${SOURCE_A:-unknown}" \
    SOURCE_B_VALUE="${SOURCE_B:-unknown}" \
    MEMORY_DIGEST_VALUE="$(memory_digest 2>/dev/null || printf unavailable)" \
    python3 - "${OUTPUT_DIR}" <<'PY'
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

root = Path(sys.argv[1])
artifacts = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(root.glob("*"))
    if path.is_file() and "failure" not in path.name
}
payload = {
    "schema_version": "librarian-restart-persistence-proof/v1",
    "status": "FAIL_QUARANTINED",
    "failed_at": datetime.now(timezone.utc).isoformat(),
    "failure_line": int(os.environ["FAILURE_LINE_VALUE"]),
    "exit_code": int(os.environ["FAILURE_CODE_VALUE"]),
    "candidate_sha": os.environ["CANDIDATE_SHA_VALUE"],
    "verifier_sha256": os.environ["VERIFIER_SHA256_VALUE"],
    "proof_namespace": os.environ["NAMESPACE_VALUE"],
    "source_ids": [os.environ["SOURCE_A_VALUE"], os.environ["SOURCE_B_VALUE"]],
    "memory_sha256_at_failure": os.environ["MEMORY_DIGEST_VALUE"],
    "containment": {
        "service_stopped": True,
        "namespace_retained_for_audit": True,
        "memory_deleted_or_rewound": False,
    },
    "artifact_sha256": artifacts,
}
(root / "restart-persistence-failure.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
    chmod -R go-rwx "${OUTPUT_DIR}" >/dev/null 2>&1
    echo "ERROR: live proof namespace is quarantined; librarian.service is stopped" >&2
  fi
  exit "${exit_code}"
}
trap 'on_error ${LINENO}' ERR
trap 'on_error ${LINENO} 130' INT
trap 'on_error ${LINENO} 143' TERM
trap 'on_error ${LINENO} 129' HUP
chmod 0600 "${CURL_CONFIG}" "${REQUEST_FILE}"

escaped_user="${AUTH_USER//\\/\\\\}"
escaped_user="${escaped_user//\"/\\\"}"
escaped_password="${AUTH_PASSWORD//\\/\\\\}"
escaped_password="${escaped_password//\"/\\\"}"
printf 'silent\nshow-error\nfail\nmax-time = 120\nuser = "%s:%s"\n' \
  "${escaped_user}" "${escaped_password}" >"${CURL_CONFIG}"
unset AUTH_PASSWORD escaped_password

SOURCE_A="${PROOF_NAMESPACE}-source-a"
SOURCE_B="${PROOF_NAMESPACE}-source-b"

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

write_request() {
  local kind="$1"
  local output="$2"
  KIND="${kind}" \
  SOURCE_A_VALUE="${SOURCE_A}" \
  SOURCE_B_VALUE="${SOURCE_B}" \
  NAMESPACE_VALUE="${PROOF_NAMESPACE}" \
  python3 - "${output}" <<'PY'
import json
import os
import sys

namespace = os.environ["NAMESPACE_VALUE"]
kind = os.environ["KIND"]
if kind == "source-a":
    payload = {
        "source_id": os.environ["SOURCE_A_VALUE"],
        "text": (
            f"Release proof namespace {namespace}. "
            f"In release-proof, {namespace}'s production-quota is "
            "100 units per minute. "
            f"In release-proof, {namespace}'s retention-marker is alpha."
        ),
    }
elif kind == "source-b":
    payload = {
        "source_id": os.environ["SOURCE_B_VALUE"],
        "text": (
            f"Release proof namespace {namespace}. This record explicitly replaces "
            f"{os.environ['SOURCE_A_VALUE']}. In release-proof, {namespace}'s "
            "production-quota is 1000 units per minute."
        ),
    }
elif kind == "quota-query":
    payload = {
        "question": (
            f"What is {namespace}'s current production-quota in release-proof?"
        ),
        "top_k": 3,
    }
elif kind == "marker-query":
    payload = {
        "question": f"What is {namespace}'s retention-marker in release-proof?",
        "top_k": 3,
    }
else:
    raise SystemExit(f"unknown request kind: {kind}")
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
PY
}

post_json() {
  local endpoint="$1"
  local request_kind="$2"
  local output="$3"
  write_request "${request_kind}" "${REQUEST_FILE}"
  curl --config "${CURL_CONFIG}" \
    --header 'Content-Type: application/json' \
    --request POST \
    --data-binary "@${REQUEST_FILE}" \
    "${BASE_URL}${endpoint}" \
    --output "${output}"
}

get_json() {
  local endpoint="$1"
  local output="$2"
  curl --config "${CURL_CONFIG}" "${BASE_URL}${endpoint}" --output "${output}"
}

budget_checkpoint() {
  local output="$1"
  get_json /stats "${output}"
  python3 - "${OUTPUT_DIR}/stats-before.json" "${output}" "${OUTPUT_DIR}" <<'PY'
import json
from pathlib import Path
import sys

before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["ledger"]
current = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["ledger"]
root = Path(sys.argv[3])
tokens = int(current["tokens"]["total"]) - int(before["tokens"]["total"])
logical_calls = 0
for path in root.glob("ingest-*.json"):
    body = json.loads(path.read_text(encoding="utf-8"))
    logical_calls += 1 + int((body.get("trace") or {}).get("heavy_arbitrations", 0))
for path in root.glob("query-*.json"):
    body = json.loads(path.read_text(encoding="utf-8"))
    logical_calls += 2 if "->" in str(body.get("route", "")) else 1
if tokens < 0 or tokens > 25000:
    raise SystemExit("Qwen token budget exceeded at operation boundary")
if logical_calls > 10:
    raise SystemExit("provider-call exposure exceeded at operation boundary")
PY
}

wait_for_health() {
  local attempt
  for attempt in $(seq 1 30); do
    if curl --config "${CURL_CONFIG}" "${BASE_URL}/health" \
      --output "${OUTPUT_DIR}/health-after-restart.json" 2>/dev/null \
      && EXPECTED_SHA_VALUE="${EXPECTED_SHA}" python3 - \
        "${OUTPUT_DIR}/health-after-restart.json" <<'PY'
import json
import os
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
if body.get("status") != "ok" or body.get("deployed_sha") != os.environ["EXPECTED_SHA_VALUE"]:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 2
  done
  return 1
}

# Refuse to reuse a source namespace. This checks canonical claims directly and
# does not spend a Qwen call.
runuser -u librarian -- env \
  PYTHONPATH="${CURRENT_LINK}/src" \
  LIBRARIAN_MEMORY_ROOT="${MEMORY_ROOT}" \
  "${CURRENT_LINK}/.venv/bin/python" - "${SOURCE_A}" "${SOURCE_B}" <<'PY'
import os
import sys
from librarian.claims import Claim
from librarian.store import MemoryStore

forbidden = set(sys.argv[1:])
store = MemoryStore(os.environ["LIBRARIAN_MEMORY_ROOT"])
with store.transaction():
    for page in store.list_wiki_pages():
        for raw in store.claims_for_page(page):
            claim = Claim.from_dict(raw)
            if forbidden.intersection(claim.source_ids):
                raise SystemExit("proof namespace already exists in canonical memory")
PY

get_json /health "${OUTPUT_DIR}/health-before.json"
EXPECTED_SHA_VALUE="${EXPECTED_SHA}" python3 - "${OUTPUT_DIR}/health-before.json" <<'PY'
import json
import os
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
if body.get("status") != "ok" or body.get("deployed_sha") != os.environ["EXPECTED_SHA_VALUE"]:
    raise SystemExit("health response does not bind the expected deployed SHA")
PY

get_json /stats "${OUTPUT_DIR}/stats-before.json"
# Validate the pre-run provider bounds without printing the secret-bearing env.
python3 - /etc/librarian/librarian.env <<'PY'
import sys

values = {}
for raw in open(sys.argv[1], encoding="utf-8"):
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
if int(values.get("LIBRARIAN_QWEN_MAX_RETRIES", "0")) != 0:
    raise SystemExit("restart proof requires provider retries = 0")
if float(values.get("LIBRARIAN_QWEN_TIMEOUT_SECONDS", "30")) > 45:
    raise SystemExit("restart proof requires provider timeout <= 45 seconds")
if int(values.get("LIBRARIAN_QWEN_MAX_COMPLETION_TOKENS", "1600")) > 1600:
    raise SystemExit("restart proof requires max completion tokens <= 1600")
PY
post_json /ingest source-a "${OUTPUT_DIR}/ingest-a.json"
budget_checkpoint "${OUTPUT_DIR}/stats-checkpoint-1.json"
post_json /ingest source-b "${OUTPUT_DIR}/ingest-b.json"
budget_checkpoint "${OUTPUT_DIR}/stats-checkpoint-2.json"
post_json /query quota-query "${OUTPUT_DIR}/query-before-restart.json"
budget_checkpoint "${OUTPUT_DIR}/stats-checkpoint-3.json"

# Snapshot canonical claim IDs/status so the public response's selected-context
# ID trace can prove that the stale value was excluded without logging prompts.
runuser -u librarian -- env \
  PYTHONPATH="${CURRENT_LINK}/src" \
  LIBRARIAN_MEMORY_ROOT="${MEMORY_ROOT}" \
  "${CURRENT_LINK}/.venv/bin/python" - \
  "${SOURCE_A}" "${SOURCE_B}" >"${OUTPUT_DIR}/claim-state.json" <<'PY'
import json
import os
import re
import sys
from librarian.claims import Claim
from librarian.store import MemoryStore

source_a, source_b = sys.argv[1:]
store = MemoryStore(os.environ["LIBRARIAN_MEMORY_ROOT"])
claims = []
with store.transaction():
    for page in store.list_wiki_pages():
        for raw in store.claims_for_page(page):
            claim = Claim.from_dict(raw)
            if source_a in claim.source_ids or source_b in claim.source_ids:
                claim_payload = claim.to_dict()
                claim_payload["key"] = claim.key
                claims.append(claim_payload)

def quantity(value):
    match = re.fullmatch(
        r"\s*([0-9]+)(?:\s+units?\s+per\s+minute)?\s*",
        str(value),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None

namespace = source_a.removesuffix("-source-a")
quota_key = f"release-proof::{namespace}::production-quota"
marker_key = f"release-proof::{namespace}::retention-marker"
old = [
    c for c in claims
    if c["key"] == quota_key
    and quantity(c["value"]) == "100"
    and source_a in c["source_ids"]
]
new = [
    c for c in claims
    if c["key"] == quota_key
    and quantity(c["value"]) == "1000"
    and source_b in c["source_ids"]
]
marker = [
    c for c in claims
    if c["key"] == marker_key
    and c["value"].casefold() == "alpha"
    and source_a in c["source_ids"]
]
if len(old) != 1 or old[0]["status"] != "superseded":
    raise SystemExit("old quota claim is not uniquely superseded")
if len(new) != 1 or new[0]["status"] != "active":
    raise SystemExit("new quota claim is not uniquely active")
if len(marker) != 1 or marker[0]["status"] != "active":
    raise SystemExit("unrelated marker was not preserved")
print(json.dumps(
    {
        "old_claim_id": old[0]["claim_id"],
        "new_claim_id": new[0]["claim_id"],
        "marker_claim_id": marker[0]["claim_id"],
        "old_status": old[0]["status"],
        "new_status": new[0]["status"],
        "marker_status": marker[0]["status"],
    },
    indent=2,
    sort_keys=True,
))
PY

MEMORY_BEFORE="$(memory_digest)"
printf '%s\n' "${MEMORY_BEFORE}" >"${OUTPUT_DIR}/memory-before.sha256"
systemctl restart "${SERVICE_NAME}.service"
wait_for_health
MEMORY_AFTER="$(memory_digest)"
printf '%s\n' "${MEMORY_AFTER}" >"${OUTPUT_DIR}/memory-after.sha256"
if [[ "${MEMORY_BEFORE}" != "${MEMORY_AFTER}" ]]; then
  echo "ERROR: service restart changed canonical persistent memory" >&2
  false
fi

post_json /query quota-query "${OUTPUT_DIR}/query-after-restart.json"
budget_checkpoint "${OUTPUT_DIR}/stats-checkpoint-4.json"
post_json /query marker-query "${OUTPUT_DIR}/query-marker-after-restart.json"
budget_checkpoint "${OUTPUT_DIR}/stats-after.json"

SOURCE_A_VALUE="${SOURCE_A}" \
SOURCE_B_VALUE="${SOURCE_B}" \
EXPECTED_SHA_VALUE="${EXPECTED_SHA}" \
VERIFIER_SHA256_VALUE="${VERIFIER_SHA256}" \
MEMORY_DIGEST_VALUE="${MEMORY_BEFORE}" \
python3 - "${OUTPUT_DIR}" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])

def load(name):
    with (root / name).open(encoding="utf-8") as handle:
        return json.load(handle)

def require(condition, message):
    if not condition:
        raise SystemExit(message)

def quantity(value):
    match = re.fullmatch(
        r"\s*([0-9]+)(?:\s+units?\s+per\s+minute)?\s*",
        str(value),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None

def quota_fact_ids(response):
    matches = []
    for fact in response.get("facts", []):
        if quantity(fact.get("value")) != "1000":
            continue
        claim_ids = tuple(sorted(str(item) for item in fact.get("claim_ids", [])))
        if claims["new_claim_id"] in claim_ids:
            matches.append(claim_ids)
    return matches

ingest_a = load("ingest-a.json")
ingest_b = load("ingest-b.json")
before = load("query-before-restart.json")
after = load("query-after-restart.json")
marker = load("query-marker-after-restart.json")
health = load("health-after-restart.json")
claims = load("claim-state.json")
stats_before = load("stats-before.json")
stats_after = load("stats-after.json")

require(ingest_a.get("status") == "ok", "source A ingest did not pass")
require(ingest_b.get("status") == "ok", "source B ingest did not pass")
transitions = ingest_b.get("transitions") or []
require(
    any(
        item.get("from_status") == "active"
        and item.get("to_status") == "superseded"
        and item.get("evidence_spans")
        and os.environ["SOURCE_B_VALUE"] in (item.get("evidence_source_ids") or [])
        for item in transitions
    ),
    "source B did not record an evidence-bound supersession transition",
)

ledger_before = stats_before.get("ledger") or {}
ledger_after = stats_after.get("ledger") or {}
request_delta = int(ledger_after.get("requests", 0)) - int(ledger_before.get("requests", 0))
token_delta = int((ledger_after.get("tokens") or {}).get("total", 0)) - int(
    (ledger_before.get("tokens") or {}).get("total", 0)
)
provider_calls = 0
provider_calls += 1 + int((ingest_a.get("trace") or {}).get("heavy_arbitrations", 0))
provider_calls += 1 + int((ingest_b.get("trace") or {}).get("heavy_arbitrations", 0))
for response in (before, after, marker):
    route = str(response.get("route", ""))
    provider_calls += 2 if "->" in route else 1
require(request_delta == 5, "post-deploy proof observed concurrent or missing API operations")
require(0 < provider_calls <= 10, "post-deploy provider-call trace exceeded its cap")
require(0 < token_delta <= 25000, "post-deploy Qwen token usage exceeded its cap")

standalone_old = re.compile(r"(?<![0-9])100(?![0-9])")
quota_trace_modes = {}
for label, response in (("before", before), ("after", after)):
    require(response.get("status") == "ok", f"{label} quota query failed")
    require(response.get("abstained") is False, f"{label} quota query abstained")
    require(
        any(
            quantity(fact.get("value")) == "1000"
            for fact in response.get("facts", [])
        ),
        f"{label} quota answer did not select 1000",
    )
    answer_and_facts = json.dumps(
        {"answer": response.get("answer"), "facts": response.get("facts")},
        sort_keys=True,
    )
    require(not standalone_old.search(answer_and_facts), f"{label} answer leaked stale 100")
    require(
        os.environ["SOURCE_B_VALUE"] in (response.get("evidence_source_ids") or []),
        f"{label} quota answer lacks source B evidence",
    )
    require(bool(response.get("citations")), f"{label} quota answer lacks a citation")
    require(int((response.get("tokens") or {}).get("total", 0)) > 0, f"{label} lacks Qwen token usage")
    trace = response.get("trace") or {}
    require(int(trace.get("loaded_pages", 99)) <= 3, f"{label} exceeded top-K=3")
    require(int(trace.get("context_tokens", 0)) > 0, f"{label} lacks context-token trace")
    selected = set(trace.get("active_claim_ids_loaded") or []) | set(
        trace.get("disputed_claim_ids_loaded") or []
    )
    require(claims["new_claim_id"] in selected, f"{label} selected context lacks new claim")
    require(claims["old_claim_id"] not in selected, f"{label} selected context contains stale claim")
    filtered = set(trace.get("superseded_claim_ids_filtered") or [])
    quota_trace_modes[label] = (
        "filtered" if claims["old_claim_id"] in filtered else "not_retrieved"
    )

require(
    len(quota_fact_ids(before)) == 1
    and quota_fact_ids(before) == quota_fact_ids(after),
    "active quota claim identity did not survive restart",
)
require(marker.get("status") == "ok" and marker.get("abstained") is False, "marker query failed")
require(
    any(str(fact.get("value")).casefold() == "alpha" for fact in marker.get("facts", [])),
    "unrelated marker was not recalled after restart",
)
require(
    os.environ["SOURCE_A_VALUE"] in (marker.get("evidence_source_ids") or []),
    "marker result lacks source A evidence",
)
require(
    health.get("status") == "ok" and health.get("deployed_sha") == os.environ["EXPECTED_SHA_VALUE"],
    "post-restart health is not bound to the candidate SHA",
)

artifacts = {}
for path in sorted(root.glob("*.json")):
    if path.name == "restart-persistence-receipt.json":
        continue
    artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()

receipt = {
    "schema_version": "librarian-restart-persistence-proof/v1",
    "status": "PASS",
    "candidate_sha": os.environ["EXPECTED_SHA_VALUE"],
    "verifier_sha256": os.environ["VERIFIER_SHA256_VALUE"],
    "proof_namespace": os.environ["SOURCE_A_VALUE"].removesuffix("-source-a"),
    "source_ids": [os.environ["SOURCE_A_VALUE"], os.environ["SOURCE_B_VALUE"]],
    "memory_sha256_before_restart": os.environ["MEMORY_DIGEST_VALUE"],
    "memory_sha256_after_restart": os.environ["MEMORY_DIGEST_VALUE"],
    "assertions": {
        "old_claim_superseded": True,
        "new_claim_active": True,
        "unrelated_claim_preserved": True,
        "old_value_absent_from_answer_and_selected_context": True,
        "source_b_evidence_present": True,
        "decision_event_evidence_present": True,
        "top_k_and_context_trace_present": True,
        "qwen_usage_present": True,
        "exact_sha_health_present": True,
    },
    "quota_old_claim_trace_modes": quota_trace_modes,
    "budget": {
        "maximum_api_operations": 5,
        "maximum_logical_provider_calls_from_route_trace": 10,
        "maximum_provider_attempts_with_retry_zero": 10,
        "maximum_total_tokens": 25000,
        "timeout_seconds_per_http_operation": 120,
        "maximum_retries_per_provider_call": 0,
        "absolute_requested_completion_token_exposure": 16000,
        "token_checkpoint_after_each_api_operation": True,
    },
    "usage": {
        "api_operations": request_delta,
        "logical_provider_calls_from_route_trace": provider_calls,
        "maximum_provider_attempts_from_configured_retry": provider_calls,
        "total_tokens": token_delta,
    },
    "artifact_sha256": artifacts,
}
with (root / "restart-persistence-receipt.json").open("w", encoding="utf-8") as handle:
    json.dump(receipt, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

chmod -R go-rwx "${OUTPUT_DIR}"
PROOF_STARTED=0
trap - ERR INT TERM HUP
echo "restart_persistence_receipt=${OUTPUT_DIR}/restart-persistence-receipt.json"

#!/usr/bin/env python3
"""Validate a complete quarantined proof without repeating live Qwen calls."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


PROOF_ROOT = Path("/var/lib/librarian/deployments/proofs")
CURRENT_SHA = Path("/opt/librarian/current/.deployed-sha")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
CLAIM_ID_RE = re.compile(r"^[0-9a-f]{20}$")
REQUIRED_ARTIFACTS = {
    "claim-state.json",
    "health-after-restart.json",
    "health-before.json",
    "ingest-a.json",
    "ingest-b.json",
    "memory-after.sha256",
    "memory-before.sha256",
    "query-after-restart.json",
    "query-before-restart.json",
    "query-marker-after-restart.json",
    "stats-after.json",
    "stats-before.json",
    "stats-checkpoint-1.json",
    "stats-checkpoint-2.json",
    "stats-checkpoint-3.json",
    "stats-checkpoint-4.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--proof-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quantity(value: object) -> str | None:
    match = re.fullmatch(
        r"\s*([0-9]+)(?:\s+units?\s+per\s+minute)?\s*",
        str(value),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def canonical_memory_digest() -> str:
    command = (
        "find /var/lib/librarian/memory -type f "
        "! -name '.memory.lock' ! -name 'runs.jsonl' -print0 "
        "| sort -z | xargs -0 -r sha256sum | sha256sum | awk '{print $1}'"
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    require(DIGEST_RE.fullmatch(value) is not None, "current memory digest is invalid")
    return value


def ledger(body: dict) -> dict:
    value = body.get("ledger")
    require(isinstance(value, dict), "stats receipt lacks a ledger")
    return value


def request_count(body: dict) -> int:
    return int(ledger(body).get("requests", -1))


def token_count(body: dict) -> int:
    tokens = ledger(body).get("tokens") or {}
    return int(tokens.get("total", -1))


def quota_fact_ids(
    response: dict, new_claim_id: str, expected_key: str
) -> list[tuple[str, ...]]:
    matches: list[tuple[str, ...]] = []
    for fact in response.get("facts", []):
        if fact.get("key") != expected_key or quantity(fact.get("value")) != "1000":
            continue
        claim_ids = tuple(sorted(str(item) for item in fact.get("claim_ids", [])))
        if new_claim_id in claim_ids:
            matches.append(claim_ids)
    return matches


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        raise SystemExit("continuation validation must run as root")
    require(SHA_RE.fullmatch(args.candidate_sha) is not None, "invalid candidate SHA")

    root = PROOF_ROOT.resolve(strict=True)
    proof_dir = Path(args.proof_dir).resolve(strict=True)
    output_dir = Path(args.output_dir).resolve(strict=False)
    for path, label in ((proof_dir, "proof"), (output_dir, "output")):
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label} directory is outside {root}") from exc
    require(
        proof_dir.is_dir() and not Path(args.proof_dir).is_symlink(),
        "proof directory is invalid",
    )
    require(
        not output_dir.exists() and not output_dir.is_symlink(),
        "output directory already exists",
    )
    require(
        CURRENT_SHA.read_text(encoding="utf-8").strip() == args.candidate_sha,
        "current release SHA changed",
    )
    service = subprocess.run(
        ["systemctl", "is-active", "librarian.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    require(service.stdout.strip() == "inactive", "quarantined service is not inactive")

    failure_path = proof_dir / "restart-persistence-failure.json"
    failure = load(failure_path)
    require(
        failure.get("schema_version") == "librarian-restart-persistence-proof/v1",
        "failure schema drifted",
    )
    require(failure.get("status") == "FAIL_QUARANTINED", "proof is not quarantined")
    require(
        failure.get("candidate_sha") == args.candidate_sha,
        "failure belongs to another candidate",
    )
    require(
        DIGEST_RE.fullmatch(str(failure.get("verifier_sha256", ""))) is not None,
        "original verifier digest is invalid",
    )
    containment = failure.get("containment") or {}
    require(containment.get("service_stopped") is True, "failure did not stop the service")
    require(
        containment.get("namespace_retained_for_audit") is True,
        "failure namespace was not retained",
    )
    require(
        containment.get("memory_deleted_or_rewound") is False,
        "failure rewound persistent memory",
    )
    require(
        not (proof_dir / "restart-persistence-receipt.json").exists(),
        "quarantined proof already has a PASS receipt",
    )

    artifact_hashes = failure.get("artifact_sha256")
    require(isinstance(artifact_hashes, dict), "failure lacks artifact hashes")
    require(REQUIRED_ARTIFACTS <= set(artifact_hashes), "quarantined proof is incomplete")
    for name, expected in artifact_hashes.items():
        require(Path(name).name == name, "failure artifact name is not a basename")
        require(
            DIGEST_RE.fullmatch(str(expected)) is not None,
            f"invalid artifact digest: {name}",
        )
        path = proof_dir / name
        require(
            path.is_file() and not path.is_symlink(),
            f"missing regular artifact: {name}",
        )
        require(digest(path) == expected, f"quarantined artifact changed: {name}")

    memory_before = (proof_dir / "memory-before.sha256").read_text(
        encoding="utf-8"
    ).strip()
    memory_after = (proof_dir / "memory-after.sha256").read_text(
        encoding="utf-8"
    ).strip()
    require(
        DIGEST_RE.fullmatch(memory_before) is not None,
        "pre-restart memory digest is invalid",
    )
    require(memory_before == memory_after, "restart changed persistent memory")
    require(
        failure.get("memory_sha256_at_failure") == memory_after,
        "failure memory digest drifted",
    )
    require(canonical_memory_digest() == memory_after, "memory changed after quarantine")

    health_before = load(proof_dir / "health-before.json")
    health_after = load(proof_dir / "health-after-restart.json")
    for label, health in (("before", health_before), ("after", health_after)):
        require(health.get("status") == "ok", f"{label} health did not pass")
        require(
            health.get("deployed_sha") == args.candidate_sha,
            f"{label} health is for another SHA",
        )

    source_ids = failure.get("source_ids")
    require(
        isinstance(source_ids, list) and len(source_ids) == 2,
        "failure source IDs are invalid",
    )
    source_a, source_b = (str(item) for item in source_ids)
    namespace = str(failure.get("proof_namespace", ""))
    require(source_a == f"{namespace}-source-a", "source A does not bind the namespace")
    require(source_b == f"{namespace}-source-b", "source B does not bind the namespace")

    claims = load(proof_dir / "claim-state.json")
    for name in ("old_claim_id", "new_claim_id", "marker_claim_id"):
        require(
            CLAIM_ID_RE.fullmatch(str(claims.get(name, ""))) is not None,
            f"invalid {name}",
        )
    require(claims.get("old_status") == "superseded", "old claim is not superseded")
    require(claims.get("new_status") == "active", "new claim is not active")
    require(claims.get("marker_status") == "active", "marker claim is not active")

    ingest_a = load(proof_dir / "ingest-a.json")
    ingest_b = load(proof_dir / "ingest-b.json")
    require(
        ingest_a.get("status") == "ok" and ingest_b.get("status") == "ok",
        "ingest did not pass",
    )
    transitions = ingest_b.get("transitions") or []
    require(
        any(
            item.get("from_status") == "active"
            and item.get("to_status") == "superseded"
            and item.get("trigger_claim_id") == claims["new_claim_id"]
            and item.get("evidence_spans")
            and source_b in (item.get("evidence_source_ids") or [])
            for item in transitions
        ),
        "source B lacks an evidence-bound supersession transition",
    )

    stats_before = load(proof_dir / "stats-before.json")
    checkpoints = [
        load(proof_dir / f"stats-checkpoint-{index}.json")
        for index in range(1, 5)
    ]
    stats_after = load(proof_dir / "stats-after.json")
    request_before = request_count(stats_before)
    request_after = request_count(stats_after)
    token_before = token_count(stats_before)
    token_after = token_count(stats_after)
    require(
        request_after - request_before == 5,
        "proof observed concurrent or missing API operations",
    )
    require(
        [request_count(item) for item in checkpoints]
        == list(range(request_before + 1, request_before + 5)),
        "checkpoint request counts drifted",
    )
    require(
        int(ledger(stats_after).get("successes", -1))
        - int(ledger(stats_before).get("successes", -1))
        == 5,
        "proof API operation failed",
    )
    require(
        int(ledger(stats_after).get("failures", -1))
        == int(ledger(stats_before).get("failures", -1)),
        "proof added a failed API operation",
    )
    token_delta = token_after - token_before
    require(0 < token_delta <= 25000, "proof Qwen token usage is outside its cap")
    checkpoint_tokens = [token_count(item) - token_before for item in checkpoints]
    require(
        checkpoint_tokens == sorted(checkpoint_tokens),
        "checkpoint token usage is not monotonic",
    )
    require(
        all(0 < value <= 25000 for value in checkpoint_tokens),
        "checkpoint token cap was exceeded",
    )

    before = load(proof_dir / "query-before-restart.json")
    after = load(proof_dir / "query-after-restart.json")
    marker = load(proof_dir / "query-marker-after-restart.json")
    provider_calls = 0
    provider_calls += 1 + int(
        (ingest_a.get("trace") or {}).get("heavy_arbitrations", 0)
    )
    provider_calls += 1 + int(
        (ingest_b.get("trace") or {}).get("heavy_arbitrations", 0)
    )
    for response in (before, after, marker):
        provider_calls += 2 if "->" in str(response.get("route", "")) else 1
    require(0 < provider_calls <= 10, "logical provider-call trace exceeded its cap")

    old_value = re.compile(r"(?<![0-9])100(?![0-9])")
    quota_key = f"release-proof::{namespace}::production-quota"
    quota_trace_modes: dict[str, str] = {}
    for label, response in (("before", before), ("after", after)):
        require(
            response.get("status") == "ok" and response.get("abstained") is False,
            f"{label} quota query failed",
        )
        require(
            len(quota_fact_ids(response, claims["new_claim_id"], quota_key)) == 1,
            f"{label} quota fact is not unique",
        )
        answer_and_facts = json.dumps(
            {"answer": response.get("answer"), "facts": response.get("facts")},
            sort_keys=True,
        )
        require(
            not old_value.search(answer_and_facts),
            f"{label} quota response leaked stale 100",
        )
        require(
            source_b in (response.get("evidence_source_ids") or []),
            f"{label} lacks source B evidence",
        )
        require(bool(response.get("citations")), f"{label} lacks a citation")
        require(
            int((response.get("tokens") or {}).get("total", 0)) > 0,
            f"{label} lacks Qwen usage",
        )
        trace = response.get("trace") or {}
        require(int(trace.get("loaded_pages", 99)) <= 3, f"{label} exceeded top-K=3")
        require(
            int(trace.get("context_tokens", 0)) > 0,
            f"{label} lacks context trace",
        )
        selected = set(trace.get("active_claim_ids_loaded") or []) | set(
            trace.get("disputed_claim_ids_loaded") or []
        )
        require(
            claims["new_claim_id"] in selected,
            f"{label} did not load the new claim",
        )
        require(
            claims["old_claim_id"] not in selected,
            f"{label} loaded the stale claim",
        )
        filtered = set(trace.get("superseded_claim_ids_filtered") or [])
        quota_trace_modes[label] = (
            "filtered" if claims["old_claim_id"] in filtered else "not_retrieved"
        )
    require(
        quota_fact_ids(before, claims["new_claim_id"], quota_key)
        == quota_fact_ids(after, claims["new_claim_id"], quota_key),
        "active quota claim identity did not survive restart",
    )

    marker_key = f"release-proof::{namespace}::retention-marker"
    require(
        marker.get("status") == "ok" and marker.get("abstained") is False,
        "marker query failed",
    )
    marker_facts = [
        fact
        for fact in marker.get("facts", [])
        if fact.get("key") == marker_key
        and str(fact.get("value", "")).casefold() == "alpha"
        and claims["marker_claim_id"] in (fact.get("claim_ids") or [])
    ]
    require(len(marker_facts) == 1, "marker fact is not uniquely preserved")
    require(
        source_a in (marker.get("evidence_source_ids") or []),
        "marker lacks source A evidence",
    )

    output_dir.mkdir(parents=True, mode=0o700)
    receipt_path = output_dir / "restart-persistence-receipt.json"
    verifier_sha256 = digest(Path(__file__))
    artifact_receipts = {
        f"quarantined/{name}": str(expected)
        for name, expected in sorted(artifact_hashes.items())
    }
    artifact_receipts["quarantined/restart-persistence-failure.json"] = digest(
        failure_path
    )
    payload = {
        "schema_version": "librarian-restart-persistence-proof/v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha": args.candidate_sha,
        "verifier_sha256": verifier_sha256,
        "proof_namespace": namespace,
        "source_ids": [source_a, source_b],
        "continued_from_quarantine": {
            "proof_dir": str(proof_dir),
            "failure_receipt": str(failure_path),
            "failure_receipt_sha256": digest(failure_path),
            "original_verifier_sha256": failure["verifier_sha256"],
            "failure_line": int(failure.get("failure_line", -1)),
            "exit_code": int(failure.get("exit_code", -1)),
        },
        "memory_sha256_before_restart": memory_before,
        "memory_sha256_after_restart": memory_after,
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
            "quarantined_artifacts_hash_verified": True,
            "memory_unchanged_after_quarantine": True,
        },
        "quota_old_claim_trace_modes": quota_trace_modes,
        "budget": {
            "maximum_api_operations": 5,
            "maximum_logical_provider_calls_from_route_trace": 10,
            "maximum_provider_attempts_with_retry_zero": 10,
            "maximum_total_tokens": 25000,
            "maximum_retries_per_provider_call": 0,
            "token_checkpoint_after_each_api_operation": True,
        },
        "usage": {
            "api_operations": request_after - request_before,
            "logical_provider_calls_from_route_trace": provider_calls,
            "maximum_provider_attempts_from_configured_retry": provider_calls,
            "total_tokens": token_delta,
        },
        "artifact_sha256": artifact_receipts,
    }
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output_dir, delete=False
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(receipt_path)
    receipt_path.chmod(0o600)
    print("restart_continuation_status=PASS")
    print(f"restart_persistence_receipt={receipt_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"RESTART_CONTINUATION_FAIL: {exc}", file=os.sys.stderr)
        raise SystemExit(2) from exc

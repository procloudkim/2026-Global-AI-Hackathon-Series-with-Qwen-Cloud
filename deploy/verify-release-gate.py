#!/usr/bin/env python3
"""Bind live-Qwen and host-readiness evidence to one exact candidate SHA."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.contracts import candidate_tree_hash


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--live-metrics", required=True)
    parser.add_argument("--isolation-attestation", required=True)
    parser.add_argument("--infrastructure-readiness", required=True)
    parser.add_argument("--expected-target-sha256", required=True)
    parser.add_argument("--contract", default="submission/hackathon-contract.json")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    args = parse_args()
    root = Path.cwd()
    paths = {
        "live_metrics": Path(args.live_metrics),
        "isolation_attestation": Path(args.isolation_attestation),
        "infrastructure_readiness": Path(args.infrastructure_readiness),
        "contract": Path(args.contract),
    }
    require(len(args.candidate_sha) == 40 and all(c in "0123456789abcdef" for c in args.candidate_sha), "invalid candidate SHA")
    require(
        len(args.expected_target_sha256) == 64
        and all(c in "0123456789abcdef" for c in args.expected_target_sha256),
        "invalid deployment target digest",
    )
    require(all(path.is_file() for path in paths.values()), "release gate input is missing")

    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
    require(head == args.candidate_sha, "checkout HEAD does not match the candidate SHA")
    require(not dirty, "release gate requires a clean exact-SHA checkout")

    tree_sha256 = candidate_tree_hash(root)
    live = load(paths["live_metrics"])
    attestation = load(paths["isolation_attestation"])
    infrastructure = load(paths["infrastructure_readiness"])
    contract = load(paths["contract"])

    require(live.get("status") == "LIVE_QWEN_2CASE_PASS", "live Qwen contract did not pass")
    require(live.get("candidate_tree_sha256") == tree_sha256, "live Qwen receipt is for another candidate tree")
    require(live.get("scenario_count") == 2, "live gate is not the capped two-case contract")
    usage = live.get("provider_usage") or {}
    require(int(usage.get("provider_errors", -1)) == 0, "live gate recorded provider errors")
    require(0 < int(usage.get("calls", 0)) <= 18, "live gate call count is outside the contract")
    require(0 < int(usage.get("total_tokens", 0)) <= 25000, "live gate token usage is outside the contract")
    live_runtime = live.get("runtime") or {}
    require(
        live_runtime.get("base_url")
        == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "live gate used another provider endpoint",
    )
    require(live_runtime.get("light_model_configured") == "qwen-flash", "live light-model contract drifted")
    require(
        live_runtime.get("heavy_model_configured") == "qwen-plus-2025-07-28",
        "live heavy-model contract drifted",
    )
    require(live_runtime.get("transport_max_retries") == 0, "live transport retry contract drifted")

    require(attestation.get("status") == "LOCAL_MOUNT_INSPECTION_PASS", "runner isolation attestation failed")
    require(attestation.get("gold_mount_absent") is True, "gold was mounted into the live runner")
    require(attestation.get("exact_mount_allowlist_pass") is True, "live runner mount allowlist drifted")
    require(attestation.get("mount_modes_valid") is True, "live runner mount modes drifted")
    require(attestation.get("image_inventory_gold_absent") is True, "live image contains private gold")
    require(attestation.get("image_matches_run_manifest") is True, "live image identity is not receipt-bound")
    require(attestation.get("candidate_hash_matches_run_manifest") is True, "runner candidate hash is not exact")

    require(infrastructure.get("status") == "PASS", "host infrastructure readiness did not pass")
    require(
        infrastructure.get("schema_version") == "librarian-infrastructure-readiness/v1",
        "host readiness schema is unsupported",
    )
    require(infrastructure.get("candidate_sha") == args.candidate_sha, "host readiness is for another candidate")
    require(infrastructure.get("provider_signal") == "alibaba_cloud_dmi", "host is not identified as Alibaba Cloud")
    require(
        infrastructure.get("deployment_target_sha256") == args.expected_target_sha256,
        "host readiness belongs to another deployment target",
    )
    require(bool(infrastructure.get("checks")), "host readiness has no checks")
    for field in ("cloud_approval_receipt_sha256", "approval_ticket_sha256"):
        value = str(infrastructure.get(field, ""))
        require(len(value) == 64 and all(character in "0123456789abcdef" for character in value), f"host readiness has invalid {field}")
    require(
        all(item.get("passed") is True for item in (infrastructure.get("checks") or {}).values()),
        "host readiness contains a failed check",
    )
    expected_runtime = {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "light_model": "qwen-flash",
        "heavy_model": "qwen-plus-2025-07-28",
    }
    require(infrastructure.get("runtime_contract") == expected_runtime, "host Qwen runtime contract drifted")
    require((infrastructure.get("runtime_limits") or {}).get("qwen_max_retries") == 0, "host retry contract is not zero")
    used_models = set(usage.get("models") or [])
    require("qwen-flash" in used_models, "live gate did not exercise qwen-flash")
    require(used_models <= {"qwen-flash", "qwen-plus-2025-07-28"}, "live gate used a non-allowlisted model")

    audit_end = datetime.fromisoformat(contract["snapshot"]["audit_window"]["end"])
    max_age = float(contract["snapshot"]["freshness"]["max_age_hours"]["deploy"])
    age_hours = (datetime.now(UTC) - audit_end.astimezone(UTC)).total_seconds() / 3600
    require(-1 <= age_hours <= max_age, "official contract snapshot is stale for deployment")

    output = Path(args.output)
    require(not output.exists(), "release gate output already exists")
    payload = {
        "schema_version": "librarian-release-gate/v1",
        "status": "PASS",
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_sha": args.candidate_sha,
        "candidate_tree_sha256": tree_sha256,
        "deployment_target_sha256": args.expected_target_sha256,
        "official_contract_age_hours": round(age_hours, 3),
        "limits": {"maximum_calls": 18, "maximum_total_tokens": 25000, "maximum_provider_errors": 0},
        "evidence_sha256": {name: digest(path) for name, path in paths.items()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        temporary = Path(handle.name)
    temporary.replace(output)
    print("release_gate_status=PASS")
    print(f"release_gate_receipt={output}")
    print(f"release_gate_sha256={digest(output)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"RELEASE_GATE_FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

#!/usr/bin/env python3
"""Fail closed unless a masked cloud approval receipt binds this candidate."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.contracts import candidate_tree_hash


DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_KEY_PARTS = (
    "account_number",
    "access_key",
    "api_key",
    "coupon_code",
    "private_key",
    "secret_key",
    "ssh_key",
)
REQUIRED_CONTROLS = {
    "benefit_or_billing_scope_verified",
    "overage_disabled_or_hard_capped",
    "automatic_renewal_disabled",
    "budget_alert_configured",
    "spending_alert_configured",
    "persistent_storage_verified",
    "public_ip_verified",
    "security_group_reviewed",
    "workbench_access_verified",
    "resource_creation_explicitly_approved",
}
ALLOWED_RUNTIMES = {
    "ecs_trial",
    "sas_trial",
    "coupon_covered_ecs",
    "coupon_covered_sas",
    "minimal_paid_ecs",
    "minimal_paid_sas",
}
TARGET_SCHEMA_VERSION = "librarian-deployment-target/v1"
TARGET_ENV = {
    "ssh_host": "LIBRARIAN_TARGET_SSH_HOST",
    "ssh_port": "LIBRARIAN_TARGET_SSH_PORT",
    "ssh_host_key": "LIBRARIAN_TARGET_SSH_HOST_KEY",
    "public_base_url": "LIBRARIAN_TARGET_PUBLIC_BASE_URL",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-target-sha256-from-env",
        action="store_true",
        help="Print only the masked deployment-target digest derived from LIBRARIAN_TARGET_* variables.",
    )
    parser.add_argument("--receipt")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--approval-ticket-sha256")
    parser.add_argument("--expected-target-sha256")
    parser.add_argument("--contract", default="submission/hackathon-contract.json")
    parser.add_argument("--evidence-manifest", default="submission/evidence-manifest.json")
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


def deployment_target_sha256(
    *,
    ssh_host: str,
    ssh_port: str,
    ssh_host_key: str,
    public_base_url: str,
) -> str:
    """Hash the exact deployment endpoint without returning its identifiers."""

    host = ssh_host.strip()
    port_text = ssh_port.strip()
    host_key = ssh_host_key.strip()
    base_url = public_base_url.strip()
    require(re.fullmatch(r"[A-Za-z0-9.-]+", host) is not None, "deployment target SSH host is invalid")
    require(port_text.isdecimal(), "deployment target SSH port is invalid")
    port = int(port_text)
    require(1 <= port <= 65535, "deployment target SSH port is outside the valid range")
    require(bool(host_key) and "\n" not in host_key and "\r" not in host_key, "deployment target SSH host key must be one nonempty line")
    parsed = urlsplit(base_url)
    require(
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.hostname is not None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == "",
        "deployment target public base URL must be an HTTPS origin",
    )
    try:
        public_port = parsed.port
    except ValueError as exc:
        raise ValueError("deployment target public base URL port is invalid") from exc
    require(public_port is None or 1 <= public_port <= 65535, "deployment target public base URL port is outside the valid range")
    canonical = {
        "public_base_url": base_url,
        "schema_version": TARGET_SCHEMA_VERSION,
        "ssh_host": host,
        "ssh_host_key_sha256": hashlib.sha256(host_key.encode("utf-8")).hexdigest(),
        "ssh_port": port,
    }
    encoded = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def target_digest_from_env() -> str:
    values: dict[str, str] = {}
    for field, name in TARGET_ENV.items():
        value = os.environ.get(name)
        require(value is not None, f"required masked target input is missing: {name}")
        values[field] = value
    return deployment_target_sha256(**values)


def reject_sensitive_keys(value: object, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise ValueError(f"secret/account identifier field is forbidden at {path}.{key}")
            reject_sensitive_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_sensitive_keys(child, path=f"{path}[{index}]")


def main() -> int:
    args = parse_args()
    if args.print_target_sha256_from_env:
        print(target_digest_from_env())
        return 0
    require(args.receipt is not None, "--receipt is required")
    require(args.expected_sha256 is not None, "--expected-sha256 is required")
    require(args.approval_ticket_sha256 is not None, "--approval-ticket-sha256 is required")
    require(args.expected_target_sha256 is not None, "--expected-target-sha256 is required")
    receipt_path = Path(args.receipt)
    contract_path = Path(args.contract)
    manifest_path = Path(args.evidence_manifest)
    require(receipt_path.is_file(), "masked cloud approval receipt is missing")
    require(DIGEST_RE.fullmatch(args.expected_sha256) is not None, "expected receipt digest is invalid")
    require(DIGEST_RE.fullmatch(args.approval_ticket_sha256) is not None, "approval ticket digest is invalid")
    require(DIGEST_RE.fullmatch(args.expected_target_sha256) is not None, "deployment target digest is invalid")
    receipt_digest = digest(receipt_path)
    require(receipt_digest == args.expected_sha256, "masked cloud approval receipt digest mismatch")

    receipt = load(receipt_path)
    contract = load(contract_path)
    manifest = load(manifest_path)
    reject_sensitive_keys(receipt)
    require(receipt.get("schema_version") == "librarian-cloud-approval/v1", "unsupported cloud approval schema")
    require(receipt.get("status") == "APPROVED", "cloud operation is not approved")
    approval_status = receipt.get("approval_status")
    require(approval_status in {"APPROVED_ZERO_COST", "APPROVED_PAID_WITH_CEILING"}, "invalid cost approval status")
    require(receipt.get("compute_eligibility") == "VERIFIED", "Compute eligibility is not verified")
    require(receipt.get("runtime") in ALLOWED_RUNTIMES, "runtime is not allowlisted")
    require(receipt.get("max_unapproved_spend_usd") == 0, "unapproved-spend ceiling is not zero")
    approved_spend = float(receipt.get("approved_max_spend_usd", -1))
    require(approved_spend >= 0, "approved spend ceiling is invalid")
    if approval_status == "APPROVED_ZERO_COST":
        require(approved_spend == 0, "zero-cost approval has a nonzero spend ceiling")
    else:
        require(approved_spend > 0, "paid approval lacks a positive explicit ceiling")
    require(receipt.get("account_identifiers") == "masked_or_omitted", "account identifiers are not masked")

    controls = receipt.get("controls") or {}
    require(set(controls) == REQUIRED_CONTROLS, "cloud approval controls are incomplete or contain unknown fields")
    require(all(controls.get(key) is True for key in REQUIRED_CONTROLS), "one or more cloud controls are not verified")
    evidence = receipt.get("masked_evidence_sha256") or []
    require(bool(evidence) and all(DIGEST_RE.fullmatch(str(item)) for item in evidence), "masked console evidence digests are missing")

    approved_at = datetime.fromisoformat(str(receipt["approved_at"]))
    expires_at = datetime.fromisoformat(str(receipt["approval_expires_at"]))
    retention = datetime.fromisoformat(str(receipt["resource_retention_through"]))
    now = datetime.now(UTC)
    require(approved_at.tzinfo is not None and expires_at.tzinfo is not None and retention.tzinfo is not None, "approval timestamps need offsets")
    require(approved_at <= now <= expires_at, "cloud approval is not currently valid")
    judging_end = datetime.fromisoformat(str(contract["deadlines"]["judging"]["end_utc"]).replace("Z", "+00:00"))
    require(retention >= judging_end, "approved runtime retention does not cover judging")

    tree_sha256 = candidate_tree_hash(Path.cwd())
    require(receipt.get("candidate_tree_sha256") == tree_sha256, "cloud approval belongs to another candidate tree")
    require(receipt.get("approval_ticket_sha256") == args.approval_ticket_sha256, "cloud approval belongs to another approval ticket")
    require(
        receipt.get("deployment_target_sha256") == args.expected_target_sha256,
        "cloud approval belongs to another deployment target",
    )

    artifacts = manifest.get("artifacts") or []
    matches = [item for item in artifacts if item.get("id") == "cloud_approval_receipt"]
    require(len(matches) == 1, "evidence manifest lacks the unique cloud approval artifact")
    artifact = matches[0]
    require(artifact.get("status") == "verified", "cloud approval artifact is not verified")
    require(artifact.get("path") == receipt_path.as_posix(), "cloud approval artifact path drifted")
    require(artifact.get("sha256") == receipt_digest, "evidence manifest cloud approval digest drifted")

    print("cloud_approval_status=PASS")
    print(f"cloud_approval_sha256={receipt_digest}")
    print(f"deployment_target_sha256={args.expected_target_sha256}")
    print(f"approved_runtime={receipt['runtime']}")
    print(f"approved_max_spend_usd={approved_spend:g}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"CLOUD_APPROVAL_FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

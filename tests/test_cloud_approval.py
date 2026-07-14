from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from eval.contracts import candidate_tree_hash


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "verify-cloud-approval.py"
CONTROLS = {
    "benefit_or_billing_scope_verified": True,
    "overage_disabled_or_hard_capped": True,
    "automatic_renewal_disabled": True,
    "budget_alert_configured": True,
    "spending_alert_configured": True,
    "persistent_storage_verified": True,
    "public_ip_verified": True,
    "security_group_reviewed": True,
    "workbench_access_verified": True,
    "resource_creation_explicitly_approved": True,
}
TARGET_INPUTS = {
    "ssh_host": "ecs-approved.example.invalid",
    "ssh_port": "2222",
    "ssh_host_key": "ecs-approved.example.invalid ssh-ed25519 AAAATESTPINNEDHOSTKEY",
    "public_base_url": "https://demo.example.invalid",
}


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def deployment_target_digest(values: dict[str, str] = TARGET_INPUTS) -> str:
    canonical = {
        "public_base_url": values["public_base_url"],
        "schema_version": "librarian-deployment-target/v1",
        "ssh_host": values["ssh_host"],
        "ssh_host_key_sha256": hashlib.sha256(values["ssh_host_key"].encode("utf-8")).hexdigest(),
        "ssh_port": int(values["ssh_port"]),
    }
    encoded = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str, str, str]:
    now = datetime.now(UTC)
    ticket_digest = hashlib.sha256(b"approved-ticket-42").hexdigest()
    target_digest = deployment_target_digest()
    receipt = {
        "schema_version": "librarian-cloud-approval/v1",
        "status": "APPROVED",
        "approval_status": "APPROVED_ZERO_COST",
        "approved_at": (now - timedelta(minutes=5)).isoformat(),
        "approval_expires_at": (now + timedelta(days=2)).isoformat(),
        "resource_retention_through": (now + timedelta(days=60)).isoformat(),
        "candidate_tree_sha256": candidate_tree_hash(ROOT),
        "approval_ticket_sha256": ticket_digest,
        "deployment_target_sha256": target_digest,
        "runtime": "ecs_trial",
        "compute_eligibility": "VERIFIED",
        "max_unapproved_spend_usd": 0,
        "approved_max_spend_usd": 0,
        "controls": CONTROLS,
        "masked_evidence_sha256": [hashlib.sha256(b"masked-console-proof").hexdigest()],
        "account_identifiers": "masked_or_omitted",
    }
    receipt_path = tmp_path / "cloud-approval-receipt.json"
    write_json(receipt_path, receipt)
    receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()

    contract_path = tmp_path / "contract.json"
    write_json(
        contract_path,
        {"deadlines": {"judging": {"end_utc": (now + timedelta(days=30)).isoformat()}}},
    )
    manifest_path = tmp_path / "manifest.json"
    write_json(
        manifest_path,
        {
            "artifacts": [
                {
                    "id": "cloud_approval_receipt",
                    "status": "verified",
                    "path": receipt_path.as_posix(),
                    "sha256": receipt_digest,
                }
            ]
        },
    )
    return receipt_path, contract_path, manifest_path, receipt_digest, ticket_digest, target_digest


def run_verifier(
    receipt: Path,
    contract: Path,
    manifest: Path,
    digest: str,
    ticket_digest: str,
    target_digest: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--receipt",
            str(receipt),
            "--expected-sha256",
            digest,
            "--approval-ticket-sha256",
            ticket_digest,
            "--expected-target-sha256",
            target_digest,
            "--contract",
            str(contract),
            "--evidence-manifest",
            str(manifest),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cloud_approval_requires_real_candidate_bound_receipt(tmp_path: Path) -> None:
    receipt, contract, manifest, digest, ticket, target = build_fixture(tmp_path)
    result = run_verifier(receipt, contract, manifest, digest, ticket, target)
    assert result.returncode == 0, result.stderr
    assert "cloud_approval_status=PASS" in result.stdout

    wrong_digest = "0" * 64 if digest != "0" * 64 else "1" * 64
    rejected = run_verifier(receipt, contract, manifest, wrong_digest, ticket, target)
    assert rejected.returncode == 2
    assert "digest mismatch" in rejected.stderr


def test_cloud_approval_rejects_secret_shaped_fields(tmp_path: Path) -> None:
    receipt, contract, manifest, _, ticket, target = build_fixture(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["api_key"] = "masked-is-still-a-forbidden-field"
    write_json(receipt, payload)
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["artifacts"][0]["sha256"] = digest
    write_json(manifest, manifest_payload)

    result = run_verifier(receipt, contract, manifest, digest, ticket, target)
    assert result.returncode == 2
    assert "forbidden" in result.stderr


def test_cloud_approval_rejects_another_deployment_target(tmp_path: Path) -> None:
    receipt, contract, manifest, digest, ticket, target = build_fixture(tmp_path)
    wrong_target = "0" * 64 if target != "0" * 64 else "1" * 64
    result = run_verifier(receipt, contract, manifest, digest, ticket, wrong_target)
    assert result.returncode == 2
    assert "another deployment target" in result.stderr


def test_target_digest_is_deterministic_and_opaque() -> None:
    env = os.environ.copy()
    env.update(
        {
            "LIBRARIAN_TARGET_SSH_HOST": TARGET_INPUTS["ssh_host"],
            "LIBRARIAN_TARGET_SSH_PORT": TARGET_INPUTS["ssh_port"],
            "LIBRARIAN_TARGET_SSH_HOST_KEY": TARGET_INPUTS["ssh_host_key"],
            "LIBRARIAN_TARGET_PUBLIC_BASE_URL": TARGET_INPUTS["public_base_url"],
        }
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--print-target-sha256-from-env"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == deployment_target_digest()
    assert TARGET_INPUTS["ssh_host"] not in result.stdout
    assert TARGET_INPUTS["public_base_url"] not in result.stdout

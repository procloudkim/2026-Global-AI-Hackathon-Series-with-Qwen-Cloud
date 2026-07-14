"""Verify a public, signed private-holdout promotion attestation.

The private evaluator owns oracle rows and the HOLDOUT_SEED.  The public
repository receives only aggregate metrics, artifact digests, isolation claims,
and a detached RSA signature from a pre-trusted independent evaluator.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from .contracts import (
    candidate_tree_hash,
    canonical_json,
    file_sha256,
    load_json,
    stable_hash,
)
from .evaluate import _kill_findings, _production_comparison_for_repeat


ATTESTATION_KIND = "librarian.private-holdout-promotion-attestation"
ATTESTATION_SCHEMA_VERSION = "1.0"
SIGNATURE_ALGORITHM = "rsa-pkcs1v15-sha256"

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RSA_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)
_PUBLIC_FORBIDDEN_KEYS = {
    "holdout_seed",
    "seed",
    "seed_commitment",
    "gold",
    "gold_path",
    "gold_rows",
    "gold_sha256",
    "oracle_rows",
    "expected_facts",
    "forbidden_facts",
    "protected_facts",
    "required_sources",
    "required_retrieval_sources",
}


@dataclass(frozen=True)
class AttestationVerification:
    gate_status: str
    promotion_status: str
    eligible: bool
    passing_repeats: int
    required_passing_repeats: int
    candidate_git_sha: str
    candidate_tree_sha256: str
    policy_sha256: str
    dataset_manifest_sha256: str
    signature_key_id: str


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _require_exact_keys(
    value: dict[str, Any], required: set[str], location: str
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ValueError(f"{location} missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{location} has unknown fields: {', '.join(unknown)}")


def _require_sha256(value: Any, location: str) -> str:
    if not isinstance(value, str) or not _HEX_64.fullmatch(value):
        raise ValueError(f"{location} must be a lowercase SHA-256 digest")
    return value


def _require_git_sha(value: Any, location: str) -> str:
    if not isinstance(value, str) or not _GIT_SHA.fullmatch(value):
        raise ValueError(f"{location} must be a lowercase 40-character Git SHA")
    return value


def _assert_public_safe(value: Any, location: str = "$") -> None:
    """Reject private oracle material before doing any other verification."""

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _PUBLIC_FORBIDDEN_KEYS:
                raise ValueError(f"private seed/gold field is forbidden at {location}.{key}")
            _assert_public_safe(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_safe(child, f"{location}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower().replace("\\", "/")
        if "holdout_seed" in lowered or "gold.jsonl" in lowered:
            raise ValueError(f"private seed/gold value is forbidden at {location}")


def signing_payload(attestation: dict[str, Any]) -> bytes:
    """Return the exact canonical bytes the independent evaluator must sign."""

    unsigned = dict(attestation)
    unsigned.pop("signature", None)
    return canonical_json(unsigned).encode("utf-8")


def _read_ssh_field(blob: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(blob):
        raise ValueError("trusted public key is truncated")
    size = int.from_bytes(blob[offset : offset + 4], "big")
    offset += 4
    end = offset + size
    if end > len(blob):
        raise ValueError("trusted public key is truncated")
    return blob[offset:end], end


def _parse_ssh_rsa_public_key(public_key: str) -> tuple[int, int, bytes]:
    parts = public_key.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-rsa":
        raise ValueError("trusted public key must use OpenSSH ssh-rsa format")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("trusted public key has invalid base64") from exc
    key_type, offset = _read_ssh_field(blob, 0)
    exponent_bytes, offset = _read_ssh_field(blob, offset)
    modulus_bytes, offset = _read_ssh_field(blob, offset)
    if key_type != b"ssh-rsa" or offset != len(blob):
        raise ValueError("trusted public key has invalid ssh-rsa encoding")
    exponent = int.from_bytes(exponent_bytes, "big")
    modulus = int.from_bytes(modulus_bytes, "big")
    if (
        exponent < 3
        or exponent % 2 == 0
        or modulus % 2 == 0
        or modulus.bit_length() < 2048
    ):
        raise ValueError("trusted public key must be a valid RSA key of at least 2048 bits")
    return exponent, modulus, blob


def public_key_id(public_key: str) -> str:
    _, _, blob = _parse_ssh_rsa_public_key(public_key)
    return f"sha256:{hashlib.sha256(blob).hexdigest()}"


def _verify_rsa_signature(
    payload: bytes, signature_b64: str, trusted_public_key: str
) -> str:
    exponent, modulus, blob = _parse_ssh_rsa_public_key(trusted_public_key)
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("attestation signature has invalid base64") from exc
    modulus_size = (modulus.bit_length() + 7) // 8
    if len(signature) != modulus_size:
        raise ValueError("attestation signature length does not match trusted key")
    signature_value = int.from_bytes(signature, "big")
    if signature_value >= modulus:
        raise ValueError("attestation signature is outside the RSA modulus")
    encoded = pow(signature_value, exponent, modulus).to_bytes(
        modulus_size, "big"
    )
    digest_info = _RSA_SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(payload).digest()
    padding_size = modulus_size - len(digest_info) - 3
    if padding_size < 8:
        raise ValueError("trusted RSA key is too small for SHA-256")
    expected = b"\x00\x01" + (b"\xff" * padding_size) + b"\x00" + digest_info
    if not hmac.compare_digest(encoded, expected):
        raise ValueError("attestation signature is invalid")
    return f"sha256:{hashlib.sha256(blob).hexdigest()}"


def derive_repeat_decision(
    metrics: dict[str, dict[str, Any]], policy: dict[str, Any]
) -> dict[str, Any]:
    """Apply the existing production comparison gates and kill rules."""

    decision = _production_comparison_for_repeat(metrics, policy["promotion_gates"])
    kill_findings = _kill_findings(metrics, policy["kill_rules"])
    candidate = metrics.get("C", {})
    if int(candidate.get("transition_ledger_violation_count", 0)):
        kill_findings.append("transition_ledger_integrity_violation")
    decision["kill_findings"] = kill_findings
    if kill_findings:
        decision["passed"] = False
        decision["kill_rule_veto"] = True
    return decision


def _derive_overall_decision(
    repeat_decisions: dict[str, dict[str, Any]], *, isolated: bool
) -> dict[str, Any]:
    eligible_repeats = sum(
        bool(decision.get("eligible")) for decision in repeat_decisions.values()
    )
    passing_repeats = sum(
        bool(decision.get("passed")) for decision in repeat_decisions.values()
    )
    kill_findings = sorted(
        {
            str(finding)
            for decision in repeat_decisions.values()
            for finding in decision.get("kill_findings", [])
        }
    )
    if not isolated:
        gate_status = "NOT_ELIGIBLE_GOLD_NOT_ISOLATED"
        promotion_status = "NOT_ELIGIBLE"
    elif passing_repeats >= 2:
        gate_status = "PRIVATE_HOLDOUT_PROMOTION_PASS"
        promotion_status = "PROMOTE"
    elif kill_findings:
        gate_status = "PRIVATE_HOLDOUT_KILL"
        promotion_status = "KILL"
    else:
        gate_status = "PRIVATE_HOLDOUT_GATE_FAIL"
        promotion_status = "HOLD"
    return {
        "gate_status": gate_status,
        "promotion_status": promotion_status,
        "eligible_repeats": eligible_repeats,
        "passing_repeats": passing_repeats,
        "kill_findings": kill_findings,
    }


def _git_head(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip().lower()


def verify_attestation(
    attestation: dict[str, Any],
    *,
    repository_root: str | Path,
    policy_path: str | Path,
    trusted_public_key: str,
    expected_deployed_sha: str,
    expected_dataset_manifest_sha256: str,
    expected_attestor: str | None = None,
) -> AttestationVerification:
    """Fail closed unless a signed public receipt reproduces the policy decision."""

    _assert_public_safe(attestation)
    root = Path(repository_root).resolve()
    policy_file = Path(policy_path).resolve()
    top = _require_object(attestation, "attestation")
    _require_exact_keys(
        top,
        {
            "schema_version",
            "kind",
            "created_at",
            "attestor",
            "candidate",
            "artifacts",
            "boundary",
            "repeats",
            "decision",
            "signature",
        },
        "attestation",
    )
    if top["schema_version"] != ATTESTATION_SCHEMA_VERSION:
        raise ValueError("unsupported attestation schema_version")
    if top["kind"] != ATTESTATION_KIND:
        raise ValueError("attestation kind is invalid")
    try:
        created_at = datetime.fromisoformat(str(top["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("attestation created_at must be an ISO-8601 timestamp") from exc
    if created_at.tzinfo is None:
        raise ValueError("attestation created_at must include a timezone")

    attestor = _require_object(top["attestor"], "attestor")
    _require_exact_keys(
        attestor,
        {"identity", "independent_evaluator", "signature_key_id"},
        "attestor",
    )
    if not isinstance(attestor["identity"], str) or not attestor["identity"].strip():
        raise ValueError("attestor identity must be non-empty")
    if attestor["independent_evaluator"] is not True:
        raise ValueError("attestor must be independent from the candidate team")
    if expected_attestor is not None and attestor["identity"] != expected_attestor:
        raise ValueError("attestor identity does not match the trusted identity")

    signature = _require_object(top["signature"], "signature")
    _require_exact_keys(signature, {"algorithm", "value"}, "signature")
    if signature["algorithm"] != SIGNATURE_ALGORITHM:
        raise ValueError("attestation signature algorithm is invalid")
    if not isinstance(signature["value"], str) or not signature["value"]:
        raise ValueError("attestation signature value must be non-empty")
    verified_key_id = _verify_rsa_signature(
        signing_payload(top), signature["value"], trusted_public_key
    )
    if attestor["signature_key_id"] != verified_key_id:
        raise ValueError("attestor signature_key_id does not match the trusted key")

    candidate = _require_object(top["candidate"], "candidate")
    _require_exact_keys(
        candidate,
        {"evaluated_git_sha", "deployed_git_sha", "tree_sha256"},
        "candidate",
    )
    evaluated_sha = _require_git_sha(
        candidate["evaluated_git_sha"], "candidate.evaluated_git_sha"
    )
    deployed_sha = _require_git_sha(
        candidate["deployed_git_sha"], "candidate.deployed_git_sha"
    )
    expected_deployed = _require_git_sha(
        expected_deployed_sha.lower(), "expected_deployed_sha"
    )
    if deployed_sha != evaluated_sha or deployed_sha != expected_deployed:
        raise ValueError("deployed candidate SHA does not match evaluated candidate SHA")
    current_git_sha = _require_git_sha(_git_head(root), "repository HEAD")
    if current_git_sha != evaluated_sha:
        raise ValueError("repository HEAD does not match evaluated candidate SHA")
    recorded_tree = _require_sha256(candidate["tree_sha256"], "candidate.tree_sha256")
    current_tree = candidate_tree_hash(root)
    if current_tree != recorded_tree:
        raise ValueError("candidate implementation changed after holdout evaluation")

    artifacts = _require_object(top["artifacts"], "artifacts")
    _require_exact_keys(
        artifacts,
        {
            "policy_sha256",
            "dataset_manifest_sha256",
            "runner_inputs_sha256",
            "outputs_sha256",
            "aggregate_metrics_sha256",
        },
        "artifacts",
    )
    for name, value in artifacts.items():
        _require_sha256(value, f"artifacts.{name}")
    actual_policy_sha256 = file_sha256(policy_file)
    if artifacts["policy_sha256"] != actual_policy_sha256:
        raise ValueError("policy hash does not match the public policy")
    expected_dataset_hash = _require_sha256(
        expected_dataset_manifest_sha256.lower(),
        "expected_dataset_manifest_sha256",
    )
    if artifacts["dataset_manifest_sha256"] != expected_dataset_hash:
        raise ValueError("dataset manifest hash does not match the frozen holdout receipt")

    boundary = _require_object(top["boundary"], "boundary")
    _require_exact_keys(
        boundary,
        {
            "split",
            "scenario_count",
            "runner_process_isolated_from_oracle",
            "candidate_process_isolated_from_oracle",
            "evaluator_process_separate_from_runner",
            "oracle_generator",
            "oracle_uses_qwen",
            "pass_fail_judge",
            "judge_uses_qwen",
            "private_material_published",
        },
        "boundary",
    )
    if boundary["split"] != "holdout":
        raise ValueError("attestation must describe a private holdout split")
    scenario_count = boundary["scenario_count"]
    if not isinstance(scenario_count, int) or isinstance(scenario_count, bool):
        raise ValueError("boundary.scenario_count must be an integer")
    policy = load_json(policy_file)
    expected_scenario_count = (
        len(policy["dataset"]["scenario_types"])
        * int(policy["dataset"]["holdout_variants_per_type"])
    )
    if scenario_count != expected_scenario_count:
        raise ValueError(
            "private holdout must contain exactly the frozen scenario matrix"
        )
    if boundary["oracle_generator"] != policy["leakage_controls"]["gold_generator"]:
        raise ValueError("oracle generator does not match policy")
    if boundary["oracle_uses_qwen"] is not False:
        raise ValueError("Qwen must not generate private holdout truth")
    if boundary["pass_fail_judge"] != "deterministic-policy-evaluator-v1":
        raise ValueError("pass/fail judge is not the deterministic evaluator")
    if boundary["judge_uses_qwen"] is not False:
        raise ValueError("Qwen must not act as the pass/fail judge")
    if boundary["private_material_published"] is not False:
        raise ValueError("private seed/gold material must not be published")
    for field in (
        "runner_process_isolated_from_oracle",
        "candidate_process_isolated_from_oracle",
        "evaluator_process_separate_from_runner",
    ):
        if not isinstance(boundary[field], bool):
            raise ValueError(f"boundary.{field} must be a boolean")
    isolated = all(
        boundary[field]
        for field in (
            "runner_process_isolated_from_oracle",
            "candidate_process_isolated_from_oracle",
            "evaluator_process_separate_from_runner",
        )
    )

    repeats = _require_object(top["repeats"], "repeats")
    _require_exact_keys(
        repeats,
        {"required", "minimum_passing", "metrics_by_repeat", "decisions"},
        "repeats",
    )
    gates = policy["promotion_gates"]
    required_repeats = int(gates["required_repeats"])
    minimum_passing = int(gates["minimum_passing_repeats"])
    if repeats["required"] != required_repeats or repeats["minimum_passing"] != minimum_passing:
        raise ValueError("repeat contract does not match promotion policy")
    if required_repeats != 3 or minimum_passing != 2:
        raise ValueError("external attestation requires the frozen 2-of-3 policy")
    metrics_by_repeat = _require_object(
        repeats["metrics_by_repeat"], "repeats.metrics_by_repeat"
    )
    decisions = _require_object(repeats["decisions"], "repeats.decisions")
    expected_repeat_keys = {str(index) for index in range(required_repeats)}
    if set(metrics_by_repeat) != expected_repeat_keys or set(decisions) != expected_repeat_keys:
        raise ValueError("attestation must contain exactly repeat IDs 0, 1, and 2")
    recomputed_decisions: dict[str, dict[str, Any]] = {}
    for repeat in sorted(expected_repeat_keys):
        metrics = _require_object(metrics_by_repeat[repeat], f"metrics repeat {repeat}")
        if set(metrics) != {"B0", "B1", "B2", "C"}:
            raise ValueError(f"metrics repeat {repeat} must contain B0/B1/B2/C")
        for policy_id, aggregate in metrics.items():
            aggregate_object = _require_object(
                aggregate, f"metrics repeat {repeat} policy {policy_id}"
            )
            if not aggregate_object or any(
                isinstance(value, (dict, list)) for value in aggregate_object.values()
            ):
                raise ValueError("attestation metrics must be aggregate scalar values")
        if any(
            aggregate.get("scenario_count") != scenario_count
            for aggregate in metrics.values()
        ):
            raise ValueError("aggregate scenario counts do not match boundary")
        recomputed = derive_repeat_decision(metrics, policy)
        if canonical_json(decisions[repeat]) != canonical_json(recomputed):
            raise ValueError(f"repeat {repeat} decision is inconsistent with aggregate metrics")
        recomputed_decisions[repeat] = recomputed
    if artifacts["aggregate_metrics_sha256"] != stable_hash(metrics_by_repeat):
        raise ValueError("aggregate metrics hash does not match attested metrics")

    expected_decision = _derive_overall_decision(
        recomputed_decisions, isolated=isolated
    )
    supplied_decision = _require_object(top["decision"], "decision")
    _require_exact_keys(
        supplied_decision,
        {
            "gate_status",
            "promotion_status",
            "eligible_repeats",
            "passing_repeats",
            "kill_findings",
        },
        "decision",
    )
    if canonical_json(supplied_decision) != canonical_json(expected_decision):
        if not isolated:
            raise ValueError(
                "non-isolated holdout must be NOT_ELIGIBLE_GOLD_NOT_ISOLATED"
            )
        raise ValueError("overall promotion/kill decision is inconsistent")

    return AttestationVerification(
        gate_status=expected_decision["gate_status"],
        promotion_status=expected_decision["promotion_status"],
        eligible=expected_decision["promotion_status"] == "PROMOTE",
        passing_repeats=expected_decision["passing_repeats"],
        required_passing_repeats=minimum_passing,
        candidate_git_sha=evaluated_sha,
        candidate_tree_sha256=current_tree,
        policy_sha256=actual_policy_sha256,
        dataset_manifest_sha256=expected_dataset_hash,
        signature_key_id=verified_key_id,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    payload = subparsers.add_parser("payload", help="print canonical bytes to sign")
    payload.add_argument("--attestation", type=Path, required=True)
    verify = subparsers.add_parser("verify", help="verify a signed public receipt")
    verify.add_argument("--attestation", type=Path, required=True)
    verify.add_argument("--trusted-public-key", type=Path, required=True)
    verify.add_argument("--deployed-sha", required=True)
    verify.add_argument("--dataset-manifest-sha256", required=True)
    verify.add_argument("--policy", type=Path, default=Path("eval/policy.json"))
    verify.add_argument("--repository-root", type=Path, default=Path("."))
    verify.add_argument("--attestor")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    attestation = load_json(args.attestation)
    if args.command == "payload":
        print(signing_payload(attestation).decode("utf-8"))
        return
    result = verify_attestation(
        attestation,
        repository_root=args.repository_root,
        policy_path=args.policy,
        trusted_public_key=args.trusted_public_key.read_text(encoding="utf-8"),
        expected_deployed_sha=args.deployed_sha,
        expected_dataset_manifest_sha256=args.dataset_manifest_sha256,
        expected_attestor=args.attestor,
    )
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()

"""Verify a public, signed independent private-holdout v2 attestation.

The public repository receives aggregate paired statistics, artifact digests,
role-separation claims, a bounded live-Qwen receipt, and a detached RSA
signature from a pre-trusted evaluator.  Repository-generated holdouts and
deterministic repeat counts can never satisfy this contract.
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
from .private_promotion import (
    derive_private_promotion_decision,
    promotion_binding_sha256,
    validate_private_aggregate,
)


ATTESTATION_KIND = "librarian.private-holdout-promotion-attestation"
ATTESTATION_SCHEMA_VERSION = "2.0"
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
    scenario_count: int
    b2_success_delta: float
    exact_mcnemar_p: float
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


def _git_head(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip().lower()


def _require_boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{location} must be a boolean")
    return value


def _require_non_negative_int(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{location} must be a non-negative integer")
    return value


def _validate_boundary(boundary: Any, policy: dict[str, Any]) -> bool:
    value = _require_object(boundary, "boundary")
    required = {
        "split",
        "scenario_count",
        "analysis_unit",
        "collection_provenance",
        "author_pools_separate",
        "authors_separate_from_candidate_team",
        "gold_double_annotated",
        "third_party_adjudication",
        "final_candidate_outputs_hidden_during_collection",
        "intermediate_results_withheld_until_completion",
        "runner_process_isolated_from_oracle",
        "candidate_process_isolated_from_oracle",
        "evaluator_process_separate_from_runner",
        "oracle_generator",
        "oracle_uses_qwen",
        "pass_fail_judge",
        "judge_uses_qwen",
        "private_material_published",
        "repeat_semantics",
    }
    _require_exact_keys(value, required, "boundary")
    config = policy["private_promotion_v2"]
    independence = config["independence_gate"]
    if value["split"] != "external_private_holdout":
        raise ValueError("attestation must describe an external private holdout")
    if value["scenario_count"] != int(config["scenario_count"]):
        raise ValueError("boundary scenario_count does not match private promotion policy")
    if value["analysis_unit"] != config["analysis_unit"]:
        raise ValueError("boundary analysis_unit must be scenario")
    for field in (
        "author_pools_separate",
        "authors_separate_from_candidate_team",
        "gold_double_annotated",
        "third_party_adjudication",
        "final_candidate_outputs_hidden_during_collection",
        "intermediate_results_withheld_until_completion",
        "runner_process_isolated_from_oracle",
        "candidate_process_isolated_from_oracle",
        "evaluator_process_separate_from_runner",
        "oracle_uses_qwen",
        "judge_uses_qwen",
        "private_material_published",
    ):
        _require_boolean(value[field], f"boundary.{field}")
    if value["oracle_uses_qwen"] is not False:
        raise ValueError("Qwen must not generate private holdout truth")
    if value["judge_uses_qwen"] is not False:
        raise ValueError("Qwen must not act as the pass/fail judge")
    if value["private_material_published"] is not False:
        raise ValueError("private seed/gold material must not be published")

    expected_values = {
        "collection_provenance": independence["collection_provenance"],
        "oracle_generator": independence["oracle_generator"],
        "pass_fail_judge": independence["pass_fail_judge"],
        "repeat_semantics": independence["repeat_semantics"],
    }
    required_true = (
        "author_pools_separate",
        "authors_separate_from_candidate_team",
        "gold_double_annotated",
        "third_party_adjudication",
        "final_candidate_outputs_hidden_during_collection",
        "intermediate_results_withheld_until_completion",
        "runner_process_isolated_from_oracle",
        "candidate_process_isolated_from_oracle",
        "evaluator_process_separate_from_runner",
    )
    return all(value[field] is True for field in required_true) and all(
        value[field] == expected for field, expected in expected_values.items()
    )


def _validate_live_qwen(
    live_qwen: Any, policy: dict[str, Any], artifacts: dict[str, Any]
) -> bool:
    value = _require_object(live_qwen, "live_qwen")
    required = {
        "subset_count",
        "type_counts",
        "pool_counts",
        "required_runs",
        "minimum_passing_runs",
        "passing_runs",
        "run_gate_results",
        "actual_calls",
        "maximum_calls",
        "max_output_tokens",
        "timeout_seconds",
        "retry_limit",
        "model_id",
        "prompt_sha256",
        "shared_model_prompt_conditions",
        "answer_model_is_qwen",
        "oracle_uses_qwen",
        "judge_uses_qwen",
        "cost_authorization",
        "raw_responses_sha256",
        "usage_receipt_sha256",
    }
    _require_exact_keys(value, required, "live_qwen")
    config = policy["private_promotion_v2"]["live_qwen_gate"]
    scenario_types = tuple(map(str, policy["dataset"]["scenario_types"]))
    expected_subset = int(config["subset_count"])
    if value["subset_count"] != expected_subset:
        raise ValueError("live_qwen subset_count does not match policy")

    type_counts = _require_object(value["type_counts"], "live_qwen.type_counts")
    if set(type_counts) != set(scenario_types):
        raise ValueError("live_qwen type_counts does not cover the frozen scenario types")
    for scenario_type in scenario_types:
        if type_counts[scenario_type] != int(config["scenarios_per_type"]):
            raise ValueError("live_qwen type_counts does not match policy")
    if sum(type_counts.values()) != expected_subset:
        raise ValueError("live_qwen type_counts does not sum to subset_count")

    expected_pools = config["pool_counts"]
    pool_counts = _require_object(value["pool_counts"], "live_qwen.pool_counts")
    if set(pool_counts) != set(expected_pools):
        raise ValueError("live_qwen pool_counts does not match policy")
    for pool, count in expected_pools.items():
        if pool_counts[pool] != int(count):
            raise ValueError("live_qwen pool_counts does not match policy")
    if sum(pool_counts.values()) != expected_subset:
        raise ValueError("live_qwen pool_counts does not sum to subset_count")

    required_runs = int(config["required_runs"])
    minimum_passing = int(config["minimum_passing_runs"])
    if value["required_runs"] != required_runs:
        raise ValueError("live_qwen required_runs does not match policy")
    if value["minimum_passing_runs"] != minimum_passing:
        raise ValueError("live_qwen minimum_passing_runs does not match policy")
    results = value["run_gate_results"]
    if (
        not isinstance(results, list)
        or len(results) != required_runs
        or any(not isinstance(result, bool) for result in results)
    ):
        raise ValueError("live_qwen run_gate_results must contain three booleans")
    passing_runs = _require_non_negative_int(
        value["passing_runs"], "live_qwen.passing_runs"
    )
    if passing_runs != sum(results):
        raise ValueError("live_qwen passing_runs is inconsistent with run_gate_results")

    for field in (
        "actual_calls",
        "maximum_calls",
        "max_output_tokens",
        "timeout_seconds",
        "retry_limit",
    ):
        _require_non_negative_int(value[field], f"live_qwen.{field}")
    if value["maximum_calls"] != int(config["maximum_calls"]):
        raise ValueError("live_qwen maximum_calls does not match policy")
    if value["actual_calls"] > value["maximum_calls"]:
        raise ValueError("live_qwen actual_calls exceeds the capped budget")
    if value["max_output_tokens"] > int(config["max_output_tokens"]):
        raise ValueError("live_qwen max_output_tokens exceeds policy")
    if value["max_output_tokens"] == 0:
        raise ValueError("live_qwen max_output_tokens must be positive")
    if value["timeout_seconds"] > int(config["timeout_seconds"]):
        raise ValueError("live_qwen timeout exceeds policy")
    if value["timeout_seconds"] == 0:
        raise ValueError("live_qwen timeout_seconds must be positive")
    if value["retry_limit"] != int(config["retry_limit"]):
        raise ValueError("live_qwen retry_limit does not match policy")
    if not isinstance(value["model_id"], str) or not value["model_id"].strip():
        raise ValueError("live_qwen model_id must be non-empty")
    for field in (
        "prompt_sha256",
        "raw_responses_sha256",
        "usage_receipt_sha256",
    ):
        _require_sha256(value[field], f"live_qwen.{field}")
    if value["raw_responses_sha256"] != artifacts["raw_provider_responses_sha256"]:
        raise ValueError("live_qwen raw response hash does not match artifacts")
    if value["usage_receipt_sha256"] != artifacts["usage_receipt_sha256"]:
        raise ValueError("live_qwen usage receipt hash does not match artifacts")
    for field in (
        "shared_model_prompt_conditions",
        "answer_model_is_qwen",
        "oracle_uses_qwen",
        "judge_uses_qwen",
    ):
        _require_boolean(value[field], f"live_qwen.{field}")
    if value["oracle_uses_qwen"] is not False or value["judge_uses_qwen"] is not False:
        raise ValueError("Qwen must not create truth or judge the private holdout")
    minimum_completed_calls = passing_runs * expected_subset * 2
    if value["actual_calls"] < minimum_completed_calls:
        raise ValueError(
            "live_qwen actual_calls cannot support the claimed passing runs"
        )

    return bool(
        passing_runs >= minimum_passing
        and value["actual_calls"] > 0
        and value["shared_model_prompt_conditions"] is True
        and value["answer_model_is_qwen"] is True
        and value["cost_authorization"] in config["allowed_cost_authorizations"]
    )


def verify_attestation(
    attestation: dict[str, Any],
    *,
    repository_root: str | Path,
    policy_path: str | Path,
    trusted_public_key: str,
    expected_deployed_sha: str,
    expected_dataset_manifest_sha256: str,
    expected_attestor: str,
) -> AttestationVerification:
    """Fail closed unless a v2 signed receipt reproduces every public decision."""

    _assert_public_safe(attestation)
    root = Path(repository_root).resolve()
    policy_file = Path(policy_path).resolve()
    top = _require_object(attestation, "attestation")
    if top.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        if top.get("schema_version") == "1.0":
            raise ValueError(
                "legacy private holdout attestation v1 is disabled and cannot promote"
            )
        raise ValueError("unsupported attestation schema_version")
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
            "aggregate",
            "live_qwen",
            "decision",
            "signature",
        },
        "attestation",
    )
    if top["kind"] != ATTESTATION_KIND:
        raise ValueError("attestation kind is invalid")
    try:
        created_at = datetime.fromisoformat(
            str(top["created_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("attestation created_at must be an ISO-8601 timestamp") from exc
    if created_at.tzinfo is None:
        raise ValueError("attestation created_at must include a timezone")

    attestor = _require_object(top["attestor"], "attestor")
    _require_exact_keys(
        attestor,
        {
            "identity",
            "independent_evaluator",
            "implementation_owner",
            "case_author",
            "signature_key_id",
        },
        "attestor",
    )
    if not isinstance(attestor["identity"], str) or not attestor["identity"].strip():
        raise ValueError("attestor identity must be non-empty")
    if (
        attestor["independent_evaluator"] is not True
        or attestor["implementation_owner"] is not False
        or attestor["case_author"] is not False
    ):
        raise ValueError(
            "attestor must be independent from implementation and case authoring"
        )
    if not isinstance(expected_attestor, str) or not expected_attestor.strip():
        raise ValueError("expected_attestor must identify a pre-trusted evaluator")
    if attestor["identity"] != expected_attestor:
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

    policy = load_json(policy_file)
    if policy.get("schema_version") != "2.0":
        raise ValueError("private promotion requires policy schema_version 2.0")
    config = policy["private_promotion_v2"]

    candidate = _require_object(top["candidate"], "candidate")
    _require_exact_keys(
        candidate,
        {
            "evaluated_git_sha",
            "deployed_git_sha",
            "tree_sha256",
            "b2_implementation_sha256",
            "c_implementation_sha256",
            "answer_contract_sha256",
            "qwen_model_id",
            "qwen_prompt_sha256",
            "top_k",
            "context_budget",
        },
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

    implementation_files = {
        "b2_implementation_sha256": root / "eval" / "baselines.py",
        "c_implementation_sha256": root / "src" / "librarian" / "eval_adapter.py",
        "answer_contract_sha256": root / "eval" / "contracts.py",
    }
    for field, path in implementation_files.items():
        recorded_hash = _require_sha256(candidate[field], f"candidate.{field}")
        if not path.is_file() or file_sha256(path) != recorded_hash:
            raise ValueError(f"candidate.{field} does not match current source")
    if not isinstance(candidate["qwen_model_id"], str) or not candidate[
        "qwen_model_id"
    ].strip():
        raise ValueError("candidate.qwen_model_id must be non-empty")
    _require_sha256(candidate["qwen_prompt_sha256"], "candidate.qwen_prompt_sha256")
    shared = policy["primary_lane"]["shared_conditions"]
    if (
        candidate["top_k"] != int(shared["top_k"])
        or candidate["context_budget"] != int(shared["context_budget"])
    ):
        raise ValueError("candidate retrieval conditions do not match policy")

    artifacts = _require_object(top["artifacts"], "artifacts")
    _require_exact_keys(
        artifacts,
        {
            "policy_sha256",
            "protocol_sha256",
            "annotation_guide_sha256",
            "role_separation_manifest_sha256",
            "dataset_manifest_sha256",
            "runner_inputs_sha256",
            "paired_results_sha256",
            "aggregate_metrics_sha256",
            "raw_provider_responses_sha256",
            "usage_receipt_sha256",
            "bootstrap_samples_sha256",
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

    aggregate = _require_object(top["aggregate"], "aggregate")
    validate_private_aggregate(aggregate, policy)
    if artifacts["aggregate_metrics_sha256"] != stable_hash(aggregate):
        raise ValueError("aggregate metrics hash does not match attested metrics")
    if (
        artifacts["bootstrap_samples_sha256"]
        != aggregate["statistics"]["bootstrap"]["samples_sha256"]
    ):
        raise ValueError("bootstrap samples hash does not match aggregate")
    expected_bootstrap_binding = promotion_binding_sha256(
        dataset_manifest_sha256=artifacts["dataset_manifest_sha256"],
        paired_results_sha256=artifacts["paired_results_sha256"],
        candidate_tree_sha256=candidate["tree_sha256"],
        policy_sha256=artifacts["policy_sha256"],
    )
    if (
        aggregate["statistics"]["bootstrap"]["binding_sha256"]
        != expected_bootstrap_binding
    ):
        raise ValueError("bootstrap binding does not match frozen artifacts")

    independence_eligible = _validate_boundary(top["boundary"], policy)
    live_qwen_eligible = _validate_live_qwen(top["live_qwen"], policy, artifacts)
    live_qwen = top["live_qwen"]
    if candidate["qwen_model_id"] != live_qwen["model_id"]:
        raise ValueError("candidate and live_qwen model IDs do not match")
    if candidate["qwen_prompt_sha256"] != live_qwen["prompt_sha256"]:
        raise ValueError("candidate and live_qwen prompt hashes do not match")
    if candidate["qwen_prompt_sha256"] != file_sha256(
        root / "src" / "librarian" / "prompts.py"
    ):
        raise ValueError("candidate Qwen prompt hash does not match current source")

    expected_decision = derive_private_promotion_decision(
        aggregate,
        policy,
        independence_eligible=independence_eligible,
        live_qwen_eligible=live_qwen_eligible,
    )
    supplied_decision = _require_object(top["decision"], "decision")
    _require_exact_keys(
        supplied_decision,
        {
            "gate_status",
            "promotion_status",
            "checks",
            "hold_findings",
            "kill_findings",
        },
        "decision",
    )
    if canonical_json(supplied_decision) != canonical_json(expected_decision):
        raise ValueError("overall promotion/kill decision is inconsistent")

    return AttestationVerification(
        gate_status=expected_decision["gate_status"],
        promotion_status=expected_decision["promotion_status"],
        eligible=expected_decision["promotion_status"] == "PROMOTE",
        scenario_count=int(aggregate["scenario_count"]),
        b2_success_delta=float(aggregate["statistics"]["delta"]),
        exact_mcnemar_p=float(aggregate["statistics"]["exact_mcnemar_p"]),
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
    verify.add_argument("--attestor", required=True)
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

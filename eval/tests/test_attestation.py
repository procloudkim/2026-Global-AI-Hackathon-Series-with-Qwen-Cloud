from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.attestation import (
    _RSA_SHA256_DIGEST_INFO_PREFIX,
    derive_repeat_decision,
    public_key_id,
    signing_payload,
    verify_attestation,
)
from eval.contracts import file_sha256, load_json, stable_hash


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "eval" / "policy.json"
EVALUATED_SHA = "a" * 40
CANDIDATE_TREE_SHA256 = "b" * 64
DATASET_MANIFEST_SHA256 = "c" * 64

# Public test fixture only.  This private exponent has no use outside these tests.
_RSA_N = int(
    "8d90b6987e04b6730bd1d4876875d41ed9d3c2f173d82413bcaae02ef701280a"
    "a3733d566a8c7ece123d5a5193c7121630a5da2bda7310b3bf546e3edc661856"
    "76fe45c65243eeac1a9ecd804b7296b44a4a0651574f06a7226c34ec785e18d1"
    "7da464c151f83eb3002eda7cc6f8cd0b52e8f3f60c0558694314d2e3e064f171"
    "4a1bb41d8906d1b531daabf00eb0615bed6a8fb98a1de28088e8ca7aed3126b9"
    "ed72acf8fe58faeeefe85808363ed6856073612743099cc0981eb17d07d1ef1c3"
    "5ecd7bc2100d1760777e9612976a8bfac3503bc0a03ea9fa3e18337520d3be9b"
    "d0d6ea510eb06021a08b473b8401f90d92e55bfe37e64b93dafe1f1246736a3",
    16,
)
_RSA_D = int(
    "41c99691973ea96b1d7b0dc13f92425973f12d0ef83ee1c52a505e8e74cb5cc8"
    "54dd210c1fc7dac5f943f9cd1f57cbd7f4139f27f204b726512bf36fd30cbf12"
    "af89caf33717cabac0443c4bfac3edb52b30eb6eb19c50032bc40d74371bc37c"
    "d75ea65410bbc4a77ad64b42e1548e354cfa65255ac0c12d69b72baa2a85be8d"
    "ae5e58ff335c77d762f96e8d7fa6a04a9ff6d51234764713da01ac37e416d845"
    "d6e9d52854de1364e82ee56293ecc5b9e08e0bc58f0c7355354645f0be98b950"
    "fede0c962e6839fbef6b3bc07d070e71e7ed66cb9e63bedb8188b7e703285a3c"
    "feada3faa9d5b32708bcec01c715707357f2b7766a7258ba9c532009514dcd41",
    16,
)
_RSA_E = 65537


def _ssh_field(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _mpint(value: int) -> bytes:
    encoded = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if encoded[0] & 0x80:
        encoded = b"\x00" + encoded
    return _ssh_field(encoded)


def _public_key() -> str:
    blob = _ssh_field(b"ssh-rsa") + _mpint(_RSA_E) + _mpint(_RSA_N)
    return f"ssh-rsa {base64.b64encode(blob).decode('ascii')} test-only"


def _sign(attestation: dict) -> None:
    payload = signing_payload(attestation)
    modulus_size = (_RSA_N.bit_length() + 7) // 8
    digest_info = _RSA_SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(payload).digest()
    padding = b"\xff" * (modulus_size - len(digest_info) - 3)
    encoded = b"\x00\x01" + padding + b"\x00" + digest_info
    signature = pow(int.from_bytes(encoded, "big"), _RSA_D, _RSA_N).to_bytes(
        modulus_size, "big"
    )
    attestation["signature"]["value"] = base64.b64encode(signature).decode("ascii")


def _metrics() -> dict:
    common = {
        "scenario_count": 24,
        "scope_false_forget_count": 0,
        "state_violation_count": 0,
        "abstention_accuracy": 1.0,
        "citation_entailment": 1.0,
        "retrieval_recall_at_k": 1.0,
        "state_transition_accuracy": 1.0,
        "stale_leakage_rate": 0.0,
        "false_forget_count": 0,
        "transition_ledger_integrity": 1.0,
        "transition_ledger_violation_count": 0,
        "wire_citation_receipt_coverage": 1.0,
        "wire_citation_fidelity": 1.0,
    }
    return {
        "B0": {
            **common,
            "scenario_success_count": 18,
            "scenario_success_rate": 0.75,
            "tokens_per_correct_current_answer": 200.0,
        },
        "B1": {
            **common,
            "scenario_success_count": 19,
            "scenario_success_rate": 19 / 24,
            "tokens_per_correct_current_answer": 150.0,
        },
        "B2": {
            **common,
            "scenario_success_count": 18,
            "scenario_success_rate": 0.75,
            "tokens_per_correct_current_answer": 125.0,
        },
        "C": {
            **common,
            "scenario_success_count": 24,
            "scenario_success_rate": 1.0,
            "tokens_per_correct_current_answer": 100.0,
        },
    }


def _attestation(*, isolated: bool = True) -> dict:
    policy = load_json(POLICY_PATH)
    metrics_by_repeat = {str(repeat): _metrics() for repeat in range(3)}
    decisions = {
        repeat: derive_repeat_decision(metrics, policy)
        for repeat, metrics in metrics_by_repeat.items()
    }
    decision = {
        "gate_status": (
            "PRIVATE_HOLDOUT_PROMOTION_PASS"
            if isolated
            else "NOT_ELIGIBLE_GOLD_NOT_ISOLATED"
        ),
        "promotion_status": "PROMOTE" if isolated else "NOT_ELIGIBLE",
        "eligible_repeats": 3,
        "passing_repeats": 3,
        "kill_findings": [],
    }
    attestation = {
        "schema_version": "1.0",
        "kind": "librarian.private-holdout-promotion-attestation",
        "created_at": "2026-07-14T12:00:00+09:00",
        "attestor": {
            "identity": "independent-evaluator@example.test",
            "independent_evaluator": True,
            "signature_key_id": public_key_id(_public_key()),
        },
        "candidate": {
            "evaluated_git_sha": EVALUATED_SHA,
            "deployed_git_sha": EVALUATED_SHA,
            "tree_sha256": CANDIDATE_TREE_SHA256,
        },
        "artifacts": {
            "policy_sha256": file_sha256(POLICY_PATH),
            "dataset_manifest_sha256": DATASET_MANIFEST_SHA256,
            "runner_inputs_sha256": "d" * 64,
            "outputs_sha256": "e" * 64,
            "aggregate_metrics_sha256": stable_hash(metrics_by_repeat),
        },
        "boundary": {
            "split": "holdout",
            "scenario_count": 24,
            "runner_process_isolated_from_oracle": isolated,
            "candidate_process_isolated_from_oracle": isolated,
            "evaluator_process_separate_from_runner": isolated,
            "oracle_generator": "deterministic-policy-oracle-v1",
            "oracle_uses_qwen": False,
            "pass_fail_judge": "deterministic-policy-evaluator-v1",
            "judge_uses_qwen": False,
            "private_material_published": False,
        },
        "repeats": {
            "required": 3,
            "minimum_passing": 2,
            "metrics_by_repeat": metrics_by_repeat,
            "decisions": decisions,
        },
        "decision": decision,
        "signature": {
            "algorithm": "rsa-pkcs1v15-sha256",
            "value": "pending",
        },
    }
    _sign(attestation)
    return attestation


def _verify(attestation: dict):
    with (
        patch("eval.attestation._git_head", return_value=EVALUATED_SHA),
        patch(
            "eval.attestation.candidate_tree_hash",
            return_value=CANDIDATE_TREE_SHA256,
        ),
    ):
        return verify_attestation(
            attestation,
            repository_root=ROOT,
            policy_path=POLICY_PATH,
            trusted_public_key=_public_key(),
            expected_deployed_sha=EVALUATED_SHA,
            expected_dataset_manifest_sha256=DATASET_MANIFEST_SHA256,
            expected_attestor="independent-evaluator@example.test",
        )


def test_signed_aggregate_attestation_recomputes_two_of_three_promotion() -> None:
    attestation = _attestation()
    attestation["repeats"]["metrics_by_repeat"]["2"]["C"][
        "citation_entailment"
    ] = 0.9
    policy = load_json(POLICY_PATH)
    attestation["repeats"]["decisions"]["2"] = derive_repeat_decision(
        attestation["repeats"]["metrics_by_repeat"]["2"], policy
    )
    attestation["artifacts"]["aggregate_metrics_sha256"] = stable_hash(
        attestation["repeats"]["metrics_by_repeat"]
    )
    attestation["decision"]["passing_repeats"] = 2
    _sign(attestation)

    result = _verify(attestation)

    assert result.eligible is True
    assert result.gate_status == "PRIVATE_HOLDOUT_PROMOTION_PASS"
    assert result.passing_repeats == 2
    assert result.required_passing_repeats == 2


def test_holdout_requires_exactly_eight_types_times_three_variants() -> None:
    attestation = _attestation()
    attestation["boundary"]["scenario_count"] = 25
    _sign(attestation)

    with pytest.raises(ValueError, match="exactly the frozen scenario matrix"):
        _verify(attestation)


def test_kill_rules_veto_promotion_and_produce_kill_decision() -> None:
    attestation = _attestation()
    policy = load_json(POLICY_PATH)
    for repeat in ("0", "1", "2"):
        metrics = attestation["repeats"]["metrics_by_repeat"][repeat]
        metrics["C"]["scenario_success_count"] = 18
        metrics["C"]["scenario_success_rate"] = 0.75
        attestation["repeats"]["decisions"][repeat] = derive_repeat_decision(
            metrics, policy
        )
    findings = sorted(
        {
            finding
            for decision in attestation["repeats"]["decisions"].values()
            for finding in decision["kill_findings"]
        }
    )
    attestation["artifacts"]["aggregate_metrics_sha256"] = stable_hash(
        attestation["repeats"]["metrics_by_repeat"]
    )
    attestation["decision"] = {
        "gate_status": "PRIVATE_HOLDOUT_KILL",
        "promotion_status": "KILL",
        "eligible_repeats": 3,
        "passing_repeats": 0,
        "kill_findings": findings,
    }
    _sign(attestation)

    result = _verify(attestation)

    assert result.eligible is False
    assert result.promotion_status == "KILL"
    assert result.gate_status == "PRIVATE_HOLDOUT_KILL"


@pytest.mark.parametrize(
    ("field", "value"),
    (("HOLDOUT_SEED", "never-publish-this"), ("gold", [{"answer": 1000}])),
)
def test_private_seed_or_gold_fields_are_rejected(field: str, value: object) -> None:
    attestation = _attestation()
    attestation["artifacts"][field] = value

    with pytest.raises(ValueError, match="private seed/gold"):
        _verify(attestation)


def test_candidate_implementation_hash_mismatch_invalidates_attestation() -> None:
    attestation = _attestation()
    attestation["candidate"]["tree_sha256"] = "f" * 64
    _sign(attestation)

    with pytest.raises(ValueError, match="implementation changed"):
        _verify(attestation)


def test_dataset_hash_mismatch_is_rejected() -> None:
    attestation = _attestation()
    attestation["artifacts"]["dataset_manifest_sha256"] = "f" * 64
    _sign(attestation)

    with pytest.raises(ValueError, match="dataset manifest hash"):
        _verify(attestation)


def test_policy_hash_mismatch_is_rejected() -> None:
    attestation = _attestation()
    attestation["artifacts"]["policy_sha256"] = "f" * 64
    _sign(attestation)

    with pytest.raises(ValueError, match="policy hash"):
        _verify(attestation)


def test_repeat_decision_inconsistent_with_aggregate_metrics_is_rejected() -> None:
    attestation = _attestation()
    attestation["repeats"]["decisions"]["1"]["passed"] = False
    _sign(attestation)

    with pytest.raises(ValueError, match="repeat 1 decision is inconsistent"):
        _verify(attestation)


def test_non_isolated_gold_is_explicitly_not_eligible() -> None:
    result = _verify(_attestation(isolated=False))

    assert result.eligible is False
    assert result.gate_status == "NOT_ELIGIBLE_GOLD_NOT_ISOLATED"
    assert result.promotion_status == "NOT_ELIGIBLE"


def test_non_isolated_gold_cannot_claim_promotion() -> None:
    attestation = _attestation(isolated=False)
    attestation["decision"]["gate_status"] = "PRIVATE_HOLDOUT_PROMOTION_PASS"
    attestation["decision"]["promotion_status"] = "PROMOTE"
    _sign(attestation)

    with pytest.raises(ValueError, match="NOT_ELIGIBLE_GOLD_NOT_ISOLATED"):
        _verify(attestation)


def test_deployed_sha_must_equal_evaluated_sha() -> None:
    attestation = _attestation()
    attestation["candidate"]["deployed_git_sha"] = "f" * 40
    _sign(attestation)

    with pytest.raises(ValueError, match="deployed candidate SHA"):
        _verify(attestation)


@pytest.mark.parametrize("field", ("oracle_uses_qwen", "judge_uses_qwen"))
def test_qwen_cannot_generate_truth_or_judge_pass_fail(field: str) -> None:
    attestation = _attestation()
    attestation["boundary"][field] = True
    _sign(attestation)

    with pytest.raises(ValueError, match="Qwen must not"):
        _verify(attestation)

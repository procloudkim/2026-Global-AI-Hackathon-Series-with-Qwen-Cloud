from __future__ import annotations

import base64
from copy import deepcopy
from functools import lru_cache
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.attestation import (
    _RSA_SHA256_DIGEST_INFO_PREFIX,
    public_key_id,
    signing_payload,
    verify_attestation,
)
from eval.contracts import file_sha256, load_json, stable_hash
from eval.private_promotion import (
    derive_private_promotion_decision,
    promotion_binding_sha256,
    score_private_holdout,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "eval" / "policy.json"
EVALUATED_SHA = "a" * 40
CANDIDATE_TREE_SHA256 = "b" * 64
DATASET_MANIFEST_SHA256 = "c" * 64

# Public test fixture only. This private exponent has no use outside these tests.
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


def _outcome(*, success: bool = True) -> dict:
    return {
        "answer_correct": success,
        "citation_correct": True,
        "stale_absent_answer": True,
        "stale_absent_context": True,
        "unrelated_preserved": True,
        "valid_false_forget_count": 0,
        "scope_false_forget_count": 0,
        "citation_entailed_count": 1,
        "citation_count": 1,
        "retrieval_hit_count": 1,
        "retrieval_required_count": 1,
        "state_correct_count": 1,
        "state_total_count": 1,
        "state_violation_count": 0,
        "abstention_correct_count": 1,
        "abstention_total_count": 1,
        "ledger_valid_count": 1,
        "ledger_total_count": 1,
        "ledger_violation_count": 0,
        "wire_citation_correct_count": 1,
        "wire_citation_total_count": 1,
    }


@lru_cache(maxsize=1)
def _aggregate_fixture() -> dict:
    policy = load_json(POLICY_PATH)
    rows: list[dict] = []
    for scenario_type in policy["dataset"]["scenario_types"]:
        for pool in policy["private_promotion_v2"]["provenance_pools"]:
            for variant in range(24):
                rows.append(
                    {
                        "schema_version": "2.0",
                        "scenario_id": f"{scenario_type}-{pool}-{variant:02d}",
                        "scenario_type": scenario_type,
                        "provenance_pool": pool,
                        "B2": _outcome(success=variant >= 6),
                        "C": _outcome(success=True),
                    }
                )
    return score_private_holdout(
        rows,
        policy,
        bootstrap_binding_sha256=promotion_binding_sha256(
            dataset_manifest_sha256=DATASET_MANIFEST_SHA256,
            paired_results_sha256="4" * 64,
            candidate_tree_sha256=CANDIDATE_TREE_SHA256,
            policy_sha256=file_sha256(POLICY_PATH),
        ),
    )


def _boundary() -> dict:
    return {
        "split": "external_private_holdout",
        "scenario_count": 384,
        "analysis_unit": "scenario",
        "collection_provenance": "external_independent_v1",
        "author_pools_separate": True,
        "authors_separate_from_candidate_team": True,
        "gold_double_annotated": True,
        "third_party_adjudication": True,
        "final_candidate_outputs_hidden_during_collection": True,
        "intermediate_results_withheld_until_completion": True,
        "runner_process_isolated_from_oracle": True,
        "candidate_process_isolated_from_oracle": True,
        "evaluator_process_separate_from_runner": True,
        "oracle_generator": "independent-human-adjudication-v1",
        "oracle_uses_qwen": False,
        "pass_fail_judge": "deterministic-private-promotion-scorer-v2",
        "judge_uses_qwen": False,
        "private_material_published": False,
        "repeat_semantics": "reproducibility_only_not_independent_samples",
    }


def _live_qwen(*, run_gate_results: list[bool] | None = None) -> dict:
    results = run_gate_results or [True, True, True]
    policy = load_json(POLICY_PATH)
    return {
        "subset_count": 24,
        "type_counts": {
            scenario_type: 3 for scenario_type in policy["dataset"]["scenario_types"]
        },
        "pool_counts": {"N": 12, "A": 12},
        "required_runs": 3,
        "minimum_passing_runs": 2,
        "passing_runs": sum(results),
        "run_gate_results": results,
        "actual_calls": 144,
        "maximum_calls": 144,
        "max_output_tokens": 256,
        "timeout_seconds": 30,
        "retry_limit": 0,
        "model_id": "qwen-flash",
        "prompt_sha256": file_sha256(ROOT / "src" / "librarian" / "prompts.py"),
        "shared_model_prompt_conditions": True,
        "answer_model_is_qwen": True,
        "oracle_uses_qwen": False,
        "judge_uses_qwen": False,
        "cost_authorization": "FREE_QUOTA_VERIFIED",
        "raw_responses_sha256": "8" * 64,
        "usage_receipt_sha256": "6" * 64,
    }


def _attestation() -> dict:
    policy = load_json(POLICY_PATH)
    aggregate = deepcopy(_aggregate_fixture())
    boundary = _boundary()
    live_qwen = _live_qwen()
    decision = derive_private_promotion_decision(
        aggregate,
        policy,
        independence_eligible=True,
        live_qwen_eligible=True,
    )
    attestation = {
        "schema_version": "2.0",
        "kind": "librarian.private-holdout-promotion-attestation",
        "created_at": "2026-07-14T12:00:00+09:00",
        "attestor": {
            "identity": "independent-evaluator@example.test",
            "independent_evaluator": True,
            "implementation_owner": False,
            "case_author": False,
            "signature_key_id": public_key_id(_public_key()),
        },
        "candidate": {
            "evaluated_git_sha": EVALUATED_SHA,
            "deployed_git_sha": EVALUATED_SHA,
            "tree_sha256": CANDIDATE_TREE_SHA256,
            "b2_implementation_sha256": file_sha256(ROOT / "eval" / "baselines.py"),
            "c_implementation_sha256": file_sha256(
                ROOT / "src" / "librarian" / "eval_adapter.py"
            ),
            "answer_contract_sha256": file_sha256(ROOT / "eval" / "contracts.py"),
            "qwen_model_id": "qwen-flash",
            "qwen_prompt_sha256": file_sha256(
                ROOT / "src" / "librarian" / "prompts.py"
            ),
            "top_k": 3,
            "context_budget": 4000,
        },
        "artifacts": {
            "policy_sha256": file_sha256(POLICY_PATH),
            "protocol_sha256": "1" * 64,
            "annotation_guide_sha256": "2" * 64,
            "role_separation_manifest_sha256": "5" * 64,
            "dataset_manifest_sha256": DATASET_MANIFEST_SHA256,
            "runner_inputs_sha256": "3" * 64,
            "paired_results_sha256": "4" * 64,
            "aggregate_metrics_sha256": stable_hash(aggregate),
            "raw_provider_responses_sha256": "8" * 64,
            "usage_receipt_sha256": "6" * 64,
            "bootstrap_samples_sha256": aggregate["statistics"]["bootstrap"][
                "samples_sha256"
            ],
        },
        "boundary": boundary,
        "aggregate": aggregate,
        "live_qwen": live_qwen,
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


def test_signed_v2_attestation_promotes_only_independent_384_case_evidence() -> None:
    result = _verify(_attestation())

    assert result.eligible is True
    assert result.gate_status == "PRIVATE_HOLDOUT_V2_PROMOTION_PASS"
    assert result.scenario_count == 384
    assert result.b2_success_delta == 0.25
    assert result.exact_mcnemar_p < 0.05


def test_attestor_identity_must_match_pretrusted_identity() -> None:
    attestation = _attestation()
    with (
        patch("eval.attestation._git_head", return_value=EVALUATED_SHA),
        patch(
            "eval.attestation.candidate_tree_hash",
            return_value=CANDIDATE_TREE_SHA256,
        ),
        pytest.raises(ValueError, match="trusted identity"),
    ):
        verify_attestation(
            attestation,
            repository_root=ROOT,
            policy_path=POLICY_PATH,
            trusted_public_key=_public_key(),
            expected_deployed_sha=EVALUATED_SHA,
            expected_dataset_manifest_sha256=DATASET_MANIFEST_SHA256,
            expected_attestor="different-evaluator@example.test",
        )


def test_public_schema_exposes_v2_without_repeat_promotion() -> None:
    schema = load_json(ROOT / "eval" / "attestation.schema.json")
    assert schema["properties"]["schema_version"]["const"] == "2.0"
    assert schema["properties"]["boundary"]["properties"]["scenario_count"][
        "const"
    ] == 384
    assert "repeats" not in schema["required"]
    assert "repeats" not in schema["properties"]


def test_legacy_v1_attestation_is_disabled() -> None:
    with pytest.raises(ValueError, match="legacy private holdout attestation v1"):
        _verify({"schema_version": "1.0"})


def test_deterministic_repeat_claim_cannot_enter_v2_receipt() -> None:
    attestation = _attestation()
    attestation["repeats"] = {"required": 3, "minimum_passing": 2}
    _sign(attestation)
    with pytest.raises(ValueError, match="unknown fields: repeats"):
        _verify(attestation)


def test_same_builder_collection_is_explicitly_not_eligible() -> None:
    attestation = _attestation()
    attestation["boundary"][
        "collection_provenance"
    ] = "repository_scenario_builders_v1"
    policy = load_json(POLICY_PATH)
    attestation["decision"] = derive_private_promotion_decision(
        attestation["aggregate"],
        policy,
        independence_eligible=False,
        live_qwen_eligible=True,
    )
    _sign(attestation)

    result = _verify(attestation)

    assert result.eligible is False
    assert result.promotion_status == "NOT_ELIGIBLE"
    assert result.gate_status == "NOT_ELIGIBLE_EXTERNAL_INDEPENDENCE_UNPROVEN"


def test_live_qwen_one_of_three_remains_hold() -> None:
    attestation = _attestation()
    attestation["live_qwen"] = _live_qwen(
        run_gate_results=[True, False, False]
    )
    policy = load_json(POLICY_PATH)
    attestation["decision"] = derive_private_promotion_decision(
        attestation["aggregate"],
        policy,
        independence_eligible=True,
        live_qwen_eligible=False,
    )
    _sign(attestation)

    result = _verify(attestation)

    assert result.eligible is False
    assert result.promotion_status == "HOLD"
    assert "live_qwen_gate" in result.gate_status or result.gate_status.endswith("HOLD")


@pytest.mark.parametrize(
    ("field", "value"),
    (("HOLDOUT_SEED", "never-publish-this"), ("gold", [{"answer": 1000}])),
)
def test_private_seed_or_gold_fields_are_rejected(field: str, value: object) -> None:
    attestation = _attestation()
    attestation["artifacts"][field] = value
    with pytest.raises(ValueError, match="private seed/gold"):
        _verify(attestation)


def test_aggregate_mcnemar_value_is_recomputed() -> None:
    attestation = _attestation()
    attestation["aggregate"]["statistics"]["exact_mcnemar_p"] = 0.049
    attestation["artifacts"]["aggregate_metrics_sha256"] = stable_hash(
        attestation["aggregate"]
    )
    _sign(attestation)
    with pytest.raises(ValueError, match="McNemar p-value is inconsistent"):
        _verify(attestation)


def test_bootstrap_binding_must_match_frozen_artifacts() -> None:
    attestation = _attestation()
    attestation["aggregate"]["statistics"]["bootstrap"]["binding_sha256"] = "f" * 64
    attestation["artifacts"]["aggregate_metrics_sha256"] = stable_hash(
        attestation["aggregate"]
    )
    _sign(attestation)
    with pytest.raises(ValueError, match="bootstrap binding"):
        _verify(attestation)


def test_candidate_implementation_hash_mismatch_invalidates_attestation() -> None:
    attestation = _attestation()
    attestation["candidate"]["c_implementation_sha256"] = "f" * 64
    _sign(attestation)
    with pytest.raises(ValueError, match="does not match current source"):
        _verify(attestation)


def test_qwen_prompt_hash_must_match_current_source() -> None:
    attestation = _attestation()
    attestation["candidate"]["qwen_prompt_sha256"] = "f" * 64
    attestation["live_qwen"]["prompt_sha256"] = "f" * 64
    _sign(attestation)
    with pytest.raises(ValueError, match="prompt hash does not match current source"):
        _verify(attestation)


def test_dataset_hash_mismatch_is_rejected() -> None:
    attestation = _attestation()
    attestation["artifacts"]["dataset_manifest_sha256"] = "f" * 64
    _sign(attestation)
    with pytest.raises(ValueError, match="dataset manifest hash"):
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
    attestation["live_qwen"][field] = True
    _sign(attestation)
    with pytest.raises(ValueError, match="Qwen must not"):
        _verify(attestation)


def test_live_qwen_call_count_must_support_claimed_passing_runs() -> None:
    attestation = _attestation()
    attestation["live_qwen"]["actual_calls"] = 95
    _sign(attestation)
    with pytest.raises(ValueError, match="cannot support the claimed passing runs"):
        _verify(attestation)


def test_signature_tampering_is_rejected() -> None:
    attestation = _attestation()
    attestation["signature"]["value"] = (
        "A" + attestation["signature"]["value"][1:]
    )
    with pytest.raises(ValueError, match="signature is invalid"):
        _verify(attestation)

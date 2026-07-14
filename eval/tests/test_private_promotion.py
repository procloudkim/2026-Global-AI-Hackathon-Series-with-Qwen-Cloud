from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from eval.private_promotion import (
    derive_private_promotion_decision,
    exact_mcnemar_p,
    promotion_binding_sha256,
    scenario_success,
    score_private_holdout,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "eval" / "policy.json"


def _policy(*, bootstrap_resamples: int = 300) -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["private_promotion_v2"]["primary_gate"][
        "bootstrap_resamples"
    ] = bootstrap_resamples
    return policy


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


def _rows(policy: dict) -> list[dict]:
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
    return rows


def test_exact_mcnemar_uses_only_discordant_pairs() -> None:
    assert exact_mcnemar_p(8, 0) == pytest.approx(0.0078125)
    assert exact_mcnemar_p(0, 8) == pytest.approx(0.0078125)
    assert exact_mcnemar_p(4, 4) == 1.0


def test_repository_generated_gate_is_named_diagnostic_not_promotion() -> None:
    policy = _policy()
    assert "promotion_gates" not in policy
    assert (
        policy["repository_diagnostic_gates"]["gate_role"]
        == "diagnostic_only_no_promotion"
    )


def test_bootstrap_binding_covers_data_candidate_and_policy() -> None:
    first = promotion_binding_sha256(
        dataset_manifest_sha256="a" * 64,
        paired_results_sha256="b" * 64,
        candidate_tree_sha256="c" * 64,
        policy_sha256="d" * 64,
    )
    second = promotion_binding_sha256(
        dataset_manifest_sha256="a" * 64,
        paired_results_sha256="b" * 64,
        candidate_tree_sha256="e" * 64,
        policy_sha256="d" * 64,
    )
    assert first != second


def test_strict_scenario_success_is_derived_not_supplied() -> None:
    outcome = _outcome()
    assert scenario_success(outcome) is True
    outcome["stale_absent_context"] = False
    assert scenario_success(outcome) is False


def test_external_384_case_matrix_produces_paired_promotion_evidence() -> None:
    policy = _policy()
    aggregate = score_private_holdout(
        _rows(policy),
        policy,
        bootstrap_binding_sha256="a" * 64,
    )

    assert aggregate["scenario_count"] == 384
    assert aggregate["pair_table"] == {
        "both_success": 288,
        "candidate_only": 96,
        "baseline_only": 0,
        "both_fail": 0,
    }
    assert aggregate["statistics"]["delta"] == 0.25
    assert aggregate["statistics"]["exact_mcnemar_p"] < 0.05
    assert aggregate["statistics"]["bootstrap"]["lower"] > 0.0
    assert len(aggregate["cells"]) == 16
    decision = derive_private_promotion_decision(
        aggregate,
        policy,
        independence_eligible=True,
        live_qwen_eligible=True,
    )
    assert decision["promotion_status"] == "PROMOTE"


def test_missing_cell_member_is_rejected_before_scoring() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="exactly 384 paired scenarios"):
        score_private_holdout(
            _rows(policy)[:-1],
            policy,
            bootstrap_binding_sha256="b" * 64,
        )


def test_duplicate_scenario_id_is_rejected() -> None:
    policy = _policy()
    rows = _rows(policy)
    rows[1]["scenario_id"] = rows[0]["scenario_id"]
    with pytest.raises(ValueError, match="scenario_id values must be unique"):
        score_private_holdout(
            rows,
            policy,
            bootstrap_binding_sha256="c" * 64,
        )


def test_scorer_rejects_self_supplied_success_label() -> None:
    policy = _policy()
    rows = _rows(policy)
    rows[0]["C"]["scenario_success"] = True
    with pytest.raises(ValueError, match="unknown fields: scenario_success"):
        score_private_holdout(
            rows,
            policy,
            bootstrap_binding_sha256="d" * 64,
        )


def test_independence_and_live_qwen_are_non_substitutable_and_gates() -> None:
    policy = _policy()
    aggregate = score_private_holdout(
        _rows(policy),
        policy,
        bootstrap_binding_sha256="e" * 64,
    )
    no_independence = derive_private_promotion_decision(
        aggregate,
        policy,
        independence_eligible=False,
        live_qwen_eligible=True,
    )
    no_live = derive_private_promotion_decision(
        aggregate,
        policy,
        independence_eligible=True,
        live_qwen_eligible=False,
    )
    assert no_independence["promotion_status"] == "NOT_ELIGIBLE"
    assert no_live["promotion_status"] == "HOLD"


def test_any_valid_claim_false_forgetting_kills_candidate() -> None:
    policy = _policy()
    rows = _rows(policy)
    rows[0]["C"]["valid_false_forget_count"] = 1
    aggregate = score_private_holdout(
        rows,
        policy,
        bootstrap_binding_sha256="f" * 64,
    )
    decision = derive_private_promotion_decision(
        aggregate,
        policy,
        independence_eligible=True,
        live_qwen_eligible=True,
    )
    assert decision["promotion_status"] == "KILL"
    assert "valid_claim_false_forgetting" in decision["kill_findings"]


def test_bootstrap_is_bound_and_deterministic() -> None:
    policy = _policy(bootstrap_resamples=100)
    rows = _rows(policy)
    first = score_private_holdout(
        deepcopy(rows),
        policy,
        bootstrap_binding_sha256="1" * 64,
    )
    second = score_private_holdout(
        deepcopy(rows),
        policy,
        bootstrap_binding_sha256="1" * 64,
    )
    assert first["statistics"]["bootstrap"] == second["statistics"]["bootstrap"]

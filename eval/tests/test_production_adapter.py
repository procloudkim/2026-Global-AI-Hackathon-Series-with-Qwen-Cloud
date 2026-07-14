import json
from pathlib import Path

import pytest

from eval.evaluate import evaluate_outputs
from eval.generate import materialize
from eval.run import run_policies


def test_production_adapter_passes_dev_conformance_without_promotion(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    manifest = materialize(split="dev", output_dir=dataset)
    outputs = tmp_path / "run" / "outputs.jsonl"
    run_manifest = run_policies(
        cases_path=dataset / "runner-inputs" / "cases.jsonl",
        extractions_path=dataset / "runner-inputs" / "extractions.jsonl",
        output_path=outputs,
        policies=["C"],
        repeats=1,
        candidate_factory="src.librarian.eval_adapter:create_adapter",
        dataset_manifest_path=dataset / "dataset-manifest.json",
    )
    assert run_manifest["status"] == "COMPLETE"

    report = evaluate_outputs(
        cases_path=dataset / "runner-inputs" / "cases.jsonl",
        gold_path=dataset / "evaluator-only" / "gold.jsonl",
        outputs_path=outputs,
        run_manifest_path=outputs.with_name("run-manifest.json"),
        output_dir=tmp_path / "report",
    )
    metrics = report["metrics_by_repeat"]["0"]
    candidate = metrics["C"]
    assert report["gate_status"] == "PRODUCTION_CONFORMANCE_PASS"
    assert report["promotion_status"] == "HOLD"
    assert report["promoted"] is False
    assert report["proof_boundary"]["fair_baseline_comparison"] is False
    assert report["proof_boundary"]["production_code_exercised"] is True
    assert report["proof_boundary"]["candidate_execution_receipt_verified"] is True
    assert candidate["scenario_success_count"] == 8
    assert candidate["stale_leakage_rate"] == 0.0
    assert candidate["false_forget_count"] == 0
    assert candidate["citation_entailment"] == 1.0
    assert candidate["wire_citation_receipt_coverage"] == 1.0
    assert candidate["wire_citation_fidelity"] == 1.0
    assert candidate["retrieval_recall_at_k"] == 1.0
    assert candidate["transition_ledger_integrity"] == 1.0
    assert candidate["transition_ledger_violation_count"] == 0


def test_production_candidate_is_compared_with_all_fair_baselines(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    materialize(split="dev", output_dir=dataset)
    outputs = tmp_path / "run" / "outputs.jsonl"
    run_manifest = run_policies(
        cases_path=dataset / "runner-inputs" / "cases.jsonl",
        extractions_path=dataset / "runner-inputs" / "extractions.jsonl",
        output_path=outputs,
        policies=["B0", "B1", "B2", "C"],
        repeats=1,
        candidate_factory="src.librarian.eval_adapter:create_adapter",
        dataset_manifest_path=dataset / "dataset-manifest.json",
    )

    assert run_manifest["lane"] == "production_policy_comparison"
    assert run_manifest["candidate_execution"]["row_count"] > 0
    assert run_manifest["candidate_execution"]["rows_sha256"]
    report = evaluate_outputs(
        cases_path=dataset / "runner-inputs" / "cases.jsonl",
        gold_path=dataset / "evaluator-only" / "gold.jsonl",
        outputs_path=outputs,
        run_manifest_path=outputs.with_name("run-manifest.json"),
        output_dir=tmp_path / "report",
    )

    assert report["lane"] == "production_policy_comparison"
    assert report["gate_status"] == "NOT_ELIGIBLE_DEV_OR_MISSING_REPEATS"
    assert report["promoted"] is False
    assert report["proof_boundary"]["fair_baseline_comparison"] is True
    assert report["proof_boundary"]["production_code_exercised"] is True
    assert report["proof_boundary"]["candidate_factory_fingerprint_verified"] is True
    assert report["proof_boundary"]["candidate_execution_receipt_verified"] is True
    checks = report["repeat_decisions"]["0"]["checks"]
    assert any(name.startswith("comparison::") for name in checks)
    assert any(name.startswith("production::") for name in checks)
    assert report["metrics_by_repeat"]["0"]["C"][
        "transition_ledger_integrity"
    ] == 1.0

    manifest_path = outputs.with_name("run-manifest.json")
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered["candidate_execution"]["rows_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="candidate execution receipt"):
        evaluate_outputs(
            cases_path=dataset / "runner-inputs" / "cases.jsonl",
            gold_path=dataset / "evaluator-only" / "gold.jsonl",
            outputs_path=outputs,
            run_manifest_path=manifest_path,
            output_dir=tmp_path / "tampered-report",
        )
    rows = [
        json.loads(line)
        for line in outputs.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidate_rows = [row for row in rows if row["policy_id"] == "C"]
    assert candidate_rows
    assert all(
        row["citations"] == row["trace"]["wire_evidence_source_ids"]
        for row in candidate_rows
    )
    assert all(
        row["trace"]["scheduled_transitions_materialized_by_query"] == 0
        for row in candidate_rows
    )
    assert any(
        row["trace"]["scheduled_transitions_materialized_before_query"] > 0
        for row in candidate_rows
    )
    assert all(
        isinstance(row["trace"]["wire_page_citations"], list)
        for row in candidate_rows
    )

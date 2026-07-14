from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from eval.contracts import assert_oracle_free, candidate_tree_hash, seed_commitment
from eval.evaluate import (
    _aggregate,
    _score_checkpoint,
    _validate_transition_ledger,
    evaluate_outputs,
)
from eval.generate import DEFAULT_POLICY, materialize
from eval.run import run_policies
from eval.scenarios import build_dataset


class HarnessTests(unittest.TestCase):
    def test_candidate_tree_hash_is_line_ending_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lf_root = root / "lf"
            crlf_root = root / "crlf"
            files = {
                "src/librarian/example.py": "value = 1\n",
                "eval/example.py": "POLICY = 'fixed'\n",
                "pyproject.toml": "[project]\nname = 'example'\n",
                "uv.lock": "version = 1\n",
            }
            for relative, content in files.items():
                for tree, newline in ((lf_root, "\n"), (crlf_root, "\r\n")):
                    path = tree / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content.replace("\n", newline).encode("utf-8"))

            self.assertEqual(
                candidate_tree_hash(lf_root),
                candidate_tree_hash(crlf_root),
            )

    def test_candidate_tree_hash_excludes_private_promotion_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = {
                "src/librarian/example.py": "value = 1\n",
                "eval/evaluate.py": "SCORER = 'fixed'\n",
                "eval/private_promotion.py": "PRIVATE_EVALUATOR = 'first'\n",
                "pyproject.toml": "[project]\nname = 'example'\n",
                "uv.lock": "version = 1\n",
            }
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            frozen = candidate_tree_hash(root)
            (root / "eval/private_promotion.py").write_text(
                "PRIVATE_EVALUATOR = 'second'\n",
                encoding="utf-8",
            )
            self.assertEqual(frozen, candidate_tree_hash(root))

            (root / "eval/evaluate.py").write_text(
                "SCORER = 'changed'\n",
                encoding="utf-8",
            )
            self.assertNotEqual(frozen, candidate_tree_hash(root))

    def test_generator_is_deterministic_and_opaque(self) -> None:
        first = build_dataset(
            seed="deterministic-test-seed-0001",
            variants_per_type=1,
            distractor_count=3,
        )
        second = build_dataset(
            seed="deterministic-test-seed-0001",
            variants_per_type=1,
            distractor_count=3,
        )
        other = build_dataset(
            seed="deterministic-test-seed-0002",
            variants_per_type=1,
            distractor_count=3,
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        case_text = json.dumps(first[0], ensure_ascii=False)
        self.assertNotIn("explicit_supersession", case_text)
        assert_oracle_free(first[0])
        assert_oracle_free(first[1])
        self.assertTrue(
            all(
                set(query) == {"terms"}
                for extraction in first[1]
                for query in extraction["queries"].values()
            )
        )
        self.assertTrue(
            all(
                "relation" not in claim and "target_source_id" not in claim
                for extraction in first[1]
                for event in extraction["events"].values()
                for claim in event["claims"]
            )
        )

    def test_seed_commitment_does_not_expose_seed(self) -> None:
        seed = "high-entropy-private-seed-1234"
        commitment = seed_commitment(seed)
        self.assertEqual(len(commitment), 64)
        self.assertNotIn(seed, commitment)
        with self.assertRaises(ValueError):
            seed_commitment("too-short")

    def test_runner_rejects_oracle_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "oracle field"):
            assert_oracle_free({"renamed": {"expected_facts": []}})

    def test_state_accuracy_rejects_extra_contradictory_live_state(self) -> None:
        output = {
            "run_id": "run",
            "repeat": 0,
            "policy_id": "C",
            "scenario_id": "scenario",
            "checkpoint_id": "checkpoint",
            "facts": [{"key": "s::x::p", "value": "new"}],
            "citations": ["new-source"],
            "abstained": False,
            "memory_state": [
                {
                    "key": "s::x::p",
                    "value": "old",
                    "status": "superseded",
                    "source_ids": ["old-source"],
                },
                {
                    "key": "s::x::p",
                    "value": "new",
                    "status": "active",
                    "source_ids": ["new-source"],
                },
                {
                    "key": "s::x::p",
                    "value": "old",
                    "status": "active",
                    "source_ids": ["old-source"],
                },
            ],
            "trace": {"loaded_source_ids": ["new-source"]},
        }
        gold = {
            "expected_facts": [
                {
                    "key": "s::x::p",
                    "value": "new",
                    "supporting_sources": ["new-source"],
                }
            ],
            "forbidden_facts": [{"key": "s::x::p", "value": "old"}],
            "required_sources": ["new-source"],
            "required_retrieval_sources": ["new-source"],
            "expected_states": [
                {
                    "key": "s::x::p",
                    "value": "old",
                    "status": "superseded",
                    "source_ids": ["old-source"],
                },
                {
                    "key": "s::x::p",
                    "value": "new",
                    "status": "active",
                    "source_ids": ["new-source"],
                },
            ],
            "protected_facts": [],
            "must_abstain": False,
        }

        scored = _score_checkpoint(output, gold, "explicit_supersession")

        self.assertFalse(scored["state_exact"])
        self.assertEqual(scored["state_violation_count"], 1)

    def test_state_accuracy_requires_exact_source_provenance(self) -> None:
        state = {
            "key": "s::x::p",
            "value": "new",
            "status": "active",
            "source_ids": ["source-good", "source-unexpected"],
        }
        output = {
            "run_id": "run",
            "repeat": 0,
            "policy_id": "C",
            "scenario_id": "scenario",
            "checkpoint_id": "checkpoint",
            "facts": [{"key": "s::x::p", "value": "new"}],
            "citations": ["source-good"],
            "abstained": False,
            "memory_state": [state],
            "trace": {"loaded_source_ids": ["source-good"]},
        }
        gold = {
            "expected_facts": [
                {
                    "key": "s::x::p",
                    "value": "new",
                    "supporting_sources": ["source-good"],
                }
            ],
            "forbidden_facts": [],
            "required_sources": ["source-good"],
            "required_retrieval_sources": ["source-good"],
            "expected_states": [
                {
                    "key": "s::x::p",
                    "value": "new",
                    "status": "active",
                    "source_ids": ["source-good"],
                }
            ],
            "protected_facts": [],
            "must_abstain": False,
        }

        scored = _score_checkpoint(output, gold, "explicit_supersession")

        self.assertFalse(scored["state_exact"])

    def test_answer_success_rejects_extra_unapproved_fact(self) -> None:
        output = {
            "run_id": "run",
            "repeat": 0,
            "policy_id": "C",
            "scenario_id": "scenario",
            "checkpoint_id": "checkpoint",
            "facts": [
                {"key": "s::x::p", "value": "new"},
                {"key": "s::x::made-up", "value": "fabricated"},
            ],
            "citations": ["new-source"],
            "abstained": False,
            "memory_state": [
                {
                    "key": "s::x::p",
                    "value": "new",
                    "status": "active",
                    "source_ids": ["new-source"],
                }
            ],
            "trace": {"loaded_source_ids": ["new-source"]},
        }
        gold = {
            "expected_facts": [
                {
                    "key": "s::x::p",
                    "value": "new",
                    "supporting_sources": ["new-source"],
                }
            ],
            "forbidden_facts": [],
            "required_sources": ["new-source"],
            "required_retrieval_sources": ["new-source"],
            "expected_states": output["memory_state"],
            "protected_facts": output["memory_state"],
            "must_abstain": False,
        }

        scored = _score_checkpoint(output, gold, "explicit_supersession")

        self.assertFalse(scored["fact_set_exact"])
        self.assertFalse(scored["current_cited_answer_success"])

    def test_transition_ledger_validation_is_strict_and_replay_bound(self) -> None:
        case = {
            "events": [
                {
                    "event_id": "event-1",
                    "source_id": "source-1",
                    "text": "The quota is 100.",
                }
            ]
        }
        checkpoint = {
            "after_event": "event-1",
            "as_of": "2026-07-14T01:00:00Z",
        }
        creation = {
            "schema_version": "librarian-memory/v2",
            "event_id": hashlib.sha256(
                "api-policy|claim-1|new|active|2026-07-14T00:00:00Z|source_grounded_claim_creation|".encode(
                    "utf-8"
                )
            ).hexdigest()[:24],
            "timestamp": "2026-07-14T00:00:00Z",
            "page_slug": "api-policy",
            "claim_id": "claim-1",
            "from_status": None,
            "to_status": "active",
            "trigger_claim_id": None,
            "rule": "source_grounded_claim_creation",
            "relation": None,
            "model": "frozen",
            "prompt_version": "v3",
            "evidence_source_ids": ["source-1"],
            "evidence_spans": ["The quota is 100."],
            "rationale": "Source-grounded creation.",
        }
        output = {
            "transitions": [creation],
            "memory_state": [
                {
                    "claim_id": "claim-1",
                    "status": "active",
                    "source_ids": ["source-1"],
                }
            ],
        }

        valid = _validate_transition_ledger(
            output, case=case, checkpoint=checkpoint, previous=None
        )
        self.assertTrue(valid["transition_ledger_valid"])

        tampered = {
            **output,
            "transitions": [{**creation, "unexpected": True}],
        }
        invalid = _validate_transition_ledger(
            tampered,
            case=case,
            checkpoint=checkpoint,
            previous=[creation],
        )
        self.assertFalse(invalid["transition_contract_valid"])
        self.assertFalse(invalid["transition_prefix_valid"])
        self.assertFalse(invalid["transition_ledger_valid"])

    def test_extra_non_entailing_citation_fails_checkpoint(self) -> None:
        state = {
            "key": "s::x::p",
            "value": "new",
            "status": "active",
            "source_ids": ["source-good"],
        }
        output = {
            "run_id": "run",
            "repeat": 0,
            "policy_id": "C",
            "scenario_id": "scenario",
            "checkpoint_id": "checkpoint",
            "facts": [{"key": "s::x::p", "value": "new"}],
            "citations": ["source-good", "source-unrelated"],
            "abstained": False,
            "memory_state": [state],
            "trace": {"loaded_source_ids": ["source-good"]},
        }
        gold = {
            "expected_facts": [
                {
                    "key": "s::x::p",
                    "value": "new",
                    "supporting_sources": ["source-good"],
                }
            ],
            "forbidden_facts": [],
            "required_sources": ["source-good"],
            "required_retrieval_sources": ["source-good"],
            "expected_states": [state],
            "protected_facts": [state],
            "must_abstain": False,
        }

        scored = _score_checkpoint(output, gold, "explicit_supersession")

        self.assertFalse(scored["current_cited_answer_success"])
        self.assertEqual(scored["entailed_citation_count"], 1)
        self.assertEqual(scored["citation_count"], 2)
        self.assertEqual(_aggregate([scored])["citation_entailment"], 0.5)

    def test_wire_citation_receipt_requires_both_exact_surfaces(self) -> None:
        state = {
            "key": "s::x::p",
            "value": "new",
            "status": "active",
            "source_ids": ["source-good"],
        }
        output = {
            "run_id": "run",
            "repeat": 0,
            "policy_id": "C",
            "scenario_id": "scenario",
            "checkpoint_id": "checkpoint",
            "facts": [{"key": "s::x::p", "value": "new"}],
            "citations": ["source-good"],
            "abstained": False,
            "memory_state": [state],
            "trace": {
                "loaded_source_ids": ["source-good"],
                "wire_page_citations": ["page-good"],
                "wire_evidence_source_ids": ["source-good"],
            },
        }
        gold = {
            "expected_facts": [
                {
                    "key": "s::x::p",
                    "value": "new",
                    "supporting_sources": ["source-good"],
                }
            ],
            "forbidden_facts": [],
            "required_sources": ["source-good"],
            "required_retrieval_sources": ["source-good"],
            "expected_states": [state],
            "protected_facts": [state],
            "must_abstain": False,
        }

        valid = _score_checkpoint(output, gold, "explicit_supersession")
        self.assertTrue(valid["wire_citation_receipt_present"])
        self.assertTrue(valid["wire_citation_fidelity"])

        invalid = _score_checkpoint(
            {
                **output,
                "trace": {
                    "loaded_source_ids": ["source-good"],
                    "wire_page_citations": ["page-good"],
                    "wire_evidence_source_ids": ["source-other"],
                },
            },
            gold,
            "explicit_supersession",
        )
        self.assertFalse(invalid["wire_citation_fidelity"])

    def test_dev_lane_is_behaviorally_sound_but_not_promotion_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dataset = root / "dataset"
            manifest = materialize(
                split="dev", output_dir=dataset, policy_path=DEFAULT_POLICY
            )
            self.assertEqual(manifest["scenario_count"], 8)
            output = root / "run" / "outputs.jsonl"
            run_policies(
                cases_path=dataset / "runner-inputs" / "cases.jsonl",
                extractions_path=dataset / "runner-inputs" / "extractions.jsonl",
                output_path=output,
                policy_path=DEFAULT_POLICY,
                repeats=3,
            )
            report = evaluate_outputs(
                cases_path=dataset / "runner-inputs" / "cases.jsonl",
                gold_path=dataset / "evaluator-only" / "gold.jsonl",
                outputs_path=output,
                output_dir=root / "report",
                policy_path=DEFAULT_POLICY,
                run_manifest_path=output.with_name("run-manifest.json"),
            )
            self.assertEqual(
                report["gate_status"], "NOT_ELIGIBLE_DEV_OR_MISSING_REPEATS"
            )
            self.assertEqual(report["promotion_status"], "HOLD")
            self.assertFalse(report["promoted"])
            for repeat in ("0", "1", "2"):
                candidate = report["metrics_by_repeat"][repeat]["C"]
                self.assertEqual(candidate["scenario_success_count"], 8)
                self.assertEqual(candidate["stale_leakage_rate"], 0.0)
                self.assertEqual(candidate["false_forget_count"], 0)
                self.assertEqual(candidate["citation_entailment"], 1.0)
                self.assertEqual(candidate["retrieval_recall_at_k"], 1.0)
                self.assertGreater(
                    candidate["scenario_success_rate"],
                    report["metrics_by_repeat"][repeat]["B2"]["scenario_success_rate"],
                )

    def test_24_scenario_same_process_holdout_is_not_isolated_or_promotable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            old = os.environ.get("HOLDOUT_SEED")
            os.environ["HOLDOUT_SEED"] = "test-only-private-seed-00000001"
            try:
                manifest = materialize(
                    split="holdout", output_dir=root / "dataset", policy_path=DEFAULT_POLICY
                )
            finally:
                if old is None:
                    os.environ.pop("HOLDOUT_SEED", None)
                else:
                    os.environ["HOLDOUT_SEED"] = old
            self.assertEqual(manifest["scenario_count"], 24)
            self.assertEqual(
                manifest["evidence_role"], "same_builder_diagnostic_only"
            )
            self.assertFalse(manifest["promotion_eligible"])
            self.assertEqual(
                manifest["collection_provenance"],
                "repository_scenario_builders_v1",
            )
            manifest_text = json.dumps(manifest)
            self.assertNotIn("test-only-private-seed-00000001", manifest_text)
            output = root / "run" / "outputs.jsonl"
            run_policies(
                cases_path=root / "dataset" / "runner-inputs" / "cases.jsonl",
                extractions_path=root / "dataset" / "runner-inputs" / "extractions.jsonl",
                output_path=output,
                policy_path=DEFAULT_POLICY,
                repeats=3,
            )
            report = evaluate_outputs(
                cases_path=root / "dataset" / "runner-inputs" / "cases.jsonl",
                gold_path=root / "dataset" / "evaluator-only" / "gold.jsonl",
                outputs_path=output,
                output_dir=root / "report",
                policy_path=DEFAULT_POLICY,
            )
            self.assertEqual(report["gate_status"], "NOT_ELIGIBLE_GOLD_NOT_ISOLATED")
            self.assertEqual(report["promotion_status"], "HOLD")
            self.assertFalse(report["promoted"])
            self.assertEqual(report["passing_repeats"], 3)
            self.assertIn(
                "gold_runtime_isolation_unproven", report["promotion_blockers"]
            )
            run_manifest_path = output.with_name("run-manifest.json")
            tampered = json.loads(run_manifest_path.read_text(encoding="utf-8"))
            tampered["runner_process_isolation"] = True
            run_manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "local runner cannot self-attest gold isolation"
            ):
                evaluate_outputs(
                    cases_path=root / "dataset" / "runner-inputs" / "cases.jsonl",
                    gold_path=root / "dataset" / "evaluator-only" / "gold.jsonl",
                    outputs_path=output,
                    output_dir=root / "tampered-report",
                    policy_path=DEFAULT_POLICY,
                )

    def test_run_requires_untampered_dataset_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dataset = root / "dataset"
            materialize(split="dev", output_dir=dataset, policy_path=DEFAULT_POLICY)
            (dataset / "dataset-manifest.json").unlink()
            with self.assertRaisesRegex(ValueError, "dataset manifest"):
                run_policies(
                    cases_path=dataset / "runner-inputs" / "cases.jsonl",
                    extractions_path=dataset / "runner-inputs" / "extractions.jsonl",
                    output_path=root / "run" / "outputs.jsonl",
                    policy_path=DEFAULT_POLICY,
                    repeats=1,
                )

    def test_evaluator_rejects_gold_changed_after_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dataset = root / "dataset"
            materialize(split="dev", output_dir=dataset, policy_path=DEFAULT_POLICY)
            output = root / "run" / "outputs.jsonl"
            run_policies(
                cases_path=dataset / "runner-inputs" / "cases.jsonl",
                extractions_path=dataset / "runner-inputs" / "extractions.jsonl",
                output_path=output,
                policy_path=DEFAULT_POLICY,
                repeats=1,
            )
            gold = dataset / "evaluator-only" / "gold.jsonl"
            gold.write_text(gold.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "gold_sha256"):
                evaluate_outputs(
                    cases_path=dataset / "runner-inputs" / "cases.jsonl",
                    gold_path=gold,
                    outputs_path=output,
                    output_dir=root / "report",
                    policy_path=DEFAULT_POLICY,
                )

    def test_candidate_factory_accepts_only_production_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dataset = root / "dataset"
            materialize(split="dev", output_dir=dataset, policy_path=DEFAULT_POLICY)
            common = {
                "cases_path": dataset / "runner-inputs" / "cases.jsonl",
                "extractions_path": dataset / "runner-inputs" / "extractions.jsonl",
                "output_path": root / "run" / "outputs.jsonl",
                "policy_path": DEFAULT_POLICY,
                "repeats": 1,
            }
            with self.assertRaisesRegex(ValueError, "exact B0/B1/B2/C"):
                run_policies(
                    **common,
                    policies=["B0", "B2", "C"],
                    candidate_factory="src.librarian.eval_adapter:create_adapter",
                )
            comparison = run_policies(
                **common,
                policies=["B0", "B1", "B2", "C"],
                candidate_factory="src.librarian.eval_adapter:create_adapter",
            )
            self.assertEqual(comparison["lane"], "production_policy_comparison")
            self.assertEqual(comparison["candidate_execution"]["policy_id"], "C")
            self.assertGreater(comparison["candidate_execution"]["row_count"], 0)
            with self.assertRaisesRegex(ValueError, "allowlisted"):
                run_policies(
                    **common,
                    policies=["C"],
                    candidate_factory="untrusted.module:create_adapter",
                )

    def test_kill_finding_vetoes_an_otherwise_passing_policy_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
            policy["kill_rules"]["maximum_b2_negligible_delta"] = 1.0
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            dataset = root / "dataset"
            materialize(
                split="holdout",
                output_dir=dataset,
                policy_path=policy_path,
                seed="kill-veto-private-seed-0001",
            )
            output = root / "run" / "outputs.jsonl"
            run_policies(
                cases_path=dataset / "runner-inputs" / "cases.jsonl",
                extractions_path=dataset / "runner-inputs" / "extractions.jsonl",
                output_path=output,
                policy_path=policy_path,
                repeats=3,
            )
            report = evaluate_outputs(
                cases_path=dataset / "runner-inputs" / "cases.jsonl",
                gold_path=dataset / "evaluator-only" / "gold.jsonl",
                outputs_path=output,
                output_dir=root / "report",
                policy_path=policy_path,
            )

            assert report["gate_status"] == "NOT_ELIGIBLE_GOLD_NOT_ISOLATED"
            assert report["passing_repeats"] == 0
            assert all(
                decision.get("kill_rule_veto") is True
                for decision in report["repeat_decisions"].values()
            )


if __name__ == "__main__":
    unittest.main()

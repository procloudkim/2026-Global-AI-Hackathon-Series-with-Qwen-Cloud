"""Deterministically score outputs against a separately supplied oracle."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any

from . import HARNESS_VERSION
from .contracts import (
    SCHEMA_VERSION,
    candidate_tree_hash,
    file_sha256,
    load_json,
    load_jsonl,
    stable_hash,
    utc_now,
    validate_output_row,
    write_json,
    write_jsonl,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "eval" / "policy.json"
_PRODUCTION_LANES = {"production_conformance", "production_policy_comparison"}


def _candidate_execution_receipt(
    outputs: list[dict[str, Any]], factory_receipt: dict[str, Any]
) -> dict[str, Any]:
    candidate_rows = [
        row for row in outputs if str(row.get("policy_id")) == "C"
    ]
    return {
        "policy_id": "C",
        "adapter_class": factory_receipt["adapter_class"],
        "row_count": len(candidate_rows),
        "scenario_ids": sorted(
            {str(row["scenario_id"]) for row in candidate_rows}
        ),
        "repeat_ids": sorted({int(row["repeat"]) for row in candidate_rows}),
        "transition_count": sum(
            len(row.get("transitions", [])) for row in candidate_rows
        ),
        "rows_sha256": stable_hash(candidate_rows),
    }


def _pair(fact: dict[str, Any]) -> tuple[str, str]:
    return str(fact.get("key", "")), str(fact.get("value", ""))


def _state_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if (
        str(expected.get("key")) != str(actual.get("key"))
        or str(expected.get("value")) != str(actual.get("value"))
        or str(expected.get("status")) != str(actual.get("status"))
    ):
        return False
    return set(map(str, expected.get("source_ids", []))) == set(
        map(str, actual.get("source_ids", []))
    )


def _protected_present(protected: dict[str, Any], actual_states: list[dict]) -> bool:
    return any(
        str(state.get("key")) == str(protected.get("key"))
        and str(state.get("value")) == str(protected.get("value"))
        and str(state.get("status")) == "active"
        for state in actual_states
    )


def _score_checkpoint(
    output: dict[str, Any], gold: dict[str, Any], scenario_type: str
) -> dict[str, Any]:
    actual_pairs = {_pair(fact) for fact in output["facts"]}
    expected_pairs = {_pair(fact) for fact in gold["expected_facts"]}
    forbidden_pairs = {_pair(fact) for fact in gold["forbidden_facts"]}
    citations = set(map(str, output["citations"]))
    loaded = set(map(str, output["trace"].get("loaded_source_ids", [])))
    trace = output["trace"]

    wire_page_present = "wire_page_citations" in trace
    wire_source_present = "wire_evidence_source_ids" in trace
    wire_citation_receipt_present = wire_page_present or wire_source_present
    wire_citation_fidelity: bool | None = None
    if wire_citation_receipt_present:
        page_citations = trace.get("wire_page_citations")
        evidence_source_ids = trace.get("wire_evidence_source_ids")
        valid_page_citations = (
            isinstance(page_citations, list)
            and all(isinstance(item, str) and item for item in page_citations)
            and len(page_citations) == len(set(page_citations))
        )
        valid_evidence_sources = (
            isinstance(evidence_source_ids, list)
            and all(isinstance(item, str) and item for item in evidence_source_ids)
            and len(evidence_source_ids) == len(set(evidence_source_ids))
        )
        surface_shape_valid = (
            not page_citations and not evidence_source_ids
            if bool(output["abstained"])
            else bool(page_citations) and bool(evidence_source_ids)
        )
        wire_citation_fidelity = bool(
            wire_page_present
            and wire_source_present
            and valid_page_citations
            and valid_evidence_sources
            and list(map(str, output["citations"])) == evidence_source_ids
            and surface_shape_valid
        )

    stale_leak = bool(actual_pairs & forbidden_pairs)
    facts_exact = (
        actual_pairs == expected_pairs and len(actual_pairs) == len(output["facts"])
    )
    required_citations = set(map(str, gold.get("required_sources", [])))
    required_citations_present = required_citations.issubset(citations)
    entailed = 0
    allowed_citations: set[str] = set()
    for fact in gold["expected_facts"]:
        supporting = set(map(str, fact.get("supporting_sources", [])))
        allowed_citations.update(supporting)
        if _pair(fact) in actual_pairs and supporting.intersection(citations):
            entailed += 1
    entailed_citations = len(citations.intersection(allowed_citations))
    all_citations_entailed = citations.issubset(allowed_citations)
    entailment_ok = (
        entailed == len(gold["expected_facts"]) and all_citations_entailed
    )

    if bool(gold["must_abstain"]):
        current_success = (
            bool(output["abstained"])
            and not output["facts"]
            and not output["citations"]
        )
        abstention_correct = bool(output["abstained"])
        # An abstention must not smuggle a disputed value through structured facts.
        current_success = current_success and not stale_leak
    else:
        current_success = (
            not bool(output["abstained"])
            and facts_exact
            and not stale_leak
            and required_citations_present
            and entailment_ok
        )
        abstention_correct = not bool(output["abstained"])

    expected_states = gold.get("expected_states", [])
    state_matches = sum(
        1
        for expected in expected_states
        if any(_state_matches(expected, actual) for actual in output["memory_state"])
    )
    expected_state_keys = {str(expected.get("key")) for expected in expected_states}
    relevant_actual_states = [
        actual
        for actual in output["memory_state"]
        if str(actual.get("key")) in expected_state_keys
    ]
    state_exact = (
        state_matches == len(expected_states)
        and len(relevant_actual_states) == len(expected_states)
        and all(
            any(_state_matches(expected, actual) for expected in expected_states)
            for actual in relevant_actual_states
        )
    )
    protected = gold.get("protected_facts", [])
    false_forgets = sum(
        1
        for expected in protected
        if not _protected_present(expected, output["memory_state"])
    )
    required_retrieval = set(map(str, gold.get("required_retrieval_sources", [])))
    retrieved = len(required_retrieval.intersection(loaded))

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": output["run_id"],
        "repeat": output["repeat"],
        "policy_id": output["policy_id"],
        "scenario_id": output["scenario_id"],
        "scenario_type": scenario_type,
        "checkpoint_id": output["checkpoint_id"],
        "current_cited_answer_success": current_success,
        "fact_set_exact": facts_exact,
        "stale_leak": stale_leak,
        "stale_sensitive": bool(forbidden_pairs),
        "abstention_correct": abstention_correct,
        "entailed_fact_count": entailed,
        "expected_fact_count": len(gold["expected_facts"]),
        "entailed_citation_count": entailed_citations,
        "citation_count": len(citations),
        "wire_citation_receipt_present": wire_citation_receipt_present,
        "wire_citation_fidelity": wire_citation_fidelity,
        "state_match_count": state_matches,
        "expected_state_count": len(expected_states),
        "state_exact": state_exact,
        "state_violation_count": int(not state_exact),
        "false_forget_count": false_forgets,
        "protected_fact_count": len(protected),
        "scope_false_forget_count": false_forgets
        if scenario_type == "scope_coexistence"
        else 0,
        "retrieved_required_count": retrieved,
        "required_retrieval_count": len(required_retrieval),
        "context_tokens": int(output["trace"].get("context_tokens", 0)),
        "total_tokens": int(output["trace"].get("total_tokens", 0)),
    }


_TRANSITION_FIELDS = {
    "schema_version",
    "event_id",
    "timestamp",
    "page_slug",
    "claim_id",
    "from_status",
    "to_status",
    "trigger_claim_id",
    "rule",
    "relation",
    "model",
    "prompt_version",
    "evidence_source_ids",
    "evidence_spans",
    "rationale",
}
_PROVENANCE_FIELDS = {
    "schema_version",
    "event_id",
    "timestamp",
    "event_type",
    "page_slug",
    "claim_id",
    "source_id",
    "rule",
}
_ALLOWED_TRANSITIONS = {
    (None, "active"),
    ("active", "disputed"),
    ("active", "superseded"),
    ("disputed", "active"),
    ("disputed", "superseded"),
    ("superseded", "active"),
    ("superseded", "archived"),
}


def _parse_aware_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _validate_transition_ledger(
    output: dict[str, Any],
    *,
    case: dict[str, Any],
    checkpoint: dict[str, Any],
    previous: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Validate production receipts without consulting lifecycle gold labels."""

    transitions = output["transitions"]
    violations: set[str] = set()
    if previous is not None and transitions[: len(previous)] != previous:
        violations.add("ledger_not_append_only")
    if previous is not None and len(transitions) < len(previous):
        violations.add("ledger_truncated")

    event_positions = {
        str(event["event_id"]): index for index, event in enumerate(case["events"])
    }
    stop = event_positions[str(checkpoint["after_event"])] + 1
    visible_events = case["events"][:stop]
    visible_text = {
        str(event["source_id"]): str(event["text"]) for event in visible_events
    }
    cutoff = _parse_aware_timestamp(checkpoint["as_of"])
    if cutoff is None:
        violations.add("invalid_checkpoint_timestamp")

    replay: dict[str, dict[str, Any]] = {}
    event_ids: set[str] = set()
    created: set[str] = set()
    for event in transitions:
        if not isinstance(event, dict):
            violations.add("transition_not_object")
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            violations.add("invalid_event_id")
        elif event_id in event_ids:
            violations.add("duplicate_event_id")
        else:
            event_ids.add(event_id)

        timestamp = _parse_aware_timestamp(event.get("timestamp"))
        if timestamp is None:
            violations.add("invalid_transition_timestamp")
        elif cutoff is not None and timestamp > cutoff:
            violations.add("transition_after_checkpoint")

        if event.get("event_type") == "provenance_merge":
            if set(event) != _PROVENANCE_FIELDS:
                violations.add("invalid_provenance_schema")
                continue
            if (
                event.get("schema_version") != "librarian-memory/v2"
                or any(
                    not isinstance(event.get(field), str) or not event.get(field)
                    for field in (
                        "event_id",
                        "timestamp",
                        "page_slug",
                        "claim_id",
                        "source_id",
                        "rule",
                    )
                )
                or event.get("rule") != "exact_key_value_effective_time"
            ):
                violations.add("invalid_provenance_contract")
            claim_id = str(event.get("claim_id", ""))
            source_id = str(event.get("source_id", ""))
            if source_id not in visible_text:
                violations.add("provenance_source_not_visible")
            if claim_id not in replay:
                violations.add("provenance_before_creation")
                continue
            replay[claim_id]["source_ids"].add(source_id)
            continue

        if set(event) != _TRANSITION_FIELDS:
            violations.add("invalid_transition_schema")
            continue
        claim_id = str(event.get("claim_id", ""))
        from_status = event.get("from_status")
        to_status = event.get("to_status")
        if (
            event.get("schema_version") != "librarian-memory/v2"
            or any(
                not isinstance(event.get(field), str) or not event.get(field)
                for field in (
                    "event_id",
                    "timestamp",
                    "page_slug",
                    "claim_id",
                    "rule",
                    "prompt_version",
                    "rationale",
                )
            )
            or event.get("relation")
            not in {None, "supports", "contradicts", "supersedes", "unresolved"}
            or (
                event.get("model") is not None
                and not isinstance(event.get("model"), str)
            )
            or (
                event.get("trigger_claim_id") is not None
                and (
                    not isinstance(event.get("trigger_claim_id"), str)
                    or not event.get("trigger_claim_id")
                )
            )
        ):
            violations.add("invalid_transition_contract")
        if (from_status, to_status) not in _ALLOWED_TRANSITIONS:
            violations.add("invalid_state_transition")
            continue
        expected_event_id = hashlib.sha256(
            "|".join(
                (
                    str(event["page_slug"]),
                    claim_id,
                    "new" if from_status is None else str(from_status),
                    str(to_status),
                    str(event["timestamp"]),
                    str(event["rule"]),
                    str(event.get("trigger_claim_id") or ""),
                )
            ).encode("utf-8")
        ).hexdigest()[:24]
        if event.get("event_id") != expected_event_id:
            violations.add("transition_event_id_mismatch")
        sources = event.get("evidence_source_ids")
        spans = event.get("evidence_spans")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(source, str) or source not in visible_text for source in sources)
        ):
            violations.add("transition_source_not_visible")
            sources = []
        if not isinstance(spans, list) or not spans or any(
            not isinstance(span, str) or not span for span in spans
        ):
            violations.add("invalid_transition_evidence")
            spans = []
        else:
            for span in spans:
                if not any(span in visible_text[source] for source in sources):
                    violations.add("transition_span_not_source_bound")

        trigger = event.get("trigger_claim_id")
        if trigger is not None and trigger not in created:
            violations.add("trigger_before_creation")
        if from_status is None:
            if claim_id in replay:
                violations.add("duplicate_claim_creation")
                continue
            replay[claim_id] = {
                "status": str(to_status),
                "source_ids": set(map(str, sources)),
            }
            created.add(claim_id)
        else:
            current = replay.get(claim_id)
            if current is None:
                violations.add("transition_before_creation")
                continue
            if current["status"] != from_status:
                violations.add("transition_replay_mismatch")
                continue
            current["status"] = str(to_status)

    actual_state: dict[str, dict[str, Any]] = {}
    for state in output["memory_state"]:
        if not isinstance(state, dict) or not isinstance(state.get("claim_id"), str):
            violations.add("memory_state_missing_claim_id")
            continue
        claim_id = str(state["claim_id"])
        if claim_id in actual_state:
            violations.add("duplicate_memory_state_claim")
        actual_state[claim_id] = {
            "status": str(state.get("status", "")),
            "source_ids": set(map(str, state.get("source_ids", []))),
        }
    if replay != actual_state:
        violations.add("transition_replay_state_mismatch")

    contract_codes = {
        code
        for code in violations
        if code
        in {
            "transition_not_object",
            "invalid_event_id",
            "duplicate_event_id",
            "invalid_provenance_schema",
            "invalid_provenance_contract",
            "invalid_transition_schema",
            "invalid_transition_contract",
            "invalid_state_transition",
            "transition_event_id_mismatch",
        }
    }
    evidence_codes = {
        code
        for code in violations
        if code
        in {
            "invalid_transition_timestamp",
            "transition_after_checkpoint",
            "provenance_source_not_visible",
            "transition_source_not_visible",
            "invalid_transition_evidence",
            "transition_span_not_source_bound",
            "trigger_before_creation",
        }
    }
    replay_codes = violations - contract_codes - evidence_codes - {
        "ledger_not_append_only",
        "ledger_truncated",
    }
    return {
        "transition_contract_valid": not contract_codes,
        "transition_evidence_valid": not evidence_codes,
        "transition_prefix_valid": not bool(
            {"ledger_not_append_only", "ledger_truncated"} & violations
        ),
        "transition_replay_matches_state": not replay_codes,
        "transition_ledger_valid": not violations,
        "transition_violation_codes": sorted(violations),
    }


def _safe_rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scenario_checkpoints: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        scenario_checkpoints[row["scenario_id"]].append(
            bool(row["current_cited_answer_success"])
        )
    scenario_success_count = sum(all(values) for values in scenario_checkpoints.values())
    current_success_count = sum(
        bool(row["current_cited_answer_success"]) for row in rows
    )
    stale_sensitive = sum(bool(row["stale_sensitive"]) for row in rows)
    stale_leaks = sum(bool(row["stale_leak"]) for row in rows)
    citation_count = sum(int(row["citation_count"]) for row in rows)
    entailed_citations = sum(int(row["entailed_citation_count"]) for row in rows)
    expected_states = sum(int(row["expected_state_count"]) for row in rows)
    state_matches = sum(int(row["state_match_count"]) for row in rows)
    protected = sum(int(row["protected_fact_count"]) for row in rows)
    false_forgets = sum(int(row["false_forget_count"]) for row in rows)
    required_retrieval = sum(int(row["required_retrieval_count"]) for row in rows)
    retrieved = sum(int(row["retrieved_required_count"]) for row in rows)
    total_tokens = sum(int(row["total_tokens"]) for row in rows)
    transition_rows = [
        row for row in rows if row.get("transition_ledger_valid") is not None
    ]
    wire_citation_rows = [
        row for row in rows if bool(row.get("wire_citation_receipt_present"))
    ]
    transition_violations = sum(
        len(row.get("transition_violation_codes", [])) for row in transition_rows
    )
    return {
        "scenario_count": len(scenario_checkpoints),
        "checkpoint_count": len(rows),
        "scenario_success_count": scenario_success_count,
        "scenario_success_rate": _safe_rate(
            scenario_success_count, len(scenario_checkpoints)
        ),
        "current_cited_answer_success_count": current_success_count,
        "current_cited_answer_success_rate": _safe_rate(current_success_count, len(rows)),
        "stale_leakage_count": stale_leaks,
        "stale_leakage_rate": _safe_rate(stale_leaks, stale_sensitive),
        "false_forget_count": false_forgets,
        "false_forget_rate": _safe_rate(false_forgets, protected),
        "scope_false_forget_count": sum(
            int(row["scope_false_forget_count"]) for row in rows
        ),
        "state_transition_accuracy": _safe_rate(
            sum(bool(row["state_exact"]) for row in rows), len(rows)
        ),
        "state_violation_count": sum(
            int(row["state_violation_count"]) for row in rows
        ),
        "abstention_accuracy": _safe_rate(
            sum(bool(row["abstention_correct"]) for row in rows), len(rows)
        ),
        "fact_set_accuracy": _safe_rate(
            sum(bool(row["fact_set_exact"]) for row in rows), len(rows)
        ),
        "transition_ledger_integrity": (
            _safe_rate(
                sum(bool(row["transition_ledger_valid"]) for row in transition_rows),
                len(transition_rows),
            )
            if transition_rows
            else None
        ),
        "transition_ledger_violation_count": transition_violations,
        "citation_entailment": _safe_rate(entailed_citations, citation_count),
        "wire_citation_receipt_coverage": _safe_rate(
            len(wire_citation_rows), len(rows)
        ),
        "wire_citation_fidelity": (
            _safe_rate(
                sum(bool(row.get("wire_citation_fidelity")) for row in wire_citation_rows),
                len(wire_citation_rows),
            )
            if wire_citation_rows
            else None
        ),
        "retrieval_recall_at_k": _safe_rate(retrieved, required_retrieval),
        "context_tokens": sum(int(row["context_tokens"]) for row in rows),
        "total_tokens": total_tokens,
        "tokens_per_correct_current_answer": _safe_rate(
            total_tokens, current_success_count
        )
        if current_success_count
        else None,
    }


def _promotion_for_repeat(
    metrics: dict[str, dict[str, Any]], gates: dict[str, Any]
) -> dict[str, Any]:
    candidate = metrics.get("C")
    b0 = metrics.get("B0")
    b2 = metrics.get("B2")
    if not candidate or not b0 or not b2:
        return {"eligible": False, "passed": False, "reason": "B0/B2/C required"}
    enough_scenarios = candidate["scenario_count"] >= int(gates["minimum_scenario_count"])
    b2_delta = candidate["scenario_success_rate"] - b2["scenario_success_rate"]
    candidate_tpc = candidate["tokens_per_correct_current_answer"]
    b0_tpc = b0["tokens_per_correct_current_answer"]
    token_reduction = (
        1.0 - float(candidate_tpc) / float(b0_tpc)
        if candidate_tpc is not None and b0_tpc not in {None, 0}
        else 0.0
    )
    checks = {
        "minimum_scenario_success_count": candidate["scenario_success_count"]
        >= int(gates["minimum_scenario_success_count"]),
        "maximum_stale_leakage_rate": candidate["stale_leakage_rate"]
        <= float(gates["maximum_stale_leakage_rate"]),
        "maximum_scope_false_forget_count": candidate["scope_false_forget_count"]
        <= int(gates["maximum_scope_false_forget_count"]),
        "minimum_citation_entailment": candidate["citation_entailment"]
        >= float(gates["minimum_citation_entailment"]),
        "minimum_retrieval_recall_at_k": candidate["retrieval_recall_at_k"]
        >= float(gates["minimum_retrieval_recall_at_k"]),
        "minimum_state_transition_accuracy": candidate["state_transition_accuracy"]
        >= float(gates["minimum_state_transition_accuracy"]),
        "maximum_state_violation_count": candidate["state_violation_count"]
        <= int(gates["maximum_state_violation_count"]),
        "minimum_abstention_accuracy": candidate["abstention_accuracy"]
        >= float(gates["minimum_abstention_accuracy"]),
        "minimum_b2_success_delta": b2_delta
        >= float(gates["minimum_b2_success_delta"]),
        "minimum_b0_token_reduction_per_correct": token_reduction
        >= float(gates["minimum_b0_token_reduction_per_correct"]),
    }
    return {
        "eligible": enough_scenarios,
        "passed": enough_scenarios and all(checks.values()),
        "checks": checks,
        "b2_success_delta": b2_delta,
        "b0_token_reduction_per_correct": token_reduction,
    }


def _conformance_for_repeat(
    metrics: dict[str, dict[str, Any]], gates: dict[str, Any]
) -> dict[str, Any]:
    candidate = metrics.get("C")
    if not candidate:
        return {"eligible": False, "passed": False, "reason": "C required"}
    minimum_success = (
        int(gates["minimum_scenario_success_count"])
        if candidate["scenario_count"] >= int(gates["minimum_scenario_count"])
        else candidate["scenario_count"]
    )
    checks = {
        "minimum_scenario_success_count": candidate["scenario_success_count"]
        >= minimum_success,
        "maximum_stale_leakage_rate": candidate["stale_leakage_rate"]
        <= float(gates["maximum_stale_leakage_rate"]),
        "maximum_scope_false_forget_count": candidate["scope_false_forget_count"]
        <= int(gates["maximum_scope_false_forget_count"]),
        "minimum_citation_entailment": candidate["citation_entailment"]
        >= float(gates["minimum_citation_entailment"]),
        "minimum_retrieval_recall_at_k": candidate["retrieval_recall_at_k"]
        >= float(gates["minimum_retrieval_recall_at_k"]),
        "minimum_state_transition_accuracy": candidate["state_transition_accuracy"]
        >= float(gates["minimum_state_transition_accuracy"]),
        "maximum_state_violation_count": candidate["state_violation_count"]
        <= int(gates["maximum_state_violation_count"]),
        "minimum_abstention_accuracy": candidate["abstention_accuracy"]
        >= float(gates["minimum_abstention_accuracy"]),
        "maximum_false_forget_count": candidate["false_forget_count"] == 0,
        "minimum_transition_ledger_integrity": candidate[
            "transition_ledger_integrity"
        ]
        is not None
        and candidate["transition_ledger_integrity"]
        >= float(gates["minimum_transition_ledger_integrity"]),
        "maximum_transition_ledger_violation_count": candidate[
            "transition_ledger_violation_count"
        ]
        <= int(gates["maximum_transition_ledger_violation_count"]),
        "production_wire_citation_fidelity": (
            candidate["wire_citation_receipt_coverage"] == 1.0
            and candidate["wire_citation_fidelity"] == 1.0
        ),
    }
    return {
        "eligible": True,
        "passed": all(checks.values()),
        "checks": checks,
        "comparison_claim_allowed": False,
    }


def _production_comparison_for_repeat(
    metrics: dict[str, dict[str, Any]], gates: dict[str, Any]
) -> dict[str, Any]:
    """Require both fair-policy superiority and real-adapter conformance."""

    comparison = _promotion_for_repeat(metrics, gates)
    conformance = _conformance_for_repeat(metrics, gates)
    checks = {
        **{
            f"comparison::{name}": passed
            for name, passed in comparison.get("checks", {}).items()
        },
        **{
            f"production::{name}": passed
            for name, passed in conformance.get("checks", {}).items()
        },
    }
    eligible = bool(comparison.get("eligible")) and bool(
        conformance.get("eligible")
    )
    return {
        "eligible": eligible,
        "passed": eligible and all(checks.values()),
        "checks": checks,
        "b2_success_delta": comparison.get("b2_success_delta"),
        "b0_token_reduction_per_correct": comparison.get(
            "b0_token_reduction_per_correct"
        ),
        "comparison_claim_allowed": True,
    }


def _kill_findings(
    metrics: dict[str, dict[str, Any]], rules: dict[str, Any]
) -> list[str]:
    candidate = metrics.get("C")
    b2 = metrics.get("B2")
    if not candidate or not b2:
        return ["missing_candidate_or_b2"]
    delta = candidate["scenario_success_rate"] - b2["scenario_success_rate"]
    findings: list[str] = []
    if delta <= float(rules["maximum_b2_negligible_delta"]):
        findings.append("candidate_delta_vs_b2_is_negligible")
    if candidate["false_forget_count"] > int(
        rules["maximum_valid_claim_false_forget_count"]
    ):
        findings.append("valid_claim_false_forget_detected")
    if bool(rules["require_candidate_to_beat_b2"]) and delta <= 0:
        findings.append("candidate_does_not_beat_b2")
    if candidate["stale_leakage_rate"] > float(
        rules["maximum_stale_leakage_rate"]
    ):
        findings.append("stale_leakage_above_kill_threshold")
    return findings


def evaluate_outputs(
    *,
    cases_path: str | Path,
    gold_path: str | Path,
    outputs_path: str | Path,
    output_dir: str | Path,
    policy_path: str | Path = DEFAULT_POLICY,
    run_manifest_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    cases = load_jsonl(cases_path)
    gold_rows = load_jsonl(gold_path)
    outputs = load_jsonl(outputs_path)
    for row in outputs:
        validate_output_row(row)
    case_ids = {case["scenario_id"] for case in cases}
    gold_by_id = {row["scenario_id"]: row for row in gold_rows}
    if case_ids != set(gold_by_id):
        raise ValueError("case and gold scenario IDs differ")
    gold_checkpoints = {
        (scenario_id, checkpoint["checkpoint_id"]): (
            checkpoint,
            gold_by_id[scenario_id]["scenario_type"],
        )
        for scenario_id in gold_by_id
        for checkpoint in gold_by_id[scenario_id]["checkpoints"]
    }
    manifest_path = (
        Path(run_manifest_path)
        if run_manifest_path is not None
        else Path(outputs_path).with_name("run-manifest.json")
    )
    if not manifest_path.is_file():
        raise ValueError("run manifest is required")
    run_manifest = load_json(manifest_path)
    if run_manifest.get("status") != "COMPLETE":
        raise ValueError("run manifest is not complete")

    inferred_dataset_manifest = Path(cases_path).parent.parent / "dataset-manifest.json"
    dataset_manifest_file = (
        Path(dataset_manifest_path)
        if dataset_manifest_path is not None
        else inferred_dataset_manifest
    )
    if not dataset_manifest_file.is_file():
        raise ValueError("dataset manifest is required")
    dataset_manifest = load_json(dataset_manifest_file)
    extraction_path = Path(cases_path).with_name("extractions.jsonl")
    if not extraction_path.is_file():
        raise ValueError("frozen extraction snapshot is required for verification")
    cases_receipt_path = Path(outputs_path).with_name("cases.jsonl")
    transitions_path = Path(outputs_path).with_name("transitions.jsonl")
    if not cases_receipt_path.is_file() or not transitions_path.is_file():
        raise ValueError("run cases/transitions receipts are required")
    recorded_transitions = load_jsonl(transitions_path)
    expected_transitions: list[dict[str, Any]] = []
    transition_keys: set[str] = set()
    for row in outputs:
        for transition in row["transitions"]:
            receipt = {
                "run_id": row["run_id"],
                "repeat": row["repeat"],
                "policy_id": row["policy_id"],
                "scenario_id": row["scenario_id"],
                "checkpoint_id": row["checkpoint_id"],
                "transition": transition,
            }
            key = stable_hash(receipt)
            if key in transition_keys:
                continue
            transition_keys.add(key)
            expected_transitions.append(receipt)
    if recorded_transitions != expected_transitions:
        raise ValueError("transitions receipt does not match outputs")

    dataset_hashes = dataset_manifest.get("hashes")
    if not isinstance(dataset_hashes, dict):
        raise ValueError("dataset manifest hashes are required")
    verified_dataset_hashes = {
        "cases_sha256": file_sha256(cases_path),
        "extractions_sha256": file_sha256(extraction_path),
        "gold_sha256": file_sha256(gold_path),
        "policy_sha256": file_sha256(policy_path),
    }
    for key, actual in verified_dataset_hashes.items():
        if dataset_hashes.get(key) != actual:
            raise ValueError(f"{key} does not match dataset manifest")

    hashes = run_manifest.get("hashes")
    if not isinstance(hashes, dict):
        raise ValueError("run manifest hashes are required")
    verified_run_hashes = {
        "cases_sha256": verified_dataset_hashes["cases_sha256"],
        "extractions_sha256": verified_dataset_hashes["extractions_sha256"],
        "policy_sha256": verified_dataset_hashes["policy_sha256"],
        "outputs_sha256": file_sha256(outputs_path),
        "cases_receipt_sha256": file_sha256(cases_receipt_path),
        "transitions_sha256": file_sha256(transitions_path),
        "dataset_manifest_sha256": file_sha256(dataset_manifest_file),
    }
    for key, actual in verified_run_hashes.items():
        if hashes.get(key) != actual:
            raise ValueError(f"{key} does not match run manifest")
    if (run_manifest.get("dataset_manifest") or {}).get("sha256") != file_sha256(
        dataset_manifest_file
    ):
        raise ValueError("dataset manifest receipt does not match run manifest")
    if file_sha256(cases_receipt_path) != file_sha256(cases_path):
        raise ValueError("run cases receipt differs from frozen cases")
    if (run_manifest.get("dataset_manifest") or {}).get(
        "seed_commitment"
    ) != dataset_manifest.get("seed_commitment"):
        raise ValueError("seed commitment does not match run manifest")
    if run_manifest.get("split") != dataset_manifest.get("split"):
        raise ValueError("dataset split does not match run manifest")

    current_tree = candidate_tree_hash(ROOT)
    frozen_tree = (dataset_manifest.get("candidate_snapshot") or {}).get(
        "tree_sha256"
    )
    if not frozen_tree or frozen_tree != current_tree:
        raise ValueError("candidate tree changed after dataset freeze")
    if run_manifest.get("candidate_tree_sha256") != current_tree:
        raise ValueError("candidate tree does not match run manifest")
    if run_manifest.get("gold_path_not_passed") is not True:
        raise ValueError("run manifest does not prove the gold-path argument boundary")

    lane = str(run_manifest.get("lane", ""))
    if lane not in {
        "policy_comparison",
        "production_conformance",
        "production_policy_comparison",
        "diagnostic",
    }:
        raise ValueError("run manifest lane is invalid")
    cases_by_id = {str(case["scenario_id"]): case for case in cases}
    scenario_order = {
        str(case["scenario_id"]): index for index, case in enumerate(cases)
    }
    checkpoint_order = {
        (str(case["scenario_id"]), str(checkpoint["checkpoint_id"])): index
        for case in cases
        for index, checkpoint in enumerate(case["checkpoints"])
    }
    ordered_outputs = sorted(
        outputs,
        key=lambda output: (
            int(output["repeat"]),
            str(output["policy_id"]),
            scenario_order[str(output["scenario_id"])],
            checkpoint_order[
                (str(output["scenario_id"]), str(output["checkpoint_id"]))
            ],
        ),
    )
    previous_ledgers: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    output_keys: set[tuple[int, str, str, str]] = set()
    scored: list[dict[str, Any]] = []
    for output in ordered_outputs:
        key = (
            int(output["repeat"]),
            str(output["policy_id"]),
            str(output["scenario_id"]),
            str(output["checkpoint_id"]),
        )
        if key in output_keys:
            raise ValueError(f"duplicate output row: {key}")
        output_keys.add(key)
        oracle_key = (str(output["scenario_id"]), str(output["checkpoint_id"]))
        if oracle_key not in gold_checkpoints:
            raise ValueError(f"output has no oracle checkpoint: {oracle_key}")
        checkpoint_gold, scenario_type = gold_checkpoints[oracle_key]
        scored_row = _score_checkpoint(output, checkpoint_gold, scenario_type)
        if lane in _PRODUCTION_LANES and str(output["policy_id"]) == "C":
            case = cases_by_id[str(output["scenario_id"])]
            checkpoint = next(
                item
                for item in case["checkpoints"]
                if str(item["checkpoint_id"]) == str(output["checkpoint_id"])
            )
            ledger_key = (
                int(output["repeat"]),
                str(output["policy_id"]),
                str(output["scenario_id"]),
            )
            scored_row.update(
                _validate_transition_ledger(
                    output,
                    case=case,
                    checkpoint=checkpoint,
                    previous=previous_ledgers.get(ledger_key),
                )
            )
            previous_ledgers[ledger_key] = list(output["transitions"])
        else:
            scored_row.update(
                {
                    "transition_contract_valid": None,
                    "transition_evidence_valid": None,
                    "transition_prefix_valid": None,
                    "transition_replay_matches_state": None,
                    "transition_ledger_valid": None,
                    "transition_violation_codes": [],
                }
            )
        scored.append(scored_row)

    expected_keys = {
        (repeat, policy_id, str(case["scenario_id"]), str(checkpoint["checkpoint_id"]))
        for repeat in range(int(run_manifest["repeats"]))
        for policy_id in run_manifest["policies"]
        for case in cases
        for checkpoint in case["checkpoints"]
    }
    if output_keys != expected_keys:
        missing = len(expected_keys - output_keys)
        extra = len(output_keys - expected_keys)
        raise ValueError(
            f"output matrix is incomplete or unexpected: missing={missing}, extra={extra}"
        )
    if any(str(output.get("run_id")) != str(run_manifest.get("run_id")) for output in outputs):
        raise ValueError("output run_id does not match run manifest")

    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        grouped[(int(row["repeat"]), str(row["policy_id"]))].append(row)
    metrics_by_repeat: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (repeat, policy_id), rows in grouped.items():
        metrics_by_repeat[str(repeat)][policy_id] = _aggregate(rows)

    policy = load_json(policy_path)
    gates = policy["repository_diagnostic_gates"]
    factory_receipt = run_manifest.get("candidate_factory")
    candidate_execution = run_manifest.get("candidate_execution")
    if lane in _PRODUCTION_LANES:
        if not isinstance(factory_receipt, dict):
            raise ValueError("production lane requires a factory fingerprint")
        module_path = ROOT / str(factory_receipt.get("module_path", ""))
        if (
            factory_receipt.get("spec")
            != "src.librarian.eval_adapter:create_adapter"
            or not module_path.is_file()
            or factory_receipt.get("module_sha256") != file_sha256(module_path)
            or not str(factory_receipt.get("adapter_class", "")).startswith(
                "src.librarian.eval_adapter."
            )
        ):
            raise ValueError("candidate factory fingerprint is invalid")
        if candidate_execution != _candidate_execution_receipt(
            outputs, factory_receipt
        ):
            raise ValueError("candidate execution receipt is invalid")
        if int(candidate_execution.get("row_count", 0)) <= 0:
            raise ValueError("candidate execution receipt contains no output rows")
        expected_lane_policies = (
            ["C"]
            if lane == "production_conformance"
            else ["B0", "B1", "B2", "C"]
        )
        if run_manifest.get("policies") != expected_lane_policies:
            raise ValueError("production lane policy set is invalid")
    elif factory_receipt is not None or candidate_execution is not None:
        raise ValueError(
            "non-production lanes cannot use candidate factory or execution receipts"
        )
    repeat_decisions: dict[str, dict[str, Any]] = {}
    for repeat, metrics in sorted(metrics_by_repeat.items(), key=lambda item: int(item[0])):
        if lane == "production_conformance":
            decision = _conformance_for_repeat(metrics, gates)
            candidate_metrics = metrics.get("C", {})
            kill_findings = (
                ["transition_ledger_integrity_violation"]
                if int(candidate_metrics.get("transition_ledger_violation_count", 0))
                else []
            )
        elif lane == "production_policy_comparison":
            decision = _production_comparison_for_repeat(metrics, gates)
            candidate_metrics = metrics.get("C", {})
            kill_findings = _kill_findings(metrics, policy["kill_rules"])
            if int(candidate_metrics.get("transition_ledger_violation_count", 0)):
                kill_findings.append("transition_ledger_integrity_violation")
        else:
            decision = _promotion_for_repeat(metrics, gates)
            kill_findings = _kill_findings(metrics, policy["kill_rules"])
        decision["kill_findings"] = kill_findings
        if kill_findings:
            decision["passed"] = False
            decision["kill_rule_veto"] = True
        repeat_decisions[repeat] = decision

    eligible_repeats = sum(
        bool(decision.get("eligible")) for decision in repeat_decisions.values()
    )
    passing_repeats = sum(
        bool(decision.get("passed")) for decision in repeat_decisions.values()
    )
    required_repeats = int(gates["required_repeats"])
    minimum_passing = int(gates["minimum_passing_repeats"])
    split = str(dataset_manifest.get("split"))
    if run_manifest.get("runner_process_isolation") is not False:
        raise ValueError(
            "the local runner cannot self-attest gold isolation; "
            "an external attested lane is required"
        )
    # This evaluator accepts only manifests produced by the bundled local
    # runner.  Its own boolean is not evidence and can never make holdout
    # output eligible.  A future external lane must have a separate verifier
    # and a signed receipt bound to dataset/output/candidate hashes.
    runner_process_isolated = False
    if split == "holdout" and not runner_process_isolated:
        gate_status = "NOT_ELIGIBLE_GOLD_NOT_ISOLATED"
    elif lane == "production_conformance":
        gate_status = (
            "PRODUCTION_CONFORMANCE_PASS"
            if passing_repeats == len(repeat_decisions) and repeat_decisions
            else "PRODUCTION_CONFORMANCE_FAIL"
        )
    elif lane == "diagnostic":
        gate_status = "DIAGNOSTIC_ONLY"
    elif split != "holdout" or eligible_repeats < required_repeats:
        gate_status = "NOT_ELIGIBLE_DEV_OR_MISSING_REPEATS"
    elif passing_repeats >= minimum_passing:
        gate_status = "DETERMINISTIC_POLICY_GATE_PASS"
    else:
        gate_status = "DETERMINISTIC_POLICY_GATE_FAIL"

    promotion_blockers = ["offline_synthetic_evidence_cannot_promote"]
    if split == "dev":
        promotion_blockers.append("dev_split_is_not_promotion_evidence")
    if split == "holdout" and not runner_process_isolated:
        promotion_blockers.append("gold_runtime_isolation_unproven")

    report = {
        "schema_version": SCHEMA_VERSION,
        "harness_version": HARNESS_VERSION,
        "created_at": utc_now(),
        "gate_status": gate_status,
        "promotion_status": "HOLD",
        "decision": "hold",
        "promoted": False,
        "evidence_mode": "synthetic",
        "lane": lane,
        "split": split,
        "passing_repeats": passing_repeats,
        "required_passing_repeats": minimum_passing,
        "metrics_by_repeat": dict(metrics_by_repeat),
        "repeat_decisions": repeat_decisions,
        "promotion_blockers": promotion_blockers,
        "hashes": {
            "cases_sha256": file_sha256(cases_path),
            "extractions_sha256": file_sha256(extraction_path),
            "gold_sha256": file_sha256(gold_path),
            "outputs_sha256": file_sha256(outputs_path),
            "policy_sha256": file_sha256(policy_path),
            "dataset_manifest_sha256": file_sha256(dataset_manifest_file),
            "candidate_tree_sha256": current_tree,
        },
        "proof_boundary": {
            "level": "deterministic_behavioral",
            "live_qwen_proven": False,
            "analysis_unit": "scenario",
            "model_used_as_grader": False,
            "repeats_are_independent_samples": False,
            "fair_baseline_comparison": lane
            in {"policy_comparison", "production_policy_comparison"},
            "production_code_exercised": lane in _PRODUCTION_LANES,
            "candidate_factory_fingerprint_verified": lane in _PRODUCTION_LANES,
            "candidate_execution_receipt_verified": lane in _PRODUCTION_LANES,
            "runner_process_isolated_from_gold": runner_process_isolated,
            "offline_fixture_can_promote": False,
        },
    }
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    write_jsonl(destination / "checkpoint-results.jsonl", scored)
    write_json(destination / "metrics.json", report)
    (destination / "report.md").write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Librarian Memory-Policy Evaluation",
        "",
        f"- Gate status: **{report['gate_status']}**",
        f"- Product decision: **{report['promotion_status']}**",
        f"- Proof boundary: `{report['proof_boundary']['level']}`",
        "- Live Qwen proven: **NO**",
        "",
        "| repeat | policy | scenarios passed | stale leakage | false forget | state exact | abstention | citation entailment | recall@K | ledger | tokens/correct |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for repeat, policies in sorted(
        report["metrics_by_repeat"].items(), key=lambda item: int(item[0])
    ):
        for policy_id, metric in sorted(policies.items()):
            tpc = metric["tokens_per_correct_current_answer"]
            tpc_text = f"{tpc:.2f}" if tpc is not None else "n/a"
            ledger = metric["transition_ledger_integrity"]
            ledger_text = f"{ledger:.3f}" if ledger is not None else "n/a"
            lines.append(
                f"| {repeat} | {policy_id} | {metric['scenario_success_count']}/{metric['scenario_count']} | "
                f"{metric['stale_leakage_rate']:.3f} | {metric['false_forget_rate']:.3f} | "
                f"{metric['state_transition_accuracy']:.3f} | {metric['abstention_accuracy']:.3f} | "
                f"{metric['citation_entailment']:.3f} | {metric['retrieval_recall_at_k']:.3f} | "
                f"{ledger_text} | {tpc_text} |"
            )
    lines.extend(
        [
            "",
            "> This receipt proves only deterministic behavior against the frozen oracle. "
            "It does not prove live Qwen extraction or answer quality, and it cannot promote.",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--run-manifest", type=Path)
    parser.add_argument("--dataset-manifest", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = evaluate_outputs(
        cases_path=args.cases,
        gold_path=args.gold,
        outputs_path=args.outputs,
        output_dir=args.output_dir,
        policy_path=args.policy,
        run_manifest_path=args.run_manifest,
        dataset_manifest_path=args.dataset_manifest,
    )
    print(f"gate_status={report['gate_status']}")
    print(f"promotion_status={report['promotion_status']}")
    print(f"metrics={args.output_dir / 'metrics.json'}")


if __name__ == "__main__":
    main()

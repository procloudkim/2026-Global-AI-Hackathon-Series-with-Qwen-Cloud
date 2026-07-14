"""Independent private-holdout scoring and promotion contracts.

This module is public and deterministic.  It does not create scenarios, gold,
or an independence claim.  An external evaluator supplies private paired
scenario outcomes after candidate freeze; this module validates the frozen
matrix, computes paired statistics, and emits aggregate-only evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import math
from pathlib import Path
import random
import re
from typing import Any, Iterable

from .contracts import file_sha256, load_json, load_jsonl, stable_hash, write_json


PAIR_RESULT_SCHEMA_VERSION = "2.0"
AGGREGATE_SCHEMA_VERSION = "2.0"

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_POLICY_IDS = ("B2", "C")
_OUTCOME_BOOLEAN_FIELDS = {
    "answer_correct",
    "citation_correct",
    "stale_absent_answer",
    "stale_absent_context",
    "unrelated_preserved",
}
_OUTCOME_COUNT_FIELDS = {
    "valid_false_forget_count",
    "scope_false_forget_count",
    "citation_entailed_count",
    "citation_count",
    "retrieval_hit_count",
    "retrieval_required_count",
    "state_correct_count",
    "state_total_count",
    "state_violation_count",
    "abstention_correct_count",
    "abstention_total_count",
    "ledger_valid_count",
    "ledger_total_count",
    "ledger_violation_count",
    "wire_citation_correct_count",
    "wire_citation_total_count",
}
_OUTCOME_FIELDS = _OUTCOME_BOOLEAN_FIELDS | _OUTCOME_COUNT_FIELDS
_METRIC_FIELDS = {
    "scenario_count",
    "scenario_success_count",
    "scenario_success_rate",
    "stale_surface_leakage_count",
    "stale_surface_opportunity_count",
    "stale_leakage_rate",
    "valid_false_forget_count",
    "scope_false_forget_count",
    "citation_entailed_count",
    "citation_count",
    "citation_entailment",
    "retrieval_hit_count",
    "retrieval_required_count",
    "retrieval_recall_at_k",
    "state_correct_count",
    "state_total_count",
    "state_transition_accuracy",
    "state_violation_count",
    "abstention_correct_count",
    "abstention_total_count",
    "abstention_accuracy",
    "ledger_valid_count",
    "ledger_total_count",
    "transition_ledger_integrity",
    "transition_ledger_violation_count",
    "wire_citation_correct_count",
    "wire_citation_total_count",
    "wire_citation_fidelity",
}
_PAIR_FIELDS = {"both_success", "candidate_only", "baseline_only", "both_fail"}
_SUMMARY_FIELDS = {
    "scenario_count",
    "b2_success_count",
    "b2_success_rate",
    "c_success_count",
    "c_success_rate",
    "delta",
    "pair_table",
}


def _require_exact_keys(
    value: dict[str, Any], required: set[str], location: str
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ValueError(f"{location} missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{location} has unknown fields: {', '.join(unknown)}")


def _require_non_negative_int(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{location} must be a non-negative integer")
    return value


def _require_finite_rate(value: Any, location: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{location} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{location} must be a finite rate in [0, 1]")
    return result


def _require_finite_delta(value: Any, location: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{location} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not -1.0 <= result <= 1.0:
        raise ValueError(f"{location} must be a finite delta in [-1, 1]")
    return result


def _rate(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)


def exact_mcnemar_p(candidate_only: int, baseline_only: int) -> float:
    """Return the exact two-sided McNemar p-value for paired binary outcomes."""

    n10 = _require_non_negative_int(candidate_only, "candidate_only")
    n01 = _require_non_negative_int(baseline_only, "baseline_only")
    discordant = n10 + n01
    if discordant == 0:
        return 1.0
    lower = min(n10, n01)
    tail_numerator = sum(math.comb(discordant, index) for index in range(lower + 1))
    return min(1.0, 2.0 * tail_numerator / (2**discordant))


def promotion_binding_sha256(
    *,
    dataset_manifest_sha256: str,
    paired_results_sha256: str,
    candidate_tree_sha256: str,
    policy_sha256: str,
) -> str:
    """Bind bootstrap resampling to the frozen data, candidate, and policy."""

    values = {
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "paired_results_sha256": paired_results_sha256,
        "candidate_tree_sha256": candidate_tree_sha256,
        "policy_sha256": policy_sha256,
    }
    for name, value in values.items():
        if not isinstance(value, str) or not _HEX_64.fullmatch(value):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return stable_hash(values)


def _validate_outcome(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    _require_exact_keys(value, _OUTCOME_FIELDS, location)
    for field in _OUTCOME_BOOLEAN_FIELDS:
        if not isinstance(value[field], bool):
            raise ValueError(f"{location}.{field} must be a boolean")
    for field in _OUTCOME_COUNT_FIELDS:
        _require_non_negative_int(value[field], f"{location}.{field}")

    for numerator, denominator in (
        ("citation_entailed_count", "citation_count"),
        ("retrieval_hit_count", "retrieval_required_count"),
        ("state_correct_count", "state_total_count"),
        ("abstention_correct_count", "abstention_total_count"),
        ("ledger_valid_count", "ledger_total_count"),
        ("wire_citation_correct_count", "wire_citation_total_count"),
    ):
        if value[numerator] > value[denominator]:
            raise ValueError(f"{location}.{numerator} exceeds {denominator}")

    for denominator in (
        "retrieval_required_count",
        "state_total_count",
        "abstention_total_count",
        "ledger_total_count",
        "wire_citation_total_count",
    ):
        if value[denominator] == 0:
            raise ValueError(f"{location}.{denominator} must be positive")

    if value["citation_correct"] != (
        value["citation_entailed_count"] == value["citation_count"]
    ):
        raise ValueError(f"{location}.citation_correct is inconsistent with counts")
    return value


def scenario_success(outcome: dict[str, Any]) -> bool:
    """Derive strict scenario success; callers cannot self-supply this label."""

    return bool(
        outcome["answer_correct"]
        and outcome["citation_correct"]
        and outcome["stale_absent_answer"]
        and outcome["stale_absent_context"]
        and outcome["unrelated_preserved"]
        and outcome["valid_false_forget_count"] == 0
        and outcome["scope_false_forget_count"] == 0
        and outcome["retrieval_hit_count"] == outcome["retrieval_required_count"]
        and outcome["state_correct_count"] == outcome["state_total_count"]
        and outcome["state_violation_count"] == 0
        and outcome["abstention_correct_count"] == outcome["abstention_total_count"]
        and outcome["ledger_valid_count"] == outcome["ledger_total_count"]
        and outcome["ledger_violation_count"] == 0
        and outcome["wire_citation_correct_count"]
        == outcome["wire_citation_total_count"]
    )


def _promotion_config(policy: dict[str, Any]) -> dict[str, Any]:
    if policy.get("schema_version") != "2.0":
        raise ValueError("private promotion requires policy schema_version 2.0")
    config = policy.get("private_promotion_v2")
    if not isinstance(config, dict):
        raise ValueError("policy.private_promotion_v2 must be an object")
    return config


def validate_paired_rows(
    rows: Iterable[dict[str, Any]], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validate the private 8 × 2 × 24 scenario matrix without exposing gold."""

    config = _promotion_config(policy)
    scenario_types = tuple(map(str, policy["dataset"]["scenario_types"]))
    pools = tuple(map(str, config["provenance_pools"]))
    per_cell = int(config["scenarios_per_type_per_pool"])
    expected_count = int(config["scenario_count"])
    materialized = list(rows)
    if len(materialized) != expected_count:
        raise ValueError(
            f"private holdout requires exactly {expected_count} paired scenarios"
        )

    expected_row_keys = {
        "schema_version",
        "scenario_id",
        "scenario_type",
        "provenance_pool",
        "B2",
        "C",
    }
    seen_ids: set[str] = set()
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for index, row in enumerate(materialized):
        location = f"rows[{index}]"
        if not isinstance(row, dict):
            raise ValueError(f"{location} must be an object")
        _require_exact_keys(row, expected_row_keys, location)
        if row["schema_version"] != PAIR_RESULT_SCHEMA_VERSION:
            raise ValueError(f"{location}.schema_version is unsupported")
        scenario_id = row["scenario_id"]
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError(f"{location}.scenario_id must be non-empty")
        if scenario_id in seen_ids:
            raise ValueError("scenario_id values must be unique")
        seen_ids.add(scenario_id)
        scenario_type = row["scenario_type"]
        pool = row["provenance_pool"]
        if scenario_type not in scenario_types:
            raise ValueError(f"{location}.scenario_type is not in the frozen policy")
        if pool not in pools:
            raise ValueError(f"{location}.provenance_pool is not in the frozen policy")
        counts[(scenario_type, pool)] += 1
        for policy_id in _POLICY_IDS:
            _validate_outcome(row[policy_id], f"{location}.{policy_id}")

    expected_cells = {
        (scenario_type, pool)
        for scenario_type in scenario_types
        for pool in pools
    }
    if set(counts) != expected_cells or any(
        counts[cell] != per_cell for cell in expected_cells
    ):
        raise ValueError(
            "private holdout must contain every scenario-type/provenance cell "
            f"with exactly {per_cell} scenarios"
        )
    return materialized


def _aggregate_policy(
    rows: list[dict[str, Any]], policy_id: str
) -> dict[str, int | float]:
    outcomes = [row[policy_id] for row in rows]
    successes = sum(scenario_success(outcome) for outcome in outcomes)
    stale_leaks = sum(
        int(not outcome["stale_absent_answer"])
        + int(not outcome["stale_absent_context"])
        for outcome in outcomes
    )
    stale_opportunities = 2 * len(outcomes)
    totals = {
        field: sum(int(outcome[field]) for outcome in outcomes)
        for field in _OUTCOME_COUNT_FIELDS
    }
    return {
        "scenario_count": len(outcomes),
        "scenario_success_count": successes,
        "scenario_success_rate": _rate(successes, len(outcomes)),
        "stale_surface_leakage_count": stale_leaks,
        "stale_surface_opportunity_count": stale_opportunities,
        "stale_leakage_rate": _rate(stale_leaks, stale_opportunities),
        "valid_false_forget_count": totals["valid_false_forget_count"],
        "scope_false_forget_count": totals["scope_false_forget_count"],
        "citation_entailed_count": totals["citation_entailed_count"],
        "citation_count": totals["citation_count"],
        "citation_entailment": _rate(
            totals["citation_entailed_count"], totals["citation_count"]
        ),
        "retrieval_hit_count": totals["retrieval_hit_count"],
        "retrieval_required_count": totals["retrieval_required_count"],
        "retrieval_recall_at_k": _rate(
            totals["retrieval_hit_count"], totals["retrieval_required_count"]
        ),
        "state_correct_count": totals["state_correct_count"],
        "state_total_count": totals["state_total_count"],
        "state_transition_accuracy": _rate(
            totals["state_correct_count"], totals["state_total_count"]
        ),
        "state_violation_count": totals["state_violation_count"],
        "abstention_correct_count": totals["abstention_correct_count"],
        "abstention_total_count": totals["abstention_total_count"],
        "abstention_accuracy": _rate(
            totals["abstention_correct_count"], totals["abstention_total_count"]
        ),
        "ledger_valid_count": totals["ledger_valid_count"],
        "ledger_total_count": totals["ledger_total_count"],
        "transition_ledger_integrity": _rate(
            totals["ledger_valid_count"], totals["ledger_total_count"]
        ),
        "transition_ledger_violation_count": totals["ledger_violation_count"],
        "wire_citation_correct_count": totals["wire_citation_correct_count"],
        "wire_citation_total_count": totals["wire_citation_total_count"],
        "wire_citation_fidelity": _rate(
            totals["wire_citation_correct_count"],
            totals["wire_citation_total_count"],
        ),
    }


def _pair_table(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {field: 0 for field in _PAIR_FIELDS}
    for row in rows:
        baseline = scenario_success(row["B2"])
        candidate = scenario_success(row["C"])
        if baseline and candidate:
            counts["both_success"] += 1
        elif candidate:
            counts["candidate_only"] += 1
        elif baseline:
            counts["baseline_only"] += 1
        else:
            counts["both_fail"] += 1
    return counts


def _pair_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pair_table = _pair_table(rows)
    b2_success = pair_table["both_success"] + pair_table["baseline_only"]
    c_success = pair_table["both_success"] + pair_table["candidate_only"]
    count = len(rows)
    return {
        "scenario_count": count,
        "b2_success_count": b2_success,
        "b2_success_rate": _rate(b2_success, count),
        "c_success_count": c_success,
        "c_success_rate": _rate(c_success, count),
        "delta": _rate(c_success - b2_success, count),
        "pair_table": pair_table,
    }


def _bootstrap_interval(
    cells: dict[str, list[dict[str, Any]]],
    *,
    resamples: int,
    confidence: float,
    binding_sha256: str,
) -> dict[str, Any]:
    if not _HEX_64.fullmatch(binding_sha256):
        raise ValueError("bootstrap binding must be a lowercase SHA-256 digest")
    if resamples < 1:
        raise ValueError("bootstrap resamples must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("bootstrap confidence must be in (0, 1)")

    seed_material = f"librarian-stratified-paired-bootstrap-v1:{binding_sha256}"
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    cell_deltas = {
        cell: [
            int(scenario_success(row["C"])) - int(scenario_success(row["B2"]))
            for row in members
        ]
        for cell, members in cells.items()
    }
    scenario_count = sum(len(values) for values in cell_deltas.values())
    samples: list[float] = []
    for _ in range(resamples):
        total = 0
        for values in cell_deltas.values():
            total += sum(values[rng.randrange(len(values))] for _ in values)
        samples.append(total / scenario_count)
    samples.sort()
    tail = (1.0 - confidence) / 2.0
    lower_index = max(0, math.floor(tail * (resamples - 1)))
    upper_index = min(resamples - 1, math.ceil((1.0 - tail) * (resamples - 1)))
    return {
        "algorithm": "stratified-paired-percentile-bootstrap-v1",
        "stratification": "scenario_type_x_provenance_pool",
        "resamples": resamples,
        "confidence": confidence,
        "lower": samples[lower_index],
        "upper": samples[upper_index],
        "binding_sha256": binding_sha256,
        "samples_sha256": stable_hash(samples),
    }


def score_private_holdout(
    rows: Iterable[dict[str, Any]],
    policy: dict[str, Any],
    *,
    bootstrap_binding_sha256: str,
) -> dict[str, Any]:
    """Compute aggregate-only paired evidence from private scenario outcomes."""

    materialized = validate_paired_rows(rows, policy)
    config = _promotion_config(policy)
    primary = config["primary_gate"]
    pools = tuple(map(str, config["provenance_pools"]))
    scenario_types = tuple(map(str, policy["dataset"]["scenario_types"]))
    cells: dict[str, list[dict[str, Any]]] = {}
    for scenario_type in scenario_types:
        for pool in pools:
            key = f"{scenario_type}:{pool}"
            cells[key] = [
                row
                for row in materialized
                if row["scenario_type"] == scenario_type
                and row["provenance_pool"] == pool
            ]

    pair_table = _pair_table(materialized)
    candidate_only = pair_table["candidate_only"]
    baseline_only = pair_table["baseline_only"]
    delta = (candidate_only - baseline_only) / len(materialized)
    bootstrap = _bootstrap_interval(
        cells,
        resamples=int(primary["bootstrap_resamples"]),
        confidence=float(primary["bootstrap_confidence"]),
        binding_sha256=bootstrap_binding_sha256,
    )
    return {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "analysis_unit": "scenario",
        "scenario_count": len(materialized),
        "pair_table": pair_table,
        "statistics": {
            "delta": delta,
            "exact_mcnemar_p": exact_mcnemar_p(candidate_only, baseline_only),
            "bootstrap": bootstrap,
        },
        "metrics": {
            policy_id: _aggregate_policy(materialized, policy_id)
            for policy_id in _POLICY_IDS
        },
        "pools": {
            pool: _pair_summary(
                [
                    row
                    for row in materialized
                    if row["provenance_pool"] == pool
                ]
            )
            for pool in pools
        },
        "cells": {
            key: _pair_summary(members) for key, members in sorted(cells.items())
        },
    }


def _validate_pair_table(value: Any, location: str, expected_count: int) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    _require_exact_keys(value, _PAIR_FIELDS, location)
    result = {
        field: _require_non_negative_int(value[field], f"{location}.{field}")
        for field in _PAIR_FIELDS
    }
    if sum(result.values()) != expected_count:
        raise ValueError(f"{location} counts do not sum to scenario_count")
    return result


def _validate_metric_aggregate(
    value: Any, location: str, expected_count: int
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    _require_exact_keys(value, _METRIC_FIELDS, location)
    integer_fields = _METRIC_FIELDS - {
        "scenario_success_rate",
        "stale_leakage_rate",
        "citation_entailment",
        "retrieval_recall_at_k",
        "state_transition_accuracy",
        "abstention_accuracy",
        "transition_ledger_integrity",
        "wire_citation_fidelity",
    }
    for field in integer_fields:
        _require_non_negative_int(value[field], f"{location}.{field}")
    for field in _METRIC_FIELDS - integer_fields:
        _require_finite_rate(value[field], f"{location}.{field}")
    if value["scenario_count"] != expected_count:
        raise ValueError(f"{location}.scenario_count does not match boundary")
    if value["stale_surface_opportunity_count"] != 2 * expected_count:
        raise ValueError(
            f"{location}.stale_surface_opportunity_count must cover answer and context"
        )
    for denominator in (
        "retrieval_required_count",
        "state_total_count",
        "abstention_total_count",
        "ledger_total_count",
        "wire_citation_total_count",
    ):
        if value[denominator] < expected_count:
            raise ValueError(f"{location}.{denominator} undercounts scenarios")

    rate_contracts = (
        ("scenario_success_rate", "scenario_success_count", "scenario_count"),
        (
            "stale_leakage_rate",
            "stale_surface_leakage_count",
            "stale_surface_opportunity_count",
        ),
        ("citation_entailment", "citation_entailed_count", "citation_count"),
        (
            "retrieval_recall_at_k",
            "retrieval_hit_count",
            "retrieval_required_count",
        ),
        (
            "state_transition_accuracy",
            "state_correct_count",
            "state_total_count",
        ),
        (
            "abstention_accuracy",
            "abstention_correct_count",
            "abstention_total_count",
        ),
        (
            "transition_ledger_integrity",
            "ledger_valid_count",
            "ledger_total_count",
        ),
        (
            "wire_citation_fidelity",
            "wire_citation_correct_count",
            "wire_citation_total_count",
        ),
    )
    for rate_field, numerator, denominator in rate_contracts:
        expected_rate = _rate(value[numerator], value[denominator])
        if not _close(float(value[rate_field]), expected_rate):
            raise ValueError(f"{location}.{rate_field} is inconsistent with counts")
    return value


def _validate_summary(value: Any, location: str, expected_count: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    _require_exact_keys(value, _SUMMARY_FIELDS, location)
    if value["scenario_count"] != expected_count:
        raise ValueError(f"{location}.scenario_count is invalid")
    b2_success = _require_non_negative_int(
        value["b2_success_count"], f"{location}.b2_success_count"
    )
    c_success = _require_non_negative_int(
        value["c_success_count"], f"{location}.c_success_count"
    )
    if b2_success > expected_count or c_success > expected_count:
        raise ValueError(f"{location} success count exceeds scenario_count")
    pair_table = _validate_pair_table(
        value["pair_table"], f"{location}.pair_table", expected_count
    )
    if b2_success != pair_table["both_success"] + pair_table["baseline_only"]:
        raise ValueError(f"{location}.b2_success_count is inconsistent")
    if c_success != pair_table["both_success"] + pair_table["candidate_only"]:
        raise ValueError(f"{location}.c_success_count is inconsistent")
    supplied_b2_rate = _require_finite_rate(
        value["b2_success_rate"], f"{location}.b2_success_rate"
    )
    supplied_c_rate = _require_finite_rate(
        value["c_success_rate"], f"{location}.c_success_rate"
    )
    if not _close(supplied_b2_rate, _rate(b2_success, expected_count)):
        raise ValueError(f"{location}.b2_success_rate is inconsistent")
    if not _close(supplied_c_rate, _rate(c_success, expected_count)):
        raise ValueError(f"{location}.c_success_rate is inconsistent")
    expected_delta = (c_success - b2_success) / expected_count
    if not _close(_require_finite_delta(value["delta"], f"{location}.delta"), expected_delta):
        raise ValueError(f"{location}.delta is inconsistent")
    return value


def validate_private_aggregate(
    aggregate: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Recompute all public aggregate relationships that do not require private rows."""

    config = _promotion_config(policy)
    expected_count = int(config["scenario_count"])
    expected_keys = {
        "schema_version",
        "analysis_unit",
        "scenario_count",
        "pair_table",
        "statistics",
        "metrics",
        "pools",
        "cells",
    }
    _require_exact_keys(aggregate, expected_keys, "aggregate")
    if aggregate["schema_version"] != AGGREGATE_SCHEMA_VERSION:
        raise ValueError("aggregate schema_version is unsupported")
    if aggregate["analysis_unit"] != "scenario":
        raise ValueError("aggregate analysis_unit must be scenario")
    if aggregate["scenario_count"] != expected_count:
        raise ValueError(f"aggregate must contain exactly {expected_count} scenarios")

    pair_table = _validate_pair_table(
        aggregate["pair_table"], "aggregate.pair_table", expected_count
    )
    metrics = aggregate["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != set(_POLICY_IDS):
        raise ValueError("aggregate.metrics must contain exactly B2 and C")
    for policy_id in _POLICY_IDS:
        _validate_metric_aggregate(
            metrics[policy_id], f"aggregate.metrics.{policy_id}", expected_count
        )
    if metrics["B2"]["scenario_success_count"] != (
        pair_table["both_success"] + pair_table["baseline_only"]
    ):
        raise ValueError("B2 success count is inconsistent with pair table")
    if metrics["C"]["scenario_success_count"] != (
        pair_table["both_success"] + pair_table["candidate_only"]
    ):
        raise ValueError("C success count is inconsistent with pair table")

    statistics = aggregate["statistics"]
    if not isinstance(statistics, dict):
        raise ValueError("aggregate.statistics must be an object")
    _require_exact_keys(statistics, {"delta", "exact_mcnemar_p", "bootstrap"}, "aggregate.statistics")
    expected_delta = (
        pair_table["candidate_only"] - pair_table["baseline_only"]
    ) / expected_count
    if not _close(
        _require_finite_delta(statistics["delta"], "aggregate.statistics.delta"),
        expected_delta,
    ):
        raise ValueError("aggregate delta is inconsistent with pair table")
    expected_p = exact_mcnemar_p(
        pair_table["candidate_only"], pair_table["baseline_only"]
    )
    supplied_p = _require_finite_rate(
        statistics["exact_mcnemar_p"], "aggregate.statistics.exact_mcnemar_p"
    )
    if not _close(supplied_p, expected_p):
        raise ValueError("exact McNemar p-value is inconsistent with pair table")

    primary = config["primary_gate"]
    bootstrap = statistics["bootstrap"]
    if not isinstance(bootstrap, dict):
        raise ValueError("aggregate.statistics.bootstrap must be an object")
    _require_exact_keys(
        bootstrap,
        {
            "algorithm",
            "stratification",
            "resamples",
            "confidence",
            "lower",
            "upper",
            "binding_sha256",
            "samples_sha256",
        },
        "aggregate.statistics.bootstrap",
    )
    if bootstrap["algorithm"] != "stratified-paired-percentile-bootstrap-v1":
        raise ValueError("bootstrap algorithm is invalid")
    if bootstrap["stratification"] != "scenario_type_x_provenance_pool":
        raise ValueError("bootstrap stratification is invalid")
    if bootstrap["resamples"] != int(primary["bootstrap_resamples"]):
        raise ValueError("bootstrap resample count does not match policy")
    supplied_confidence = _require_finite_rate(
        bootstrap["confidence"], "bootstrap.confidence"
    )
    if not _close(supplied_confidence, float(primary["bootstrap_confidence"])):
        raise ValueError("bootstrap confidence does not match policy")
    lower = _require_finite_delta(bootstrap["lower"], "bootstrap.lower")
    upper = _require_finite_delta(bootstrap["upper"], "bootstrap.upper")
    if lower > expected_delta or upper < expected_delta or lower > upper:
        raise ValueError("bootstrap interval does not contain the observed delta")
    for field in ("binding_sha256", "samples_sha256"):
        if not isinstance(bootstrap[field], str) or not _HEX_64.fullmatch(bootstrap[field]):
            raise ValueError(f"bootstrap.{field} must be a SHA-256 digest")

    pools = aggregate["pools"]
    expected_pools = tuple(map(str, config["provenance_pools"]))
    if not isinstance(pools, dict) or set(pools) != set(expected_pools):
        raise ValueError("aggregate.pools does not match policy")
    per_pool = expected_count // len(expected_pools)
    for pool in expected_pools:
        _validate_summary(pools[pool], f"aggregate.pools.{pool}", per_pool)

    scenario_types = tuple(map(str, policy["dataset"]["scenario_types"]))
    per_cell = int(config["scenarios_per_type_per_pool"])
    expected_cells = {
        f"{scenario_type}:{pool}"
        for scenario_type in scenario_types
        for pool in expected_pools
    }
    cells = aggregate["cells"]
    if not isinstance(cells, dict) or set(cells) != expected_cells:
        raise ValueError("aggregate.cells does not match the frozen matrix")
    for key in sorted(expected_cells):
        _validate_summary(cells[key], f"aggregate.cells.{key}", per_cell)

    summed_pair_table = {
        field: sum(int(cells[key]["pair_table"][field]) for key in expected_cells)
        for field in _PAIR_FIELDS
    }
    if summed_pair_table != pair_table:
        raise ValueError("cell pair tables do not sum to the aggregate pair table")
    for pool in expected_pools:
        pool_pair_table = {
            field: sum(
                int(cells[f"{scenario_type}:{pool}"]["pair_table"][field])
                for scenario_type in scenario_types
            )
            for field in _PAIR_FIELDS
        }
        if pool_pair_table != pools[pool]["pair_table"]:
            raise ValueError(f"cell pair tables do not sum to pool {pool}")
    return aggregate


def derive_private_promotion_decision(
    aggregate: dict[str, Any],
    policy: dict[str, Any],
    *,
    independence_eligible: bool,
    live_qwen_eligible: bool,
) -> dict[str, Any]:
    """Derive the public promotion decision from validated aggregate evidence."""

    validate_private_aggregate(aggregate, policy)
    config = _promotion_config(policy)
    primary = config["primary_gate"]
    safety = config["safety_gate"]
    kill = config["kill_rules"]
    candidate = aggregate["metrics"]["C"]
    delta = float(aggregate["statistics"]["delta"])
    p_value = float(aggregate["statistics"]["exact_mcnemar_p"])
    ci_lower = float(aggregate["statistics"]["bootstrap"]["lower"])

    checks = {
        "independent_external_collection": bool(independence_eligible),
        "live_qwen_gate": bool(live_qwen_eligible),
        "minimum_candidate_success_rate": candidate["scenario_success_rate"]
        >= float(primary["minimum_candidate_success_rate"]),
        "minimum_b2_success_delta": delta
        >= float(primary["minimum_b2_success_delta"]),
        "exact_mcnemar_alpha": p_value <= float(primary["exact_mcnemar_alpha"]),
        "bootstrap_ci_lower_bound": ci_lower
        > float(primary["minimum_delta_ci_lower_bound_exclusive"]),
        "positive_delta_per_pool": all(
            float(summary["delta"]) > 0.0
            for summary in aggregate["pools"].values()
        ),
        "non_negative_delta_per_cell": all(
            float(summary["delta"]) >= 0.0
            for summary in aggregate["cells"].values()
        ),
        "maximum_stale_leakage_rate": candidate["stale_leakage_rate"]
        <= float(safety["maximum_stale_leakage_rate"]),
        "maximum_valid_false_forget_count": candidate["valid_false_forget_count"]
        <= int(safety["maximum_valid_false_forget_count"]),
        "maximum_scope_false_forget_count": candidate["scope_false_forget_count"]
        <= int(safety["maximum_scope_false_forget_count"]),
        "minimum_citation_entailment": candidate["citation_entailment"]
        >= float(safety["minimum_citation_entailment"]),
        "minimum_retrieval_recall_at_k": candidate["retrieval_recall_at_k"]
        >= float(safety["minimum_retrieval_recall_at_k"]),
        "minimum_state_transition_accuracy": candidate["state_transition_accuracy"]
        >= float(safety["minimum_state_transition_accuracy"]),
        "maximum_state_violation_count": candidate["state_violation_count"]
        <= int(safety["maximum_state_violation_count"]),
        "minimum_abstention_accuracy": candidate["abstention_accuracy"]
        >= float(safety["minimum_abstention_accuracy"]),
        "minimum_transition_ledger_integrity": candidate[
            "transition_ledger_integrity"
        ]
        >= float(safety["minimum_transition_ledger_integrity"]),
        "maximum_transition_ledger_violation_count": candidate[
            "transition_ledger_violation_count"
        ]
        <= int(safety["maximum_transition_ledger_violation_count"]),
        "minimum_wire_citation_fidelity": candidate["wire_citation_fidelity"]
        >= float(safety["minimum_wire_citation_fidelity"]),
    }
    kill_findings: list[str] = []
    if bool(kill["kill_when_candidate_does_not_beat_b2"]) and delta <= 0.0:
        kill_findings.append("candidate_does_not_beat_b2")
    if candidate["stale_leakage_rate"] > float(
        kill["maximum_stale_leakage_rate"]
    ):
        kill_findings.append("stale_leakage_kill_threshold_exceeded")
    if candidate["valid_false_forget_count"] > int(
        kill["maximum_valid_false_forget_count"]
    ):
        kill_findings.append("valid_claim_false_forgetting")

    hold_findings = sorted(name for name, passed in checks.items() if not passed)
    if not independence_eligible:
        gate_status = "NOT_ELIGIBLE_EXTERNAL_INDEPENDENCE_UNPROVEN"
        promotion_status = "NOT_ELIGIBLE"
    elif kill_findings:
        gate_status = "PRIVATE_HOLDOUT_V2_KILL"
        promotion_status = "KILL"
    elif hold_findings:
        gate_status = "PRIVATE_HOLDOUT_V2_HOLD"
        promotion_status = "HOLD"
    else:
        gate_status = "PRIVATE_HOLDOUT_V2_PROMOTION_PASS"
        promotion_status = "PROMOTE"
    return {
        "gate_status": gate_status,
        "promotion_status": promotion_status,
        "checks": checks,
        "hold_findings": hold_findings,
        "kill_findings": sorted(kill_findings),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-results", type=Path, required=True)
    parser.add_argument("--dataset-manifest-sha256", required=True)
    parser.add_argument("--candidate-tree-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("eval/policy.json"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    policy = load_json(args.policy)
    binding = promotion_binding_sha256(
        dataset_manifest_sha256=args.dataset_manifest_sha256,
        paired_results_sha256=file_sha256(args.paired_results),
        candidate_tree_sha256=args.candidate_tree_sha256,
        policy_sha256=file_sha256(args.policy),
    )
    aggregate = score_private_holdout(
        load_jsonl(args.paired_results),
        policy,
        bootstrap_binding_sha256=binding,
    )
    write_json(args.output, aggregate)
    print(f"scenario_count={aggregate['scenario_count']}")
    print(f"delta={aggregate['statistics']['delta']}")
    print(f"exact_mcnemar_p={aggregate['statistics']['exact_mcnemar_p']}")
    print(f"aggregate={args.output}")


if __name__ == "__main__":
    main()

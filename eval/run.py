"""Run memory policies without access to gold labels."""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import subprocess
import time
from typing import Any

from . import HARNESS_VERSION
from .baselines import PolicyAdapter, make_builtin_adapter
from .contracts import (
    SCHEMA_VERSION,
    assert_oracle_free,
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
ALLOWED_CANDIDATE_FACTORY = "src.librarian.eval_adapter:create_adapter"
_PRIMARY_POLICIES = ("B0", "B1", "B2", "C")


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load_factory(
    spec: str, policy_config: dict[str, Any]
) -> tuple[PolicyAdapter, dict[str, str]]:
    if spec != ALLOWED_CANDIDATE_FACTORY:
        raise ValueError(
            "candidate factory is not allowlisted; use "
            f"{ALLOWED_CANDIDATE_FACTORY}"
        )
    if ":" not in spec:
        raise ValueError("candidate factory must use module:function syntax")
    module_name, function_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    module_path = Path(str(getattr(module, "__file__", ""))).resolve()
    try:
        module_path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("candidate factory must resolve inside the repository") from exc
    factory = getattr(module, function_name)
    adapter = factory(policy_id="C", policy_config=policy_config)
    if not hasattr(adapter, "run_case"):
        raise TypeError("candidate factory must return an object with run_case()")
    return adapter, {
        "spec": spec,
        "module_path": module_path.relative_to(ROOT).as_posix(),
        "module_sha256": file_sha256(module_path),
        "adapter_class": (
            f"{type(adapter).__module__}.{type(adapter).__qualname__}"
        ),
    }


def _candidate_execution_receipt(
    rows: list[dict[str, Any]], factory_receipt: dict[str, str]
) -> dict[str, Any]:
    """Bind the factory fingerprint to the rows actually returned by candidate C."""

    candidate_rows = [row for row in rows if str(row.get("policy_id")) == "C"]
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


def _validate_extraction_contract(extractions: list[dict[str, Any]]) -> None:
    for scenario in extractions:
        queries = scenario.get("queries")
        if not isinstance(queries, dict):
            raise ValueError("extraction scenario queries must be an object")
        for checkpoint_id, query in queries.items():
            if not isinstance(query, dict) or set(query) != {"terms"}:
                raise ValueError(
                    f"query extraction {checkpoint_id} must contain only terms"
                )
            terms = query.get("terms")
            if (
                not isinstance(terms, list)
                or not terms
                or any(not isinstance(term, str) or not term.strip() for term in terms)
            ):
                raise ValueError(f"query extraction {checkpoint_id} has invalid terms")


def run_policies(
    *,
    cases_path: str | Path,
    extractions_path: str | Path,
    output_path: str | Path,
    policy_path: str | Path = DEFAULT_POLICY,
    policies: list[str] | None = None,
    repeats: int = 3,
    candidate_factory: str | None = None,
    dataset_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    config = load_json(policy_path)
    selected_policies = policies or list(config["primary_lane"]["policies"])
    unknown = sorted(set(selected_policies) - {"B0", "B1", "B2", "C"})
    if unknown:
        raise ValueError(f"unknown policies: {', '.join(unknown)}")

    cases = load_jsonl(cases_path)
    extractions = load_jsonl(extractions_path)
    assert_oracle_free(cases, location=str(cases_path))
    assert_oracle_free(extractions, location=str(extractions_path))
    _validate_extraction_contract(extractions)
    cases_by_id = {row["scenario_id"]: row for row in cases}
    extraction_by_id = {row["scenario_id"]: row for row in extractions}
    if len(cases_by_id) != len(cases) or len(extraction_by_id) != len(extractions):
        raise ValueError("scenario_id values must be unique")
    if set(cases_by_id) != set(extraction_by_id):
        raise ValueError("case and extraction scenario IDs differ")

    inferred_manifest = Path(cases_path).with_name("dataset-manifest.json")
    if not inferred_manifest.exists():
        inferred_manifest = Path(cases_path).parent.parent / "dataset-manifest.json"
    dataset_manifest_file = (
        Path(dataset_manifest_path)
        if dataset_manifest_path is not None
        else inferred_manifest
    )
    if not dataset_manifest_file.is_file():
        raise ValueError("dataset manifest is required and was not found")
    dataset_manifest = load_json(dataset_manifest_file)
    current_tree_hash = candidate_tree_hash(ROOT)
    expected_hashes = dataset_manifest.get("hashes")
    if not isinstance(expected_hashes, dict):
        raise ValueError("dataset manifest hashes are required")
    expected_actual = {
        "cases_sha256": file_sha256(cases_path),
        "extractions_sha256": file_sha256(extractions_path),
        "policy_sha256": file_sha256(policy_path),
    }
    for key, actual in expected_actual.items():
        if expected_hashes.get(key) != actual:
            raise ValueError(f"{key} does not match dataset manifest")
    if dataset_manifest.get("split") not in {"dev", "holdout"}:
        raise ValueError("dataset manifest split must be dev or holdout")
    if int(dataset_manifest.get("scenario_count", -1)) != len(cases):
        raise ValueError("scenario count does not match dataset manifest")
    frozen_tree = (dataset_manifest.get("candidate_snapshot") or {}).get(
        "tree_sha256"
    )
    if not frozen_tree or frozen_tree != current_tree_hash:
        raise ValueError(
            "candidate tree changed after dataset materialization; regenerate dataset"
        )

    if candidate_factory:
        if selected_policies == ["C"]:
            lane = "production_conformance"
        elif tuple(selected_policies) == _PRIMARY_POLICIES:
            lane = "production_policy_comparison"
        else:
            raise ValueError(
                "candidate factory requires either only policy C or the exact "
                "B0/B1/B2/C primary comparison lane"
            )
    elif tuple(selected_policies) == _PRIMARY_POLICIES:
        lane = "policy_comparison"
    else:
        lane = "diagnostic"

    answer_calls = sum(len(case["checkpoints"]) for case in cases)
    answer_calls *= repeats * len(selected_policies)
    budget = config["run_budget"]
    if answer_calls > int(budget["maximum_answer_calls"]):
        raise ValueError(
            f"planned answer calls {answer_calls} exceed budget "
            f"{budget['maximum_answer_calls']}"
        )

    policy_config = config["primary_lane"]["shared_conditions"]
    adapters: dict[str, PolicyAdapter] = {
        policy_id: make_builtin_adapter(policy_id, policy_config)
        for policy_id in selected_policies
    }
    factory_receipt: dict[str, str] | None = None
    if candidate_factory and "C" in adapters:
        adapters["C"], factory_receipt = _load_factory(
            candidate_factory, policy_config
        )

    started_at = utc_now()
    start_clock = time.monotonic()
    run_id = stable_hash(
        {
            "started_at": started_at,
            "cases": file_sha256(cases_path),
            "extractions": file_sha256(extractions_path),
            "dataset_manifest": file_sha256(dataset_manifest_file),
            "policy": file_sha256(policy_path),
            "policies": selected_policies,
            "repeats": repeats,
            "candidate_factory": factory_receipt,
        }
    )[:20]
    rows: list[dict[str, Any]] = []
    total_tokens = 0
    status = "COMPLETE"
    stop_reason: str | None = None
    for repeat in range(repeats):
        for policy_id in selected_policies:
            adapter = adapters[policy_id]
            for scenario_id in sorted(cases_by_id):
                generated = adapter.run_case(
                    case=cases_by_id[scenario_id],
                    extraction=extraction_by_id[scenario_id],
                    repeat=repeat,
                )
                expected_checkpoints = {
                    checkpoint["checkpoint_id"]
                    for checkpoint in cases_by_id[scenario_id]["checkpoints"]
                }
                actual_checkpoints = {
                    str(result.get("checkpoint_id", "")) for result in generated
                }
                if len(actual_checkpoints) != len(generated):
                    raise ValueError(
                        f"adapter {policy_id} returned duplicate checkpoints for {scenario_id}"
                    )
                if actual_checkpoints != expected_checkpoints:
                    raise ValueError(
                        f"adapter {policy_id} checkpoint set mismatch for {scenario_id}"
                    )
                for result in generated:
                    if str(result.get("scenario_id")) != scenario_id:
                        raise ValueError(
                            f"adapter {policy_id} returned wrong scenario_id"
                        )
                    row = {
                        "schema_version": SCHEMA_VERSION,
                        "run_id": run_id,
                        "repeat": repeat,
                        "policy_id": policy_id,
                        **result,
                    }
                    validate_output_row(row)
                    rows.append(row)
                    total_tokens += int(row["trace"].get("total_tokens", 0))
                    if total_tokens > int(budget["maximum_total_tokens"]):
                        status = "INCOMPLETE"
                        stop_reason = "maximum_total_tokens"
                        break
                    if time.monotonic() - start_clock > int(
                        budget["maximum_wall_seconds"]
                    ):
                        status = "INCOMPLETE"
                        stop_reason = "maximum_wall_seconds"
                        break
                if status != "COMPLETE":
                    break
            if status != "COMPLETE":
                break
        if status != "COMPLETE":
            break

    output = Path(output_path)
    write_jsonl(output, rows)
    cases_receipt_path = output.with_name("cases.jsonl")
    transitions_path = output.with_name("transitions.jsonl")
    write_jsonl(cases_receipt_path, cases)
    transition_rows: list[dict[str, Any]] = []
    transition_keys: set[str] = set()
    for row in rows:
        for transition in row["transitions"]:
            receipt = {
                "run_id": run_id,
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
            transition_rows.append(receipt)
    write_jsonl(transitions_path, transition_rows)
    candidate_execution = (
        _candidate_execution_receipt(rows, factory_receipt)
        if factory_receipt is not None
        else None
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "harness_version": HARNESS_VERSION,
        "run_id": run_id,
        "status": status,
        "stop_reason": stop_reason,
        "started_at": started_at,
        "completed_at": utc_now(),
        "git_commit": _git_commit(),
        "candidate_tree_sha256": current_tree_hash,
        "candidate_tree_matches_dataset": True,
        "split": dataset_manifest["split"],
        "lane": lane,
        "policies": selected_policies,
        "repeats": repeats,
        "repeat_semantics": "deterministic_replay_not_independent_samples",
        "candidate_factory": factory_receipt,
        "candidate_execution": candidate_execution,
        "scenario_count": len(cases),
        "answer_calls_planned": answer_calls,
        "answer_rows_written": len(rows),
        "total_tokens": total_tokens,
        "hashes": {
            "cases_sha256": file_sha256(cases_path),
            "extractions_sha256": file_sha256(extractions_path),
            "policy_sha256": file_sha256(policy_path),
            "outputs_sha256": file_sha256(output),
            "cases_receipt_sha256": file_sha256(cases_receipt_path),
            "transitions_sha256": file_sha256(transitions_path),
            "dataset_manifest_sha256": file_sha256(dataset_manifest_file),
        },
        "gold_path_not_passed": True,
        "runner_process_isolation": False,
        "dataset_manifest": {
            "path": str(dataset_manifest_file),
            "sha256": file_sha256(dataset_manifest_file),
            "seed_commitment": dataset_manifest.get("seed_commitment"),
            "split": dataset_manifest["split"],
        },
    }
    manifest_path = output.with_name("run-manifest.json")
    write_json(manifest_path, manifest)
    write_json(output.with_name("manifest.json"), manifest)
    if status != "COMPLETE":
        raise RuntimeError(f"run stopped: {stop_reason}; partial receipt at {manifest_path}")
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--extractions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--policies", nargs="+", default=["B0", "B1", "B2", "C"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dataset-manifest", type=Path)
    parser.add_argument(
        "--candidate-factory",
        help="optional module:function factory replacing policy C; it never receives gold",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    manifest = run_policies(
        cases_path=args.cases,
        extractions_path=args.extractions,
        output_path=args.output,
        policy_path=args.policy,
        policies=args.policies,
        repeats=args.repeats,
        candidate_factory=args.candidate_factory,
        dataset_manifest_path=args.dataset_manifest,
    )
    print(f"run_id={manifest['run_id']}")
    print(f"status={manifest['status']}")
    print(f"outputs={args.output}")


if __name__ == "__main__":
    main()

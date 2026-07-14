"""Materialize deterministic repository-owned diagnostic artifacts.

Neither split is independent promotion evidence.  The holdout option changes
values through a secret seed but deliberately retains the repository-owned
collection recipe, so its manifest is always diagnostic-only.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
from typing import Any

from . import HARNESS_VERSION
from .contracts import (
    candidate_tree_hash,
    file_sha256,
    load_json,
    seed_commitment,
    utc_now,
    write_json,
    write_jsonl,
)
from .scenarios import build_dataset


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "eval" / "policy.json"
DEFAULT_DEV_RECIPE = ROOT / "eval" / "dev" / "recipe.json"


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


def materialize(
    *,
    split: str,
    output_dir: str | Path,
    policy_path: str | Path = DEFAULT_POLICY,
    seed: str | None = None,
) -> dict[str, Any]:
    policy = load_json(policy_path)
    if split == "dev":
        recipe = load_json(DEFAULT_DEV_RECIPE)
        actual_seed = str(recipe["seed"])
        variants = int(recipe["variants_per_type"])
        distractors = int(recipe["distractor_count"])
    elif split == "holdout":
        actual_seed = seed if seed is not None else os.getenv("HOLDOUT_SEED", "")
        if not actual_seed:
            raise ValueError("HOLDOUT_SEED is required for holdout materialization")
        # Validate without persisting or logging the secret.
        seed_commitment(actual_seed)
        variants = int(policy["dataset"]["holdout_variants_per_type"])
        distractors = int(policy["dataset"]["distractor_count"])
    else:
        raise ValueError("split must be dev or holdout")

    cases, extractions, gold = build_dataset(
        seed=actual_seed,
        variants_per_type=variants,
        distractor_count=distractors,
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    runner_dir = destination / "runner-inputs"
    evaluator_dir = destination / "evaluator-only"
    cases_path = runner_dir / "cases.jsonl"
    extractions_path = runner_dir / "extractions.jsonl"
    gold_path = evaluator_dir / "gold.jsonl"
    write_jsonl(cases_path, cases)
    write_jsonl(extractions_path, extractions)
    write_jsonl(gold_path, gold)

    manifest = {
        "schema_version": "1.0",
        "harness_version": HARNESS_VERSION,
        "created_at": utc_now(),
        "split": split,
        "evidence_role": (
            "public_dev_regression"
            if split == "dev"
            else "same_builder_diagnostic_only"
        ),
        "promotion_eligible": False,
        "collection_provenance": "repository_scenario_builders_v1",
        "scenario_count": len(cases),
        "variants_per_type": variants,
        "seed_commitment": seed_commitment(actual_seed),
        "seed_stored": False,
        "hashes": {
            "cases_sha256": file_sha256(cases_path),
            "extractions_sha256": file_sha256(extractions_path),
            "gold_sha256": file_sha256(gold_path),
            "policy_sha256": file_sha256(policy_path),
        },
        "candidate_snapshot": {
            "git_commit": _git_commit(),
            "tree_sha256": candidate_tree_hash(ROOT),
        },
        "boundary": {
            "runner_inputs": [
                "runner-inputs/cases.jsonl",
                "runner-inputs/extractions.jsonl"
            ],
            "evaluator_inputs": [
                "runner-inputs/cases.jsonl",
                "evaluator-only/gold.jsonl",
                "outputs.jsonl"
            ],
            "runner_must_not_receive": ["evaluator-only/gold.jsonl"],
        },
    }
    write_json(destination / "dataset-manifest.json", manifest)
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--commitment-only",
        action="store_true",
        help="print SHA-256 commitment for HOLDOUT_SEED without materializing data",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.commitment_only:
        seed = os.getenv("HOLDOUT_SEED", "")
        if not seed:
            raise SystemExit("HOLDOUT_SEED is required")
        print(seed_commitment(seed))
        return
    if args.output_dir is None:
        raise SystemExit("--output-dir is required unless --commitment-only is used")
    manifest = materialize(
        split=args.split,
        output_dir=args.output_dir,
        policy_path=args.policy,
    )
    print(f"materialized {manifest['scenario_count']} {args.split} scenarios")
    print(f"seed_commitment={manifest['seed_commitment']}")
    print(f"manifest={args.output_dir / 'dataset-manifest.json'}")


if __name__ == "__main__":
    main()

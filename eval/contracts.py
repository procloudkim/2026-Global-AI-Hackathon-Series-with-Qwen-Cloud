"""Shared file contracts for the evaluation harness.

The runner imports this module, but never imports the scenario oracle or evaluator.
Keeping the helpers free of product code also lets an external candidate adapter use
the same wire contract without gaining access to gold labels.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"

_ORACLE_KEYS = {
    "expected_facts",
    "forbidden_facts",
    "required_sources",
    "required_retrieval_sources",
    "expected_states",
    "protected_facts",
    "must_abstain",
    "scenario_type",
    "gold",
    "oracle",
    "promotion",
    # Semantic lifecycle labels are not gold answers, but exposing them in the
    # frozen extraction snapshot would let the candidate replay the generator's
    # decision instead of inferring the relationship from source evidence.
    "relation",
    "target_source_id",
    "claim_ref",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_commitment(seed: str) -> str:
    if len(seed.strip()) < 16:
        raise ValueError("seed must contain at least 16 non-whitespace characters")
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _canonical_source_bytes(path: Path) -> bytes:
    """Return text bytes with platform-specific line endings normalized."""
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def candidate_tree_hash(root: str | Path) -> str:
    """Hash every executable/config artifact that can affect a receipt.

    Private datasets, generated runs, and the independent private-promotion
    evaluator are excluded.  The private evaluator must never enter the live
    candidate image; its policy and evaluation artifacts are hashed by their
    own contracts.  The policy is hashed separately because changing a gate
    must invalidate both dataset and run manifests with an explicit,
    reviewable mismatch.
    """
    repository = Path(root)
    digest = hashlib.sha256()
    files: list[Path] = []
    for base in (repository / "src" / "librarian", repository / "eval"):
        if base.exists():
            files.extend(path for path in base.rglob("*.py") if path.is_file())
    for path in sorted(files):
        relative = path.relative_to(repository).as_posix()
        if (
            relative.startswith("eval/private/")
            or relative.startswith("eval/runs/")
            or relative == "eval/private_promotion.py"
        ):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_source_bytes(path))
        digest.update(b"\0")
    for relative in ("pyproject.toml", "uv.lock"):
        path = repository / relative
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_source_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"expected object at {path}:{line_number}")
        rows.append(row)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical_json(row) + "\n" for row in rows)
    destination.write_text(payload, encoding="utf-8")


def assert_oracle_free(value: Any, *, location: str = "runner input") -> None:
    """Fail closed if labels or promotion fields cross into the runner boundary."""

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            leaked = sorted(set(node) & _ORACLE_KEYS)
            if leaked:
                raise ValueError(
                    f"oracle field(s) {', '.join(leaked)} found in {location} at {path}"
                )
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value, "$")


def validate_output_row(row: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "run_id",
        "repeat",
        "policy_id",
        "scenario_id",
        "checkpoint_id",
        "answer",
        "facts",
        "citations",
        "abstained",
        "memory_state",
        "transitions",
        "trace",
    }
    missing = sorted(required - set(row))
    if missing:
        raise ValueError(f"output row missing fields: {', '.join(missing)}")
    unknown = sorted(set(row) - required)
    if unknown:
        raise ValueError(f"output row has unknown fields: {', '.join(unknown)}")
    for field in ("schema_version", "run_id", "policy_id", "scenario_id", "checkpoint_id", "answer"):
        if not isinstance(row[field], str) or not row[field]:
            raise ValueError(f"output {field} must be a non-empty string")
    if not isinstance(row["repeat"], int) or isinstance(row["repeat"], bool):
        raise ValueError("output repeat must be an integer")
    if not isinstance(row["abstained"], bool):
        raise ValueError("output abstained must be a boolean")
    if not isinstance(row["facts"], list) or not isinstance(row["citations"], list):
        raise ValueError("facts and citations must be arrays")
    if not isinstance(row["memory_state"], list) or not isinstance(
        row["transitions"], list
    ):
        raise ValueError("memory_state and transitions must be arrays")
    if not isinstance(row["trace"], dict):
        raise ValueError("trace must be an object")
    for fact in row["facts"]:
        if not isinstance(fact, dict) or set(fact) != {"key", "value", "claim_ids"}:
            raise ValueError("each fact must use the key/value/claim_ids contract")
        if any(
            not isinstance(fact[field], str) or not fact[field]
            for field in ("key", "value")
        ):
            raise ValueError("fact key and value must be non-empty strings")
        if (
            not isinstance(fact["claim_ids"], list)
            or not fact["claim_ids"]
            or any(
                not isinstance(claim_id, str) or not claim_id
                for claim_id in fact["claim_ids"]
            )
        ):
            raise ValueError("fact claim_ids must contain non-empty strings")
    if any(not isinstance(item, str) or not item for item in row["citations"]):
        raise ValueError("citations must contain non-empty strings")
    for state in row["memory_state"]:
        if not isinstance(state, dict) or set(state) not in (
            {"key", "value", "status", "source_ids"},
            {"claim_id", "key", "value", "status", "source_ids"},
        ):
            raise ValueError("memory_state record has invalid fields")
        if any(
            not isinstance(state.get(field), str) or not state.get(field)
            for field in ("key", "value", "status")
        ) or state["status"] not in {"active", "disputed", "superseded", "archived"}:
            raise ValueError("memory_state record has invalid values")
        if "claim_id" in state and (
            not isinstance(state["claim_id"], str) or not state["claim_id"]
        ):
            raise ValueError("memory_state claim_id must be non-empty")
        if not isinstance(state["source_ids"], list) or any(
            not isinstance(source_id, str) or not source_id
            for source_id in state["source_ids"]
        ):
            raise ValueError("memory_state source_ids must contain strings")
    if row["answer"] != render_answer(row["facts"], bool(row["abstained"])):
        raise ValueError("answer must use the shared deterministic fact serializer")


def render_answer(facts: list[dict[str, Any]], abstained: bool) -> str:
    """Render the one shared answer surface used by every memory policy."""
    if abstained:
        if facts:
            raise ValueError("an abstention cannot contain facts")
        return "Insufficient supported memory."
    if not facts:
        raise ValueError("a non-abstaining answer must contain facts")
    rendered: list[str] = []
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("facts must contain objects")
        key = str(fact.get("key", "")).strip()
        value = str(fact.get("value", "")).strip()
        if not key or not value:
            raise ValueError("facts require non-empty key and value")
        rendered.append(f"{key} = {value}.")
    return " ".join(rendered)

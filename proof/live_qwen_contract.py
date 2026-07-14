"""External, fail-closed live-Qwen proof runner.

This file deliberately lives outside ``src/librarian`` and ``eval`` so adding or
hardening the proof runner does not change the frozen candidate-tree hash.  The
runner never accepts a gold path.  Its ``evaluate`` command is a separate process
that reuses the frozen deterministic scorer after the runner has exited.

Promotion is intentionally impossible here.  The strongest status emitted by
this v1 contract is ``LIVE_QWEN_2CASE_PASS``; a 24-case live comparison still
requires an externally attested evaluator and a fair live B2/C lane.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter
from typing import Any, Iterable
from urllib.parse import urlparse


CONTRACT_VERSION = "proof-first-live-qwen/v1"
SCHEMA_VERSION = "1.0"
ORACLE_KEYS = {
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
    "relation",
    "target_source_id",
    "claim_ref",
}


class ContractError(RuntimeError):
    """A fail-closed proof-contract violation."""


def _repo_root() -> Path | None:
    candidates = [Path(__file__).resolve().parents[1], Path.cwd()]
    for candidate in candidates:
        if (candidate / "src" / "librarian").is_dir():
            return candidate
    return None


ROOT = _repo_root()
if ROOT is not None:
    root_path = str(ROOT)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    source_path = str(ROOT / "src")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _run_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{secrets.token_hex(4)}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_source_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _candidate_tree_hash(root: str | Path) -> str:
    """Recompute the frozen candidate hash inside the actual runner tree."""
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
            raise ContractError(f"candidate artifact missing in runner: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_source_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def _write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def _write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_canonical_json(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ContractError(f"expected object at {path}:{line_number}")
        rows.append(value)
    return rows


def _assert_oracle_free(value: Any, location: str) -> None:
    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            leaked = sorted(set(node) & ORACLE_KEYS)
            if leaked:
                raise ContractError(
                    f"oracle field(s) {', '.join(leaked)} found in {location} at {path}"
                )
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value, "$")


def _assert_no_gold_visible() -> None:
    candidates = [
        Path("/app/eval/private"),
        Path("/app/evaluator-only"),
        Path("/runner-inputs/gold.jsonl"),
        Path("/gold.jsonl"),
    ]
    visible = [str(path) for path in candidates if path.exists()]
    if visible:
        raise ContractError(f"gold path visible to runner: {visible}")


def _prepare_output_dir(path: str | Path) -> Path:
    output = Path(path)
    if output.exists() and any(output.iterdir()):
        raise ContractError(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _redact(text: str) -> str:
    redacted = text
    for name in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        secret = os.environ.get(name, "")
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted[:4000]


@dataclass(frozen=True)
class Budget:
    max_calls: int
    max_total_tokens: int
    max_requested_completion_tokens: int
    max_seconds: int
    max_provider_errors: int
    max_user_chars: int
    timeout_seconds: float


class RecordingRouter:
    """Record every real provider call while enforcing hard stop limits."""

    def __init__(self, raw_path: Path, budget: Budget) -> None:
        from librarian.llm import ModelRouter

        self._inner = ModelRouter()
        if not hasattr(self._inner._client, "with_options"):
            raise ContractError("provider client cannot enforce retry/timeout contract")
        self._inner._client = self._inner._client.with_options(
            timeout=budget.timeout_seconds,
            max_retries=0,
        )
        self._raw_path = raw_path
        self._budget = budget
        self._started = monotonic()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.provider_errors = 0
        self.requested_completion_tokens = 0
        self.models: set[str] = set()
        self.total_latency_ms = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def chat(
        self,
        tier: Any,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Any:
        if self.calls >= self._budget.max_calls:
            raise ContractError("provider call budget exhausted")
        if monotonic() - self._started >= self._budget.max_seconds:
            raise ContractError("provider time budget exhausted")
        if len(user) > self._budget.max_user_chars:
            raise ContractError("provider user-payload budget exhausted")
        if max_tokens is None:
            raise ContractError("every provider call requires a completion cap")
        if (
            self.requested_completion_tokens + max_tokens
            > self._budget.max_requested_completion_tokens
        ):
            raise ContractError("requested completion-token budget exhausted")
        call_index = self.calls + 1
        started_at = _utc_now()
        started = perf_counter()
        self.calls += 1
        self.requested_completion_tokens += max_tokens
        try:
            result = self._inner.chat(
                tier,
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            self.total_latency_ms += latency_ms
            self.provider_errors += 1
            _append_jsonl(
                self._raw_path,
                {
                    "call_index": call_index,
                    "completed_at": _utc_now(),
                    "error_class": type(exc).__name__,
                    "error_message": _redact(str(exc)),
                    "latency_ms": latency_ms,
                    "max_tokens": max_tokens,
                    "status": "provider_error",
                    "system": system,
                    "system_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                    "temperature": temperature,
                    "tier": str(getattr(tier, "value", tier)),
                    "user": user,
                    "user_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
                    "started_at": started_at,
                },
            )
            if self.provider_errors >= self._budget.max_provider_errors:
                raise ContractError("provider error stop threshold reached") from exc
            raise
        latency_ms = int((perf_counter() - started) * 1000)
        self.total_latency_ms += latency_ms
        prompt_tokens = int(getattr(result, "prompt_tokens", 0))
        completion_tokens = int(getattr(result, "completion_tokens", 0))
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        model = str(getattr(result, "model", "unknown"))
        self.models.add(model)
        _append_jsonl(
            self._raw_path,
            {
                "call_index": call_index,
                "completed_at": _utc_now(),
                "completion_tokens": completion_tokens,
                "latency_ms": latency_ms,
                "max_tokens": max_tokens,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "response_text": str(getattr(result, "text", "")),
                "status": (
                    "ok" if prompt_tokens + completion_tokens > 0 else "usage_missing"
                ),
                "system": system,
                "system_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                "temperature": temperature,
                "tier": str(getattr(tier, "value", tier)),
                "total_tokens": prompt_tokens + completion_tokens,
                "user": user,
                "user_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
                "started_at": started_at,
            },
        )
        if prompt_tokens + completion_tokens <= 0:
            raise ContractError("provider returned no token usage")
        if self.total_tokens > self._budget.max_total_tokens:
            raise ContractError("provider token budget exhausted")
        return result


def _forward_result(
    *,
    test_id: str,
    wrapper: str,
    runtime_identity: str,
    raw_path: str,
    decision: str,
    failure_class: str,
    source_restoration: str,
    claim_ledger: str,
    safety_boundary: str,
) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "wrapper": wrapper,
        "evidence_mode": "live_runtime",
        "runtime_session_id": test_id,
        "runtime_identity": runtime_identity,
        "validator_availability": "available",
        "raw_output_archive_path": raw_path,
        "runtime_proof_source": "actual_runtime_archive",
        "ambiguity_gate": "hold",
        "source_restoration": source_restoration,
        "claim_ledger": claim_ledger,
        "safety_boundary": safety_boundary,
        "decision": decision,
        "failure_class": failure_class,
        "promoted": False,
    }


def _runtime_identity() -> str:
    configured = os.environ.get("LIVE_RUNTIME_ID", "").strip()
    if configured:
        return configured
    return f"host:{platform.system()}:{platform.machine()}:{platform.python_version()}"


def _budget_dict(budget: Budget) -> dict[str, int]:
    return {
        "max_calls": budget.max_calls,
        "max_requested_completion_tokens": budget.max_requested_completion_tokens,
        "max_total_tokens": budget.max_total_tokens,
        "max_seconds": budget.max_seconds,
        "max_provider_errors": budget.max_provider_errors,
        "max_user_chars": budget.max_user_chars,
        "timeout_seconds": budget.timeout_seconds,
        "max_retries": 0,
    }


def _usage_dict(router: RecordingRouter) -> dict[str, Any]:
    return {
        "calls": router.calls,
        "completion_tokens": router.completion_tokens,
        "models": sorted(router.models),
        "prompt_tokens": router.prompt_tokens,
        "provider_errors": router.provider_errors,
        "requested_completion_tokens": router.requested_completion_tokens,
        "total_latency_ms": router.total_latency_ms,
        "total_tokens": router.total_tokens,
    }


def _runtime_metadata() -> dict[str, Any]:
    from importlib.metadata import version

    from librarian.config import get_settings

    settings = get_settings()
    return {
        "base_url": settings.base_url,
        "base_url_host": urlparse(settings.base_url).hostname or "unknown",
        "heavy_model_configured": settings.heavy_model,
        "light_model_configured": settings.light_model,
        "openai_version": version("openai"),
        "python_version": platform.python_version(),
        "transport_max_retries": 0,
    }


def connectivity(args: argparse.Namespace) -> int:
    output = _prepare_output_dir(args.output_dir)
    raw_path = output / "raw-calls.jsonl"
    run_id = _run_id("live-connectivity")
    budget = Budget(1, 200, 8, 60, 1, 2000, 30.0)
    router: RecordingRouter | None = None
    status = "LIVE_CONNECTIVITY_FAIL"
    failure_class = "none"
    response_text = ""
    try:
        _assert_no_gold_visible()
        if ROOT is None or _candidate_tree_hash(ROOT) != args.candidate_hash:
            raise ContractError("connectivity candidate hash mismatch")
        from librarian.llm import Tier

        router = RecordingRouter(raw_path, budget)
        result = router.chat(
            Tier.LIGHT,
            system="You are a health check. Reply with exactly: pong",
            user="ping",
            temperature=0.0,
            max_tokens=8,
        )
        response_text = str(result.text).strip()
        if response_text != "pong":
            raise ContractError("provider response was not exact pong")
        if result.total_tokens <= 0:
            raise ContractError("provider returned no token usage")
        status = "LIVE_CONNECTIVITY_ONLY_PASS"
    except Exception as exc:
        failure_class = type(exc).__name__
    usage = _usage_dict(router) if router is not None else {}
    raw_hash = _file_sha256(raw_path) if raw_path.is_file() else "unavailable"
    receipt = {
        "contract_version": CONTRACT_VERSION,
        "stage": "connectivity",
        "status": status,
        "candidate_tree_sha256": args.candidate_hash,
        "created_at": _utc_now(),
        "budget": _budget_dict(budget),
        "usage": usage,
        "runtime": _runtime_metadata() if router is not None else {},
        "response_sha256": hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
        "raw_archive_sha256": raw_hash,
        "runner_sha256": _file_sha256(__file__),
        "forward_test_result": _forward_result(
            test_id=run_id,
            wrapper="proof.live_qwen_contract:connectivity",
            runtime_identity=_runtime_identity(),
            raw_path="raw-calls.jsonl" if raw_path.is_file() else "unavailable",
            decision="hold" if status.endswith("PASS") else "fail",
            failure_class=failure_class,
            source_restoration="hold",
            claim_ledger="hold",
            safety_boundary="hold" if raw_path.is_file() else "fail",
        ),
        "promotion_status": "HOLD",
        "promoted": False,
    }
    _write_json(output / "receipt.json", receipt)
    print(f"status={status}")
    print(f"receipt={output / 'receipt.json'}")
    return 0 if status.endswith("PASS") else 2


def _render_answer(facts: list[dict[str, Any]], abstained: bool) -> str:
    if abstained:
        if facts:
            raise ContractError("abstention cannot contain facts")
        return "Insufficient supported memory."
    if not facts:
        raise ContractError("non-abstaining answer requires facts")
    return " ".join(
        f"{str(fact['key']).strip()} = {str(fact['value']).strip()}."
        for fact in facts
    )


def _memory_state(store: Any) -> list[dict[str, Any]]:
    from librarian.claims import Claim

    state: list[dict[str, Any]] = []
    for page in store.list_wiki_pages():
        for raw in store.claims_for_page(page):
            claim = Claim.from_dict(raw)
            state.append(
                {
                    "claim_id": claim.claim_id,
                    "key": claim.key,
                    "value": claim.value,
                    "status": claim.status.value,
                    "source_ids": list(claim.source_ids),
                }
            )
    return state


def _validate_dataset_boundary(
    cases_path: Path,
    manifest_path: Path,
    candidate_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = _load_jsonl(cases_path)
    for case in cases:
        _assert_oracle_free(case, "cases")
    manifest = _load_json(manifest_path)
    frozen = str((manifest.get("candidate_snapshot") or {}).get("tree_sha256", ""))
    if frozen != candidate_hash:
        raise ContractError("candidate hash differs from frozen dataset")
    expected_cases = str((manifest.get("hashes") or {}).get("cases_sha256", ""))
    if expected_cases != _file_sha256(cases_path):
        raise ContractError("cases hash differs from dataset manifest")
    return cases, manifest


def run_live(args: argparse.Namespace) -> int:
    output = _prepare_output_dir(args.output_dir)
    raw_path = output / "raw-calls.jsonl"
    outputs_path = output / "outputs.jsonl"
    manifest_path = output / "run-manifest.json"
    run_id = _run_id("live-qwen")
    budget = Budget(
        args.max_calls,
        args.max_total_tokens,
        args.max_requested_completion_tokens,
        args.max_seconds,
        args.max_provider_errors,
        args.max_user_chars,
        args.timeout_seconds,
    )
    router: RecordingRouter | None = None
    rows: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    status = "FAILED"
    failure_class = "none"
    failure_message = ""
    started_at = _utc_now()
    try:
        _assert_no_gold_visible()
        cases, dataset_manifest = _validate_dataset_boundary(
            Path(args.cases), Path(args.dataset_manifest), args.candidate_hash
        )
        if ROOT is None:
            raise ContractError("runner candidate root is unavailable")
        computed_candidate_hash = _candidate_tree_hash(ROOT)
        if computed_candidate_hash != args.candidate_hash:
            raise ContractError("runner tree differs from declared candidate hash")
        ordered = sorted(cases, key=lambda row: str(row["scenario_id"]))
        if args.scenario_id:
            if len(args.scenario_id) != args.limit:
                raise ContractError("scenario-id count must equal limit")
            cases_by_id = {str(case["scenario_id"]): case for case in ordered}
            missing = [item for item in args.scenario_id if item not in cases_by_id]
            if missing:
                raise ContractError(f"requested scenario IDs are unavailable: {missing}")
            selected = [cases_by_id[item] for item in args.scenario_id]
        else:
            selected = ordered[: args.limit]
        if len(selected) != args.limit:
            raise ContractError("requested scenario count is unavailable")
        selected_ids = [str(case["scenario_id"]) for case in selected]

        from librarian.ingest import ingest_source
        from librarian.llm import ModelRouter  # noqa: F401 - import proves runtime dependency
        from librarian.prompts import PROMPT_VERSION
        from librarian.query import answer_question
        from librarian.store import MemoryStore

        router = RecordingRouter(raw_path, budget)
        for case in selected:
            event_positions = {
                str(event["event_id"]): index
                for index, event in enumerate(case["events"])
            }
            ingested_through = -1
            with TemporaryDirectory(prefix="librarian-live-") as temporary:
                memory_path = Path(temporary) / "memory"
                store = MemoryStore(memory_path)
                ingested_tokens = 0
                for checkpoint in case["checkpoints"]:
                    target = event_positions[str(checkpoint["after_event"])]
                    for position in range(ingested_through + 1, target + 1):
                        event = case["events"][position]
                        ingest_result = ingest_source(
                            source_id=str(event["source_id"]),
                            source_text=str(event["text"]),
                            observed_at=str(event["at"]),
                            store=store,
                            router=router,
                        )
                        ingested_tokens += int(ingest_result.total_tokens)
                    ingested_through = max(ingested_through, target)
                    if bool(checkpoint.get("restart")):
                        store = MemoryStore(memory_path)
                    with store.transaction():
                        due_events = store.apply_due_transitions(
                            as_of=str(checkpoint["as_of"]),
                            prompt_version=PROMPT_VERSION,
                        )
                    result = answer_question(
                        question=str(checkpoint["query"]),
                        store=store,
                        router=router,
                        top_k=int(checkpoint["top_k"]),
                        as_of=str(checkpoint["as_of"]),
                        context_budget_chars=int(checkpoint["context_budget"]) * 4,
                    )
                    serialized_answer = _render_answer(result.facts, result.abstained)
                    rows.append(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "run_id": run_id,
                            "repeat": 0,
                            "policy_id": "C",
                            "scenario_id": str(case["scenario_id"]),
                            "checkpoint_id": str(checkpoint["checkpoint_id"]),
                            "answer": serialized_answer,
                            "facts": result.facts,
                            "citations": result.evidence_source_ids,
                            "abstained": result.abstained,
                            "memory_state": _memory_state(store),
                            "transitions": store.decision_events(),
                            "trace": {
                                "corpus_sources": target + 1,
                                "candidate_sources": result.trace.get("candidate_pages", 0),
                                "loaded_source_ids": result.trace.get("loaded_source_ids", []),
                                "wire_page_citations": list(result.citations),
                                "wire_evidence_source_ids": list(result.evidence_source_ids),
                                "scheduled_transitions_materialized_before_query": len(due_events),
                                "scheduled_transitions_materialized_by_query": result.trace.get(
                                    "scheduled_transitions_materialized_by_query", 0
                                ),
                                "loaded_pages": result.trace.get("loaded_pages", 0),
                                "active_claims_loaded": result.trace.get(
                                    "active_claims_loaded", 0
                                ),
                                "disputed_claims_loaded": result.trace.get(
                                    "disputed_claims_loaded", 0
                                ),
                                "superseded_claims_filtered": result.trace.get(
                                    "superseded_claims_filtered", 0
                                ),
                                "context_tokens": result.trace.get("context_tokens", 0),
                                "prompt_tokens": result.prompt_tokens,
                                "completion_tokens": result.completion_tokens,
                                "total_tokens": result.total_tokens,
                                "ingest_tokens_through_checkpoint": ingested_tokens,
                                "provider_calls_through_checkpoint": router.calls,
                                "provider_tokens_through_checkpoint": router.total_tokens,
                                "query_model": result.model,
                                "query_route": result.route,
                            },
                        }
                    )
        _write_jsonl(outputs_path, rows)
        status = "COMPLETE"
    except Exception as exc:
        failure_class = type(exc).__name__
        failure_message = _redact(str(exc))

    usage = _usage_dict(router) if router is not None else {}
    if rows and not outputs_path.exists():
        _write_jsonl(outputs_path, rows)
    raw_hash = _file_sha256(raw_path) if raw_path.is_file() else "unavailable"
    outputs_hash = _file_sha256(outputs_path) if outputs_path.is_file() else "unavailable"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "lane": "live_qwen_c_only",
        "status": status,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "candidate_tree_sha256": args.candidate_hash,
        "candidate_tree_sha256_computed_in_runner": (
            _candidate_tree_hash(ROOT) if ROOT is not None else "unavailable"
        ),
        "dataset_manifest": {
            "path": Path(args.dataset_manifest).name,
            "sha256": _file_sha256(args.dataset_manifest),
        },
        "cases_sha256": _file_sha256(args.cases),
        "selected_scenario_ids": selected_ids,
        "scenario_count": len(selected_ids),
        "answer_rows_written": len(rows),
        "budget": _budget_dict(budget),
        "usage": usage,
        "runtime": _runtime_metadata() if router is not None else {},
        "hashes": {
            "outputs_sha256": outputs_hash,
            "raw_archive_sha256": raw_hash,
            "runner_sha256": _file_sha256(__file__),
        },
        "runtime_identity": _runtime_identity(),
        "policy_sha256": str(
            ((_load_json(args.dataset_manifest).get("hashes") or {}).get("policy_sha256", ""))
        ),
        "gold_path_not_passed": True,
        "runner_process_isolation": "PENDING_EXTERNAL_ATTESTATION",
        "failure_class": failure_class,
        "failure_message": failure_message,
        "forward_test_result": _forward_result(
            test_id=run_id,
            wrapper="proof.live_qwen_contract:run",
            runtime_identity=_runtime_identity(),
            raw_path="raw-calls.jsonl" if raw_path.is_file() else "unavailable",
            decision="hold" if status == "COMPLETE" else "fail",
            failure_class=failure_class,
            source_restoration="hold",
            claim_ledger="hold",
            safety_boundary="hold" if raw_path.is_file() else "fail",
        ),
        "promotion_status": "HOLD",
        "promoted": False,
    }
    _write_json(manifest_path, manifest)
    print(f"status={status}")
    print(f"manifest={manifest_path}")
    return 0 if status == "COMPLETE" else 2


def attest_docker(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    manifest_path = output / "run-manifest.json"
    manifest = _load_json(manifest_path)

    def inspect_format(template: str) -> str:
        completed = subprocess.run(
            ["docker", "inspect", "--format", template, args.container],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    mounts = json.loads(inspect_format("{{json .Mounts}}"))
    image_id = inspect_format("{{.Image}}")
    image_name = inspect_format("{{.Config.Image}}")
    command = json.loads(inspect_format("{{json .Config.Cmd}}"))
    inventory_forbidden: list[str] = []
    export = subprocess.Popen(
        ["docker", "export", args.container],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if export.stdout is None:
        raise ContractError("docker export did not provide an image stream")
    with tarfile.open(fileobj=export.stdout, mode="r|*") as archive:
        for member in archive:
            normalized = member.name.replace("\\", "/").casefold()
            if any(
                marker in normalized
                for marker in ("gold.jsonl", "evaluator-only", "eval/private")
            ):
                inventory_forbidden.append(member.name)
                if len(inventory_forbidden) >= 20:
                    break
    export.stdout.close()
    stderr = export.stderr.read().decode("utf-8", errors="replace") if export.stderr else ""
    return_code = export.wait()
    if return_code != 0:
        raise ContractError(f"docker export failed: {_redact(stderr)}")
    safe_mounts = [
        {
            "destination": str(item.get("Destination", "")),
            "mode": str(item.get("Mode", "")),
            "rw": bool(item.get("RW")),
            "source_sha256": (
                _file_sha256(item["Source"])
                if Path(str(item.get("Source", ""))).is_file()
                else None
            ),
            "type": str(item.get("Type", "")),
        }
        for item in mounts
    ]
    forbidden = [
        item
        for item in mounts
        if any(
            marker in str(item.get(field, "")).casefold()
            for marker in ("gold.jsonl", "evaluator-only", "eval/private")
            for field in ("Source", "Destination")
        )
    ]
    expected_mounts = {
        "/runner-inputs/cases.jsonl": {"rw": False, "directory": False},
        "/dataset-manifest.json": {"rw": False, "directory": False},
        "/out": {"rw": True, "directory": True},
    }
    mounts_by_destination = {
        str(item.get("Destination", "")): item for item in mounts
    }
    exact_mounts = set(mounts_by_destination) == set(expected_mounts)
    mount_modes_valid = exact_mounts and all(
        bool(mounts_by_destination[destination].get("RW")) == contract["rw"]
        and Path(str(mounts_by_destination[destination].get("Source", ""))).is_dir()
        == contract["directory"]
        for destination, contract in expected_mounts.items()
    )
    expected_image = str(manifest.get("runtime_identity", ""))
    image_matches = image_id in expected_image or expected_image in {image_id, image_name}
    computed_hash = str(manifest.get("candidate_tree_sha256_computed_in_runner", ""))
    candidate_hash_matches = (
        computed_hash
        and computed_hash == str(manifest.get("candidate_tree_sha256", ""))
    )
    passed = (
        not forbidden
        and image_matches
        and exact_mounts
        and mount_modes_valid
        and candidate_hash_matches
        and not inventory_forbidden
    )
    attestation = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "attestation_level": "local_docker_mount_inspection",
        "container": args.container,
        "created_at": _utc_now(),
        "image_id": image_id,
        "image_name": image_name,
        "image_matches_run_manifest": image_matches,
        "candidate_hash_matches_run_manifest": candidate_hash_matches,
        "command": command,
        "mounts": safe_mounts,
        "gold_mount_absent": not forbidden,
        "exact_mount_allowlist_pass": exact_mounts,
        "mount_modes_valid": mount_modes_valid,
        "image_inventory_forbidden_paths": inventory_forbidden,
        "image_inventory_gold_absent": not inventory_forbidden,
        "independent_external_signature": False,
        "status": "LOCAL_MOUNT_INSPECTION_PASS" if passed else "FAIL",
        "promotion_status": "HOLD",
        "promoted": False,
    }
    _write_json(output / "isolation-attestation.json", attestation)
    print(f"status={attestation['status']}")
    print(f"attestation={output / 'isolation-attestation.json'}")
    return 0 if passed else 2


def evaluate(args: argparse.Namespace) -> int:
    if ROOT is None:
        raise ContractError("evaluate must run from the repository checkout")
    from eval.contracts import load_json, load_jsonl, validate_output_row
    from eval.evaluate import (
        _aggregate,
        _conformance_for_repeat,
        _score_checkpoint,
        _validate_transition_ledger,
    )

    run_dir = Path(args.run_dir)
    manifest = load_json(run_dir / "run-manifest.json")
    if manifest.get("status") != "COMPLETE":
        raise ContractError("live run manifest is not complete")
    outputs_path = run_dir / "outputs.jsonl"
    raw_path = run_dir / "raw-calls.jsonl"
    if _file_sha256(outputs_path) != manifest["hashes"]["outputs_sha256"]:
        raise ContractError("live outputs hash mismatch")
    if _file_sha256(raw_path) != manifest["hashes"]["raw_archive_sha256"]:
        raise ContractError("raw provider archive hash mismatch")
    dataset_manifest = load_json(args.dataset_manifest)
    dataset_hashes = dataset_manifest.get("hashes") or {}
    verified_hashes = {
        "cases_sha256": _file_sha256(args.cases),
        "gold_sha256": _file_sha256(args.gold),
        "policy_sha256": _file_sha256(args.policy),
    }
    for key, actual in verified_hashes.items():
        if dataset_hashes.get(key) != actual:
            raise ContractError(f"{key} differs from frozen dataset manifest")
    current_candidate_hash = _candidate_tree_hash(ROOT)
    frozen_candidate_hash = str(
        (dataset_manifest.get("candidate_snapshot") or {}).get("tree_sha256", "")
    )
    if not (
        current_candidate_hash
        == frozen_candidate_hash
        == str(manifest.get("candidate_tree_sha256", ""))
        == str(manifest.get("candidate_tree_sha256_computed_in_runner", ""))
    ):
        raise ContractError("candidate hash chain is not exact")
    if (
        _file_sha256(args.dataset_manifest)
        != str((manifest.get("dataset_manifest") or {}).get("sha256", ""))
    ):
        raise ContractError("dataset manifest hash differs from live run receipt")
    attestation = load_json(run_dir / "isolation-attestation.json")
    if (
        attestation.get("status") != "LOCAL_MOUNT_INSPECTION_PASS"
        or not attestation.get("gold_mount_absent")
        or not attestation.get("exact_mount_allowlist_pass")
        or not attestation.get("mount_modes_valid")
    ):
        raise ContractError("runner isolation attestation failed")

    cases = load_jsonl(args.cases)
    gold_rows = load_jsonl(args.gold)
    outputs = load_jsonl(outputs_path)
    for row in outputs:
        validate_output_row(row)
    selected_ids = set(manifest["selected_scenario_ids"])
    cases_by_id = {
        str(case["scenario_id"]): case
        for case in cases
        if str(case["scenario_id"]) in selected_ids
    }
    gold_by_id = {
        str(row["scenario_id"]): row
        for row in gold_rows
        if str(row["scenario_id"]) in selected_ids
    }
    if set(cases_by_id) != selected_ids or set(gold_by_id) != selected_ids:
        raise ContractError("selected case/gold matrix is incomplete")
    gold_checkpoints = {
        (scenario_id, str(checkpoint["checkpoint_id"])): (
            checkpoint,
            str(gold_by_id[scenario_id]["scenario_type"]),
        )
        for scenario_id in gold_by_id
        for checkpoint in gold_by_id[scenario_id]["checkpoints"]
    }
    expected_keys = {
        (scenario_id, str(checkpoint["checkpoint_id"]))
        for scenario_id, case in cases_by_id.items()
        for checkpoint in case["checkpoints"]
    }
    output_keys = {
        (str(row["scenario_id"]), str(row["checkpoint_id"])) for row in outputs
    }
    if output_keys != expected_keys:
        raise ContractError("live output checkpoint matrix is incomplete")

    checkpoint_order = {
        (scenario_id, str(checkpoint["checkpoint_id"])): index
        for scenario_id, case in cases_by_id.items()
        for index, checkpoint in enumerate(case["checkpoints"])
    }
    scored: list[dict[str, Any]] = []
    previous_ledgers: dict[str, list[dict[str, Any]]] = {}
    for output in sorted(
        outputs,
        key=lambda row: (
            str(row["scenario_id"]),
            checkpoint_order[(str(row["scenario_id"]), str(row["checkpoint_id"]))],
        ),
    ):
        scenario_id = str(output["scenario_id"])
        checkpoint_id = str(output["checkpoint_id"])
        checkpoint_gold, scenario_type = gold_checkpoints[(scenario_id, checkpoint_id)]
        scored_row = _score_checkpoint(output, checkpoint_gold, scenario_type)
        case = cases_by_id[scenario_id]
        checkpoint = next(
            item
            for item in case["checkpoints"]
            if str(item["checkpoint_id"]) == checkpoint_id
        )
        scored_row.update(
            _validate_transition_ledger(
                output,
                case=case,
                checkpoint=checkpoint,
                previous=previous_ledgers.get(scenario_id),
            )
        )
        previous_ledgers[scenario_id] = list(output["transitions"])
        scored.append(scored_row)

    metrics = _aggregate(scored)
    policy = load_json(args.policy)
    decision = _conformance_for_repeat(
        {"C": metrics}, policy["repository_diagnostic_gates"]
    )
    provider_ok = int(manifest["usage"].get("provider_errors", 0)) == 0
    passed = bool(decision.get("passed")) and provider_ok
    status = "LIVE_QWEN_2CASE_PASS" if passed else "LIVE_QWEN_2CASE_FAIL"
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "created_at": _utc_now(),
        "status": status,
        "evidence_mode": "live_runtime",
        "candidate_tree_sha256": manifest["candidate_tree_sha256"],
        "hashes": {
            **verified_hashes,
            "dataset_manifest_sha256": _file_sha256(args.dataset_manifest),
            "scorer_sha256": _file_sha256(ROOT / "eval" / "evaluate.py"),
            "runner_sha256": manifest["hashes"]["runner_sha256"],
        },
        "scenario_count": metrics["scenario_count"],
        "metrics": metrics,
        "checks": decision.get("checks", {}),
        "provider_usage": manifest["usage"],
        "runtime": manifest.get("runtime") or {},
        "isolation_attestation": {
            "level": attestation["attestation_level"],
            "gold_mount_absent": attestation["gold_mount_absent"],
            "independent_external_signature": False,
        },
        "proof_boundary": {
            "actual_qwen_runtime": True,
            "actual_runtime_archive": True,
            "candidate_hash_bound": True,
            "gold_absent_from_runner_container": True,
            "independent_external_verifier": False,
            "fair_live_b2_comparison": False,
        },
        "promotion_blockers": [
            "two_case_dev_slice_only",
            "independent_external_verifier_missing",
            "fair_live_b2_comparison_missing",
        ],
        "promotion_status": "HOLD",
        "promoted": False,
    }
    _write_json(run_dir / "live-metrics.json", report)
    print(f"status={status}")
    print(f"metrics={run_dir / 'live-metrics.json'}")
    return 0 if passed else 2


def describe_contract() -> int:
    contract = {
        "contract_version": CONTRACT_VERSION,
        "fail_closed_rule": "missing receipt or gate means HOLD",
        "candidate_mutation_rule": (
            "any src/librarian/*.py, eval/*.py, pyproject.toml, or uv.lock change "
            "invalidates the frozen holdout"
        ),
        "stages": [
            {
                "stage": 0,
                "name": "CONTRACT_FROZEN",
                "exit_gate": "current candidate hash equals private dataset and run manifests",
            },
            {
                "stage": 1,
                "name": "LIVE_CONNECTIVITY_ONLY_PASS",
                "budget": {"calls": 1, "tokens": 200, "seconds": 60},
                "exit_gate": "exact pong, model identity, token usage, raw archive",
            },
            {
                "stage": 2,
                "name": "LIVE_QWEN_2CASE_PASS",
                "budget": {
                    "calls": 18,
                    "actual_tokens": 25000,
                    "requested_completion_tokens": 10808,
                    "seconds": 600,
                    "provider_retries": 0,
                },
                "exit_gate": (
                    "two public dev scenarios pass every production conformance check "
                    "with zero provider errors in a no-gold container"
                ),
            },
            {
                "stage": 3,
                "name": "LIVE_PRIVATE_ISOLATED_PASS",
                "exit_gate": (
                    "24 private cases, external verifier/signature, user-approved cost budget"
                ),
                "requires_explicit_budget": True,
            },
            {
                "stage": 4,
                "name": "WINNING_PROOF_READY",
                "exit_gate": (
                    "pre-registered gates plus fair live B2/C comparison under identical model, "
                    "prompt, top-K, and context budget"
                ),
            },
        ],
        "current_contract_ceiling": "LIVE_QWEN_2CASE_PASS",
        "promotion_status": "HOLD",
        "promoted": False,
    }
    print(json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def self_test() -> int:
    sample = {"scenario_id": "opaque", "events": [], "checkpoints": []}
    _assert_oracle_free(sample, "self-test")
    try:
        _assert_oracle_free({"gold": "forbidden"}, "self-test")
    except ContractError:
        pass
    else:
        raise ContractError("oracle guard self-test failed")
    if _stable_hash({"b": 2, "a": 1}) != _stable_hash({"a": 1, "b": 2}):
        raise ContractError("canonical hash self-test failed")
    if _render_answer(
        [{"key": "scope::subject::predicate", "value": "v", "claim_ids": ["c"]}],
        False,
    ) != "scope::subject::predicate = v.":
        raise ContractError("answer serializer self-test failed")
    if ROOT is not None and (ROOT / "eval" / "contracts.py").is_file():
        from eval.contracts import candidate_tree_hash

        if _candidate_tree_hash(ROOT) != candidate_tree_hash(ROOT):
            raise ContractError("candidate hash implementation drifted")
    print("SELF_TEST_PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("describe")
    subparsers.add_parser("self-test")

    connectivity_parser = subparsers.add_parser("connectivity")
    connectivity_parser.add_argument("--output-dir", required=True)
    connectivity_parser.add_argument("--candidate-hash", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--cases", required=True)
    run_parser.add_argument("--dataset-manifest", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--candidate-hash", required=True)
    run_parser.add_argument("--limit", type=int, default=2)
    run_parser.add_argument("--scenario-id", action="append", default=[])
    run_parser.add_argument("--max-calls", type=int, default=18)
    run_parser.add_argument("--max-total-tokens", type=int, default=25000)
    run_parser.add_argument("--max-requested-completion-tokens", type=int, default=10808)
    run_parser.add_argument("--max-seconds", type=int, default=600)
    run_parser.add_argument("--max-provider-errors", type=int, default=1)
    run_parser.add_argument("--max-user-chars", type=int, default=20000)
    run_parser.add_argument("--timeout-seconds", type=float, default=45.0)

    attest_parser = subparsers.add_parser("attest-docker")
    attest_parser.add_argument("--container", required=True)
    attest_parser.add_argument("--output-dir", required=True)

    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--cases", required=True)
    evaluate_parser.add_argument("--gold", required=True)
    evaluate_parser.add_argument("--dataset-manifest", required=True)
    evaluate_parser.add_argument("--run-dir", required=True)
    evaluate_parser.add_argument("--policy", default="eval/policy.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "describe":
        return describe_contract()
    if args.command == "self-test":
        return self_test()
    if args.command == "connectivity":
        return connectivity(args)
    if args.command == "run":
        return run_live(args)
    if args.command == "attest-docker":
        return attest_docker(args)
    if args.command == "evaluate":
        return evaluate(args)
    raise ContractError(f"unknown command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"CONTRACT_FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

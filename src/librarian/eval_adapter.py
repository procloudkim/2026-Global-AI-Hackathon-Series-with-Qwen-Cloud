"""Production-code adapter for the deterministic Track 1 evaluation lane.

This adapter replaces model extraction and answer generation with the frozen,
oracle-free extraction snapshot while exercising the real MemoryStore, ingest
lifecycle, graph retrieval, query filtering, restart, and transition ledger.
It is not a live-Qwen receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from eval.contracts import render_answer

from .claims import Claim, normalize_component
from .ingest import ingest_source
from .llm import Tier
from .prompts import (
    INGEST_SYSTEM_PREFIX,
    PROMPT_VERSION,
    QUERY_HEAVY_SYSTEM_PREFIX,
    QUERY_LIGHT_SYSTEM_PREFIX,
    RELATION_SYSTEM_PREFIX,
)
from .query import answer_question
from .store import MemoryStore


@dataclass(frozen=True)
class _Response:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class _FrozenRouter:
    def __init__(self) -> None:
        self.event: dict[str, Any] | None = None
        self.event_extraction: dict[str, Any] | None = None

    def set_event(
        self,
        *,
        event: dict[str, Any],
        extraction: dict[str, Any],
    ) -> None:
        self.event = event
        self.event_extraction = extraction

    def chat(
        self,
        tier: Tier,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> _Response:
        del tier, temperature, max_tokens
        if system == INGEST_SYSTEM_PREFIX:
            payload = self._ingest_payload()
            model = "frozen-extraction-v1"
        elif system == RELATION_SYSTEM_PREFIX:
            payload = self._relation_payload(user)
            model = "deterministic-relation-v1"
        elif system in {QUERY_LIGHT_SYSTEM_PREFIX, QUERY_HEAVY_SYSTEM_PREFIX}:
            payload = self._answer_payload(user)
            model = "deterministic-answer-v1"
        else:
            raise ValueError("evaluation router received an unknown prompt contract")
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return _Response(
            text=text,
            model=model,
            prompt_tokens=max(1, len(user.encode("utf-8")) // 4),
            completion_tokens=max(1, len(text.encode("utf-8")) // 4),
        )

    def _ingest_payload(self) -> dict[str, Any]:
        if self.event is None or self.event_extraction is None:
            raise RuntimeError("evaluation event was not configured")
        extracted_claims = self.event_extraction.get("claims", [])
        claims: list[dict[str, Any]] = []
        for raw in extracted_claims:
            claims.append(
                {
                    "kind": raw["kind"],
                    "scope": raw["scope"],
                    "subject": raw["subject"],
                    "predicate": raw["predicate"],
                    "value": raw["value"],
                    "effective_at": raw["effective_at"],
                    "evidence_spans": [raw["evidence_span"]],
                }
            )
        first = claims[0] if claims else None
        title = (
            f"{first['scope']} {first['subject']} {first['predicate']}"
            if first
            else str(self.event["source_id"])
        )
        tags = (
            [str(first["scope"]), str(first["subject"]), str(first["predicate"])]
            if first
            else []
        )
        return {
            "title": title,
            "summary": str(self.event["text"]),
            "body": str(self.event["text"]),
            "links": [],
            "tags": tags,
            "claims": claims,
        }

    def _relation_payload(self, user: str) -> dict[str, Any]:
        """Return a fail-closed result for genuinely ambiguous relations.

        Explicit replacement, effective-time, duplicate, and restoration rules
        are evaluated by production ingest code from the raw source text.  The
        frozen router must not receive or replay generator lifecycle labels.
        """
        supplied = json.loads(user)
        new_claim = supplied["new_claim"]
        candidates = list(supplied.get("candidate_claims", []))
        provided = [new_claim, *candidates]
        source_ids = list(
            dict.fromkeys(
                source
                for claim in provided
                for source in claim.get("source_ids", [])
            )
        )
        evidence_spans = list(
            dict.fromkeys(
                span
                for claim in provided
                for span in claim.get("evidence_spans", [])
            )
        )
        return {
            "relation": "unresolved",
            "winner_claim_id": None,
            "evidence_source_ids": source_ids,
            "evidence_spans": evidence_spans,
            "rationale": "Frozen adapter does not arbitrate ambiguous evidence.",
        }

    def _answer_payload(self, user: str) -> dict[str, Any]:
        envelope = json.loads(user)
        active = self._claims_matching_question(
            envelope.get("active_claims", []), str(envelope.get("question", ""))
        )
        disputed = self._claims_matching_question(
            envelope.get("disputed_claims", []),
            str(envelope.get("question", "")),
        )
        values = {str(claim.get("value", "")) for claim in active}
        if disputed or not active or len(values) != 1:
            return {
                "answer": "The selected memory does not establish one current value.",
                "facts": [],
                "citations": [],
                "confidence": 0.0,
                "abstained": True,
            }
        value = next(iter(values))
        claim_ids = list(dict.fromkeys(str(claim["claim_id"]) for claim in active))
        citations = list(
            dict.fromkeys(str(claim["citation_id"]) for claim in active)
        )
        return {
            "answer": f"The current value is {value}.",
            "facts": [
                {
                    "key": str(active[0]["key"]),
                    "value": value,
                    "claim_ids": claim_ids,
                }
            ],
            "citations": citations,
            "confidence": 1.0,
            "abstained": False,
        }

    @staticmethod
    def _claims_matching_question(
        claims: list[dict[str, Any]], question: str
    ) -> list[dict[str, Any]]:
        """Pick one unambiguous key using only prompt-visible question text."""
        normalized_question = normalize_component(question)
        grouped: dict[str, list[dict[str, Any]]] = {}
        scores: dict[str, int] = {}
        for claim in claims:
            key = str(claim.get("key", ""))
            parts = [part for part in key.split("::") if part]
            score = sum(part in normalized_question for part in parts)
            if not score:
                continue
            grouped.setdefault(key, []).append(claim)
            scores[key] = score
        if not scores:
            return []
        best = max(scores.values())
        winners = [key for key, score in scores.items() if score == best]
        return grouped[winners[0]] if len(winners) == 1 else []


class ProductionCandidateAdapter:
    policy_id = "C"

    def __init__(self, policy_config: dict[str, Any] | None = None) -> None:
        self.policy_config = policy_config or {}

    def run_case(
        self,
        *,
        case: dict[str, Any],
        extraction: dict[str, Any],
        repeat: int,
    ) -> list[dict[str, Any]]:
        del repeat
        event_positions = {
            str(event["event_id"]): index for index, event in enumerate(case["events"])
        }
        outputs: list[dict[str, Any]] = []
        ingested_through = -1
        with TemporaryDirectory(prefix="librarian-eval-") as temporary:
            memory_path = Path(temporary) / "memory"
            store = MemoryStore(memory_path)
            router = _FrozenRouter()
            for checkpoint in case["checkpoints"]:
                target_position = event_positions[str(checkpoint["after_event"])]
                for position in range(ingested_through + 1, target_position + 1):
                    event = case["events"][position]
                    event_extraction = extraction["events"][event["event_id"]]
                    router.set_event(event=event, extraction=event_extraction)
                    ingest_source(
                        source_id=str(event["source_id"]),
                        source_text=str(event["text"]),
                        observed_at=str(event["at"]),
                        store=store,
                        router=router,
                    )
                ingested_through = max(ingested_through, target_position)
                if bool(checkpoint.get("restart")):
                    store = MemoryStore(memory_path)
                # Scheduled lifecycle mutation belongs to maintenance, not the
                # limited-context query trace. The following query only reads
                # graph metadata and its selected top-K canonical pages.
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
                fair_context_tokens = int(result.trace.get("context_tokens", 0))
                fair_prompt_tokens = fair_context_tokens + 24
                serialized_answer = render_answer(result.facts, result.abstained)
                fair_completion_tokens = _token_estimate(serialized_answer)
                outputs.append(
                    {
                        "scenario_id": str(case["scenario_id"]),
                        "checkpoint_id": str(checkpoint["checkpoint_id"]),
                        "answer": serialized_answer,
                        "facts": result.facts,
                        "citations": result.evidence_source_ids,
                        "abstained": result.abstained,
                        "memory_state": _memory_state(store),
                        "transitions": store.decision_events(),
                        "trace": {
                            "corpus_sources": target_position + 1,
                            "candidate_sources": result.trace.get("candidate_pages", 0),
                            "loaded_source_ids": result.trace.get(
                                "loaded_source_ids", []
                            ),
                            "wire_page_citations": list(result.citations),
                            "wire_evidence_source_ids": list(
                                result.evidence_source_ids
                            ),
                            "scheduled_transitions_materialized_before_query": len(
                                due_events
                            ),
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
                            "prompt_tokens": fair_prompt_tokens,
                            "completion_tokens": fair_completion_tokens,
                            "total_tokens": fair_prompt_tokens
                            + fair_completion_tokens,
                        },
                    }
                )
        return outputs


def _memory_state(store: MemoryStore) -> list[dict[str, Any]]:
    state: list[dict[str, Any]] = []
    for page in store.list_wiki_pages():
        for raw in store.claims_for_page(page):
            try:
                claim = Claim.from_dict(raw)
            except ValueError:
                continue
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


def _token_estimate(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def create_adapter(
    *,
    policy_id: str,
    policy_config: dict[str, Any] | None = None,
) -> ProductionCandidateAdapter:
    if policy_id != "C":
        raise ValueError("production adapter supports only candidate policy C")
    return ProductionCandidateAdapter(policy_config)


__all__ = ["ProductionCandidateAdapter", "create_adapter"]

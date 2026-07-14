"""Offline reference policies and the candidate adapter protocol.

The deterministic policies isolate memory-policy behavior from model variance.
They are not claims about live Qwen performance. Production code is exercised in
the separate, non-comparative conformance lane.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Any, Protocol

from .contracts import render_answer


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _token_estimate(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


@dataclass
class _Claim:
    key: str
    value: str
    source_ids: list[str]
    observed_at: str
    effective_at: str
    status: str = "active"


class PolicyAdapter(Protocol):
    policy_id: str

    def run_case(
        self,
        *,
        case: dict[str, Any],
        extraction: dict[str, Any],
        repeat: int,
    ) -> list[dict[str, Any]]: ...


class DeterministicPolicyAdapter:
    def __init__(self, policy_id: str, config: dict[str, Any] | None = None) -> None:
        if policy_id not in {"B0", "B1", "B2", "C"}:
            raise ValueError(f"unknown policy: {policy_id}")
        self.policy_id = policy_id
        self.config = config or {}

    def run_case(
        self,
        *,
        case: dict[str, Any],
        extraction: dict[str, Any],
        repeat: int,
    ) -> list[dict[str, Any]]:
        del repeat  # deterministic adapter intentionally ignores sampling repeat
        events = case["events"]
        event_positions = {event["event_id"]: index for index, event in enumerate(events)}
        outputs: list[dict[str, Any]] = []
        for checkpoint in case["checkpoints"]:
            stop = event_positions[checkpoint["after_event"]] + 1
            visible_events = events[:stop]
            as_of = checkpoint["as_of"]
            claims, transitions = self._build_state(
                visible_events, extraction["events"], as_of
            )
            query_info = extraction["queries"][checkpoint["checkpoint_id"]]
            loaded, candidate_count, context_tokens = self._retrieve(
                visible_events=visible_events,
                extraction_events=extraction["events"],
                claims=claims,
                query=query_info,
                as_of=as_of,
                top_k=int(checkpoint["top_k"]),
                context_budget=int(checkpoint["context_budget"]),
            )
            answer, facts, citations, abstained = self._answer(
                claims=claims,
                query=query_info,
                loaded_sources=loaded,
                visible_events=visible_events,
                as_of=as_of,
            )
            completion_tokens = _token_estimate(answer)
            outputs.append(
                {
                    "scenario_id": case["scenario_id"],
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "answer": answer,
                    "facts": facts,
                    "citations": citations,
                    "abstained": abstained,
                    "memory_state": [
                        {
                            "key": claim.key,
                            "value": claim.value,
                            "status": claim.status,
                            "source_ids": claim.source_ids,
                        }
                        for claim in claims
                    ],
                    "transitions": transitions,
                    "trace": {
                        "corpus_sources": len(visible_events),
                        "candidate_sources": candidate_count,
                        "loaded_source_ids": loaded,
                        "loaded_pages": len(loaded),
                        "active_claims_loaded": self._count_loaded_status(
                            claims, loaded, "active"
                        ),
                        "disputed_claims_loaded": self._count_loaded_status(
                            claims, loaded, "disputed"
                        ),
                        "superseded_claims_filtered": sum(
                            1 for claim in claims if claim.status == "superseded"
                        ),
                        "context_tokens": context_tokens,
                        "prompt_tokens": context_tokens + 24,
                        "completion_tokens": completion_tokens,
                        "total_tokens": context_tokens + 24 + completion_tokens,
                    },
                }
            )
        return outputs

    @staticmethod
    def _count_loaded_status(
        claims: list[_Claim], loaded: list[str], status: str
    ) -> int:
        loaded_set = set(loaded)
        return sum(
            1
            for claim in claims
            if claim.status == status and loaded_set.intersection(claim.source_ids)
        )

    def _build_state(
        self,
        events: list[dict[str, Any]],
        extraction_events: dict[str, Any],
        as_of: str,
    ) -> tuple[list[_Claim], list[dict[str, Any]]]:
        claims: list[_Claim] = []
        transitions: list[dict[str, Any]] = []
        for event in events:
            snapshot = extraction_events[event["event_id"]]
            for incoming in snapshot["claims"]:
                if self.policy_id in {"B0", "B1"}:
                    claims.append(self._record(incoming))
                    continue
                if self.policy_id == "B2":
                    for existing in claims:
                        if existing.key == incoming["key"] and existing.status == "active":
                            existing.status = "superseded"
                    claims.append(self._record(incoming))
                    continue
                self._apply_lifecycle(
                    claims,
                    incoming,
                    source_text=str(event["text"]),
                    as_of=as_of,
                    transitions=transitions,
                )
        return claims, transitions

    @staticmethod
    def _record(incoming: dict[str, Any], *, status: str = "active") -> _Claim:
        return _Claim(
            key=str(incoming["key"]),
            value=str(incoming["value"]),
            source_ids=[str(incoming["source_id"])],
            observed_at=str(incoming["observed_at"]),
            effective_at=str(incoming.get("effective_at") or incoming["observed_at"]),
            status=status,
        )

    def _apply_lifecycle(
        self,
        claims: list[_Claim],
        incoming: dict[str, Any],
        source_text: str,
        as_of: str,
        transitions: list[dict[str, Any]],
    ) -> None:
        same_key = [claim for claim in claims if claim.key == incoming["key"]]
        same_value = next(
            (claim for claim in same_key if claim.value == str(incoming["value"])), None
        )
        source_id = str(incoming["source_id"])
        normalized_text = source_text.casefold()
        duplicate_signal = bool(re.search(r"\bduplicate\b|중복", normalized_text))
        restore_signal = bool(
            re.search(r"\b(restores?|reinstates?)\b|복원", normalized_text)
        )
        negated_replacement = bool(
            re.search(
                r"\bdoes\s+not\s+replace\b|\bno\s+replacement\s+authority\b|"
                r"\bwithout\s+replacement\s+authority\b|대체하지\s*않",
                normalized_text,
            )
        )
        supersede_signal = not negated_replacement and bool(
            re.search(
                r"\b(replaces?|replacement|corrects?|correction|retracts?)\b|"
                r"대체|정정|철회|변경",
                normalized_text,
            )
        )

        if duplicate_signal and same_value is not None:
            same_value.source_ids.append(source_id)
            transitions.append(
                self._transition(incoming, same_value.status, same_value.status, "provenance_merge")
            )
            return

        if restore_signal:
            target = next(
                (
                    claim
                    for claim in same_key
                    if any(source in source_text for source in claim.source_ids)
                ),
                same_value,
            )
            if target is None:
                claims.append(self._record(incoming, status="disputed"))
                transitions.append(
                    self._transition(incoming, "new", "disputed", "missing_restore_target")
                )
                return
            for existing in same_key:
                previous = existing.status
                existing.status = "active" if existing is target else "superseded"
                if existing.status != previous:
                    transitions.append(
                        self._transition(
                            incoming, previous, existing.status, "explicit_restore"
                        )
                    )
            if source_id not in target.source_ids:
                target.source_ids.append(source_id)
            return

        if supersede_signal:
            effective_at = str(
                incoming.get("effective_at") or incoming["observed_at"]
            )
            if _parse_time(effective_at) <= _parse_time(as_of):
                for existing in same_key:
                    previous = existing.status
                    existing.status = "superseded"
                    if previous != "superseded":
                        transitions.append(
                            self._transition(
                                incoming, previous, "superseded", "explicit_supersession"
                            )
                        )
            claims.append(self._record(incoming))
            transitions.append(
                self._transition(incoming, "new", "active", "explicit_supersession")
            )
            return

        if any(
            claim.value != str(incoming["value"])
            and claim.status in {"active", "disputed"}
            for claim in same_key
        ):
            for existing in same_key:
                if existing.status in {"active", "disputed"}:
                    previous = existing.status
                    existing.status = "disputed"
                    if previous != "disputed":
                        transitions.append(
                            self._transition(
                                incoming, previous, "disputed", "unresolved_conflict"
                            )
                        )
            claims.append(self._record(incoming, status="disputed"))
            transitions.append(
                self._transition(incoming, "new", "disputed", "unresolved_conflict")
            )
            return

        if same_value is not None:
            same_value.source_ids.append(source_id)
            transitions.append(
                self._transition(incoming, same_value.status, same_value.status, "provenance_merge")
            )
            return

        claims.append(self._record(incoming))
        transitions.append(self._transition(incoming, "new", "active", "new_claim"))

    @staticmethod
    def _transition(
        incoming: dict[str, Any], from_status: str, to_status: str, rule: str
    ) -> dict[str, Any]:
        return {
            "trigger_source_id": incoming["source_id"],
            "key": incoming["key"],
            "from_status": from_status,
            "to_status": to_status,
            "rule": rule,
        }

    def _retrieve(
        self,
        *,
        visible_events: list[dict[str, Any]],
        extraction_events: dict[str, Any],
        claims: list[_Claim],
        query: dict[str, Any],
        as_of: str,
        top_k: int,
        context_budget: int,
    ) -> tuple[list[str], int, int]:
        if self.policy_id == "B0":
            candidate_events = list(visible_events)
        else:
            allowed_sources: set[str] | None = None
            if self.policy_id in {"B2", "C"}:
                allowed_sources = {
                    source
                    for claim in claims
                    if claim.status in {"active", "disputed"}
                    and (
                        self.policy_id != "C"
                        or _parse_time(claim.effective_at) <= _parse_time(as_of)
                    )
                    for source in claim.source_ids
                }
            scored: list[tuple[int, int, dict[str, Any]]] = []
            terms = [str(term).casefold() for term in query.get("terms", [])]
            for position, event in enumerate(visible_events):
                if allowed_sources is not None and event["source_id"] not in allowed_sources:
                    continue
                text = str(event["text"]).casefold()
                score = sum(3 for term in terms if term in text)
                if self.policy_id in {"B2", "C"}:
                    event_claims = extraction_events[event["event_id"]]["claims"]
                    if any(
                        all(term in str(claim["key"]).casefold() for term in terms)
                        for claim in event_claims
                    ):
                        score += 100
                if score > 0:
                    scored.append((score, position, event))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            candidate_events = [event for _, _, event in scored[:top_k]]

        loaded: list[str] = []
        consumed = 0
        for event in candidate_events:
            tokens = _token_estimate(str(event["text"]))
            if loaded and consumed + tokens > context_budget:
                continue
            loaded.append(str(event["source_id"]))
            consumed += tokens
            if self.policy_id == "B0" and consumed >= context_budget:
                break
        return loaded, len(candidate_events), consumed

    def _answer(
        self,
        *,
        claims: list[_Claim],
        query: dict[str, Any],
        loaded_sources: list[str],
        visible_events: list[dict[str, Any]],
        as_of: str,
    ) -> tuple[str, list[dict[str, Any]], list[str], bool]:
        loaded = set(loaded_sources)
        source_order = {
            event["source_id"]: index for index, event in enumerate(visible_events)
        }
        query_key = self._select_query_key(claims, query)
        if query_key is None:
            return render_answer([], True), [], [], True
        relevant = [
            claim
            for claim in claims
            if claim.key == query_key and loaded.intersection(claim.source_ids)
        ]
        if self.policy_id == "C" and any(
            claim.status == "disputed" for claim in relevant
        ):
            return render_answer([], True), [], [], True

        eligible = [claim for claim in relevant if claim.status == "active"]
        if self.policy_id == "C":
            eligible = [
                claim
                for claim in eligible
                if _parse_time(claim.effective_at) <= _parse_time(as_of)
            ]
        if not eligible:
            return render_answer([], True), [], [], True

        winner = max(
            eligible,
            key=lambda claim: (
                _parse_time(claim.effective_at),
                _parse_time(claim.observed_at),
            ),
        )
        cited_candidates = [source for source in winner.source_ids if source in loaded]
        cited_candidates.sort(key=lambda source: source_order.get(source, -1), reverse=True)
        citations = cited_candidates[:1]
        claim_ids = [f"{source}#{winner.key}" for source in winner.source_ids]
        facts = [{"key": winner.key, "value": winner.value, "claim_ids": claim_ids}]
        return (
            render_answer(facts, False),
            facts,
            citations,
            False,
        )

    @staticmethod
    def _select_query_key(
        claims: list[_Claim], query: dict[str, Any]
    ) -> str | None:
        """Resolve a query against atomic claim metadata, without a hidden key."""
        terms = [str(term).casefold() for term in query.get("terms", []) if str(term)]
        if not terms:
            return None
        scored: list[tuple[int, str]] = []
        for key in sorted({claim.key for claim in claims}):
            score = sum(term in key.casefold() for term in terms)
            if score:
                scored.append((score, key))
        if not scored:
            return None
        best = max(score for score, _ in scored)
        winners = [key for score, key in scored if score == best]
        return winners[0] if len(winners) == 1 else None


def make_builtin_adapter(
    policy_id: str, policy_config: dict[str, Any] | None = None
) -> DeterministicPolicyAdapter:
    return DeterministicPolicyAdapter(policy_id, policy_config)

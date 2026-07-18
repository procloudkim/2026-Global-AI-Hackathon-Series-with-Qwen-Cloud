"""Graph-first limited-context retrieval and evidence-validated answering."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any, Protocol

from .claims import (
    Claim,
    ClaimStatus,
    TransitionEvent,
    canonical_timestamp,
    normalize_component,
)
from .llm import Tier
from .prompts import QUERY_HEAVY_SYSTEM_PREFIX, QUERY_LIGHT_SYSTEM_PREFIX, PROMPT_VERSION
from .store import MemoryStore, WikiPage


class SupportsChat(Protocol):
    def chat(
        self,
        tier: Tier,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ): ...


@dataclass(frozen=True)
class QueryResult:
    answer: str
    citations: list[str]
    confidence: float
    route: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_version: str
    facts: list[dict[str, Any]]
    evidence_claim_ids: list[str]
    evidence_source_ids: list[str]
    abstained: bool
    trace: dict[str, Any]


@dataclass(frozen=True)
class _ContextClaim:
    page_slug: str
    page_title: str
    claim: Claim


@dataclass(frozen=True)
class _ValidatedAnswer:
    answer: str
    citations: list[str]
    confidence: float
    facts: list[dict[str, Any]]
    claim_ids: list[str]
    source_ids: list[str]
    abstained: bool


def answer_question(
    *,
    question: str,
    store: MemoryStore,
    router: SupportsChat,
    top_k: int = 3,
    confidence_threshold: float = 0.3,
    as_of: str | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
    context_budget_chars: int = 12000,
) -> QueryResult:
    with store.transaction():
        return _answer_question_locked(
            question=question,
            store=store,
            router=router,
            top_k=top_k,
            confidence_threshold=confidence_threshold,
            as_of=as_of,
            valid_at=valid_at,
            known_at=known_at,
            context_budget_chars=context_budget_chars,
        )


def _answer_question_locked(
    *,
    question: str,
    store: MemoryStore,
    router: SupportsChat,
    top_k: int = 3,
    confidence_threshold: float = 0.3,
    as_of: str | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
    context_budget_chars: int = 12000,
) -> QueryResult:
    if not question.strip():
        raise ValueError("question must be non-empty")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if context_budget_chars < 1000:
        raise ValueError("context_budget_chars must be at least 1000")
    valid_time, knowledge_time = _resolve_temporal_cutoffs(
        as_of=as_of,
        valid_at=valid_at,
        known_at=known_at,
    )

    revision_tail_repaired = store.repair_partial_claim_revision_tail()
    pending_claim_revisions_recovered = store.recover_pending_claim_revisions()
    pending_transition_recovered = store.recover_pending_transition()
    recovered_ingest_keys = store.recover_pending_ingest(
        prompt_version=PROMPT_VERSION,
    )
    projection_repaired = store.repair_dirty_projection()
    transition_events = store.decision_events()
    selected, retrieval_trace = _select_top_k_pages_with_trace(
        store,
        question,
        k=top_k,
        valid_at=valid_time.isoformat(),
        known_at=knowledge_time.isoformat(),
        transition_events=transition_events,
    )
    revision_view = store.claim_revision_view(
        known_at=knowledge_time.isoformat(),
        page_slugs={page.slug for page in selected},
    )
    active, disputed, state_trace, superseded = _build_claim_view(
        selected,
        valid_time,
        known_at=knowledge_time,
        transition_events=transition_events,
        revision_snapshots=revision_view.snapshots,
        revision_claim_ids=revision_view.tracked_claim_ids,
        incomplete_revision_claim_ids=revision_view.incomplete_claim_ids,
        projected_active_claim_ids=set(
            retrieval_trace.get("temporal_active_claim_ids", [])
        ),
    )
    trace = {
        **retrieval_trace,
        **state_trace,
        "valid_at": valid_time.isoformat(),
        "known_at": knowledge_time.isoformat(),
        "bitemporal_axes_separated": int(valid_time != knowledge_time),
        # Normal retrieval does not materialize due lifecycle transitions.
        # Crash recovery above is an exceptional maintenance prelude; the
        # selected pages still receive an as-of logical view below.
        "scheduled_transitions_applied": 0,
        "scheduled_transitions_materialized_by_query": 0,
        "pending_transition_recovered": int(pending_transition_recovered),
        "claim_revision_tail_repaired": int(revision_tail_repaired),
        "pending_claim_revisions_recovered": int(
            pending_claim_revisions_recovered
        ),
        "pending_ingest_keys_recovered": len(recovered_ingest_keys),
        "projection_recovery_applied": int(projection_repaired),
        "loaded_source_ids": [],
        "selected_page_slugs": [page.slug for page in selected],
        "active_claim_ids_loaded": [],
        "disputed_claim_ids_loaded": [],
        "superseded_claim_ids_filtered": [
            item.claim.claim_id for item in superseded
        ],
    }
    strict_bitemporal = valid_at is not None or known_at is not None
    history_complete = not (
        state_trace["untracked_claim_histories"]
        or state_trace["incomplete_claim_histories"]
        or retrieval_trace.get("graph_untracked_claim_histories", 0)
        or retrieval_trace.get("graph_incomplete_claim_histories", 0)
    )
    trace["bitemporal_history_complete"] = int(history_complete)
    if strict_bitemporal and not history_complete:
        return _abstention_result(
            answer="Bitemporal history is incomplete for the selected memory.",
            route="none",
            model="none",
            prompt_tokens=0,
            completion_tokens=0,
            trace={**trace, "context_tokens": 0, "citation_entailment_pass": 0},
        )
    if not selected or (not active and not disputed):
        return _abstention_result(
            answer="I do not have a supported current claim for that question.",
            route="none",
            model="none",
            prompt_tokens=0,
            completion_tokens=0,
            trace={**trace, "context_tokens": 0, "citation_entailment_pass": 0},
        )

    (
        payload,
        context_counts,
        loaded_source_ids,
        context_tokens,
        context_active,
        context_disputed,
    ) = _build_context_payload(
        question,
        active=active,
        disputed=disputed,
        context_budget_chars=context_budget_chars,
    )
    trace.update(context_counts)
    trace["loaded_source_ids"] = loaded_source_ids
    trace["active_claim_ids_loaded"] = [
        item.claim.claim_id for item in context_active
    ]
    trace["disputed_claim_ids_loaded"] = [
        item.claim.claim_id for item in context_disputed
    ]
    trace["context_tokens"] = context_tokens
    light_resp = router.chat(
        Tier.LIGHT,
        system=QUERY_LIGHT_SYSTEM_PREFIX,
        user=payload,
        temperature=0.0,
        max_tokens=320,
    )
    light_parsed = _try_parse_query_json(light_resp.text)
    light_validated = _validate_answer(
        light_parsed,
        active=context_active,
        superseded=superseded,
        selected=selected,
    )
    light_prompt = int(getattr(light_resp, "prompt_tokens", 0))
    light_completion = int(getattr(light_resp, "completion_tokens", 0))
    if (
        light_validated is not None
        and not light_validated.abstained
        and light_validated.confidence >= confidence_threshold
    ):
        return _query_result(
            validated=light_validated,
            route=Tier.LIGHT.value,
            model=str(getattr(light_resp, "model", "unknown")),
            prompt_tokens=light_prompt,
            completion_tokens=light_completion,
            trace={**trace, "citation_entailment_pass": 1},
        )

    heavy_resp = router.chat(
        Tier.HEAVY,
        system=QUERY_HEAVY_SYSTEM_PREFIX,
        user=payload,
        temperature=0.0,
        max_tokens=480,
    )
    heavy_prompt = int(getattr(heavy_resp, "prompt_tokens", 0))
    heavy_completion = int(getattr(heavy_resp, "completion_tokens", 0))
    heavy_parsed = _try_parse_query_json(heavy_resp.text)
    heavy_validated = _validate_answer(
        heavy_parsed,
        active=context_active,
        superseded=superseded,
        selected=selected,
    )
    route = f"{Tier.LIGHT.value}->{Tier.HEAVY.value}"
    prompt_tokens = light_prompt + heavy_prompt
    completion_tokens = light_completion + heavy_completion
    if heavy_validated is None:
        return _abstention_result(
            answer="I cannot produce an evidence-valid cited answer from the selected memory.",
            route=route,
            model=str(getattr(heavy_resp, "model", "unknown")),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            trace={**trace, "citation_entailment_pass": 0},
        )
    return _query_result(
        validated=heavy_validated,
        route=route,
        model=str(getattr(heavy_resp, "model", "unknown")),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        trace={
            **trace,
            "citation_entailment_pass": int(not heavy_validated.abstained),
        },
    )


def select_top_k_pages(
    store: MemoryStore,
    question: str,
    k: int = 5,
    *,
    as_of: str | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
) -> list[WikiPage]:
    pages, _ = _select_top_k_pages_with_trace(
        store,
        question,
        k=k,
        as_of=as_of,
        valid_at=valid_at,
        known_at=known_at,
    )
    return pages


def _select_top_k_pages_with_trace(
    store: MemoryStore,
    question: str,
    *,
    k: int,
    as_of: str | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
    transition_events: list[dict[str, Any]] | None = None,
) -> tuple[list[WikiPage], dict[str, Any]]:
    slugs, trace = store.select_graph_candidates(
        question,
        k=k,
        as_of=as_of,
        valid_at=valid_at,
        known_at=known_at,
        transition_events=transition_events,
    )
    pages: list[WikiPage] = []
    for slug in slugs:
        try:
            pages.append(store.read_wiki_page(slug))
        except FileNotFoundError:
            continue
    trace["loaded_pages"] = len(pages)
    return pages, trace


def _build_claim_view(
    pages: list[WikiPage],
    valid_at: datetime,
    *,
    known_at: datetime | None = None,
    transition_events: list[dict[str, Any]] | None = None,
    revision_snapshots: dict[str, dict[str, Any]] | None = None,
    revision_claim_ids: frozenset[str] | set[str] | None = None,
    incomplete_revision_claim_ids: frozenset[str] | set[str] | None = None,
    projected_active_claim_ids: set[str] | None = None,
) -> tuple[list[_ContextClaim], list[_ContextClaim], dict[str, int], list[_ContextClaim]]:
    knowledge_cutoff = known_at or valid_at
    active_candidates: list[_ContextClaim] = []
    disputed: list[_ContextClaim] = []
    superseded: list[_ContextClaim] = []
    invalid = 0
    future_filtered = 0
    archived_filtered = 0
    observed_after_as_of_filtered = 0
    lifecycle_transitions_rewound = 0
    provenance_merges_rewound = 0
    revision_snapshots = revision_snapshots or {}
    tracked_revision_ids = set(revision_claim_ids or ())
    incomplete_revision_ids = set(incomplete_revision_claim_ids or ())
    graph_active_ids = set(projected_active_claim_ids or ())
    revision_snapshots_loaded = 0
    untracked_claim_histories = 0
    incomplete_claim_histories = 0
    knowledge_visible: list[tuple[_ContextClaim, datetime]] = []
    events_by_claim: dict[str, list[TransitionEvent]] = {}
    provenance_by_claim: dict[str, list[dict[str, Any]]] = {}
    for raw_event in transition_events or []:
        # Provenance merges share the append-only ledger but do not change a
        # claim's lifecycle state.
        if raw_event.get("event_type") == "provenance_merge":
            claim_id = str(raw_event.get("claim_id", ""))
            source_id = str(raw_event.get("source_id", ""))
            timestamp = str(raw_event.get("timestamp", ""))
            if not claim_id or not source_id or not timestamp:
                raise ValueError("invalid provenance merge event")
            _parse_timestamp(timestamp, "provenance merge timestamp")
            provenance_by_claim.setdefault(claim_id, []).append(raw_event)
            continue
        event = TransitionEvent.from_dict(raw_event)
        events_by_claim.setdefault(event.claim_id, []).append(event)
    for page in pages:
        raw_claims = page.metadata.get("claims", [])
        if not isinstance(raw_claims, list):
            invalid += 1
            continue
        for raw in raw_claims:
            if not isinstance(raw, dict):
                invalid += 1
                continue
            try:
                claim = Claim.from_dict(raw)
            except ValueError:
                invalid += 1
                continue
            current_claim_id = claim.claim_id
            revision_backed = current_claim_id in tracked_revision_ids
            if current_claim_id in incomplete_revision_ids:
                incomplete_claim_histories += 1
                superseded.append(_ContextClaim(page.slug, page.title, claim))
                continue
            if revision_backed:
                snapshot = revision_snapshots.get(current_claim_id)
                if snapshot is None:
                    observed_after_as_of_filtered += 1
                    superseded.append(_ContextClaim(page.slug, page.title, claim))
                    continue
                try:
                    claim = Claim.from_dict(snapshot)
                except ValueError:
                    invalid += 1
                    continue
                revision_snapshots_loaded += 1
            else:
                untracked_claim_histories += 1
            item = _ContextClaim(page.slug, page.title, claim)
            if _parse_timestamp(claim.observed_at, "observed_at") > knowledge_cutoff:
                observed_after_as_of_filtered += 1
                superseded.append(item)
                continue
            projected_status: ClaimStatus | None = claim.status
            if not revision_backed:
                for event in reversed(events_by_claim.get(claim.claim_id, [])):
                    if (
                        _parse_timestamp(event.timestamp, "transition timestamp")
                        <= knowledge_cutoff
                    ):
                        continue
                    if projected_status is not event.to_status:
                        raise ValueError(
                            "decision ledger cannot rewind canonical claim state: "
                            f"{claim.claim_id}"
                        )
                    projected_status = event.from_status
                    lifecycle_transitions_rewound += 1
            if projected_status is None:
                observed_after_as_of_filtered += 1
                superseded.append(item)
                continue
            if projected_status is not claim.status:
                claim = Claim.from_dict(
                    {**claim.to_dict(), "status": projected_status.value}
                )
            if (
                claim.claim_id in graph_active_ids
                and claim.status is not ClaimStatus.ACTIVE
            ):
                claim = Claim.from_dict(
                    {**claim.to_dict(), "status": ClaimStatus.ACTIVE.value}
                )
            hidden_sources = set() if revision_backed else {
                str(event["source_id"])
                for event in provenance_by_claim.get(claim.claim_id, [])
                if _parse_timestamp(
                    str(event["timestamp"]), "provenance merge timestamp"
                )
                > knowledge_cutoff
            }
            if hidden_sources:
                source_ids = [
                    source_id
                    for source_id in claim.source_ids
                    if source_id not in hidden_sources
                ]
                evidence = [
                    evidence.to_dict()
                    for evidence in claim.evidence
                    if evidence.source_id not in hidden_sources
                ]
                provenance_merges_rewound += len(hidden_sources)
                if not source_ids or not evidence:
                    invalid += 1
                    continue
                claim = Claim.from_dict(
                    {
                        **claim.to_dict(),
                        "source_ids": source_ids,
                        "evidence": evidence,
                    }
                )
            item = _ContextClaim(page.slug, page.title, claim)
            if claim.status is ClaimStatus.ARCHIVED:
                archived_filtered += 1
                superseded.append(item)
                continue
            valid_from = _parse_timestamp(
                claim.effective_at or claim.observed_at,
                "effective_at" if claim.effective_at else "observed_at",
            )
            knowledge_visible.append((item, valid_from))

    # A stored SUPERSEDED state is only eligible for valid-time rewind when a
    # knowledge-visible successor actually names it. This preserves the
    # long-standing safety rule that an orphaned stale claim cannot become
    # active merely because no successor happened to be retrieved.
    known_superseded_ids = {
        loser_id
        for item, _valid_from in knowledge_visible
        for loser_id in item.claim.supersedes
    }
    for item, valid_from in knowledge_visible:
        claim = item.claim
        if valid_from > valid_at:
            future_filtered += 1
            superseded.append(item)
            continue
        if (
            claim.status is ClaimStatus.SUPERSEDED
            and claim.claim_id not in known_superseded_ids
        ):
            superseded.append(item)
            continue
        if claim.status is ClaimStatus.DISPUTED:
            disputed.append(item)
            continue
        active_candidates.append(item)

    active: list[_ContextClaim] = []
    view_disputed = 0
    groups: dict[str, list[_ContextClaim]] = {}
    for item in active_candidates:
        groups.setdefault(item.claim.key, []).append(item)
    disputed_keys = {item.claim.key for item in disputed}
    for key, group in groups.items():
        if key in disputed_keys:
            disputed.extend(group)
            view_disputed += len(group)
            continue
        group_ids = {item.claim.claim_id for item in group}
        superseded_ids = {
            loser_id
            for item in group
            for loser_id in item.claim.supersedes
            if loser_id in group_ids
        }
        terminals = [
            item for item in group if item.claim.claim_id not in superseded_ids
        ]
        terminal_values = {item.claim.normalized_value for item in terminals}
        if terminals and len(terminal_values) == 1:
            active.extend(terminals)
            superseded.extend(
                item for item in group if item.claim.claim_id in superseded_ids
            )
            continue
        disputed.extend(group)
        view_disputed += len(group)

    return (
        active,
        disputed,
        {
            "active_claims_loaded": len(active),
            "disputed_claims_loaded": len(disputed),
            "superseded_claims_filtered": len(superseded),
            "future_effective_claims_filtered": future_filtered,
            "observed_after_as_of_claims_filtered": observed_after_as_of_filtered,
            "observed_after_known_at_claims_filtered": observed_after_as_of_filtered,
            "lifecycle_transitions_rewound": lifecycle_transitions_rewound,
            "provenance_merges_rewound": provenance_merges_rewound,
            "claim_revision_snapshots_loaded": revision_snapshots_loaded,
            "untracked_claim_histories": untracked_claim_histories,
            "incomplete_claim_histories": incomplete_claim_histories,
            "archived_claims_filtered": archived_filtered,
            "invalid_claims_filtered": invalid,
            "view_conflicts_disputed": view_disputed,
        },
        superseded,
    )


def _build_context_payload(
    question: str,
    *,
    active: list[_ContextClaim],
    disputed: list[_ContextClaim],
    context_budget_chars: int,
) -> tuple[
    str,
    dict[str, int],
    list[str],
    int,
    list[_ContextClaim],
    list[_ContextClaim],
]:
    envelope: dict[str, Any] = {
        "question": question,
        "active_claims": [],
        "disputed_claims": [],
    }
    loaded_sources: list[str] = []
    included = {
        "active_claims_loaded": 0,
        "disputed_claims_loaded": 0,
        "context_budget_claims_filtered": 0,
    }
    included_claims: dict[str, list[_ContextClaim]] = {
        "active_claims": [],
        "disputed_claims": [],
    }
    for label, claims, trace_key in (
        ("active_claims", active, "active_claims_loaded"),
        ("disputed_claims", disputed, "disputed_claims_loaded"),
    ):
        for item in claims:
            answer_evidence = [
                evidence.span
                for evidence in item.claim.evidence
                if _contains_value(evidence.span, item.claim.value)
            ] or [evidence.span for evidence in item.claim.evidence]
            candidate = {
                "citation_id": item.page_slug,
                "claim_id": item.claim.claim_id,
                "key": item.claim.key,
                "value": item.claim.value,
                "evidence_spans": [span[:500] for span in answer_evidence],
            }
            envelope[label].append(candidate)
            serialized = json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(serialized) > context_budget_chars:
                envelope[label].pop()
                included["context_budget_claims_filtered"] += 1
                continue
            included[trace_key] += 1
            included_claims[label].append(item)
            loaded_sources.extend(item.claim.source_ids)
    payload = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    context_only = json.dumps(
        {
            "active_claims": envelope["active_claims"],
            "disputed_claims": envelope["disputed_claims"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        payload,
        included,
        list(dict.fromkeys(loaded_sources)),
        max(1, len(context_only.encode("utf-8")) // 4),
        included_claims["active_claims"],
        included_claims["disputed_claims"],
    )


def _try_parse_query_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    expected = {"answer", "facts", "citations", "confidence", "abstained"}
    if set(data) != expected:
        return None
    if not isinstance(data["answer"], str) or not isinstance(data["abstained"], bool):
        return None
    if not isinstance(data["facts"], list) or not isinstance(data["citations"], list):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in data["citations"]):
        return None
    try:
        confidence = float(data["confidence"])
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    facts: list[dict[str, Any]] = []
    for raw in data["facts"]:
        if not isinstance(raw, dict) or set(raw) != {"key", "value", "claim_ids"}:
            return None
        if not isinstance(raw["key"], str) or not raw["key"].strip():
            return None
        if not isinstance(raw["value"], str) or not raw["value"].strip():
            return None
        if (
            not isinstance(raw["claim_ids"], list)
            or not raw["claim_ids"]
            or any(not isinstance(item, str) or not item.strip() for item in raw["claim_ids"])
        ):
            return None
        facts.append(
            {
                "key": raw["key"].strip(),
                "value": raw["value"].strip(),
                "claim_ids": list(dict.fromkeys(item.strip() for item in raw["claim_ids"])),
            }
        )
    if data["abstained"] and (facts or data["citations"]):
        return None
    if not data["abstained"] and (not facts or not data["citations"]):
        return None
    return {
        "answer": data["answer"].strip(),
        "facts": facts,
        "citations": [item.strip().lower() for item in data["citations"]],
        "confidence": confidence,
        "abstained": data["abstained"],
    }


def _validate_answer(
    parsed: dict[str, Any] | None,
    *,
    active: list[_ContextClaim],
    superseded: list[_ContextClaim],
    selected: list[WikiPage],
) -> _ValidatedAnswer | None:
    if parsed is None:
        return None
    if parsed["abstained"]:
        return _ValidatedAnswer(
            answer="Insufficient supported memory.",
            citations=[],
            confidence=parsed["confidence"],
            facts=[],
            claim_ids=[],
            source_ids=[],
            abstained=True,
        )

    allowed_pages = {page.slug for page in selected}
    raw_citations = list(dict.fromkeys(parsed["citations"]))
    citations = [citation for citation in raw_citations if citation in allowed_pages]
    if not citations or len(citations) != len(raw_citations):
        return None

    active_by_id = {item.claim.claim_id: item for item in active}
    used_claim_ids: list[str] = []
    used_source_ids: list[str] = []
    used_page_slugs: set[str] = set()
    canonical_facts: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in parsed["facts"]:
        referenced: list[_ContextClaim] = []
        for claim_id in fact["claim_ids"]:
            item = active_by_id.get(claim_id)
            if item is None:
                return None
            referenced.append(item)
        expected_value = normalize_component(fact["value"])
        if any(
            item.claim.key != fact["key"]
            or item.claim.normalized_value != expected_value
            or item.page_slug not in citations
            for item in referenced
        ):
            return None
        entailing_sources = [
            list(
                dict.fromkeys(
                    evidence.source_id
                    for evidence in item.claim.evidence
                    if evidence.source_id in item.claim.source_ids
                    and _contains_value(evidence.span, fact["value"])
                )
            )
            for item in referenced
        ]
        if any(not sources for sources in entailing_sources):
            return None
        if not _contains_value(parsed["answer"], fact["value"]):
            return None
        for item, source_ids in zip(referenced, entailing_sources, strict=True):
            used_claim_ids.append(item.claim.claim_id)
            used_source_ids.extend(source_ids)
            used_page_slugs.add(item.page_slug)
            pair = (item.claim.key, item.claim.normalized_value)
            canonical = canonical_facts.setdefault(
                pair,
                {
                    "key": item.claim.key,
                    "value": item.claim.value,
                    "claim_ids": [],
                },
            )
            canonical["claim_ids"].append(item.claim.claim_id)

    if set(citations) != used_page_slugs:
        return None

    supported_values = {
        normalize_component(fact["value"])
        for fact in parsed["facts"]
    }
    for stale in superseded:
        if (
            stale.claim.normalized_value not in supported_values
            and _contains_value(parsed["answer"], stale.claim.value)
        ):
            return None

    validated_facts = sorted(
        (
            {
                **fact,
                "claim_ids": sorted(set(fact["claim_ids"])),
            }
            for fact in canonical_facts.values()
        ),
        key=lambda fact: (str(fact["key"]), normalize_component(fact["value"])),
    )
    return _ValidatedAnswer(
        # The model selects and cites facts, but it does not get an unchecked
        # prose channel in which unsupported assertions can hide.
        answer=_render_facts(validated_facts),
        citations=citations,
        confidence=parsed["confidence"],
        facts=validated_facts,
        claim_ids=list(dict.fromkeys(used_claim_ids)),
        source_ids=list(dict.fromkeys(used_source_ids)),
        abstained=False,
    )


def _contains_value(text: str, value: str) -> bool:
    haystack = normalize_component(text)
    needle = normalize_component(value)
    if not needle:
        return False
    if re.fullmatch(r"[\w.%-]+", needle, flags=re.UNICODE):
        return bool(re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))
    return needle in haystack


def _render_facts(facts: list[dict[str, Any]]) -> str:
    return " ".join(
        f"{str(fact['key']).strip()} = {str(fact['value']).strip()}."
        for fact in facts
    )


def _query_result(
    *,
    validated: _ValidatedAnswer,
    route: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    trace: dict[str, Any],
) -> QueryResult:
    return QueryResult(
        answer=validated.answer,
        citations=validated.citations,
        confidence=validated.confidence,
        route=route,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_version=PROMPT_VERSION,
        facts=validated.facts,
        evidence_claim_ids=validated.claim_ids,
        evidence_source_ids=validated.source_ids,
        abstained=validated.abstained,
        trace=trace,
    )


def _abstention_result(
    *,
    answer: str,
    route: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    trace: dict[str, Any],
) -> QueryResult:
    return QueryResult(
        answer=answer,
        citations=[],
        confidence=0.0,
        route=route,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_version=PROMPT_VERSION,
        facts=[],
        evidence_claim_ids=[],
        evidence_source_ids=[],
        abstained=True,
        trace=trace,
    )


def _resolve_temporal_cutoffs(
    *,
    as_of: str | None,
    valid_at: str | None,
    known_at: str | None,
) -> tuple[datetime, datetime]:
    if as_of is not None and (valid_at is not None or known_at is not None):
        raise ValueError("as_of cannot be combined with valid_at or known_at")
    if (valid_at is None) != (known_at is None):
        raise ValueError("valid_at and known_at must be provided together")
    if as_of is not None:
        cutoff = _parse_timestamp(as_of, "as_of")
        return cutoff, cutoff
    if valid_at is not None and known_at is not None:
        return (
            _parse_timestamp(valid_at, "valid_at"),
            _parse_timestamp(known_at, "known_at"),
        )
    now = datetime.now(UTC)
    return now, now


def _parse_timestamp(value: str, label: str) -> datetime:
    normalized = canonical_timestamp(value, label)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))

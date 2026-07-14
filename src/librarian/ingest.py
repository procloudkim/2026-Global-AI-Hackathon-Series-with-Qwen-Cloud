"""Ingest pipeline: immutable source -> atomic claims -> lifecycle reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any, Protocol

from .claims import (
    Claim,
    ClaimStatus,
    EvidenceRef,
    Relation,
    RelationDecision,
    TransitionEvent,
    canonical_timestamp,
    has_explicit_supersession,
    normalize_component,
    supersession_evidence_binds_winner,
)
from .llm import Tier
from .prompts import INGEST_SYSTEM_PREFIX, PROMPT_VERSION, RELATION_SYSTEM_PREFIX
from .store import MemoryStore, WikiPage

_MEMORY_SCHEMA_VERSION = "librarian-memory/v2"
_EXPLICIT_RESTORE = re.compile(
    r"\b(restores?|reinstates?)\b|복원",
    flags=re.IGNORECASE,
)
_GENERIC_VERSION_REPLACEMENT = re.compile(
    r"^\s*(?:version|revision|v)\s*[\w.-]+\s+"
    r"(?:explicitly\s+)?(?:replaces?|supersedes?)\s+"
    r"(?:version|revision|v)\s*[\w.-]+[.!?]?\s*$",
    flags=re.IGNORECASE,
)
_CROSS_SENTENCE_RECORD_REPLACEMENT = re.compile(
    r"\bthis\s+record\b.*\b(?:replaces?|supersedes?)\s+(?:src|source)[\w.-]+",
    flags=re.IGNORECASE,
)
_EXPLICIT_POSSESSIVE_ASSERTION = re.compile(
    r"\bin\s+(?P<scope>[^\W_][\w.-]*)\s*,\s*"
    r"(?P<subject>[^\s,;:]+?)['’]s\s+"
    r"(?P<predicate>[^\W][\w.-]*)\s+is\s+"
    r"(?P<value>[^.!?\r\n]+?)(?:[.!?]|$)",
    flags=re.IGNORECASE | re.UNICODE,
)


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
class IngestResult:
    page: WikiPage
    source_path: str
    prompt_version: str
    route_tier: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    claim_ids: list[str]
    transition_events: list[dict[str, Any]]
    trace: dict[str, int]


def ingest_source(
    *,
    source_id: str,
    source_text: str,
    store: MemoryStore,
    router: SupportsChat,
    observed_at: str | None = None,
) -> IngestResult:
    with store.transaction():
        return _ingest_source_locked(
            source_id=source_id,
            source_text=source_text,
            store=store,
            router=router,
            observed_at=observed_at,
        )


def _ingest_source_locked(
    *,
    source_id: str,
    source_text: str,
    store: MemoryStore,
    router: SupportsChat,
    observed_at: str | None = None,
) -> IngestResult:
    """Extract source-grounded claims and reconcile only affected claim keys."""
    pending_transition_recovered = store.recover_pending_transition()
    recovered_ingest_keys = store.recover_pending_ingest(
        prompt_version=PROMPT_VERSION,
    )
    projection_recovery_applied = store.repair_dirty_projection()
    source_id = source_id.strip()
    if not source_id:
        raise ValueError("source_id must be non-empty")
    if not source_text.strip():
        raise ValueError("source_text must be non-empty")
    timestamp = canonical_timestamp(
        observed_at or datetime.now(UTC).isoformat(), "observed_at"
    )

    source_path = store.save_raw_source(source_id, source_text)
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    user_payload = (
        f"Source ID:\n{source_id}\n\n"
        f"Observed at:\n{timestamp}\n\n"
        f"Raw source:\n{source_text[:12000]}"
    )
    light_resp = router.chat(
        Tier.LIGHT,
        system=INGEST_SYSTEM_PREFIX,
        user=user_payload,
        temperature=0.0,
        max_tokens=1400,
    )
    payload = _parse_ingest_json(light_resp.text)
    extracted = _materialize_claims(
        payload["claims"],
        source_id=source_id,
        source_hash=source_hash,
        source_text=source_text,
        observed_at=timestamp,
    )
    incoming_values_by_key: dict[str, set[str]] = {}
    for claim in extracted:
        incoming_values_by_key.setdefault(claim.key, set()).add(
            claim.normalized_value
        )
    multi_value_keys = {
        key for key, values in incoming_values_by_key.items() if len(values) > 1
    }
    allow_generic_version_replacement = len(extracted) == 1

    prompt_tokens = int(getattr(light_resp, "prompt_tokens", 0))
    completion_tokens = int(getattr(light_resp, "completion_tokens", 0))
    models = [str(getattr(light_resp, "model", "unknown"))]
    heavy_calls = 0
    invalid_existing_claims = 0
    duplicate_merges = 0
    transition_events: list[dict[str, Any]] = []

    title = payload["title"].strip() or source_id
    target_slug = store.slug_for(title)
    target_exists = (store.wiki_dir / f"{target_slug}.md").exists()
    if extracted:
        prior_claims: list[tuple[str, Claim]] = []
        seen_prior_claim_ids: set[str] = set()
        for affected_key in sorted(incoming_values_by_key):
            candidates, _ = _claims_for_key(store, affected_key)
            for page_slug, candidate in candidates:
                if candidate.claim_id in seen_prior_claim_ids:
                    continue
                seen_prior_claim_ids.add(candidate.claim_id)
                prior_claims.append((page_slug, candidate))
        store.stage_ingest_operation(
            source_id=source_id,
            source_hash=source_hash,
            observed_at=timestamp,
            target_slug=target_slug,
            affected_keys=sorted(incoming_values_by_key),
            incoming_claim_ids=[claim.claim_id for claim in extracted],
            prior_claims=prior_claims,
        )
    additions: list[Claim] = []
    duplicate_pages: list[WikiPage] = []
    reconciliation_targets: list[tuple[str, str]] = []
    canonical_claim_ids: list[str] = []
    restore_signal = bool(_EXPLICIT_RESTORE.search(source_text))
    recorded_events = store.decision_events()
    known_event_ids = {
        str(event.get("event_id", "")) for event in recorded_events
    }
    creation_receipts = {
        (str(event.get("page_slug", "")), str(event.get("claim_id", "")))
        for event in recorded_events
        if event.get("from_status") is None and event.get("to_status") == "active"
    }

    for new_claim in extracted:
        candidates, invalid = _claims_for_key(store, new_claim.key)
        invalid_existing_claims += invalid
        duplicate = next(
            (
                (page_slug, existing)
                for page_slug, existing in candidates
                if existing.claim_id == new_claim.claim_id
            ),
            None,
        )
        if duplicate is None:
            additions.append(new_claim)
            canonical_claim_ids.append(new_claim.claim_id)
            continue
        page_slug, existing = duplicate
        merged = _merge_provenance(existing, new_claim)
        provenance_changed = merged != existing
        merged_page = (
            _replace_claim(store, page_slug, merged)
            if provenance_changed
            else store.read_wiki_page(page_slug)
        )
        duplicate_pages.append(merged_page)
        canonical_claim_ids.append(merged.claim_id)
        if (page_slug, merged.claim_id) not in creation_receipts:
            creation = _transition_event(
                page_slug=page_slug,
                claim=merged,
                from_status=None,
                to_status=ClaimStatus.ACTIVE,
                timestamp=merged.observed_at,
                trigger_claim_id=None,
                rule="source_grounded_claim_creation",
                relation=None,
                model=models[0],
                evidence_claims=[merged],
                rationale="Recovered missing source-grounded creation receipt on retry.",
            )
            store.append_decision_event(creation)
            transition_events.append(creation.to_dict())
            known_event_ids.add(creation.event_id)
            creation_receipts.add((page_slug, merged.claim_id))
        has_live_conflict = (
            existing.status in {ClaimStatus.ACTIVE, ClaimStatus.DISPUTED}
            and any(
                candidate.claim_id != existing.claim_id
                and candidate.normalized_value != existing.normalized_value
                and candidate.status in {ClaimStatus.ACTIVE, ClaimStatus.DISPUTED}
                for _, candidate in candidates
            )
        )
        if restore_signal or has_live_conflict or merged.key in recovered_ingest_keys:
            reconciliation_targets.append((page_slug, merged.claim_id))
        merge_event = {
            "schema_version": _MEMORY_SCHEMA_VERSION,
            "event_id": _event_id(
                page_slug,
                merged.claim_id,
                existing.status.value,
                existing.status.value,
                timestamp,
                "duplicate_provenance_merge",
                source_id,
            ),
            "timestamp": timestamp,
            "event_type": "provenance_merge",
            "page_slug": page_slug,
            "claim_id": merged.claim_id,
            "source_id": source_id,
            "rule": "exact_key_value_effective_time",
        }
        should_record_merge = provenance_changed or len(existing.source_ids) > 1
        if should_record_merge and merge_event["event_id"] not in known_event_ids:
            store.append_decision_event(merge_event)
            known_event_ids.add(str(merge_event["event_id"]))
            duplicate_merges += 1

    existing_target_claims: list[dict[str, Any]] = []
    existing_sources: list[str] = []
    if target_exists:
        current_target = store.read_wiki_page(target_slug)
        existing_target_claims = store.claims_for_page(current_target)
        raw_sources = current_target.metadata.get("sources", [])
        if isinstance(raw_sources, list):
            existing_sources = [str(item) for item in raw_sources]

    page: WikiPage
    if target_exists or additions or not extracted:
        page = store.upsert_wiki_page(
            title=title,
            body=payload["body"].strip() or payload["summary"].strip(),
            slug=target_slug,
            metadata={
                "schema_version": _MEMORY_SCHEMA_VERSION,
                "summary": payload["summary"].strip(),
                "links": _as_string_list(payload["links"]),
                "tags": _as_string_list(payload["tags"]),
                "sources": sorted(set([*existing_sources, source_id])),
                "claims": [*existing_target_claims, *(c.to_dict() for c in additions)],
                "prompt_version": PROMPT_VERSION,
            },
        )
    elif duplicate_pages:
        page = duplicate_pages[0]
    else:  # defensive: the empty extraction branch above normally owns this case
        page = store.upsert_wiki_page(
            title=title,
            body=payload["body"].strip(),
            metadata={"schema_version": _MEMORY_SCHEMA_VERSION, "claims": []},
        )

    for claim in additions:
        creation = _transition_event(
            page_slug=page.slug,
            claim=claim,
            from_status=None,
            to_status=ClaimStatus.ACTIVE,
            timestamp=timestamp,
            trigger_claim_id=None,
            rule="source_grounded_claim_creation",
            relation=None,
            model=models[0],
            evidence_claims=[claim],
            rationale="Atomic claim extracted from exact source evidence.",
        )
        store.append_decision_event(creation)
        transition_events.append(creation.to_dict())
        reconciliation_targets.append((page.slug, claim.claim_id))

    processed_multi_value_keys: set[str] = set()
    for new_page_slug, claim_id in reconciliation_targets:
        new_claim = _claim_on_page(store, new_page_slug, claim_id)
        if new_claim is None:
            continue
        candidates, invalid = _claims_for_key(store, new_claim.key)
        invalid_existing_claims += invalid
        if new_claim.key in multi_value_keys:
            if new_claim.key in processed_multi_value_keys:
                continue
            processed_multi_value_keys.add(new_claim.key)
            evidence_claims = [
                candidate
                for _, candidate in candidates
                if candidate.status is not ClaimStatus.ARCHIVED
            ]
            for candidate_slug, candidate in candidates:
                if candidate.status is not ClaimStatus.ACTIVE:
                    continue
                event = _apply_transition(
                    store,
                    page_slug=candidate_slug,
                    claim_id=candidate.claim_id,
                    to_status=ClaimStatus.DISPUTED,
                    timestamp=timestamp,
                    trigger_claim_id=None,
                    rule="multi_claim_source_conflict",
                    relation=Relation.UNRESOLVED,
                    model=None,
                    evidence_claims=evidence_claims,
                    rationale=(
                        "One source asserted multiple values for the same claim key; "
                        "no extraction order is allowed to select a winner."
                    ),
                )
                if event is not None:
                    transition_events.append(event.to_dict())
            continue
        all_conflicts = [
            (candidate_slug, candidate)
            for candidate_slug, candidate in candidates
            if candidate.claim_id != new_claim.claim_id
            and candidate.normalized_value != new_claim.normalized_value
            and candidate.status is not ClaimStatus.ARCHIVED
        ]
        new_is_future = _is_future_effective(new_claim, timestamp)
        conflicts = (
            all_conflicts
            if new_is_future
            else [
                item
                for item in all_conflicts
                if not _is_future_effective(item[1], timestamp)
            ]
        )
        if not conflicts:
            continue

        current_or_disputed = [
            item
            for item in conflicts
            if item[1].status in {ClaimStatus.ACTIVE, ClaimStatus.DISPUTED}
        ]
        explicit = _has_scoped_replacement(
            source_text,
            new_claim,
            allow_generic_version=allow_generic_version_replacement,
            candidate_source_ids={
                source
                for _, candidate in current_or_disputed
                for source in candidate.source_ids
            },
            candidate_values={
                candidate.value for _, candidate in current_or_disputed
            },
        )
        effective_signal = _has_explicit_effective_signal(source_text, new_claim)
        dated_winner = (
            _explicit_temporal_winner(new_claim, conflicts, timestamp)
            if effective_signal
            else None
        )
        if new_is_future:
            same_effective = _same_effective_records(
                new_claim, current_or_disputed
            )
            if same_effective:
                evidence_claims = [
                    new_claim, *(claim for _, claim in same_effective)
                ]
                for candidate_slug, candidate in [
                    (new_page_slug, new_claim), *same_effective
                ]:
                    if candidate.status is not ClaimStatus.ACTIVE:
                        continue
                    event = _apply_transition(
                        store,
                        page_slug=candidate_slug,
                        claim_id=candidate.claim_id,
                        to_status=ClaimStatus.DISPUTED,
                        timestamp=timestamp,
                        trigger_claim_id=same_effective[0][1].claim_id,
                        rule="same_effective_time_conflict",
                        relation=Relation.UNRESOLVED,
                        model=None,
                        evidence_claims=evidence_claims,
                        rationale=(
                            "Different values share the same effective time; "
                            "no ingest order may choose a winner."
                        ),
                    )
                    if event is not None:
                        transition_events.append(event.to_dict())
                continue
            if explicit or effective_signal:
                predecessors, successors = _temporal_neighbors(
                    new_claim,
                    current_or_disputed,
                )
                new_claim = _add_relation_evidence(
                    store,
                    new_page_slug,
                    new_claim,
                    source_id=source_id,
                    source_hash=source_hash,
                    source_text=source_text,
                    allow_generic_version=allow_generic_version_replacement,
                )
                new_claim = _add_supersedes(
                    store,
                    new_page_slug,
                    new_claim,
                    [claim.claim_id for _, claim in predecessors],
                )
                if len(successors) == 1:
                    successor_slug, successor = successors[0]
                    _splice_successor_supersedes(
                        store,
                        successor_slug,
                        successor,
                        replaced_predecessor_ids={
                            claim.claim_id for _, claim in predecessors
                        },
                        new_predecessor_id=new_claim.claim_id,
                    )
            continue
        if explicit or dated_winner == new_claim.claim_id:
            new_claim = _add_relation_evidence(
                store,
                new_page_slug,
                new_claim,
                source_id=source_id,
                source_hash=source_hash,
                source_text=source_text,
                allow_generic_version=allow_generic_version_replacement,
            )
            loser_ids = [claim.claim_id for _, claim in current_or_disputed]
            if loser_ids:
                new_claim = _add_supersedes(store, new_page_slug, new_claim, loser_ids)
            if new_claim.status in {ClaimStatus.DISPUTED, ClaimStatus.SUPERSEDED}:
                event = _apply_transition(
                    store,
                    page_slug=new_page_slug,
                    claim_id=new_claim.claim_id,
                    to_status=ClaimStatus.ACTIVE,
                    timestamp=timestamp,
                    trigger_claim_id=None,
                    rule="explicit_restore",
                    relation=Relation.SUPERSEDES,
                    model=None,
                    evidence_claims=[new_claim, *(claim for _, claim in conflicts)],
                    rationale="Explicit restore evidence reactivates the previously supported value.",
                )
                if event is not None:
                    transition_events.append(event.to_dict())
            for loser_slug, loser in current_or_disputed:
                event = _apply_transition(
                    store,
                    page_slug=loser_slug,
                    claim_id=loser.claim_id,
                    to_status=ClaimStatus.SUPERSEDED,
                    timestamp=timestamp,
                    trigger_claim_id=new_claim.claim_id,
                    rule=(
                        "explicit_replacement_language"
                        if explicit
                        else "explicit_effective_time"
                    ),
                    relation=Relation.SUPERSEDES,
                    model=None,
                    evidence_claims=[new_claim, loser],
                    rationale="Explicit source evidence establishes the newer current value.",
                )
                if event is not None:
                    transition_events.append(event.to_dict())
            continue

        decision, heavy_resp = _arbitrate_relation(
            router=router,
            new_claim=new_claim,
            candidates=[claim for _, claim in conflicts],
        )
        heavy_calls += 1
        prompt_tokens += int(getattr(heavy_resp, "prompt_tokens", 0))
        completion_tokens += int(getattr(heavy_resp, "completion_tokens", 0))
        models.append(str(getattr(heavy_resp, "model", "unknown")))
        provided_claims = [new_claim, *(claim for _, claim in conflicts)]
        decision = _validated_decision(decision, provided_claims)

        if decision.relation is Relation.SUPERSEDES and decision.winner_claim_id:
            winner_record = next(
                item
                for item in [(new_page_slug, new_claim), *conflicts]
                if item[1].claim_id == decision.winner_claim_id
            )
            loser_records = [
                item
                for item in [(new_page_slug, new_claim), *conflicts]
                if item[1].claim_id != decision.winner_claim_id
                and item[1].status in {ClaimStatus.ACTIVE, ClaimStatus.DISPUTED}
            ]
            winner_slug, winner = winner_record
            winner = _add_supersedes(
                store,
                winner_slug,
                winner,
                [claim.claim_id for _, claim in loser_records],
            )
            refreshed_winner = _claim_on_page(store, winner_slug, winner.claim_id)
            if refreshed_winner and refreshed_winner.status in {
                ClaimStatus.DISPUTED,
                ClaimStatus.SUPERSEDED,
            }:
                event = _apply_transition(
                    store,
                    page_slug=winner_slug,
                    claim_id=winner.claim_id,
                    to_status=ClaimStatus.ACTIVE,
                    timestamp=timestamp,
                    trigger_claim_id=new_claim.claim_id,
                    rule="validated_heavy_arbitration",
                    relation=Relation.SUPERSEDES,
                    model=str(getattr(heavy_resp, "model", "unknown")),
                    evidence_claims=provided_claims,
                    rationale=decision.rationale,
                    evidence_source_ids=list(decision.evidence_source_ids),
                    evidence_spans=list(decision.evidence_spans),
                )
                if event is not None:
                    transition_events.append(event.to_dict())
            for loser_slug, loser in loser_records:
                event = _apply_transition(
                    store,
                    page_slug=loser_slug,
                    claim_id=loser.claim_id,
                    to_status=ClaimStatus.SUPERSEDED,
                    timestamp=timestamp,
                    trigger_claim_id=winner.claim_id,
                    rule="validated_heavy_arbitration",
                    relation=Relation.SUPERSEDES,
                    model=str(getattr(heavy_resp, "model", "unknown")),
                    evidence_claims=provided_claims,
                    rationale=decision.rationale,
                    evidence_source_ids=list(decision.evidence_source_ids),
                    evidence_spans=list(decision.evidence_spans),
                )
                if event is not None:
                    transition_events.append(event.to_dict())
        else:
            relation = (
                decision.relation
                if decision.relation is Relation.CONTRADICTS
                else Relation.UNRESOLVED
            )
            for candidate_slug, candidate in [(new_page_slug, new_claim), *conflicts]:
                if candidate.status is not ClaimStatus.ACTIVE:
                    continue
                event = _apply_transition(
                    store,
                    page_slug=candidate_slug,
                    claim_id=candidate.claim_id,
                    to_status=ClaimStatus.DISPUTED,
                    timestamp=timestamp,
                    trigger_claim_id=new_claim.claim_id,
                    rule="ambiguous_conflict",
                    relation=relation,
                    model=str(getattr(heavy_resp, "model", "unknown")),
                    evidence_claims=provided_claims,
                    rationale=decision.rationale,
                )
                if event is not None:
                    transition_events.append(event.to_dict())

    # From this point every canonical mutation and lifecycle receipt for the
    # operation is durable.  Mark committed before best-effort presentation log
    # work so an unrelated log failure cannot trigger conservative rollback.
    store.complete_ingest_operation()
    page = store.read_wiki_page(page.slug)
    store.append_log(
        "ingest",
        f"{source_id} -> {page.slug} claims={len(extracted)} transitions={len(transition_events)}",
    )
    unique_models = list(dict.fromkeys(models))
    return IngestResult(
        page=page,
        source_path=str(source_path),
        prompt_version=PROMPT_VERSION,
        route_tier=(
            f"{Tier.LIGHT.value}->{Tier.HEAVY.value}"
            if heavy_calls
            else Tier.LIGHT.value
        ),
        model=" -> ".join(unique_models),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        claim_ids=list(dict.fromkeys(canonical_claim_ids)),
        transition_events=transition_events,
        trace={
            "claims_extracted": len(extracted),
            "duplicate_provenance_merges": duplicate_merges,
            "lifecycle_transitions": len(transition_events),
            "heavy_arbitrations": heavy_calls,
            "invalid_existing_claims": invalid_existing_claims,
            "pending_transition_recovered": int(pending_transition_recovered),
            "pending_ingest_keys_recovered": len(recovered_ingest_keys),
            "projection_recovery_applied": int(projection_recovery_applied),
        },
    )


def _parse_ingest_json(text: str) -> dict[str, Any]:
    data = _parse_json_object(text, "ingest")
    expected = {"title", "summary", "body", "links", "tags", "claims"}
    missing = expected.difference(data)
    unknown = set(data).difference(expected)
    if missing or unknown:
        raise ValueError(
            "ingest output fields mismatch: "
            f"missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    for required in ("title", "summary", "body"):
        if not isinstance(data[required], str):
            raise ValueError(f"ingest field {required} must be a string")
    for required in ("links", "tags", "claims"):
        if not isinstance(data[required], list):
            raise ValueError(f"ingest field {required} must be an array")
    for required in ("links", "tags"):
        if any(not isinstance(item, str) for item in data[required]):
            raise ValueError(f"ingest field {required} must contain only strings")
    return data


def _materialize_claims(
    raw_claims: list[Any],
    *,
    source_id: str,
    source_hash: str,
    source_text: str,
    observed_at: str,
) -> list[Claim]:
    expected = {
        "kind",
        "scope",
        "subject",
        "predicate",
        "value",
        "effective_at",
        "evidence_spans",
    }
    by_id: dict[str, Claim] = {}
    for index, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            raise ValueError(f"claim[{index}] must be an object")
        if set(raw) != expected:
            raise ValueError(f"claim[{index}] fields do not match the v2 contract")
        if not isinstance(raw["evidence_spans"], list) or any(
            not isinstance(item, str) for item in raw["evidence_spans"]
        ):
            raise ValueError(f"claim[{index}].evidence_spans must contain only strings")
        spans = _as_string_list(raw["evidence_spans"])
        if not spans:
            raise ValueError(f"claim[{index}] must contain evidence_spans")
        for span in spans:
            if span not in source_text:
                raise ValueError(
                    f"claim[{index}] evidence span is not verbatim in the raw source"
                )
        raw = _repair_explicit_possessive_components(raw, spans=spans)
        value = _required_string(raw, "value", index)
        scope = _required_string(raw, "scope", index)
        subject = _required_string(raw, "subject", index)
        predicate = _required_string(raw, "predicate", index)
        spans = _expand_unique_value_spans(
            spans,
            source_text=source_text,
            scope=scope,
            subject=subject,
            predicate=predicate,
            value=value,
        )
        if not any(_span_supports_value(span, value) for span in spans):
            raise ValueError(
                f"claim[{index}] value is not present in its exact evidence spans"
            )
        if not any(
            _claim_key_span_supports_components(
                span,
                scope=scope,
                subject=subject,
                predicate=predicate,
                value=value,
            )
            for span in spans
        ):
            raise ValueError(
                f"claim[{index}] scope/subject/predicate are not grounded in "
                "the value-bearing evidence sentence"
            )
        effective_at = raw["effective_at"]
        if effective_at is not None:
            if not isinstance(effective_at, str):
                raise ValueError(f"claim[{index}].effective_at must be a string or null")
            effective_at = canonical_timestamp(
                effective_at, f"claim[{index}].effective_at"
            )
            if not any(
                _effective_span_supports_components(
                    span,
                    effective_at=effective_at,
                    subject=subject,
                    predicate=predicate,
                    value=value,
                )
                for span in spans
            ):
                raise ValueError(
                    f"claim[{index}].effective_at is not grounded in a "
                    "claim-scoped evidence sentence"
                )
        evidence = [
            EvidenceRef.create(
                source_id=source_id,
                source_hash=source_hash,
                span=span,
            )
            for span in spans
        ]
        claim = Claim.create(
            kind=raw["kind"],
            scope=scope,
            subject=subject,
            predicate=predicate,
            value=value,
            observed_at=observed_at,
            effective_at=effective_at,
            source_ids=[source_id],
            evidence=evidence,
        )
        previous = by_id.get(claim.claim_id)
        by_id[claim.claim_id] = claim if previous is None else _merge_provenance(previous, claim)
    return list(by_id.values())


def _repair_explicit_possessive_components(
    raw: dict[str, Any], *, spans: list[str]
) -> dict[str, Any]:
    """Canonicalize an unambiguous claim key from its own exact evidence.

    The model occasionally drops an explicit scope or prefixes a predicate with
    ``has``.  For the narrow possessive assertion grammar below, the source
    sentence itself is authoritative.  Ambiguous or non-matching evidence is
    left untouched so the normal grounding validator remains fail-closed.
    """

    subject = raw.get("subject")
    value = raw.get("value")
    if not isinstance(subject, str) or not isinstance(value, str):
        return raw
    matches: set[tuple[str, str]] = set()
    for span in spans:
        for match in _EXPLICIT_POSSESSIVE_ASSERTION.finditer(span):
            if (
                normalize_component(match.group("subject"))
                == normalize_component(subject)
                and normalize_component(match.group("value"))
                == normalize_component(value)
            ):
                matches.add((match.group("scope"), match.group("predicate")))
    if len(matches) != 1:
        return raw
    scope, predicate = next(iter(matches))
    return {**raw, "scope": scope, "predicate": predicate}


def _claims_for_key(store: MemoryStore, key: str) -> tuple[list[tuple[str, Claim]], int]:
    records: list[tuple[str, Claim]] = []
    invalid = 0
    for slug in store.find_claim_pages(key):
        for raw in store.claims_for_page(slug):
            try:
                claim = Claim.from_dict(raw)
            except ValueError:
                invalid += 1
                continue
            if claim.key == key:
                records.append((slug, claim))
    return records, invalid


def _claim_on_page(store: MemoryStore, page_slug: str, claim_id: str) -> Claim | None:
    for raw in store.claims_for_page(page_slug):
        if str(raw.get("claim_id", "")) != claim_id:
            continue
        try:
            return Claim.from_dict(raw)
        except ValueError:
            return None
    return None


def _replace_claim(store: MemoryStore, page_slug: str, replacement: Claim) -> WikiPage:
    page = store.read_wiki_page(page_slug)
    raw_claims = store.claims_for_page(page)
    raw_sources = page.metadata.get("sources", [])
    page_sources = [str(item) for item in raw_sources] if isinstance(raw_sources, list) else []
    for index, raw in enumerate(raw_claims):
        if str(raw.get("claim_id", "")) == replacement.claim_id:
            raw_claims[index] = replacement.to_dict()
            return store.write_page_claims(
                page_slug,
                raw_claims,
                metadata_updates={
                    "sources": sorted(set([*page_sources, *replacement.source_ids]))
                },
            )
    raise KeyError(f"claim not found on page {page_slug}: {replacement.claim_id}")


def _merge_provenance(existing: Claim, incoming: Claim) -> Claim:
    evidence_by_source = {
        (item.evidence_id, item.source_id): item for item in existing.evidence
    }
    evidence_by_source.update(
        {
            (item.evidence_id, item.source_id): item
            for item in incoming.evidence
        }
    )
    return Claim.from_dict(
        {
            **existing.to_dict(),
            "source_ids": sorted(set([*existing.source_ids, *incoming.source_ids])),
            "evidence": [item.to_dict() for item in evidence_by_source.values()],
        }
    )


def _add_supersedes(
    store: MemoryStore,
    page_slug: str,
    claim: Claim,
    loser_ids: list[str],
) -> Claim:
    if not loser_ids:
        return claim
    updated = Claim.from_dict(
        {
            **claim.to_dict(),
            "supersedes": sorted(set([*claim.supersedes, *loser_ids])),
        }
    )
    _replace_claim(store, page_slug, updated)
    return updated


def _add_relation_evidence(
    store: MemoryStore,
    page_slug: str,
    claim: Claim,
    *,
    source_id: str,
    source_hash: str,
    source_text: str,
    allow_generic_version: bool,
) -> Claim:
    evidence_by_id = {item.evidence_id: item for item in claim.evidence}
    for span in _relation_spans(
        source_text, claim, allow_generic_version=allow_generic_version
    ):
        evidence = EvidenceRef.create(
            source_id=source_id,
            source_hash=source_hash,
            span=span,
        )
        evidence_by_id[evidence.evidence_id] = evidence
    updated = Claim.from_dict(
        {
            **claim.to_dict(),
            "evidence": [item.to_dict() for item in evidence_by_id.values()],
        }
    )
    if updated.evidence != claim.evidence:
        _replace_claim(store, page_slug, updated)
    return updated


def _explicit_temporal_winner(
    new_claim: Claim,
    conflicts: list[tuple[str, Claim]],
    as_of: str,
) -> str | None:
    if new_claim.effective_at is None:
        return None
    new_time = _parse_timestamp(new_claim.effective_at, "effective_at")
    if new_time > _parse_timestamp(as_of, "as_of"):
        return None
    dated = [
        (claim.claim_id, _parse_timestamp(claim.effective_at, "effective_at"))
        for _, claim in conflicts
        if claim.effective_at is not None
        and _parse_timestamp(claim.effective_at, "effective_at")
        <= _parse_timestamp(as_of, "as_of")
    ]
    if not dated or all(new_time > candidate_time for _, candidate_time in dated):
        return new_claim.claim_id
    return max(dated, key=lambda item: item[1])[0]


def _temporal_neighbors(
    new_claim: Claim,
    candidates: list[tuple[str, Claim]],
) -> tuple[list[tuple[str, Claim]], list[tuple[str, Claim]]]:
    """Return unambiguous immediate predecessor and successor timeline nodes."""
    if new_claim.effective_at is None:
        return [], []
    new_time = _parse_timestamp(new_claim.effective_at, "effective_at")
    predecessors: list[tuple[datetime, tuple[str, Claim]]] = []
    successors: list[tuple[datetime, tuple[str, Claim]]] = []
    for record in candidates:
        candidate = record[1]
        candidate_time = (
            _parse_timestamp(candidate.effective_at, "effective_at")
            if candidate.effective_at is not None
            else datetime.min.replace(tzinfo=UTC)
        )
        if candidate_time < new_time:
            predecessors.append((candidate_time, record))
        elif candidate_time > new_time:
            successors.append((candidate_time, record))

    immediate_predecessors: list[tuple[str, Claim]] = []
    if predecessors:
        latest = max(timestamp for timestamp, _ in predecessors)
        matches = [record for timestamp, record in predecessors if timestamp == latest]
        if len(matches) == 1:
            immediate_predecessors = matches

    immediate_successors: list[tuple[str, Claim]] = []
    if successors:
        earliest = min(timestamp for timestamp, _ in successors)
        matches = [record for timestamp, record in successors if timestamp == earliest]
        if len(matches) == 1:
            immediate_successors = matches
    return immediate_predecessors, immediate_successors


def _same_effective_records(
    new_claim: Claim,
    candidates: list[tuple[str, Claim]],
) -> list[tuple[str, Claim]]:
    if new_claim.effective_at is None:
        return []
    new_time = _parse_timestamp(new_claim.effective_at, "effective_at")
    return [
        record
        for record in candidates
        if record[1].effective_at is not None
        and _parse_timestamp(record[1].effective_at, "effective_at") == new_time
    ]


def _splice_successor_supersedes(
    store: MemoryStore,
    page_slug: str,
    successor: Claim,
    *,
    replaced_predecessor_ids: set[str],
    new_predecessor_id: str,
) -> Claim:
    retained = [
        claim_id
        for claim_id in successor.supersedes
        if claim_id not in replaced_predecessor_ids
    ]
    updated = Claim.from_dict(
        {
            **successor.to_dict(),
            "supersedes": sorted(set([*retained, new_predecessor_id])),
        }
    )
    _replace_claim(store, page_slug, updated)
    return updated


def _is_future_effective(claim: Claim, as_of: str) -> bool:
    return bool(
        claim.effective_at
        and _parse_timestamp(claim.effective_at, "effective_at")
        > _parse_timestamp(as_of, "as_of")
    )


def _sentences(text: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r"(?<=[.!?])\s+|[\r\n]+", text)
        if segment.strip()
    ]


def _expand_unique_value_spans(
    spans: list[str],
    *,
    source_text: str,
    scope: str,
    subject: str,
    predicate: str,
    value: str,
) -> list[str]:
    """Recover a full grounding sentence only when the mapping is unique.

    Qwen occasionally returns a verbatim value phrase instead of the requested
    sentence. Expanding that phrase is safe only when exactly one raw-source
    sentence contains it and independently binds the complete claim key. Any
    ambiguity is left untouched so the existing validator still fails closed.
    """
    source_sentences = _sentences(source_text)
    expanded: list[str] = []
    for span in spans:
        if _claim_key_span_supports_components(
            span,
            scope=scope,
            subject=subject,
            predicate=predicate,
            value=value,
        ):
            expanded.append(span)
            continue
        if not _span_supports_value(span, value):
            expanded.append(span)
            continue
        candidates = list(
            dict.fromkeys(
                sentence
                for sentence in source_sentences
                if span in sentence
                and _claim_key_span_supports_components(
                    sentence,
                    scope=scope,
                    subject=subject,
                    predicate=predicate,
                    value=value,
                )
            )
        )
        expanded.append(candidates[0] if len(candidates) == 1 else span)
    return list(dict.fromkeys(expanded))


def _component_terms(component: str) -> set[str]:
    return {
        token
        for token in re.findall(
            r"[^\W_]+", normalize_component(component), flags=re.UNICODE
        )
        if len(token) >= 2
    }


def _sentence_matches_components(
    sentence: str, *, subject: str, predicate: str
) -> bool:
    normalized = normalize_component(sentence)
    return all(
        terms and any(term in normalized for term in terms)
        for terms in (_component_terms(subject), _component_terms(predicate))
    )


def _effective_span_supports_components(
    span: str,
    *,
    effective_at: str,
    subject: str,
    predicate: str,
    value: str,
) -> bool:
    date_token = canonical_timestamp(effective_at, "effective_at")[:10]
    sentences = _sentences(span)
    if any(
        date_token in sentence
        and _span_supports_value(sentence, value)
        and _sentence_matches_components(
            sentence, subject=subject, predicate=predicate
        )
        for sentence in sentences
    ):
        return True
    for index, relation_sentence in enumerate(sentences[:-1]):
        if (
            date_token not in relation_sentence
            or not has_explicit_supersession(relation_sentence)
            or not _CROSS_SENTENCE_RECORD_REPLACEMENT.search(relation_sentence)
        ):
            continue
        claim_sentence = sentences[index + 1]
        if _span_supports_value(
            claim_sentence, value
        ) and _sentence_matches_components(
            claim_sentence, subject=subject, predicate=predicate
        ):
            return True
    return False


def _claim_key_span_supports_components(
    span: str,
    *,
    scope: str,
    subject: str,
    predicate: str,
    value: str,
) -> bool:
    component_sets = [_component_terms(subject), _component_terms(predicate)]
    if normalize_component(scope) != "unspecified":
        component_sets.insert(0, _component_terms(scope))
    return any(
        _span_supports_value(sentence, value)
        and all(
            terms and any(term in normalize_component(sentence) for term in terms)
            for terms in component_sets
        )
        for sentence in _sentences(span)
    )


def _claim_scoped_supersession_sentence(
    sentence: str,
    claim: Claim,
    *,
    candidate_source_ids: set[str] | frozenset[str] = frozenset(),
    candidate_values: set[str] | frozenset[str] = frozenset(),
) -> bool:
    clauses = [clause.strip() for clause in re.split(r"[:;]", sentence) if clause.strip()]
    for index, clause in enumerate(clauses):
        # A source-targeted relation may precede the exact claim after a colon,
        # e.g. ``restores SRC-A and rejects SRC-B: scope subject key is VALUE``.
        # Requiring a known candidate source in the relation clause keeps this
        # distinct from an unrelated ``correction to the logo: ...`` sentence.
        normalized_relation = normalize_component(clause)
        if (
            index + 1 < len(clauses)
            and has_explicit_supersession(clause)
            and any(
                normalize_component(source_id) in normalized_relation
                for source_id in candidate_source_ids
            )
            and _span_supports_value(clauses[index + 1], claim.value)
            and _sentence_matches_components(
                clauses[index + 1],
                subject=claim.subject,
                predicate=claim.predicate,
            )
        ):
            return True
    for clause in clauses:
        if not (
            has_explicit_supersession(clause)
            and _span_supports_value(clause, claim.value)
            and _sentence_matches_components(
                clause, subject=claim.subject, predicate=claim.predicate
            )
        ):
            continue
        normalized = normalize_component(clause)
        if any(
            normalize_component(source_id) in normalized
            for source_id in candidate_source_ids
        ) or any(
            _span_supports_value(clause, value)
            for value in candidate_values
            if normalize_component(value) != claim.normalized_value
        ):
            return True
        predicate_terms = _component_terms(claim.predicate)
        if any(
            re.search(
                rf"\b(?:prior|previous|old)\s+{re.escape(term)}\b",
                normalized,
            )
            for term in predicate_terms
        ):
            return True
    return False


def _has_scoped_replacement(
    source_text: str,
    claim: Claim,
    *,
    allow_generic_version: bool,
    candidate_source_ids: set[str],
    candidate_values: set[str],
) -> bool:
    """Require replacement evidence to bind to this claim's value and key."""

    sentences = _sentences(source_text)
    for index, sentence in enumerate(sentences):
        if allow_generic_version and _GENERIC_VERSION_REPLACEMENT.fullmatch(sentence):
            return True
        if _claim_scoped_supersession_sentence(
            sentence,
            claim,
            candidate_source_ids=candidate_source_ids,
            candidate_values=candidate_values,
        ):
            return True
        if (
            has_explicit_supersession(sentence)
            and "this record" in normalize_component(sentence)
            and any(
                normalize_component(source_id) in normalize_component(sentence)
                for source_id in candidate_source_ids
            )
            and index + 1 < len(sentences)
            and _span_supports_value(sentences[index + 1], claim.value)
            and _sentence_matches_components(
                sentences[index + 1],
                subject=claim.subject,
                predicate=claim.predicate,
            )
        ):
            return True
    return False


def _has_explicit_effective_signal(source_text: str, claim: Claim) -> bool:
    del source_text
    if not claim.effective_at:
        return False
    return any(
        _effective_span_supports_components(
            evidence.span,
            effective_at=claim.effective_at,
            subject=claim.subject,
            predicate=claim.predicate,
            value=claim.value,
        )
        for evidence in claim.evidence
    )


def _relation_spans(
    source_text: str,
    claim: Claim,
    *,
    allow_generic_version: bool,
) -> list[str]:
    spans: list[str] = []
    for candidate in _sentences(source_text):
        scoped_supersession = _claim_scoped_supersession_sentence(candidate, claim)
        generic_supersession = (
            allow_generic_version
            and _GENERIC_VERSION_REPLACEMENT.fullmatch(candidate) is not None
        )
        scoped_effective = bool(
            claim.effective_at
            and _effective_span_supports_components(
                candidate,
                effective_at=claim.effective_at,
                subject=claim.subject,
                predicate=claim.predicate,
                value=claim.value,
            )
        )
        if scoped_supersession or generic_supersession or scoped_effective:
            spans.append(candidate[:500])
    return spans[:3]


def _arbitrate_relation(
    *,
    router: SupportsChat,
    new_claim: Claim,
    candidates: list[Claim],
) -> tuple[RelationDecision | None, Any]:
    user = json.dumps(
        {
            "new_claim": _relation_claim_payload(new_claim),
            "candidate_claims": [_relation_claim_payload(c) for c in candidates],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    resp = router.chat(
        Tier.HEAVY,
        system=RELATION_SYSTEM_PREFIX,
        user=user,
        temperature=0.0,
        max_tokens=400,
    )
    try:
        decision = RelationDecision.from_dict(_parse_json_object(resp.text, "relation"))
    except ValueError:
        decision = None
    return decision, resp


def _validated_decision(
    decision: RelationDecision | None,
    claims: list[Claim],
) -> RelationDecision:
    fallback = RelationDecision(
        relation=Relation.UNRESOLVED,
        winner_claim_id=None,
        evidence_source_ids=(),
        evidence_spans=(),
        rationale="Model output did not satisfy the evidence validator.",
    )
    if decision is None:
        return fallback
    claim_ids = {claim.claim_id for claim in claims}
    source_ids = {source for claim in claims for source in claim.source_ids}
    spans = {evidence.span for claim in claims for evidence in claim.evidence}
    if decision.winner_claim_id and decision.winner_claim_id not in claim_ids:
        return fallback
    if not set(decision.evidence_source_ids).issubset(source_ids):
        return fallback
    if not set(decision.evidence_spans).issubset(spans):
        return fallback
    if decision.relation is Relation.SUPERSEDES and (
        not decision.evidence_source_ids or not decision.evidence_spans
    ):
        return fallback
    if decision.relation is Relation.SUPERSEDES and decision.winner_claim_id:
        if not supersession_evidence_binds_winner(decision, claims):
            return fallback
    return decision


def _relation_claim_payload(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "key": claim.key,
        "value": claim.value,
        "observed_at": claim.observed_at,
        "effective_at": claim.effective_at,
        "status": claim.status.value,
        "source_ids": list(claim.source_ids),
        "evidence_spans": [item.span for item in claim.evidence],
    }


def _apply_transition(
    store: MemoryStore,
    *,
    page_slug: str,
    claim_id: str,
    to_status: ClaimStatus,
    timestamp: str,
    trigger_claim_id: str | None,
    rule: str,
    relation: Relation | None,
    model: str | None,
    evidence_claims: list[Claim],
    rationale: str,
    evidence_source_ids: list[str] | None = None,
    evidence_spans: list[str] | None = None,
) -> TransitionEvent | None:
    current = _claim_on_page(store, page_slug, claim_id)
    if current is None:
        raise KeyError(f"claim not found: {claim_id}")
    if current.status is to_status:
        return None
    event = _transition_event(
        page_slug=page_slug,
        claim=current,
        from_status=current.status,
        to_status=to_status,
        timestamp=timestamp,
        trigger_claim_id=trigger_claim_id,
        rule=rule,
        relation=relation,
        model=model,
        evidence_claims=evidence_claims,
        rationale=rationale,
        evidence_source_ids=evidence_source_ids,
        evidence_spans=evidence_spans,
    )
    store.apply_claim_transition(
        page_slug=page_slug,
        claim_id=claim_id,
        to_status=to_status,
        event=event,
    )
    return event


def _transition_event(
    *,
    page_slug: str,
    claim: Claim,
    from_status: ClaimStatus | None,
    to_status: ClaimStatus,
    timestamp: str,
    trigger_claim_id: str | None,
    rule: str,
    relation: Relation | None,
    model: str | None,
    evidence_claims: list[Claim],
    rationale: str,
    evidence_source_ids: list[str] | None = None,
    evidence_spans: list[str] | None = None,
) -> TransitionEvent:
    sources = evidence_source_ids or [
        source for item in evidence_claims for source in item.source_ids
    ]
    spans = evidence_spans or [
        evidence.span for item in evidence_claims for evidence in item.evidence
    ]
    return TransitionEvent(
        schema_version=_MEMORY_SCHEMA_VERSION,
        event_id=_event_id(
            page_slug,
            claim.claim_id,
            "new" if from_status is None else from_status.value,
            to_status.value,
            timestamp,
            rule,
            trigger_claim_id or "",
        ),
        timestamp=timestamp,
        page_slug=page_slug,
        claim_id=claim.claim_id,
        from_status=from_status,
        to_status=to_status,
        trigger_claim_id=trigger_claim_id,
        rule=rule,
        relation=relation,
        model=model,
        prompt_version=PROMPT_VERSION,
        evidence_source_ids=tuple(dict.fromkeys(sources)),
        evidence_spans=tuple(dict.fromkeys(spans)),
        rationale=rationale,
    )


def _event_id(*parts: str) -> str:
    encoded = "|".join(parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _parse_json_object(text: str, label: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} output must be a JSON object")
    return data


def _required_string(data: dict[str, Any], key: str, index: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"claim[{index}].{key} must be a non-empty string")
    return value.strip()


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _parse_timestamp(value: str, label: str) -> datetime:
    normalized = canonical_timestamp(value, label)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _span_supports_value(span: str, value: str) -> bool:
    haystack = normalize_component(span)
    needle = normalize_component(value)
    if re.fullmatch(r"[\w.%-]+", needle, flags=re.UNICODE):
        return bool(re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))
    return needle in haystack

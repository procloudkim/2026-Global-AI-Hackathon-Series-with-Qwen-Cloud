"""Idempotent lifecycle audit/repair for claim-level safe forgetting."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Protocol

from .claims import (
    Claim,
    ClaimStatus,
    Relation,
    RelationDecision,
    TransitionEvent,
    canonical_timestamp,
    has_explicit_supersession,
    supersession_evidence_binds_winner,
)
from .llm import Tier
from .prompts import PROMPT_VERSION, RELATION_SYSTEM_PREFIX
from .store import MemoryStore

_MEMORY_SCHEMA_VERSION = "librarian-memory/v2"


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
class LintFinding:
    finding_type: str
    page: str
    message: str
    archived: bool = False
    claim_id: str | None = None
    repaired: bool = False


@dataclass(frozen=True)
class LintResult:
    findings: list[LintFinding]
    archived_pages: list[str]
    route_tier: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    archived_claims: list[str]
    repaired_projections: bool
    transitioned_claims: list[str]


def run_lint(
    *,
    store: MemoryStore,
    router: SupportsChat,
    apply_archive: bool = True,
) -> LintResult:
    with store.transaction():
        return _run_lint_locked(
            store=store,
            router=router,
            apply_archive=apply_archive,
        )


def _run_lint_locked(
    *,
    store: MemoryStore,
    router: SupportsChat,
    apply_archive: bool = True,
) -> LintResult:
    """Audit canonical claims and optionally apply evidence-validated repairs.

    ``apply_archive`` is retained for API compatibility.  It now means "apply
    safe repairs"; this function never archives or deletes an entire page.
    """
    findings: list[LintFinding] = []
    transitioned: list[str] = []
    transition_events: list[dict[str, Any]] = []
    prompt_tokens = 0
    completion_tokens = 0
    models: list[str] = []
    heavy_calls = 0
    repair_timestamp = canonical_timestamp(datetime.now(UTC).isoformat())

    claim_revision_tail_repaired = (
        store.repair_partial_claim_revision_tail() if apply_archive else False
    )
    if not apply_archive:
        store.claim_revisions()
    if claim_revision_tail_repaired:
        findings.append(
            LintFinding(
                finding_type="claim_revision_ledger_partial_tail",
                page=store.claim_revisions_path.name,
                message=(
                    "Repaired the final claim-revision JSONL boundary or "
                    "truncated fragment."
                ),
                repaired=True,
            )
        )

    pending_claim_revisions_found = store.pending_claim_revisions_path.exists()
    pending_claim_revisions_recovered = (
        store.recover_pending_claim_revisions()
        if apply_archive and pending_claim_revisions_found
        else False
    )
    if pending_claim_revisions_found:
        findings.append(
            LintFinding(
                finding_type="pending_claim_revisions",
                page=store.pending_claim_revisions_path.name,
                message=(
                    "Completed the crash-interrupted page/revision boundary."
                    if pending_claim_revisions_recovered
                    else "Crash-interrupted claim revisions require repair mode."
                ),
                repaired=pending_claim_revisions_recovered,
            )
        )

    ledger_tail_repaired = (
        store.repair_partial_decision_tail()
        if apply_archive
        else False
    )
    if not apply_archive:
        # Audit-only mode still fails closed on malformed history.
        store.decision_events()
    if ledger_tail_repaired:
        findings.append(
            LintFinding(
                finding_type="decision_ledger_partial_tail",
                page="decisions.jsonl",
                message="Repaired the final JSONL boundary or truncated fragment.",
                repaired=True,
            )
        )

    pending_transition_found = store.pending_transition_path.exists()
    pending_transition_recovered = (
        store.recover_pending_transition()
        if apply_archive and pending_transition_found
        else False
    )
    if pending_transition_found:
        findings.append(
            LintFinding(
                finding_type="pending_transition",
                page=store.pending_transition_path.name,
                message=(
                    "Completed the crash-interrupted canonical/ledger transition."
                    if pending_transition_recovered
                    else "Crash-interrupted transition requires repair mode."
                ),
                repaired=pending_transition_recovered,
            )
        )

    pending_ingest_found = store.pending_ingest_path.exists()
    recovered_ingest_keys = (
        store.recover_pending_ingest(prompt_version=PROMPT_VERSION)
        if apply_archive and pending_ingest_found
        else []
    )
    if pending_ingest_found:
        findings.append(
            LintFinding(
                finding_type="pending_ingest",
                page=store.pending_ingest_path.name,
                message=(
                    "Restored the pre-ingest boundary and fail-closed unresolved affected keys."
                    if recovered_ingest_keys
                    else "Crash-interrupted ingest requires repair mode."
                ),
                repaired=bool(recovered_ingest_keys),
            )
        )

    due_events = (
        store.apply_due_transitions(
            as_of=repair_timestamp,
            prompt_version=PROMPT_VERSION,
        )
        if apply_archive
        else []
    )
    if due_events:
        transitioned.extend(str(event["claim_id"]) for event in due_events)
        transition_events.extend(due_events)
        findings.append(
            LintFinding(
                finding_type="scheduled_transition_due",
                page="graph.json",
                message=f"Materialized {len(due_events)} evidence-backed due transitions.",
                repaired=True,
            )
        )

    projection_drift = not store.projection_is_consistent()
    repaired_projections = False
    if projection_drift:
        if apply_archive:
            repaired_projections = store.repair_projections()
        findings.append(
            LintFinding(
                finding_type="projection_drift",
                page="graph.json/index.md",
                message=(
                    "Derived graph/index rebuilt from canonical wiki pages."
                    if repaired_projections
                    else "Derived graph does not match canonical wiki pages."
                ),
                repaired=repaired_projections,
            )
        )

    for slug in store.legacy_unindexed_pages():
        findings.append(
            LintFinding(
                finding_type="legacy_unindexed",
                page=slug,
                message="Page has no v2 claims array and is excluded from claim retrieval.",
            )
        )

    claims_by_key: dict[str, list[tuple[str, Claim]]] = {}
    all_claims: list[tuple[str, Claim]] = []
    for page in store.list_wiki_pages():
        raw_claims = page.metadata.get("claims", [])
        if not isinstance(raw_claims, list):
            continue
        for raw in raw_claims:
            if not isinstance(raw, dict):
                findings.append(
                    LintFinding(
                        finding_type="invalid_claim",
                        page=page.slug,
                        message="Claim entry is not an object; no repair applied.",
                    )
                )
                continue
            try:
                claim = Claim.from_dict(raw)
            except ValueError as exc:
                findings.append(
                    LintFinding(
                        finding_type="invalid_claim",
                        page=page.slug,
                        claim_id=str(raw.get("claim_id", "")) or None,
                        message=f"Invalid claim contract; no repair applied: {exc}",
                    )
                )
                continue
            all_claims.append((page.slug, claim))
            claims_by_key.setdefault(claim.key, []).append((page.slug, claim))

    creation_receipts = {
        (str(event.get("page_slug", "")), str(event.get("claim_id", "")))
        for event in store.decision_events()
        if event.get("from_status") is None and event.get("to_status") == "active"
    }
    for page_slug, claim in all_claims:
        if (page_slug, claim.claim_id) in creation_receipts:
            continue
        repaired = False
        if apply_archive and claim.status is ClaimStatus.ACTIVE:
            creation = _event(
                page_slug=page_slug,
                claim=claim,
                from_status=None,
                to_status=ClaimStatus.ACTIVE,
                timestamp=claim.observed_at,
                trigger_claim_id=None,
                rule="audit_repair_missing_creation_event",
                relation=None,
                model=None,
                evidence_source_ids=list(claim.source_ids),
                evidence_spans=[item.span for item in claim.evidence],
                rationale="Reconstructed deterministic creation receipt from canonical claim evidence.",
            )
            store.append_decision_event(creation)
            transition_events.append(creation.to_dict())
            repaired = True
        findings.append(
            LintFinding(
                finding_type="audit_gap",
                page=page_slug,
                claim_id=claim.claim_id,
                message=(
                    "Missing creation receipt reconstructed from canonical evidence."
                    if repaired
                    else (
                        "Non-active claim has no verifiable lifecycle history; "
                        "report-only repair required."
                        if claim.status is not ClaimStatus.ACTIVE
                        else "Claim has no creation receipt in decisions.jsonl."
                    )
                ),
                repaired=repaired,
            )
        )

    disputed_groups: list[list[tuple[str, Claim]]] = []
    for records in claims_by_key.values():
        live = [
            (slug, claim)
            for slug, claim in records
            if claim.status in {ClaimStatus.ACTIVE, ClaimStatus.DISPUTED}
        ]
        if len(live) < 2 or not any(
            claim.status is ClaimStatus.DISPUTED for _, claim in live
        ):
            continue
        normalized_group: list[tuple[str, Claim]] = []
        for page_slug, claim in live:
            if (
                apply_archive
                and claim.status is ClaimStatus.ACTIVE
            ):
                event = _event(
                    page_slug=page_slug,
                    claim=claim,
                    from_status=ClaimStatus.ACTIVE,
                    to_status=ClaimStatus.DISPUTED,
                    timestamp=repair_timestamp,
                    trigger_claim_id=None,
                    rule="lint_fail_closed_partial_conflict",
                    relation=Relation.UNRESOLVED,
                    model=None,
                    evidence_source_ids=list(
                        dict.fromkeys(
                            source
                            for _, member in live
                            for source in member.source_ids
                        )
                    ),
                    evidence_spans=list(
                        dict.fromkeys(
                            evidence.span
                            for _, member in live
                            for evidence in member.evidence
                        )
                    ),
                    rationale=(
                        "An active/disputed split can result from a partial write; "
                        "the claim key is made fail-closed before arbitration."
                    ),
                )
                updated = store.apply_claim_transition(
                    page_slug=page_slug,
                    claim_id=claim.claim_id,
                    to_status=ClaimStatus.DISPUTED,
                    event=event,
                )
                transition_events.append(event.to_dict())
                transitioned.append(claim.claim_id)
                normalized_group.append((page_slug, updated))
            else:
                normalized_group.append((page_slug, claim))
        disputed_groups.append(normalized_group)
    for group in disputed_groups:
        key = group[0][1].key
        if not apply_archive:
            findings.append(
                LintFinding(
                    finding_type="disputed_conflict",
                    page=",".join(sorted({slug for slug, _ in group})),
                    message=f"Unresolved disputed claim group: {key}",
                )
            )
            continue

        decision, response = _resolve_disputed(group, router)
        heavy_calls += 1
        prompt_tokens += int(getattr(response, "prompt_tokens", 0))
        completion_tokens += int(getattr(response, "completion_tokens", 0))
        model = str(getattr(response, "model", "unknown"))
        models.append(model)
        validated = _validate_decision(decision, [claim for _, claim in group])
        if validated is None:
            findings.append(
                LintFinding(
                    finding_type="disputed_conflict",
                    page=",".join(sorted({slug for slug, _ in group})),
                    message=f"Arbitration evidence invalid; group remains disputed: {key}",
                )
            )
            continue

        if validated.relation is Relation.SUPERSEDES and validated.winner_claim_id:
            winner_slug, winner = next(
                item for item in group if item[1].claim_id == validated.winner_claim_id
            )
            winner = _add_supersedes(
                store,
                winner_slug,
                winner,
                [
                    claim.claim_id
                    for _, claim in group
                    if claim.claim_id != winner.claim_id
                ],
                recorded_at=repair_timestamp,
            )
            winner_event = _event(
                page_slug=winner_slug,
                claim=winner,
                from_status=ClaimStatus.DISPUTED,
                to_status=ClaimStatus.ACTIVE,
                timestamp=repair_timestamp,
                trigger_claim_id=None,
                rule="lint_validated_arbitration",
                relation=Relation.SUPERSEDES,
                model=model,
                evidence_source_ids=list(validated.evidence_source_ids),
                evidence_spans=list(validated.evidence_spans),
                rationale=validated.rationale,
            )
            store.apply_claim_transition(
                page_slug=winner_slug,
                claim_id=winner.claim_id,
                to_status=ClaimStatus.ACTIVE,
                event=winner_event,
            )
            transition_events.append(winner_event.to_dict())
            transitioned.append(winner.claim_id)
            for loser_slug, loser in group:
                if loser.claim_id == winner.claim_id:
                    continue
                loser_event = _event(
                    page_slug=loser_slug,
                    claim=loser,
                    from_status=ClaimStatus.DISPUTED,
                    to_status=ClaimStatus.SUPERSEDED,
                    timestamp=repair_timestamp,
                    trigger_claim_id=winner.claim_id,
                    rule="lint_validated_arbitration",
                    relation=Relation.SUPERSEDES,
                    model=model,
                    evidence_source_ids=list(validated.evidence_source_ids),
                    evidence_spans=list(validated.evidence_spans),
                    rationale=validated.rationale,
                )
                store.apply_claim_transition(
                    page_slug=loser_slug,
                    claim_id=loser.claim_id,
                    to_status=ClaimStatus.SUPERSEDED,
                    event=loser_event,
                )
                transition_events.append(loser_event.to_dict())
                transitioned.append(loser.claim_id)
            findings.append(
                LintFinding(
                    finding_type="disputed_resolved",
                    page=winner_slug,
                    claim_id=winner.claim_id,
                    message=f"Evidence-validated winner activated for {key}; losers superseded.",
                    repaired=True,
                )
            )
        elif validated.relation is Relation.SUPPORTS and len(
            {claim.normalized_value for _, claim in group}
        ) == 1:
            for page_slug, claim in group:
                event = _event(
                    page_slug=page_slug,
                    claim=claim,
                    from_status=ClaimStatus.DISPUTED,
                    to_status=ClaimStatus.ACTIVE,
                    timestamp=repair_timestamp,
                    trigger_claim_id=None,
                    rule="lint_validated_support",
                    relation=Relation.SUPPORTS,
                    model=model,
                    evidence_source_ids=list(validated.evidence_source_ids),
                    evidence_spans=list(validated.evidence_spans),
                    rationale=validated.rationale,
                )
                store.apply_claim_transition(
                    page_slug=page_slug,
                    claim_id=claim.claim_id,
                    to_status=ClaimStatus.ACTIVE,
                    event=event,
                )
                transition_events.append(event.to_dict())
                transitioned.append(claim.claim_id)
        else:
            findings.append(
                LintFinding(
                    finding_type="disputed_conflict",
                    page=",".join(sorted({slug for slug, _ in group})),
                    message=f"No safe lifecycle transition for disputed group: {key}",
                )
            )

    if transition_events:
        store.append_log("lint", f"claim_transitions={len(transition_events)}")
    return LintResult(
        findings=findings,
        archived_pages=[],
        route_tier=Tier.HEAVY.value if heavy_calls else "none",
        model=" -> ".join(dict.fromkeys(models)) if models else "none",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        archived_claims=[],
        repaired_projections=repaired_projections,
        transitioned_claims=list(dict.fromkeys(transitioned)),
    )


def _resolve_disputed(
    group: list[tuple[str, Claim]],
    router: SupportsChat,
) -> tuple[RelationDecision | None, Any]:
    payload = {
        "new_claim": _claim_payload(group[-1][1]),
        "candidate_claims": [_claim_payload(claim) for _, claim in group[:-1]],
    }
    response = router.chat(
        Tier.HEAVY,
        system=RELATION_SYSTEM_PREFIX,
        user=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        temperature=0.0,
        max_tokens=400,
    )
    cleaned = response.text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        raw = json.loads(cleaned)
        decision = RelationDecision.from_dict(raw)
    except (json.JSONDecodeError, ValueError):
        decision = None
    return decision, response


def _validate_decision(
    decision: RelationDecision | None,
    claims: list[Claim],
) -> RelationDecision | None:
    if decision is None:
        return None
    claim_ids = {claim.claim_id for claim in claims}
    source_ids = {source for claim in claims for source in claim.source_ids}
    spans = {evidence.span for claim in claims for evidence in claim.evidence}
    if decision.winner_claim_id and decision.winner_claim_id not in claim_ids:
        return None
    if not set(decision.evidence_source_ids).issubset(source_ids):
        return None
    if not set(decision.evidence_spans).issubset(spans):
        return None
    if decision.relation in {Relation.SUPERSEDES, Relation.SUPPORTS} and (
        not decision.evidence_source_ids or not decision.evidence_spans
    ):
        return None
    if decision.relation is Relation.SUPERSEDES and not has_explicit_supersession(
        "\n".join(decision.evidence_spans)
    ):
        return None
    if decision.relation is Relation.SUPERSEDES and not supersession_evidence_binds_winner(
        decision, claims
    ):
        return None
    return decision


def _claim_payload(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "key": claim.key,
        "value": claim.value,
        "observed_at": claim.observed_at,
        "effective_at": claim.effective_at,
        "status": claim.status.value,
        "source_ids": list(claim.source_ids),
        "evidence_spans": [evidence.span for evidence in claim.evidence],
    }


def _add_supersedes(
    store: MemoryStore,
    page_slug: str,
    winner: Claim,
    loser_ids: list[str],
    *,
    recorded_at: str,
) -> Claim:
    updated = Claim.from_dict(
        {
            **winner.to_dict(),
            "supersedes": sorted(set([*winner.supersedes, *loser_ids])),
        }
    )
    claims = store.claims_for_page(page_slug)
    for index, raw in enumerate(claims):
        if str(raw.get("claim_id", "")) == winner.claim_id:
            claims[index] = updated.to_dict()
            operation_id = hashlib.sha256(
                "|".join(
                    (
                        "lint_add_supersedes",
                        page_slug,
                        winner.claim_id,
                        recorded_at,
                        ",".join(sorted(set(loser_ids))),
                    )
                ).encode("utf-8")
            ).hexdigest()[:24]
            store.write_page_claims(
                page_slug,
                claims,
                revision_recorded_at=recorded_at,
                revision_operation_id=operation_id,
                revision_reason="lint validated supersession relation",
            )
            return updated
    raise KeyError(f"claim not found on page {page_slug}: {winner.claim_id}")


def _event(
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
    evidence_source_ids: list[str],
    evidence_spans: list[str],
    rationale: str,
) -> TransitionEvent:
    event_id = hashlib.sha256(
        "|".join(
            (
                page_slug,
                claim.claim_id,
                "new" if from_status is None else from_status.value,
                to_status.value,
                timestamp,
                rule,
                trigger_claim_id or "",
            )
        ).encode("utf-8")
    ).hexdigest()[:24]
    return TransitionEvent(
        schema_version=_MEMORY_SCHEMA_VERSION,
        event_id=event_id,
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
        evidence_source_ids=tuple(dict.fromkeys(evidence_source_ids)),
        evidence_spans=tuple(dict.fromkeys(evidence_spans)),
        rationale=rationale,
    )

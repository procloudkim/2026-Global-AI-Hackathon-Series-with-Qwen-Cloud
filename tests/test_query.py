from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from librarian.claims import Claim, ClaimStatus, EvidenceRef, TransitionEvent
from librarian.query import _parse_timestamp, answer_question, select_top_k_pages
from librarian.store import MemoryStore
from tests.support import ScriptedRouter, canonical_claim, query_answer


def _lifecycle_event(
    *,
    page_slug: str,
    event_id: str,
    claim: Claim,
    timestamp: str,
    from_status: ClaimStatus | None,
    to_status: ClaimStatus,
    trigger_claim_id: str | None = None,
) -> TransitionEvent:
    return TransitionEvent(
        schema_version="librarian-memory/v2",
        event_id=event_id,
        timestamp=timestamp,
        page_slug=page_slug,
        claim_id=claim.claim_id,
        from_status=from_status,
        to_status=to_status,
        trigger_claim_id=trigger_claim_id,
        rule=(
            "source_grounded_claim_creation"
            if from_status is None
            else "explicit_replacement_language"
        ),
        relation=None,
        model="frozen" if from_status is None else None,
        prompt_version="v4",
        evidence_source_ids=claim.source_ids,
        evidence_spans=tuple(item.span for item in claim.evidence),
        rationale="Source-grounded lifecycle event.",
    )


def _stage_page_version(
    store: MemoryStore,
    *,
    title: str,
    slug: str,
    source_id: str,
    observed_at: str,
    claims: list[Claim],
    incoming_claims: list[Claim],
    prior_claims: list[tuple[str, Claim]],
):
    affected_keys = sorted(
        {claim.key for claim in [*incoming_claims, *(item[1] for item in prior_claims)]}
    )
    store.stage_ingest_operation(
        source_id=source_id,
        source_hash=hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
        observed_at=observed_at,
        target_slug=slug,
        affected_keys=affected_keys,
        incoming_claim_ids=[claim.claim_id for claim in incoming_claims],
        prior_claims=prior_claims,
    )
    page = store.upsert_wiki_page(
        title,
        f"Temporal memory for {title}.",
        slug=slug,
        metadata={"summary": title, "claims": [claim.to_dict() for claim in claims]},
    )
    store.complete_ingest_operation()
    return page


def test_query_timestamp_requires_explicit_timezone() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        _parse_timestamp("2026-07-14T02:00:00", "as_of")


def test_historical_as_of_rewinds_correction_without_mutating_store(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old_active = canonical_claim(
        value="100",
        source_id="source-old",
        evidence_span="The production API quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    new = canonical_claim(
        value="200",
        source_id="source-new",
        evidence_span="Correction: the production API quota is 200.",
        observed_at="2026-07-15T00:00:00Z",
        supersedes=(old_active.claim_id,),
    )
    old = Claim.from_dict({**old_active.to_dict(), "status": "superseded"})
    page = store.upsert_wiki_page(
        "Production API Quota",
        "Current quota memory.",
        metadata={
            "summary": "production API quota",
            "claims": [old.to_dict(), new.to_dict()],
        },
    )

    store.append_decision_event(
        _lifecycle_event(
            page_slug=page.slug,
            event_id="old-created",
            claim=old,
            timestamp=old.observed_at,
            from_status=None,
            to_status=ClaimStatus.ACTIVE,
        )
    )
    store.append_decision_event(
        _lifecycle_event(
            page_slug=page.slug,
            event_id="new-created",
            claim=new,
            timestamp=new.observed_at,
            from_status=None,
            to_status=ClaimStatus.ACTIVE,
        )
    )
    store.append_decision_event(
        _lifecycle_event(
            page_slug=page.slug,
            event_id="old-superseded",
            claim=old,
            timestamp=new.observed_at,
            from_status=ClaimStatus.ACTIVE,
            to_status=ClaimStatus.SUPERSEDED,
            trigger_claim_id=new.claim_id,
        )
    )
    page_before = page.path.read_bytes()
    ledger_before = store.decisions_path.read_bytes()
    response = query_answer(
        answer="The production API quota was 100.",
        key=old.key,
        value=old.value,
        claim_id=old.claim_id,
        citation=page.slug,
    )

    result = answer_question(
        question="What was the production API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        as_of="2026-07-14T00:00:00Z",
    )

    assert result.abstained is False
    assert result.facts == [
        {"key": old.key, "value": old.value, "claim_ids": [old.claim_id]}
    ]
    assert result.evidence_source_ids == ["source-old"]
    assert result.trace["observed_after_as_of_claims_filtered"] == 1
    assert result.trace["lifecycle_transitions_rewound"] == 1
    assert page.path.read_bytes() == page_before
    assert store.decisions_path.read_bytes() == ledger_before
    canonical = {
        item.claim_id: item.status
        for item in (
            Claim.from_dict(raw) for raw in store.claims_for_page(page.slug)
        )
    }
    assert canonical == {
        old.claim_id: ClaimStatus.SUPERSEDED,
        new.claim_id: ClaimStatus.ACTIVE,
    }


def test_bitemporal_retroactive_correction_separates_valid_and_known_time(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old_active = canonical_claim(
        value="100",
        source_id="source-old",
        evidence_span="The production API quota is 100.",
        observed_at="2026-07-01T00:00:00Z",
    )
    new = canonical_claim(
        value="200",
        source_id="source-new",
        evidence_span=(
            "Correction recorded on July 10: the production API quota is 200 "
            "effective 2026-07-05."
        ),
        observed_at="2026-07-10T00:00:00Z",
        effective_at="2026-07-05T00:00:00Z",
        supersedes=(old_active.claim_id,),
    )
    old = Claim.from_dict({**old_active.to_dict(), "status": "superseded"})
    page_slug = store.slug_for("Production API Quota Timeline")
    store.stage_ingest_operation(
        source_id="source-old",
        source_hash=hashlib.sha256(b"source-old").hexdigest(),
        observed_at=old_active.observed_at,
        target_slug=page_slug,
        affected_keys=[old_active.key],
        incoming_claim_ids=[old_active.claim_id],
        prior_claims=[],
    )
    page = store.upsert_wiki_page(
        "Production API Quota Timeline",
        "Initial quota memory.",
        metadata={
            "summary": "production API quota timeline",
            "claims": [old_active.to_dict()],
        },
    )
    store.complete_ingest_operation()
    store.stage_ingest_operation(
        source_id="source-new",
        source_hash=hashlib.sha256(b"source-new").hexdigest(),
        observed_at=new.observed_at,
        target_slug=page.slug,
        affected_keys=[new.key],
        incoming_claim_ids=[new.claim_id],
        prior_claims=[(page.slug, old_active)],
    )
    page = store.upsert_wiki_page(
        "Production API Quota Timeline",
        "Current quota memory with one retroactive correction.",
        slug=page.slug,
        metadata={
            "summary": "production API quota timeline",
            "claims": [old.to_dict(), new.to_dict()],
        },
    )
    store.complete_ingest_operation()
    for transition in (
        _lifecycle_event(
            page_slug=page.slug,
            event_id="retro-old-created",
            claim=old,
            timestamp=old.observed_at,
            from_status=None,
            to_status=ClaimStatus.ACTIVE,
        ),
        _lifecycle_event(
            page_slug=page.slug,
            event_id="retro-new-created",
            claim=new,
            timestamp=new.observed_at,
            from_status=None,
            to_status=ClaimStatus.ACTIVE,
        ),
        _lifecycle_event(
            page_slug=page.slug,
            event_id="retro-old-superseded",
            claim=old,
            timestamp=new.observed_at,
            from_status=ClaimStatus.ACTIVE,
            to_status=ClaimStatus.SUPERSEDED,
            trigger_claim_id=new.claim_id,
        ),
    ):
        store.append_decision_event(transition)

    page_before = page.path.read_bytes()
    ledger_before = store.decisions_path.read_bytes()

    def ask(*, valid_at: str, known_at: str, expected: Claim):
        response = query_answer(
            answer=f"The production API quota was {expected.value}.",
            key=expected.key,
            value=expected.value,
            claim_id=expected.claim_id,
            citation=page.slug,
        )
        return answer_question(
            question="What was the production API quota?",
            store=store,
            router=ScriptedRouter(response),
            top_k=1,
            valid_at=valid_at,
            known_at=known_at,
        )

    before_correction_was_known = ask(
        valid_at="2026-07-07T00:00:00Z",
        known_at="2026-07-08T00:00:00Z",
        expected=old,
    )
    before_effect_and_before_knowledge = ask(
        valid_at="2026-07-04T00:00:00Z",
        known_at="2026-07-08T00:00:00Z",
        expected=old,
    )
    after_correction_was_known = ask(
        valid_at="2026-07-07T00:00:00Z",
        known_at="2026-07-11T00:00:00Z",
        expected=new,
    )
    before_retroactive_effect = ask(
        valid_at="2026-07-04T00:00:00Z",
        known_at="2026-07-11T00:00:00Z",
        expected=old,
    )

    assert before_correction_was_known.facts[0]["value"] == "100"
    assert before_effect_and_before_knowledge.facts[0]["value"] == "100"
    assert after_correction_was_known.facts[0]["value"] == "200"
    assert before_retroactive_effect.facts[0]["value"] == "100"
    assert page.path.read_bytes() == page_before
    assert store.decisions_path.read_bytes() == ledger_before


def test_bitemporal_cutoffs_reject_mixed_or_partial_inputs(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    router = ScriptedRouter()

    with pytest.raises(ValueError, match="cannot be combined"):
        answer_question(
            question="What was the quota?",
            store=store,
            router=router,
            as_of="2026-07-07T00:00:00Z",
            valid_at="2026-07-07T00:00:00Z",
            known_at="2026-07-08T00:00:00Z",
        )
    with pytest.raises(ValueError, match="must be provided together"):
        answer_question(
            question="What was the quota?",
            store=store,
            router=router,
            valid_at="2026-07-07T00:00:00Z",
        )
    assert router.calls == []


def test_bitemporal_query_fails_closed_before_v2_migration_watermark(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-old",
        evidence_span="The production API quota is 100.",
        observed_at="2026-07-01T00:00:00Z",
    )
    page = store.upsert_wiki_page(
        "Production API Quota",
        "Current quota memory.",
        metadata={"summary": "production API quota", "claims": [claim.to_dict()]},
    )
    assert store.baseline_claim_history(
        recorded_at="2026-07-10T00:00:00Z",
        reason="v2 migration cutover",
    ) == 1

    before_cutover_router = ScriptedRouter()
    before_cutover = answer_question(
        question="What was the production API quota?",
        store=store,
        router=before_cutover_router,
        top_k=1,
        valid_at="2026-07-05T00:00:00Z",
        known_at="2026-07-09T00:00:00Z",
    )
    assert before_cutover.abstained is True
    assert before_cutover.trace["bitemporal_history_complete"] == 0
    assert before_cutover.trace["incomplete_claim_histories"] == 1
    assert before_cutover_router.calls == []

    response = query_answer(
        answer="The production API quota was 100.",
        key=claim.key,
        value=claim.value,
        claim_id=claim.claim_id,
        citation=page.slug,
    )
    after_cutover = answer_question(
        question="What was the production API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        valid_at="2026-07-05T00:00:00Z",
        known_at="2026-07-11T00:00:00Z",
    )
    assert after_cutover.facts[0]["value"] == "100"
    assert after_cutover.trace["bitemporal_history_complete"] == 1
    assert store.claim_revisions()[0]["recorded_at"] == "2026-07-10T00:00:00Z"


def test_bitemporal_revision_hides_late_provenance_before_known_time(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    original = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The production API quota is 100.",
        observed_at="2026-07-01T00:00:00Z",
    )
    duplicate = canonical_claim(
        value="100",
        source_id="source-b",
        evidence_span="Independent receipt: the production API quota is 100.",
        observed_at="2026-07-03T00:00:00Z",
    )
    assert duplicate.claim_id == original.claim_id
    merged = Claim.from_dict(
        {
            **original.to_dict(),
            "source_ids": ["source-a", "source-b"],
            "evidence": [
                *(item.to_dict() for item in original.evidence),
                *(item.to_dict() for item in duplicate.evidence),
            ],
        }
    )
    page = _stage_page_version(
        store,
        title="Production API Quota",
        slug="provenance-timeline",
        source_id="source-a",
        observed_at=original.observed_at,
        claims=[original],
        incoming_claims=[original],
        prior_claims=[],
    )
    _stage_page_version(
        store,
        title="Production API Quota",
        slug=page.slug,
        source_id="source-b",
        observed_at=duplicate.observed_at,
        claims=[merged],
        incoming_claims=[duplicate],
        prior_claims=[(page.slug, original)],
    )

    def ask(known_at: str):
        response = query_answer(
            answer="The production API quota is 100.",
            key=original.key,
            value=original.value,
            claim_id=original.claim_id,
            citation=page.slug,
        )
        return answer_question(
            question="What is the production API quota?",
            store=store,
            router=ScriptedRouter(response),
            top_k=1,
            valid_at="2026-07-04T00:00:00Z",
            known_at=known_at,
        )

    before_merge = ask("2026-07-02T00:00:00Z")
    after_merge = ask("2026-07-04T00:00:00Z")
    assert before_merge.evidence_source_ids == ["source-a"]
    assert before_merge.trace["loaded_source_ids"] == ["source-a"]
    assert set(after_merge.evidence_source_ids) == {"source-a", "source-b"}
    assert set(after_merge.trace["loaded_source_ids"]) == {"source-a", "source-b"}


def test_bitemporal_revision_restores_successor_chain_before_late_insertion(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    first = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="Quota is 100 effective July 1.",
        observed_at="2026-07-01T00:00:00Z",
        effective_at="2026-07-01T00:00:00Z",
    )
    future = canonical_claim(
        value="300",
        source_id="source-c",
        evidence_span="Quota becomes 300 effective July 5.",
        observed_at="2026-07-02T00:00:00Z",
        effective_at="2026-07-05T00:00:00Z",
        supersedes=(first.claim_id,),
    )
    inserted = canonical_claim(
        value="200",
        source_id="source-b",
        evidence_span="Late record: quota became 200 effective July 3.",
        observed_at="2026-07-04T00:00:00Z",
        effective_at="2026-07-03T00:00:00Z",
        supersedes=(first.claim_id,),
    )
    first_superseded = Claim.from_dict(
        {**first.to_dict(), "status": ClaimStatus.SUPERSEDED.value}
    )
    future_spliced = Claim.from_dict(
        {**future.to_dict(), "supersedes": [inserted.claim_id]}
    )
    page = _stage_page_version(
        store,
        title="Quota Timeline",
        slug="quota-timeline",
        source_id="source-a",
        observed_at=first.observed_at,
        claims=[first],
        incoming_claims=[first],
        prior_claims=[],
    )
    _stage_page_version(
        store,
        title="Quota Timeline",
        slug=page.slug,
        source_id="source-c",
        observed_at=future.observed_at,
        claims=[first, future],
        incoming_claims=[future],
        prior_claims=[(page.slug, first)],
    )
    _stage_page_version(
        store,
        title="Quota Timeline",
        slug=page.slug,
        source_id="source-b",
        observed_at=inserted.observed_at,
        claims=[first_superseded, inserted, future_spliced],
        incoming_claims=[inserted],
        prior_claims=[(page.slug, first), (page.slug, future)],
    )

    def ask(known_at: str, expected: Claim):
        response = query_answer(
            answer=f"The production API quota is {expected.value}.",
            key=expected.key,
            value=expected.value,
            claim_id=expected.claim_id,
            citation=page.slug,
        )
        return answer_question(
            question="What is the production API quota?",
            store=store,
            router=ScriptedRouter(response),
            top_k=1,
            valid_at="2026-07-04T12:00:00Z",
            known_at=known_at,
        )

    before_insertion = ask("2026-07-03T00:00:00Z", first)
    after_insertion = ask("2026-07-05T00:00:00Z", inserted)
    assert before_insertion.facts[0]["value"] == "100"
    assert after_insertion.facts[0]["value"] == "200"
    july_four_revisions = [
        row
        for row in store.claim_revisions()
        if row["recorded_at"] == "2026-07-04T00:00:00Z"
    ]
    assert len(july_four_revisions) == 3
    assert [row["ordinal"] for row in store.claim_revisions()] == list(
        range(1, len(store.claim_revisions()) + 1)
    )


def test_bitemporal_revision_projects_archive_by_known_time(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The production API quota is 100.",
        observed_at="2026-07-01T00:00:00Z",
    )
    page = _stage_page_version(
        store,
        title="Production API Quota",
        slug="archive-timeline",
        source_id="source-a",
        observed_at=claim.observed_at,
        claims=[claim],
        incoming_claims=[claim],
        prior_claims=[],
    )
    supersede_event = _lifecycle_event(
        page_slug=page.slug,
        event_id="supersede-before-archive",
        claim=claim,
        timestamp="2026-07-02T00:00:00Z",
        from_status=ClaimStatus.ACTIVE,
        to_status=ClaimStatus.SUPERSEDED,
    )
    superseded = store.apply_claim_transition(
        page_slug=page.slug,
        claim_id=claim.claim_id,
        to_status=ClaimStatus.SUPERSEDED,
        event=supersede_event,
        recorded_at="2026-07-02T00:00:00Z",
    )
    archive_event = _lifecycle_event(
        page_slug=page.slug,
        event_id="archive-claim",
        claim=superseded,
        timestamp="2026-07-03T00:00:00Z",
        from_status=ClaimStatus.SUPERSEDED,
        to_status=ClaimStatus.ARCHIVED,
    )
    store.apply_claim_transition(
        page_slug=page.slug,
        claim_id=claim.claim_id,
        to_status=ClaimStatus.ARCHIVED,
        event=archive_event,
        recorded_at="2026-07-03T00:00:00Z",
    )

    response = query_answer(
        answer="The production API quota is 100.",
        key=claim.key,
        value=claim.value,
        claim_id=claim.claim_id,
        citation=page.slug,
    )
    before_archive = answer_question(
        question="What is the production API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        valid_at="2026-07-01T12:00:00Z",
        known_at="2026-07-01T12:00:00Z",
    )
    after_router = ScriptedRouter()
    after_archive = answer_question(
        question="What is the production API quota?",
        store=store,
        router=after_router,
        top_k=1,
        valid_at="2026-07-04T00:00:00Z",
        known_at="2026-07-04T00:00:00Z",
    )
    assert before_archive.facts[0]["value"] == "100"
    assert after_archive.abstained is True
    assert after_archive.trace["archived_claims_filtered"] == 1
    assert after_router.calls == []


def test_graph_first_retrieval_reads_no_more_than_top_k_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    for index in range(10):
        claim = canonical_claim(
            value=str(index),
            source_id=f"source-{index}",
            evidence_span=f"Shared quota topic value is {index}.",
            subject=f"service-{index}",
        )
        store.upsert_wiki_page(
            f"Shared Quota Topic {index}",
            f"Memory {index}",
            metadata={
                "summary": "Shared quota topic",
                "tags": ["shared", "quota"],
                "claims": [claim.to_dict()],
            },
        )

    original_read = store.read_wiki_page
    page_reads: list[str] = []

    def counted_read(slug: str):
        page_reads.append(slug)
        return original_read(slug)

    monkeypatch.setattr(store, "read_wiki_page", counted_read)
    selected = select_top_k_pages(store, "shared quota topic", k=2)

    assert len(selected) == 2
    assert len(page_reads) == 2
    assert set(page_reads) == {page.slug for page in selected}


def test_bitemporal_graph_selects_cross_page_winner_with_top_k_one(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old_active = canonical_claim(
        value="100",
        source_id="source-old",
        evidence_span="The production API quota is 100.",
        observed_at="2026-07-01T00:00:00Z",
    )
    new = canonical_claim(
        value="200",
        source_id="source-new",
        evidence_span="The production API quota is now 200.",
        observed_at="2026-07-03T00:00:00Z",
        supersedes=(old_active.claim_id,),
    )
    old = Claim.from_dict({**old_active.to_dict(), "status": "superseded"})

    store.stage_ingest_operation(
        source_id="source-old",
        source_hash=hashlib.sha256(b"source-old").hexdigest(),
        observed_at=old_active.observed_at,
        target_slug="opaque-alpha",
        affected_keys=[old_active.key],
        incoming_claim_ids=[old_active.claim_id],
        prior_claims=[],
    )
    old_page = store.upsert_wiki_page(
        "Opaque Alpha",
        "Opaque memory alpha.",
        slug="opaque-alpha",
        metadata={"summary": "opaque alpha", "claims": [old_active.to_dict()]},
    )
    store.complete_ingest_operation()

    store.stage_ingest_operation(
        source_id="source-new",
        source_hash=hashlib.sha256(b"source-new").hexdigest(),
        observed_at=new.observed_at,
        target_slug="opaque-beta",
        affected_keys=[new.key],
        incoming_claim_ids=[new.claim_id],
        prior_claims=[(old_page.slug, old_active)],
    )
    store.write_page_claims(old_page.slug, [old])
    new_page = store.upsert_wiki_page(
        "Opaque Beta",
        "Opaque memory beta.",
        slug="opaque-beta",
        metadata={"summary": "opaque beta", "claims": [new.to_dict()]},
    )
    store.complete_ingest_operation()
    for transition in (
        _lifecycle_event(
            page_slug=old_page.slug,
            event_id="cross-old-created",
            claim=old,
            timestamp=old.observed_at,
            from_status=None,
            to_status=ClaimStatus.ACTIVE,
        ),
        _lifecycle_event(
            page_slug=new_page.slug,
            event_id="cross-new-created",
            claim=new,
            timestamp=new.observed_at,
            from_status=None,
            to_status=ClaimStatus.ACTIVE,
        ),
        _lifecycle_event(
            page_slug=old_page.slug,
            event_id="cross-old-superseded",
            claim=old,
            timestamp=new.observed_at,
            from_status=ClaimStatus.ACTIVE,
            to_status=ClaimStatus.SUPERSEDED,
            trigger_claim_id=new.claim_id,
        ),
    ):
        store.append_decision_event(transition)

    original_read = store.read_wiki_page
    page_reads: list[str] = []

    def counted_read(slug: str):
        page_reads.append(slug)
        return original_read(slug)

    monkeypatch.setattr(store, "read_wiki_page", counted_read)

    def ask(at: str, expected: Claim, citation: str):
        response = query_answer(
            answer=f"The production API quota is {expected.value}.",
            key=expected.key,
            value=expected.value,
            claim_id=expected.claim_id,
            citation=citation,
        )
        return answer_question(
            question="What is the production API quota?",
            store=store,
            router=ScriptedRouter(response),
            top_k=1,
            valid_at=at,
            known_at=at,
        )

    historical = ask("2026-07-02T00:00:00Z", old, old_page.slug)
    assert historical.facts, historical.trace
    assert historical.facts[0]["value"] == "100"
    assert historical.trace["selected_page_slugs"] == [old_page.slug]
    assert page_reads == [old_page.slug]

    page_reads.clear()
    current = ask("2026-07-04T00:00:00Z", new, new_page.slug)
    assert current.facts[0]["value"] == "200"
    assert current.trace["selected_page_slugs"] == [new_page.slug]
    assert page_reads == [new_page.slug]


def test_answer_path_reads_only_selected_top_k_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    claims_by_slug = {}
    for index in range(10):
        claim = canonical_claim(
            value=f"value-{index}",
            source_id=f"source-{index}",
            evidence_span=f"Shared retrieval topic value is value-{index}.",
            subject=f"service-{index}",
        )
        page = store.upsert_wiki_page(
            f"Shared Retrieval Topic {index}",
            f"Memory {index}",
            metadata={
                "summary": "shared retrieval topic",
                "tags": ["shared", "retrieval", "topic"],
                "claims": [claim.to_dict()],
            },
        )
        claims_by_slug[page.slug] = claim

    selected_slugs, _ = store.select_graph_candidates(
        "shared retrieval topic",
        k=2,
        as_of="2026-07-14T02:00:00+00:00",
    )
    selected_claim = claims_by_slug[selected_slugs[0]]
    response = query_answer(
        answer=f"The value is {selected_claim.value}.",
        key=selected_claim.key,
        value=selected_claim.value,
        claim_id=selected_claim.claim_id,
        citation=selected_slugs[0],
    )
    original_read = store.read_wiki_page
    page_reads: list[str] = []

    def counted_read(slug: str):
        page_reads.append(slug)
        return original_read(slug)

    monkeypatch.setattr(store, "read_wiki_page", counted_read)
    result = answer_question(
        question="shared retrieval topic",
        store=store,
        router=ScriptedRouter(response),
        top_k=2,
        as_of="2026-07-14T02:00:00Z",
    )

    assert result.abstained is False
    assert page_reads == selected_slugs
    assert len(page_reads) <= 2
    assert result.trace["corpus_pages"] == 10
    assert result.trace["loaded_pages"] == 2


def test_query_uses_logical_due_view_without_materializing_transitions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old = canonical_claim(
        value="100",
        source_id="source-old",
        evidence_span="The API quota is 100.",
    )
    future = canonical_claim(
        value="1000",
        source_id="source-future",
        evidence_span="Effective at T2, this replaces quota 100 with quota 1000.",
        observed_at="2026-07-14T01:00:00Z",
        effective_at="2026-07-14T03:00:00Z",
        supersedes=(old.claim_id,),
    )
    page = store.upsert_wiki_page(
        "Scheduled API Quota",
        "Scheduled quota memory.",
        metadata={
            "summary": "scheduled API quota",
            "tags": ["api", "quota"],
            "claims": [old.to_dict(), future.to_dict()],
        },
    )
    response = query_answer(
        answer="The API quota is 1000.",
        key=future.key,
        value=future.value,
        claim_id=future.claim_id,
        citation=page.slug,
    )

    def forbidden_materialization(*args, **kwargs):
        raise AssertionError("query must not materialize scheduled transitions")

    monkeypatch.setattr(store, "apply_due_transitions", forbidden_materialization)
    original_read = store.read_wiki_page
    page_reads: list[str] = []

    def counted_read(slug: str):
        page_reads.append(slug)
        return original_read(slug)

    monkeypatch.setattr(store, "read_wiki_page", counted_read)
    result = answer_question(
        question="What is the scheduled API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        as_of="2026-07-14T04:00:00Z",
    )

    assert result.abstained is False
    assert result.facts[0]["value"] == "1000"
    assert page_reads == [page.slug]
    assert result.trace["scheduled_transitions_applied"] == 0
    assert result.trace["scheduled_transitions_materialized_by_query"] == 0
    assert store.decision_events() == []


def test_due_chain_retrieves_only_terminal_winner_at_top_k_one(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old = canonical_claim(
        value="100",
        source_id="source-old",
        evidence_span="The production API quota is 100.",
    )
    middle = canonical_claim(
        value="200",
        source_id="source-middle",
        evidence_span="The production API quota becomes 200 on 2026-08-01.",
        observed_at="2026-07-14T01:00:00Z",
        effective_at="2026-08-01T00:00:00Z",
        supersedes=(old.claim_id,),
    )
    terminal = canonical_claim(
        value="300",
        source_id="source-terminal",
        evidence_span="The production API quota becomes 300 on 2026-09-01.",
        observed_at="2026-07-14T02:00:00Z",
        effective_at="2026-09-01T00:00:00Z",
        supersedes=(middle.claim_id,),
    )
    pages = [
        store.upsert_wiki_page(
            title,
            "Scheduled quota memory.",
            metadata={"summary": "production API quota", "claims": [claim.to_dict()]},
        )
        for title, claim in (
            ("Old API Quota", old),
            ("Middle API Quota", middle),
            ("Terminal API Quota", terminal),
        )
    ]
    selected_slugs, _ = store.select_graph_candidates(
        "What is the production API quota?",
        k=1,
        as_of="2026-10-01T00:00:00Z",
    )
    assert selected_slugs == [pages[2].slug]
    response = query_answer(
        answer="The production API quota is 300.",
        key=terminal.key,
        value=terminal.value,
        claim_id=terminal.claim_id,
        citation=pages[2].slug,
    )
    original_read = store.read_wiki_page
    page_reads: list[str] = []

    def counted_read(slug: str):
        page_reads.append(slug)
        return original_read(slug)

    monkeypatch.setattr(store, "read_wiki_page", counted_read)
    result = answer_question(
        question="What is the production API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        as_of="2026-10-01T00:00:00Z",
    )

    assert result.abstained is False
    assert result.facts[0]["value"] == "300"
    assert page_reads == [pages[2].slug]
    assert result.trace["loaded_pages"] == 1


def test_same_page_due_chain_uses_transitive_terminal_winner(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    old = canonical_claim(
        value="100",
        source_id="source-old",
        evidence_span="The production API quota is 100.",
    )
    middle = canonical_claim(
        value="200",
        source_id="source-middle",
        evidence_span="The production API quota becomes 200 on 2026-08-01.",
        observed_at="2026-07-14T01:00:00Z",
        effective_at="2026-08-01T00:00:00Z",
        supersedes=(old.claim_id,),
    )
    terminal = canonical_claim(
        value="300",
        source_id="source-terminal",
        evidence_span="The production API quota becomes 300 on 2026-09-01.",
        observed_at="2026-07-14T02:00:00Z",
        effective_at="2026-09-01T00:00:00Z",
        supersedes=(middle.claim_id,),
    )
    page = store.upsert_wiki_page(
        "API Quota Timeline",
        "Scheduled quota memory.",
        metadata={
            "summary": "production API quota",
            "claims": [old.to_dict(), middle.to_dict(), terminal.to_dict()],
        },
    )
    response = query_answer(
        answer="The production API quota is 300.",
        key=terminal.key,
        value=terminal.value,
        claim_id=terminal.claim_id,
        citation=page.slug,
    )

    result = answer_question(
        question="What is the production API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        as_of="2026-10-01T00:00:00Z",
    )

    assert result.abstained is False
    assert result.facts[0]["value"] == "300"
    assert result.trace["superseded_claims_filtered"] == 2


def test_invalid_then_empty_citation_escalates_and_abstains(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="1000",
        source_id="source-b",
        evidence_span="The production API quota is 1000.",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Current API policy.",
        metadata={
            "summary": "Production API quota",
            "tags": ["api", "quota"],
            "claims": [claim.to_dict()],
        },
    )
    light_empty = {
        "answer": "The current quota is 1000.",
        "facts": [
            {"key": claim.key, "value": "1000", "claim_ids": [claim.claim_id]}
        ],
        "citations": [],
        "confidence": 0.95,
        "abstained": False,
    }
    heavy_invalid = query_answer(
        answer="The current quota is 1000.",
        key=claim.key,
        value="1000",
        claim_id=claim.claim_id,
        citation="not-a-selected-page",
    )
    router = ScriptedRouter(light_empty, heavy_invalid)

    result = answer_question(
        question="What is the production API quota?",
        store=store,
        router=router,
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
    )

    assert page.slug == "api-policy"
    assert len(router.calls) == 2
    assert result.route == "light->heavy"
    assert result.abstained is True
    assert result.facts == []
    assert result.citations == []
    assert result.trace["citation_entailment_pass"] == 0


def test_context_budget_omitted_claim_cannot_pass_validation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    filler_value = "x" * 260
    filler = canonical_claim(
        value=filler_value,
        source_id="filler",
        evidence_span=filler_value + " " + ("y" * 700),
        predicate="filler",
    )
    target = canonical_claim(
        value="secret-42",
        source_id="target",
        evidence_span="Target is secret-42.",
        predicate="target",
    )
    page = store.upsert_wiki_page(
        "Budget Policy",
        "Budget memory.",
        metadata={
            "summary": "Budget policy target",
            "claims": [filler.to_dict(), target.to_dict()],
        },
    )
    omitted_answer = query_answer(
        answer="Target is secret-42.",
        key=target.key,
        value=target.value,
        claim_id=target.claim_id,
        citation=page.slug,
    )
    router = ScriptedRouter(omitted_answer, omitted_answer)

    result = answer_question(
        question="What is the budget policy target?",
        store=store,
        router=router,
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
        context_budget_chars=1000,
    )

    context = json.loads(router.calls[0]["user"])
    emitted_ids = {item["claim_id"] for item in context["active_claims"]}
    assert target.claim_id not in emitted_ids
    assert result.trace["context_budget_claims_filtered"] == 1
    assert result.abstained is True
    assert result.facts == []


def test_active_claim_is_blocked_when_same_key_has_dispute(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    active = canonical_claim(
        value="100",
        source_id="active",
        evidence_span="Quota is 100.",
    )
    disputed = canonical_claim(
        value="200",
        source_id="disputed",
        evidence_span="Quota may be 200.",
        status="disputed",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Conflicting quota memory.",
        metadata={
            "summary": "API quota",
            "claims": [active.to_dict(), disputed.to_dict()],
        },
    )
    unsafe = query_answer(
        answer="Quota is 100.",
        key=active.key,
        value=active.value,
        claim_id=active.claim_id,
        citation=page.slug,
    )
    router = ScriptedRouter(unsafe, unsafe)

    result = answer_question(
        question="What is the API quota?",
        store=store,
        router=router,
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
    )

    context = json.loads(router.calls[0]["user"])
    assert context["active_claims"] == []
    assert {item["value"] for item in context["disputed_claims"]} == {"100", "200"}
    assert result.abstained is True


def test_stale_fact_cannot_hide_outside_structured_facts(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    stale = canonical_claim(
        value="100",
        source_id="old",
        evidence_span="Quota was 100.",
        status="superseded",
    )
    region = canonical_claim(
        value="us-east",
        source_id="region",
        evidence_span="Region is us-east.",
        predicate="region",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "API policy memory.",
        metadata={
            "summary": "Current API policy region and quota",
            "claims": [stale.to_dict(), region.to_dict()],
        },
    )
    hidden_stale = query_answer(
        answer="Region is us-east; the quota is 100.",
        key=region.key,
        value=region.value,
        claim_id=region.claim_id,
        citation=page.slug,
    )
    router = ScriptedRouter(hidden_stale, hidden_stale)

    result = answer_question(
        question="What is the current API policy?",
        store=store,
        router=router,
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
    )

    assert result.abstained is True
    assert result.facts == []


def test_unstructured_prose_is_replaced_by_validated_fact_rendering(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    region = canonical_claim(
        value="us-east",
        source_id="region",
        evidence_span="Region is us-east.",
        predicate="region",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "API policy memory.",
        metadata={"summary": "API region", "claims": [region.to_dict()]},
    )
    unsafe_prose = query_answer(
        answer="Region is us-east and the unsupported price is 9.",
        key=region.key,
        value=region.value,
        claim_id=region.claim_id,
        citation=page.slug,
    )

    result = answer_question(
        question="What is the API region?",
        store=store,
        router=ScriptedRouter(unsafe_prose),
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
    )

    assert result.abstained is False
    assert result.answer == f"{region.key} = us-east."
    assert "price" not in result.answer


def test_query_repairs_dirty_projection_before_candidate_selection(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="1000",
        source_id="source-b",
        evidence_span="The production API quota is 1000.",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Current API policy.",
        metadata={"summary": "Production API quota", "claims": [claim.to_dict()]},
    )
    store.graph_path.write_text('{"corrupt":true}', encoding="utf-8")
    store.projection_dirty_path.write_text("interrupted", encoding="utf-8")
    response = query_answer(
        answer="The quota is 1000.",
        key=claim.key,
        value=claim.value,
        claim_id=claim.claim_id,
        citation=page.slug,
    )

    result = answer_question(
        question="What is the production API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
    )

    assert result.abstained is False
    assert result.trace["projection_recovery_applied"] == 1
    assert result.trace["loaded_pages"] == 1
    assert not store.projection_dirty_path.exists()
    assert store.projection_is_consistent()


def test_selected_but_unrelated_extra_citation_is_rejected(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    quota = canonical_claim(
        value="1000",
        source_id="quota-source",
        evidence_span="The production API quota is 1000.",
    )
    region = canonical_claim(
        value="us-east",
        source_id="region-source",
        evidence_span="The production API region is us-east.",
        predicate="region",
    )
    quota_page = store.upsert_wiki_page(
        "API Quota",
        "Quota memory.",
        metadata={"summary": "production API quota", "claims": [quota.to_dict()]},
    )
    region_page = store.upsert_wiki_page(
        "API Region",
        "Region memory.",
        metadata={"summary": "production API quota region", "claims": [region.to_dict()]},
    )
    extra_citation = {
        "answer": "The quota is 1000.",
        "facts": [
            {"key": quota.key, "value": quota.value, "claim_ids": [quota.claim_id]}
        ],
        "citations": [quota_page.slug, region_page.slug],
        "confidence": 0.9,
        "abstained": False,
    }

    result = answer_question(
        question="What is the production API quota?",
        store=store,
        router=ScriptedRouter(extra_citation, extra_citation),
        top_k=2,
        as_of="2026-07-14T02:00:00Z",
    )

    assert result.abstained is True
    assert result.trace["citation_entailment_pass"] == 0


def test_each_cited_page_must_entail_the_fact_value(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    supported = canonical_claim(
        value="1000",
        source_id="supported-source",
        evidence_span="The production API quota is 1000.",
    )
    unsupported = canonical_claim(
        value="1000",
        source_id="unsupported-source",
        evidence_span="This source discusses the production API quota.",
        effective_at="2026-07-13T00:00:00Z",
    )
    supported_page = store.upsert_wiki_page(
        "Supported Quota",
        "Supported quota memory.",
        metadata={
            "summary": "production API quota",
            "claims": [supported.to_dict()],
        },
    )
    unsupported_page = store.upsert_wiki_page(
        "Unsupported Quota",
        "Unsupported quota memory.",
        metadata={
            "summary": "production API quota",
            "claims": [unsupported.to_dict()],
        },
    )
    response = {
        "answer": "The production API quota is 1000.",
        "facts": [
            {
                "key": supported.key,
                "value": "1000",
                "claim_ids": [supported.claim_id, unsupported.claim_id],
            }
        ],
        "citations": [supported_page.slug, unsupported_page.slug],
        "confidence": 0.9,
        "abstained": False,
    }

    result = answer_question(
        question="What is the production API quota?",
        store=store,
        router=ScriptedRouter(response, response),
        top_k=2,
        as_of="2026-07-14T02:00:00Z",
    )

    assert result.abstained is True
    assert result.citations == []
    assert result.trace["citation_entailment_pass"] == 0


def test_evidence_source_ids_include_only_sources_that_entail_fact(
    tmp_path: Path,
) -> None:
    supporting_span = "The production API quota is 1000."
    noise_span = "This source discusses the production API quota."
    supporting_evidence = EvidenceRef.create(
        source_id="supporting-source",
        source_hash=hashlib.sha256(supporting_span.encode("utf-8")).hexdigest(),
        span=supporting_span,
    )
    noise_evidence = EvidenceRef.create(
        source_id="noise-source",
        source_hash=hashlib.sha256(noise_span.encode("utf-8")).hexdigest(),
        span=noise_span,
    )
    claim = Claim.create(
        kind="fact",
        scope="production",
        subject="api",
        predicate="quota",
        value="1000",
        observed_at="2026-07-14T00:00:00Z",
        effective_at=None,
        source_ids=["supporting-source", "noise-source"],
        evidence=[supporting_evidence, noise_evidence],
    )
    store = MemoryStore(tmp_path / "memory")
    page = store.upsert_wiki_page(
        "API Quota Evidence",
        "Quota evidence memory.",
        metadata={"summary": "production API quota", "claims": [claim.to_dict()]},
    )
    response = query_answer(
        answer="The production API quota is 1000.",
        key=claim.key,
        value=claim.value,
        claim_id=claim.claim_id,
        citation=page.slug,
    )

    result = answer_question(
        question="What is the production API quota?",
        store=store,
        router=ScriptedRouter(response),
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
    )

    assert result.abstained is False
    assert result.citations == [page.slug]
    assert result.evidence_source_ids == ["supporting-source"]

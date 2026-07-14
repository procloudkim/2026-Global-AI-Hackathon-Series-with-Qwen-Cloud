from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from librarian.claims import Claim, EvidenceRef
from librarian.query import _parse_timestamp, answer_question, select_top_k_pages
from librarian.store import MemoryStore
from tests.support import ScriptedRouter, canonical_claim, query_answer


def test_query_timestamp_requires_explicit_timezone() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        _parse_timestamp("2026-07-14T02:00:00", "as_of")


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

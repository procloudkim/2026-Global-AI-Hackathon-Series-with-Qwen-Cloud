from __future__ import annotations

import json
from pathlib import Path

from librarian.claims import Claim, ClaimStatus
from librarian.ingest import ingest_source
from librarian.query import answer_question
from librarian.store import MemoryStore
from tests.support import ScriptedRouter, extracted_claim, ingest_payload, query_answer


def test_explicit_supersession_restart_and_stale_free_query(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    quota_a = "The production API quota is 100."
    unrelated = "The production API region is us-east."
    source_a = f"{quota_a} {unrelated}"
    source_b = "Version 2 replaces version 1. The production API quota is 1000."
    payload_a = ingest_payload(
        title="API Memory",
        summary="Production API quota and region",
        claims=[
            extracted_claim(value="100", evidence_span=quota_a),
            extracted_claim(
                value="us-east",
                evidence_span=unrelated,
                predicate="region",
            ),
        ],
    )
    payload_b = ingest_payload(
        title="API Memory",
        summary="Production API quota and region",
        claims=[
            extracted_claim(
                value="1000",
                evidence_span="The production API quota is 1000.",
            )
        ],
    )

    first = ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(payload_a),
        observed_at="2026-07-14T00:00:00Z",
    )
    second = ingest_source(
        source_id="source-b",
        source_text=source_b,
        store=store,
        router=ScriptedRouter(payload_b),
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page(first.page.slug)]
    old_quota = next(claim for claim in claims if claim.value == "100")
    new_quota = next(claim for claim in claims if claim.value == "1000")
    region = next(claim for claim in claims if claim.predicate == "region")
    assert old_quota.status is ClaimStatus.SUPERSEDED
    assert new_quota.status is ClaimStatus.ACTIVE
    assert old_quota.claim_id in new_quota.supersedes
    assert region.status is ClaimStatus.ACTIVE

    restarted = MemoryStore(store.base)
    query_router = ScriptedRouter(
        query_answer(
            answer="The current production API quota is 1000.",
            key=new_quota.key,
            value="1000",
            claim_id=new_quota.claim_id,
            citation=first.page.slug,
        )
    )
    answer = answer_question(
        question="What is the current production API quota?",
        store=restarted,
        router=query_router,
        top_k=1,
        as_of="2026-07-14T02:00:00Z",
    )

    selected_context = json.loads(query_router.calls[0]["user"])
    active_values = {item["value"] for item in selected_context["active_claims"]}
    disputed_values = {item["value"] for item in selected_context["disputed_claims"]}
    assert answer.abstained is False
    assert answer.facts[0]["value"] == "1000"
    assert answer.citations == [first.page.slug]
    assert answer.evidence_source_ids == ["source-b"]
    assert "1000" in active_values
    assert "100" not in active_values | disputed_values
    assert "us-east" in active_values
    assert answer.trace["superseded_claims_filtered"] == 1
    assert old_quota.claim_id in answer.trace["superseded_claim_ids_filtered"]
    assert old_quota.claim_id not in answer.trace["active_claim_ids_loaded"]
    assert new_quota.claim_id in answer.trace["active_claim_ids_loaded"]

    persisted = [
        Claim.from_dict(item) for item in restarted.claims_for_page(first.page.slug)
    ]
    assert {claim.value for claim in persisted} == {"100", "1000", "us-east"}
    assert (restarted.wiki_dir / f"{first.page.slug}.md").exists()
    assert not (restarted.archive_dir / f"{first.page.slug}.md").exists()
    transitions = restarted.decision_events()
    assert any(
        event.get("claim_id") == old_quota.claim_id
        and event.get("from_status") == "active"
        and event.get("to_status") == "superseded"
        and event.get("trigger_claim_id") == new_quota.claim_id
        and event.get("evidence_spans")
        for event in transitions
    )
    assert second.trace["heavy_arbitrations"] == 0

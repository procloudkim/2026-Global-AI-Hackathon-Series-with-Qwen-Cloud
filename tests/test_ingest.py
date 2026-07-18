from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from librarian.claims import Claim, ClaimStatus
from librarian.ingest import ingest_source
from librarian.store import MemoryStore
from tests.support import NoCallRouter, ScriptedRouter, extracted_claim, ingest_payload


def test_unique_value_only_evidence_expands_to_full_grounding_sentence(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    source = "The production API quota is 100 units per minute."
    value = "100 units per minute"

    result = ingest_source(
        source_id="source-a",
        source_text=source,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[extracted_claim(value=value, evidence_span=value)],
            )
        ),
        observed_at="2026-07-14T00:00:00Z",
    )

    claim = Claim.from_dict(store.claims_for_page(result.page.slug)[0])
    assert [item.span for item in claim.evidence] == [source]


def test_ambiguous_value_only_evidence_still_fails_closed(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    value = "100 units per minute"
    source = (
        "The production API quota is 100 units per minute. "
        "The production API quota remains 100 units per minute."
    )

    with pytest.raises(ValueError, match="not grounded"):
        ingest_source(
            source_id="source-a",
            source_text=source,
            store=store,
            router=ScriptedRouter(
                ingest_payload(
                    title="API Policy",
                    claims=[extracted_claim(value=value, evidence_span=value)],
                )
            ),
            observed_at="2026-07-14T00:00:00Z",
        )
    assert store.list_wiki_pages() == []


def test_explicit_possessive_evidence_repairs_scope_and_predicate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    source = "In workspace, service-a's tier is premium."
    model_claim = extracted_claim(
        value="premium",
        evidence_span=source,
        scope="unspecified",
        subject="service-a",
        predicate="has tier",
    )

    result = ingest_source(
        source_id="source-a",
        source_text=source,
        store=store,
        router=ScriptedRouter(
            ingest_payload(title="Workspace tier", claims=[model_claim])
        ),
        observed_at="2026-07-14T00:00:00Z",
    )

    claim = Claim.from_dict(store.claims_for_page(result.page.slug)[0])
    assert claim.key == "workspace::service-a::tier"
    assert claim.scope == "workspace"
    assert claim.predicate == "tier"


def test_possessive_record_replacement_reconciles_across_page_titles(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    namespace = "release-proof-f7dc8458eb92"
    source_a_id = f"{namespace}-source-a"
    quota_a = (
        f"In release-proof, {namespace}'s production-quota is "
        "100 units per minute."
    )
    quota_b = (
        f"In release-proof, {namespace}'s production-quota is "
        "1000 units per minute."
    )
    source_a = f"Release proof namespace {namespace}. {quota_a}"
    source_b = (
        f"Release proof namespace {namespace}. This record explicitly replaces "
        f"{source_a_id}. {quota_b}"
    )
    first = ingest_source(
        source_id=source_a_id,
        source_text=source_a,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="Release proof configuration",
                claims=[
                    extracted_claim(
                        value="100 units per minute",
                        evidence_span=quota_a,
                        scope="unspecified",
                        subject=namespace,
                        predicate="production quota",
                    )
                ],
            )
        ),
        observed_at="2026-07-15T12:00:00Z",
    )
    second = ingest_source(
        source_id=f"{namespace}-source-b",
        source_text=source_b,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="Production quota update",
                claims=[
                    extracted_claim(
                        value="1000 units per minute",
                        evidence_span=quota_b,
                        scope="unspecified",
                        subject=namespace,
                        predicate="production quota",
                    )
                ],
            )
        ),
        observed_at="2026-07-15T12:01:00Z",
    )

    claims = [
        Claim.from_dict(raw)
        for page in store.list_wiki_pages()
        for raw in store.claims_for_page(page)
    ]
    by_value = {claim.value: claim for claim in claims}
    expected_key = f"release-proof::{namespace}::production-quota"
    assert first.page.slug != second.page.slug
    assert {claim.key for claim in claims} == {expected_key}
    assert by_value["100 units per minute"].status is ClaimStatus.SUPERSEDED
    assert by_value["1000 units per minute"].status is ClaimStatus.ACTIVE
    assert by_value["100 units per minute"].claim_id in by_value[
        "1000 units per minute"
    ].supersedes
    assert second.trace["heavy_arbitrations"] == 0


def test_duplicate_claim_merges_provenance_without_new_claim(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    span_a = "production API quota is 100"
    span_b = "production API quota is 100"
    source_a = f"Policy A states {span_a}."
    source_b = f"Policy B independently confirms {span_b}."
    payload_a = ingest_payload(
        title="API Policy A",
        claims=[extracted_claim(value="100", evidence_span=span_a)],
    )
    payload_b = ingest_payload(
        title="API Policy B",
        claims=[extracted_claim(value="100", evidence_span=span_b)],
    )

    first = ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(payload_a),
        observed_at="2026-07-14T00:00:00Z",
    )
    second_router = ScriptedRouter(payload_b)
    second = ingest_source(
        source_id="source-b",
        source_text=source_b,
        store=store,
        router=second_router,
        observed_at="2026-07-14T01:00:00Z",
    )

    persisted = Claim.from_dict(store.claims_for_page(first.page.slug)[0])
    assert second.claim_ids == [persisted.claim_id]
    assert second.trace["duplicate_provenance_merges"] == 1
    assert persisted.source_ids == ("source-a", "source-b")
    assert {item.source_id for item in persisted.evidence} == {"source-a", "source-b"}
    assert len(persisted.evidence) == 2
    assert second_router.remaining == 0
    assert len(second_router.calls) == 1  # duplicate merge never asks HEAVY


def test_ambiguous_conflict_marks_both_claims_disputed(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    source_a = "The production API quota is 100."
    source_b = "The production API quota is 200."
    payload_a = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=source_a)],
    )
    payload_b = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="200", evidence_span=source_b)],
    )
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "No explicit replacement or effective time is present.",
    }

    ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(payload_a),
        observed_at="2026-07-14T00:00:00Z",
    )
    router = ScriptedRouter(payload_b, unresolved)
    result = ingest_source(
        source_id="source-b",
        source_text=source_b,
        store=store,
        router=router,
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page(result.page.slug)]
    assert {claim.value for claim in claims} == {"100", "200"}
    assert {claim.status for claim in claims} == {ClaimStatus.DISPUTED}
    assert result.route_tier == "light->heavy"
    assert result.trace["heavy_arbitrations"] == 1
    assert len(router.calls) == 2


def test_different_scopes_coexist_without_arbitration(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    source_a = "The production API quota is 100."
    source_b = "The staging API quota is 200."
    payload_a = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=source_a)],
    )
    payload_b = ingest_payload(
        title="API Policy",
        claims=[
            extracted_claim(
                value="200",
                evidence_span=source_b,
                scope="staging",
            )
        ],
    )

    ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(payload_a),
        observed_at="2026-07-14T00:00:00Z",
    )
    router = ScriptedRouter(payload_b)
    result = ingest_source(
        source_id="source-b",
        source_text=source_b,
        store=store,
        router=router,
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page(result.page.slug)]
    assert {(claim.scope, claim.value, claim.status) for claim in claims} == {
        ("production", "100", ClaimStatus.ACTIVE),
        ("staging", "200", ClaimStatus.ACTIVE),
    }
    assert result.trace["heavy_arbitrations"] == 0
    assert len(router.calls) == 1


def test_unrelated_replacement_language_cannot_supersede_claim(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    source_a = "The production API quota is 100."
    source_b = (
        "Version 2 replaces version 1 for the logo. "
        "The production API quota is 200."
    )
    payload_a = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=source_a)],
    )
    payload_b = ingest_payload(
        title="API Policy",
        claims=[
            extracted_claim(
                value="200",
                evidence_span="The production API quota is 200.",
            )
        ],
    )
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "The replacement statement belongs to a different subject.",
    }

    ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(payload_a),
        observed_at="2026-07-14T00:00:00Z",
    )
    ingest_source(
        source_id="source-b",
        source_text=source_b,
        store=store,
        router=ScriptedRouter(payload_b, unresolved),
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert {claim.status for claim in claims} == {ClaimStatus.DISPUTED}
    assert not any(claim.status is ClaimStatus.SUPERSEDED for claim in claims)


def test_broad_evidence_span_cannot_move_unrelated_replacement_to_claim(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    source_a = "The production API quota is 100."
    source_b = (
        "Logo version 2 replaces logo version 1. "
        "The production API quota is 200."
    )
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "Replacement evidence is not scoped to the quota claim.",
    }
    ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[extracted_claim(value="100", evidence_span=source_a)],
            )
        ),
        observed_at="2026-07-14T00:00:00Z",
    )
    ingest_source(
        source_id="source-b",
        source_text=source_b,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[extracted_claim(value="200", evidence_span=source_b)],
            ),
            unresolved,
        ),
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert {claim.status for claim in claims} == {ClaimStatus.DISPUTED}


def test_effective_time_must_be_grounded_in_same_claim_sentence(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    source = (
        "The logo update is effective 2026-08-01. "
        "The production API quota is 200."
    )
    payload = ingest_payload(
        title="API Policy",
        claims=[
            extracted_claim(
                value="200",
                evidence_span=source,
                effective_at="2026-08-01T00:00:00Z",
            )
        ],
    )

    with pytest.raises(ValueError, match="claim-scoped evidence sentence"):
        ingest_source(
            source_id="source-b",
            source_text=source,
            store=store,
            router=ScriptedRouter(payload),
            observed_at="2026-07-14T01:00:00Z",
        )

    assert store.list_wiki_pages() == []


def test_hallucinated_scope_is_rejected_before_key_reconciliation(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    production = "The production API quota is 100."
    staging = "The staging API quota is 200."
    ingest_source(
        source_id="source-production",
        source_text=production,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[extracted_claim(value="100", evidence_span=production)],
            )
        ),
        observed_at="2026-07-14T00:00:00Z",
    )
    malicious = ingest_payload(
        title="API Policy",
        claims=[
            extracted_claim(
                value="200",
                evidence_span=staging,
                scope="production",
            )
        ],
    )

    with pytest.raises(ValueError, match="scope/subject/predicate"):
        ingest_source(
            source_id="source-staging",
            source_text=staging,
            store=store,
            router=ScriptedRouter(malicious),
            observed_at="2026-07-14T01:00:00Z",
        )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert [(claim.value, claim.status) for claim in claims] == [
        ("100", ClaimStatus.ACTIVE)
    ]


@pytest.mark.parametrize(
    "update",
    [
        "Correction to the logo: API quota is 200.",
        "Correction to logo, API quota is 200.",
    ],
)
def test_unrelated_same_sentence_correction_cannot_supersede_claim(
    tmp_path: Path,
    update: str,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old_text = "The API quota is 100."
    ingest_source(
        source_id="source-a",
        source_text=old_text,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[
                    extracted_claim(
                        value="100",
                        evidence_span=old_text,
                        scope="unspecified",
                    )
                ],
            )
        ),
        observed_at="2026-07-14T00:00:00Z",
    )
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "The correction explicitly targets the logo, not quota.",
    }
    ingest_source(
        source_id="source-b",
        source_text=update,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[
                    extracted_claim(
                        value="200",
                        evidence_span=update,
                        scope="unspecified",
                    )
                ],
            ),
            unresolved,
        ),
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert {claim.status for claim in claims} == {ClaimStatus.DISPUTED}


def test_heavy_supersession_evidence_must_belong_to_named_winner(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    source_a = "The production API quota is 100."
    ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[extracted_claim(value="100", evidence_span=source_a)],
            )
        ),
        observed_at="2026-07-14T00:00:00Z",
    )
    old_claim = Claim.from_dict(store.claims_for_page("api-policy")[0])
    # Force the relation through HEAVY by keeping deterministic source evidence
    # non-explicit while the model cites an unrelated winner-owned span.
    ambiguous_source = "The production API quota is 200."
    wrong_winner = {
        "relation": "supersedes",
        "winner_claim_id": old_claim.claim_id,
        "evidence_source_ids": ["source-b"],
        "evidence_spans": [ambiguous_source],
        "rationale": "Incorrectly names the old claim as winner.",
    }
    ingest_source(
        source_id="source-b",
        source_text=ambiguous_source,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[extracted_claim(value="200", evidence_span=ambiguous_source)],
            ),
            wrong_winner,
        ),
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert {claim.status for claim in claims} == {ClaimStatus.DISPUTED}


def test_multiple_values_from_one_source_are_order_independent(tmp_path: Path) -> None:
    source = (
        "Version 2 replaces version 1. "
        "The old production API quota is 100. "
        "The new production API quota is 1000."
    )
    old = extracted_claim(
        value="100",
        evidence_span="The old production API quota is 100.",
    )
    new = extracted_claim(
        value="1000",
        evidence_span="The new production API quota is 1000.",
    )
    outcomes: list[set[tuple[str, ClaimStatus]]] = []

    for index, ordered in enumerate(([old, new], [new, old])):
        store = MemoryStore(tmp_path / f"memory-{index}")
        ingest_source(
            source_id=f"source-{index}",
            source_text=source,
            store=store,
            router=ScriptedRouter(
                ingest_payload(title="API Policy", claims=list(ordered))
            ),
            observed_at="2026-07-14T00:00:00Z",
        )
        outcomes.append(
            {
                (claim.value, claim.status)
                for claim in (
                    Claim.from_dict(item)
                    for item in store.claims_for_page("api-policy")
                )
            }
        )

    assert outcomes[0] == outcomes[1] == {
        ("100", ClaimStatus.DISPUTED),
        ("1000", ClaimStatus.DISPUTED),
    }


def test_future_restore_stays_scheduled_until_effective_time(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    source_a = "The production API quota is 100."
    source_b = "Version 2 replaces version 1. The production API quota is 1000."
    source_c = "Restore production API quota 100 effective 2026-08-01T00:00:00Z."
    payload_a = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=source_a)],
    )
    payload_b = ingest_payload(
        title="API Policy",
        claims=[
            extracted_claim(
                value="1000",
                evidence_span="The production API quota is 1000.",
            )
        ],
    )
    payload_c = ingest_payload(
        title="API Policy",
        claims=[
            extracted_claim(
                value="100",
                evidence_span=source_c,
                effective_at="2026-08-01T00:00:00Z",
            )
        ],
    )

    ingest_source(
        source_id="source-a",
        source_text=source_a,
        store=store,
        router=ScriptedRouter(payload_a),
        observed_at="2026-07-14T00:00:00Z",
    )
    ingest_source(
        source_id="source-b",
        source_text=source_b,
        store=store,
        router=ScriptedRouter(payload_b),
        observed_at="2026-07-14T01:00:00Z",
    )
    ingest_source(
        source_id="source-c",
        source_text=source_c,
        store=store,
        router=ScriptedRouter(payload_c),
        observed_at="2026-07-14T02:00:00Z",
    )

    before = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    current = next(claim for claim in before if claim.value == "1000")
    future = next(claim for claim in before if claim.effective_at is not None)
    assert current.status is ClaimStatus.ACTIVE
    assert future.status is ClaimStatus.ACTIVE
    assert current.claim_id in future.supersedes

    store.apply_due_transitions(
        as_of="2026-08-02T00:00:00Z",
        prompt_version="v2",
    )
    after = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert next(claim for claim in after if claim.claim_id == current.claim_id).status is ClaimStatus.SUPERSEDED
    assert next(claim for claim in after if claim.claim_id == future.claim_id).status is ClaimStatus.ACTIVE
    due_revision = next(
        row
        for row in reversed(store.claim_revisions())
        if row["claim_id"] == current.claim_id
        and row["reason"] == "effective_time_reached"
    )
    assert due_revision["recorded_at"] == "2026-08-02T00:00:00Z"


def test_identical_source_bytes_keep_evidence_for_each_source(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    source = "The production API quota is 100."
    payload = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=source)],
    )

    for source_id, observed_at in (
        ("source-a", "2026-07-14T00:00:00Z"),
        ("source-b", "2026-07-14T01:00:00Z"),
    ):
        ingest_source(
            source_id=source_id,
            source_text=source,
            store=store,
            router=ScriptedRouter(payload),
            observed_at=observed_at,
        )

    claim = Claim.from_dict(store.claims_for_page("api-policy")[0])
    assert set(claim.source_ids) == {"source-a", "source-b"}
    assert {evidence.source_id for evidence in claim.evidence} == {
        "source-a",
        "source-b",
    }


def test_multiple_future_updates_form_an_effective_time_chain(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    records = (
        (
            "source-a",
            "The production API quota is 100.",
            "100",
            None,
            "2026-07-14T00:00:00Z",
        ),
        (
            "source-b",
            "The production API quota replaces the prior quota with 200 effective 2026-08-01.",
            "200",
            "2026-08-01T00:00:00Z",
            "2026-07-14T01:00:00Z",
        ),
        (
            "source-c",
            "The production API quota replaces the prior quota with 300 effective 2026-09-01.",
            "300",
            "2026-09-01T00:00:00Z",
            "2026-07-14T02:00:00Z",
        ),
    )
    for source_id, source_text, value, effective_at, observed_at in records:
        payload = ingest_payload(
            title="API Policy",
            claims=[
                extracted_claim(
                    value=value,
                    evidence_span=source_text,
                    effective_at=effective_at,
                )
            ],
        )
        ingest_source(
            source_id=source_id,
            source_text=source_text,
            store=store,
            router=ScriptedRouter(payload),
            observed_at=observed_at,
        )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    by_value = {claim.value: claim for claim in claims}
    assert by_value["100"].claim_id in by_value["200"].supersedes
    assert by_value["200"].claim_id in by_value["300"].supersedes
    assert by_value["100"].claim_id not in by_value["300"].supersedes

    store.apply_due_transitions(
        as_of="2026-08-02T00:00:00Z",
        prompt_version="v2",
    )
    august = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert next(claim for claim in august if claim.value == "100").status is ClaimStatus.SUPERSEDED
    assert next(claim for claim in august if claim.value == "200").status is ClaimStatus.ACTIVE

    store.apply_due_transitions(
        as_of="2026-09-02T00:00:00Z",
        prompt_version="v2",
    )
    september = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert next(claim for claim in september if claim.value == "200").status is ClaimStatus.SUPERSEDED
    assert next(claim for claim in september if claim.value == "300").status is ClaimStatus.ACTIVE


def test_future_timeline_is_spliced_when_updates_arrive_in_reverse_order(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    records = (
        (
            "source-a",
            "The production API quota is 100.",
            "100",
            None,
            "2026-07-14T00:00:00Z",
        ),
        (
            "source-c",
            "The production API quota replaces the prior quota with 300 effective 2026-09-01.",
            "300",
            "2026-09-01T00:00:00Z",
            "2026-07-14T01:00:00Z",
        ),
        (
            "source-b",
            "The production API quota replaces the prior quota with 200 effective 2026-08-01.",
            "200",
            "2026-08-01T00:00:00Z",
            "2026-07-14T02:00:00Z",
        ),
    )
    for source_id, text, value, effective_at, observed_at in records:
        ingest_source(
            source_id=source_id,
            source_text=text,
            store=store,
            router=ScriptedRouter(
                ingest_payload(
                    title="API Policy",
                    claims=[
                        extracted_claim(
                            value=value,
                            evidence_span=text,
                            effective_at=effective_at,
                        )
                    ],
                )
            ),
            observed_at=observed_at,
        )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    by_value = {claim.value: claim for claim in claims}
    assert by_value["100"].claim_id in by_value["200"].supersedes
    assert by_value["200"].claim_id in by_value["300"].supersedes
    assert by_value["100"].claim_id not in by_value["300"].supersedes

    store.apply_due_transitions(
        as_of="2026-09-02T00:00:00Z",
        prompt_version="v2",
    )
    final = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert next(claim for claim in final if claim.value == "100").status is ClaimStatus.SUPERSEDED
    assert next(claim for claim in final if claim.value == "200").status is ClaimStatus.SUPERSEDED
    assert next(claim for claim in final if claim.value == "300").status is ClaimStatus.ACTIVE


def test_observed_timestamp_is_canonical_and_naive_input_fails_before_io(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    source = "The production API quota is 100."
    payload = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=source)],
    )
    for observed_at in (
        "2026-07-14T00:00:00Z",
        "2026-07-14T00:00:00+00:00",
        "2026-07-14T00:00:00Z",
    ):
        ingest_source(
            source_id="source-a",
            source_text=source,
            store=store,
            router=ScriptedRouter(payload),
            observed_at=observed_at,
        )

    events = store.decision_events()
    assert len({event["event_id"] for event in events}) == len(events) == 1
    assert {event["timestamp"] for event in events} == {"2026-07-14T00:00:00Z"}

    second_store = MemoryStore(tmp_path / "naive-memory")
    with pytest.raises(ValueError, match="timezone offset"):
        ingest_source(
            source_id="source-naive",
            source_text=source,
            store=second_store,
            router=NoCallRouter(),
            observed_at="2026-07-14T00:00:00",
        )
    assert list(second_store.raw_dir.iterdir()) == []


def test_concurrent_ingests_serialize_the_full_reconciliation_pipeline(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "memory"
    store = MemoryStore(memory)
    initial = "The production API quota is 100."
    ingest_source(
        source_id="source-a",
        source_text=initial,
        store=store,
        router=ScriptedRouter(
            ingest_payload(
                title="API Policy",
                claims=[extracted_claim(value="100", evidence_span=initial)],
            )
        ),
        observed_at="2026-07-14T00:00:00Z",
    )
    barrier = Barrier(2)
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "Concurrent updates do not establish a winner.",
    }

    def update(value: str) -> None:
        text = f"The production API quota is {value}."
        barrier.wait()
        ingest_source(
            source_id=f"source-{value}",
            source_text=text,
            store=MemoryStore(memory),
            router=ScriptedRouter(
                ingest_payload(
                    title="API Policy",
                    claims=[extracted_claim(value=value, evidence_span=text)],
                ),
                unresolved,
            ),
            observed_at=f"2026-07-14T0{1 if value == '200' else 2}:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(update, ("200", "300")))

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert {claim.value for claim in claims} == {"100", "200", "300"}
    assert {claim.status for claim in claims} == {ClaimStatus.DISPUTED}
    assert store.projection_is_consistent()


def test_duplicate_retry_resumes_an_interrupted_conflict_reconciliation(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old_text = "The production API quota is 100."
    new_text = "The production API quota is 200."
    old_payload = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=old_text)],
    )
    new_payload = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="200", evidence_span=new_text)],
    )
    ingest_source(
        source_id="source-a",
        source_text=old_text,
        store=store,
        router=ScriptedRouter(old_payload),
        observed_at="2026-07-14T00:00:00Z",
    )

    with pytest.raises(AssertionError, match="unexpected model call"):
        ingest_source(
            source_id="source-b",
            source_text=new_text,
            store=store,
            router=ScriptedRouter(new_payload),
            observed_at="2026-07-14T01:00:00Z",
        )
    interrupted = [
        Claim.from_dict(item) for item in store.claims_for_page("api-policy")
    ]
    assert {claim.status for claim in interrupted} == {ClaimStatus.ACTIVE}
    assert store.pending_ingest_path.exists()

    restarted = MemoryStore(store.base)
    recovered_keys = restarted.recover_pending_ingest(prompt_version="v2")
    fail_closed = [
        Claim.from_dict(item)
        for item in restarted.claims_for_page("api-policy")
    ]
    assert recovered_keys == [fail_closed[0].key]
    assert {claim.status for claim in fail_closed} == {ClaimStatus.DISPUTED}
    assert not restarted.pending_ingest_path.exists()

    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "No explicit replacement evidence.",
    }
    ingest_source(
        source_id="source-b",
        source_text=new_text,
        store=restarted,
        router=ScriptedRouter(new_payload, unresolved),
        observed_at="2026-07-14T01:00:00Z",
    )

    repaired = [
        Claim.from_dict(item)
        for item in restarted.claims_for_page("api-policy")
    ]
    assert {claim.status for claim in repaired} == {ClaimStatus.DISPUTED}


def test_ingest_repairs_dirty_projection_before_duplicate_lookup(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    text = "The production API quota is 100."
    payload = ingest_payload(
        title="API Policy",
        claims=[extracted_claim(value="100", evidence_span=text)],
    )
    ingest_source(
        source_id="source-a",
        source_text=text,
        store=store,
        router=ScriptedRouter(payload),
        observed_at="2026-07-14T00:00:00Z",
    )
    store.graph_path.write_text('{"corrupt":true}', encoding="utf-8")
    store.projection_dirty_path.write_text("interrupted", encoding="utf-8")

    result = ingest_source(
        source_id="source-b",
        source_text=text,
        store=store,
        router=ScriptedRouter(payload),
        observed_at="2026-07-14T01:00:00Z",
    )

    claims = [Claim.from_dict(item) for item in store.claims_for_page("api-policy")]
    assert len(claims) == 1
    assert set(claims[0].source_ids) == {"source-a", "source-b"}
    assert result.trace["projection_recovery_applied"] == 1

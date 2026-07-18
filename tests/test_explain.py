from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from librarian import main, mcp_server
from librarian.claims import Claim, ClaimStatus, TransitionEvent
from librarian.explain import explain_memory
from librarian.store import MemoryStore

from .support import NoCallRouter, canonical_claim


def _replacement_store(tmp_path: Path) -> tuple[MemoryStore, Claim, Claim]:
    store = MemoryStore(tmp_path / "memory")
    old = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="In production, api quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    store.upsert_wiki_page(
        "Quota",
        "Original quota.",
        slug="quota",
        metadata={"claims": [old.to_dict()]},
        revision_recorded_at="2026-07-14T00:00:00Z",
        revision_operation_id="ingest-source-a",
        revision_reason="source-a ingest",
    )

    old_superseded = Claim.from_dict(
        {**old.to_dict(), "status": ClaimStatus.SUPERSEDED.value}
    )
    new = canonical_claim(
        value="1000",
        source_id="source-b",
        evidence_span="Source B changes the production api quota to 1000.",
        observed_at="2026-07-15T00:00:00Z",
        supersedes=(old.claim_id,),
    )
    store.upsert_wiki_page(
        "Quota",
        "Current quota with retained history.",
        slug="quota",
        metadata={"claims": [old_superseded.to_dict(), new.to_dict()]},
        revision_recorded_at="2026-07-15T00:00:00Z",
        revision_operation_id="ingest-source-b",
        revision_reason="explicit source-b correction",
    )
    store.append_decision_event(
        TransitionEvent(
            schema_version="1.0",
            event_id="supersede-old-quota",
            timestamp="2026-07-15T00:00:00Z",
            page_slug="quota",
            claim_id=old.claim_id,
            from_status=ClaimStatus.ACTIVE,
            to_status=ClaimStatus.SUPERSEDED,
            trigger_claim_id=new.claim_id,
            rule="explicit_supersession",
            relation="supersedes",
            model=None,
            prompt_version="deterministic-test",
            evidence_source_ids=("source-b",),
            evidence_spans=("Source B changes the production api quota to 1000.",),
            rationale="Source B explicitly replaces the old quota.",
        ).to_dict()
    )
    return store, old, new


def test_explain_memory_returns_current_history_and_decision(tmp_path: Path) -> None:
    store, old, new = _replacement_store(tmp_path)

    result = explain_memory(store=store, key="Production::API::Quota")

    assert result["key"] == "production::api::quota"
    assert result["resolution_status"] == "resolved"
    assert result["winner_claim_ids"] == [new.claim_id]
    assert [(row["value"], row["status"]) for row in result["current_claims"]] == [
        ("1000", "active"),
    ]
    assert [(row["value"], row["status"]) for row in result["canonical_claims"]] == [
        ("1000", "active"),
        ("100", "superseded"),
    ]
    assert result["history"][0]["value"] == "100"
    assert result["history"][0]["status"] == "active"
    assert {
        (row["value"], row["status"]) for row in result["history"][1:]
    } == {("100", "superseded"), ("1000", "active")}
    assert [row["ordinal"] for row in result["history"]] == [1, 2, 3]
    assert result["decisions"] == [
        {
            "event_id": "supersede-old-quota",
            "event_type": "transition",
            "timestamp": "2026-07-15T00:00:00Z",
            "claim_id": old.claim_id,
            "trigger_claim_id": new.claim_id,
            "from_status": "active",
            "to_status": "superseded",
            "rule": "explicit_supersession",
            "relation": "supersedes",
            "evidence_source_ids": ["source-b"],
            "rationale": "Source B explicitly replaces the old quota.",
        }
    ]
    assert result["proof_boundary"] == {
        "read_only": True,
        "memory_mutations": 0,
        "provider_calls": 0,
        "interpretation": "bitemporal ledger projection, not model judgment",
    }
    assert result["integrity"] == {"status": "ok", "recovery_required": []}


def test_explain_memory_is_key_scoped_and_fails_closed(tmp_path: Path) -> None:
    store, _, _ = _replacement_store(tmp_path)

    result = explain_memory(store=store, key="production::api::missing")

    assert result["resolution_status"] == "not_found"
    assert result["winner_claim_ids"] == []
    assert result["current_claims"] == []
    assert result["canonical_claims"] == []
    assert result["history"] == []
    assert result["decisions"] == []


def test_explain_memory_reports_unresolved_dispute(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    first = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="In production, api quota is 100.",
        status=ClaimStatus.DISPUTED,
    )
    second = canonical_claim(
        value="200",
        source_id="source-b",
        evidence_span="In production, api quota is 200.",
        status=ClaimStatus.DISPUTED,
    )
    store.upsert_wiki_page(
        "Quota conflict",
        "Conflicting quota sources.",
        metadata={"claims": [first.to_dict(), second.to_dict()]},
        revision_recorded_at="2026-07-14T00:00:00Z",
        revision_operation_id="conflict-ingest",
        revision_reason="unresolved source conflict",
    )

    result = explain_memory(store=store, key="production::api::quota")

    assert result["resolution_status"] == "unresolved"
    assert result["winner_claim_ids"] == []
    assert {row["value"] for row in result["current_claims"]} == {"100", "200"}


def test_explain_memory_uses_same_effective_time_chain_as_query(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    current = canonical_claim(
        value="1000",
        source_id="source-current",
        evidence_span="The production API quota is 1000.",
        observed_at="2026-07-14T01:00:00Z",
    )
    future = canonical_claim(
        value="100",
        source_id="source-future",
        evidence_span="On August 1, the production API quota becomes 100.",
        observed_at="2026-07-14T02:00:00Z",
        effective_at="2026-08-01T00:00:00Z",
        supersedes=(current.claim_id,),
    )
    store.upsert_wiki_page(
        "Quota schedule",
        "Current and scheduled quota.",
        slug="quota-schedule",
        metadata={"claims": [current.to_dict(), future.to_dict()]},
        revision_recorded_at="2026-07-14T02:00:00Z",
        revision_operation_id="schedule-ingest",
        revision_reason="record scheduled replacement",
    )

    before = explain_memory(
        store=store,
        key="production::api::quota",
        valid_at="2026-07-20T00:00:00Z",
        known_at="2026-07-20T00:00:00Z",
    )
    after = explain_memory(
        store=store,
        key="production::api::quota",
        valid_at="2026-08-02T00:00:00Z",
        known_at="2026-08-02T00:00:00Z",
    )

    assert before["resolution_status"] == "resolved"
    assert before["winner_claim_ids"] == [current.claim_id]
    assert after["resolution_status"] == "resolved"
    assert after["winner_claim_ids"] == [future.claim_id]


def test_explain_memory_hides_receipts_not_yet_known(tmp_path: Path) -> None:
    store, old, new = _replacement_store(tmp_path)

    result = explain_memory(
        store=store,
        key="production::api::quota",
        valid_at="2026-07-14T12:00:00Z",
        known_at="2026-07-14T12:00:00Z",
    )

    assert result["resolution_status"] == "resolved"
    assert result["winner_claim_ids"] == [old.claim_id]
    assert [(row["value"], row["status"]) for row in result["canonical_claims"]] == [
        ("100", "active"),
    ]
    assert [row["claim_id"] for row in result["history"]] == [old.claim_id]
    assert result["decisions"] == []
    assert new.claim_id not in str(result)


def test_explain_memory_refuses_crash_incomplete_projection(tmp_path: Path) -> None:
    store, _, _ = _replacement_store(tmp_path)
    store.projection_dirty_path.write_text("{}", encoding="utf-8")

    result = explain_memory(store=store, key="production::api::quota")

    assert result["status"] == "incomplete"
    assert result["resolution_status"] == "recovery_required"
    assert result["winner_claim_ids"] == []
    assert result["current_claims"] == []
    assert result["history"] == []
    assert result["integrity"] == {
        "status": "recovery_required",
        "recovery_required": ["projection_dirty"],
    }


def test_explain_memory_excludes_cross_key_transition(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    quota = canonical_claim(
        value="100",
        source_id="quota-source",
        evidence_span="The production API quota is 100.",
    )
    region = canonical_claim(
        value="seoul",
        source_id="region-source",
        evidence_span="The production API region is Seoul.",
        predicate="region",
    )
    store.upsert_wiki_page(
        "Mixed policy",
        "Two distinct keys.",
        metadata={"claims": [quota.to_dict(), region.to_dict()]},
        revision_recorded_at="2026-07-14T00:00:00Z",
        revision_operation_id="mixed-ingest",
        revision_reason="record independent keys",
    )
    store.append_decision_event(
        TransitionEvent(
            schema_version="1.0",
            event_id="cross-key-event",
            timestamp="2026-07-15T00:00:00Z",
            page_slug="mixed-policy",
            claim_id=quota.claim_id,
            from_status=ClaimStatus.ACTIVE,
            to_status=ClaimStatus.SUPERSEDED,
            trigger_claim_id=region.claim_id,
            rule="legacy_cross_key",
            relation="supersedes",
            model=None,
            prompt_version="legacy",
            evidence_source_ids=("region-source",),
            evidence_spans=("The production API region is Seoul.",),
            rationale="Malformed legacy event must not cross key boundaries.",
        ).to_dict()
    )

    result = explain_memory(store=store, key="production::api::quota")

    assert result["decisions"] == []


def test_explain_memory_rejects_ambiguous_key(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")

    try:
        explain_memory(store=store, key="production/api/quota")
    except ValueError as exc:
        assert "scope::subject::predicate" in str(exc)
    else:
        raise AssertionError("invalid key must fail closed")


def test_rest_and_mcp_explanations_match_without_provider_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store, _, _ = _replacement_store(tmp_path)
    no_call_router = NoCallRouter()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "get_router", lambda: no_call_router)
    monkeypatch.setattr(mcp_server, "store", store)
    monkeypatch.setattr(mcp_server, "get_router", lambda: no_call_router)

    rest = TestClient(main.app).get(
        "/memory/explain",
        params={
            "key": "production::api::quota",
            "as_of": "2026-07-16T00:00:00Z",
        },
    )
    mcp = mcp_server.memory_explain_impl(
        "production::api::quota",
        "2026-07-16T00:00:00Z",
    )

    assert rest.status_code == 200
    assert rest.json() == mcp


def test_rest_explain_rejects_ambiguous_key(monkeypatch, tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    no_call_router = NoCallRouter()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "get_router", lambda: no_call_router)

    response = TestClient(main.app).get(
        "/memory/explain",
        params={"key": "production/api/quota"},
    )

    assert response.status_code == 422
    assert "scope::subject::predicate" in response.json()["detail"]


def test_rest_explain_classifies_store_integrity_failure_as_503(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    monkeypatch.setattr(main, "store", store)

    def fail_integrity(**_kwargs):
        raise ValueError("corrupt revision ledger")

    monkeypatch.setattr(main, "explain_memory", fail_integrity)

    response = TestClient(main.app).get(
        "/memory/explain",
        params={"key": "production::api::quota"},
    )

    assert response.status_code == 503
    assert "memory integrity check failed" in response.json()["detail"]

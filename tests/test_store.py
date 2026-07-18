from __future__ import annotations

import hashlib
import multiprocessing
from pathlib import Path

import pytest

from librarian.claims import Claim, ClaimStatus, TransitionEvent, make_claim_id
from librarian.store import MemoryStore
from tests.support import canonical_claim


def _hold_memory_transaction(memory_path: str, entered, release) -> None:
    store = MemoryStore(memory_path)
    with store.transaction():
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test did not release the first memory transaction")


def _acquire_memory_transaction(memory_path: str, started, acquired) -> None:
    store = MemoryStore(memory_path)
    started.set()
    with store.transaction():
        acquired.set()


def test_raw_sources_are_content_hashed_and_immutable(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    first_text = "quota=100"
    second_text = "quota=1000"

    first = store.save_raw_source("policy", first_text)
    first_again = store.save_raw_source("policy", first_text)
    second = store.save_raw_source("policy", second_text)

    assert first == first_again
    assert first != second
    assert first.name == f"policy--{hashlib.sha256(first_text.encode()).hexdigest()}.md"
    assert second.name == f"policy--{hashlib.sha256(second_text.encode()).hexdigest()}.md"
    assert first.read_text(encoding="utf-8") == first_text
    assert second.read_text(encoding="utf-8") == second_text


def test_memory_transaction_serializes_across_processes(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    MemoryStore(memory)
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    started = context.Event()
    acquired = context.Event()
    holder = context.Process(
        target=_hold_memory_transaction,
        args=(str(memory), entered, release),
    )
    waiter = context.Process(
        target=_acquire_memory_transaction,
        args=(str(memory), started, acquired),
    )
    try:
        holder.start()
        assert entered.wait(10), "first process did not acquire the memory lock"
        waiter.start()
        assert started.wait(10), "second process did not start"
        assert not acquired.wait(0.4), "second process bypassed the memory lock"
        release.set()
        assert acquired.wait(10), "second process did not acquire the released lock"
        holder.join(10)
        waiter.join(10)
        assert holder.exitcode == 0
        assert waiter.exitcode == 0
    finally:
        release.set()
        for process in (holder, waiter):
            if process.is_alive():
                process.terminate()
            process.join(5)
def test_store_rolls_back_superseded_claim_without_deleting_page(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"summary": "API quota policy", "claims": [claim.to_dict()]},
    )

    supersede = _transition(
        claim=claim,
        page_slug=page.slug,
        from_status=ClaimStatus.ACTIVE,
        to_status=ClaimStatus.SUPERSEDED,
        event_id="event-supersede",
        rationale="Erroneous supersession under review.",
    )
    store.apply_claim_transition(
        page_slug=page.slug,
        claim_id=claim.claim_id,
        to_status=ClaimStatus.SUPERSEDED,
        event=supersede,
    )

    rollback = _transition(
        claim=claim,
        page_slug=page.slug,
        from_status=ClaimStatus.SUPERSEDED,
        to_status=ClaimStatus.ACTIVE,
        event_id="event-rollback",
        rationale="Audit restored the previously valid claim.",
    )
    restored = store.apply_claim_transition(
        page_slug=page.slug,
        claim_id=claim.claim_id,
        to_status=ClaimStatus.ACTIVE,
        event=rollback,
    )

    restarted = MemoryStore(store.base)
    persisted = Claim.from_dict(restarted.claims_for_page(page.slug)[0])
    assert restored.status is ClaimStatus.ACTIVE
    assert persisted.status is ClaimStatus.ACTIVE
    assert (restarted.wiki_dir / f"{page.slug}.md").exists()
    assert not (restarted.archive_dir / f"{page.slug}.md").exists()
    assert [event["event_id"] for event in restarted.decision_events()] == [
        "event-supersede",
        "event-rollback",
    ]


def test_transition_retry_is_exactly_event_id_idempotent(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"summary": "API quota", "claims": [claim.to_dict()]},
    )
    first = _transition(
        claim=claim,
        page_slug=page.slug,
        from_status=ClaimStatus.ACTIVE,
        to_status=ClaimStatus.SUPERSEDED,
        event_id="event-one",
        rationale="First transition.",
    )
    different = _transition(
        claim=claim,
        page_slug=page.slug,
        from_status=ClaimStatus.ACTIVE,
        to_status=ClaimStatus.SUPERSEDED,
        event_id="event-two",
        rationale="Different transition must not masquerade as a retry.",
    )

    store.apply_claim_transition(
        page_slug=page.slug,
        claim_id=claim.claim_id,
        to_status=ClaimStatus.SUPERSEDED,
        event=first,
    )
    store.apply_claim_transition(
        page_slug=page.slug,
        claim_id=claim.claim_id,
        to_status=ClaimStatus.SUPERSEDED,
        event=first,
    )
    with pytest.raises(ValueError, match="different recorded transition"):
        store.apply_claim_transition(
            page_slug=page.slug,
            claim_id=claim.claim_id,
            to_status=ClaimStatus.SUPERSEDED,
            event=different,
        )

    assert [event["event_id"] for event in store.decision_events()] == ["event-one"]


def test_pending_transition_recovers_page_write_without_ledger_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"summary": "quota", "claims": [claim.to_dict()]},
    )
    transition = _transition(
        claim=claim,
        page_slug=page.slug,
        from_status=ClaimStatus.ACTIVE,
        to_status=ClaimStatus.SUPERSEDED,
        event_id="event-crash",
        rationale="Simulated crash between canonical write and ledger append.",
    )
    real_append = store.append_decision_event

    def fail_append(event):
        raise RuntimeError("simulated append crash")

    monkeypatch.setattr(store, "append_decision_event", fail_append)
    with pytest.raises(RuntimeError, match="simulated append crash"):
        store.apply_claim_transition(
            page_slug=page.slug,
            claim_id=claim.claim_id,
            to_status=ClaimStatus.SUPERSEDED,
            event=transition,
        )

    persisted = Claim.from_dict(store.claims_for_page(page.slug)[0])
    assert persisted.status is ClaimStatus.SUPERSEDED
    assert store.pending_transition_path.exists()
    monkeypatch.setattr(store, "append_decision_event", real_append)

    assert store.recover_pending_transition() is True
    assert not store.pending_transition_path.exists()
    assert [event["event_id"] for event in store.decision_events()] == ["event-crash"]
    revisions = store.claim_revisions()
    assert [row["ordinal"] for row in revisions] == [1, 2]
    assert [row["claim"]["status"] for row in revisions] == [
        "active",
        "superseded",
    ]


def test_pending_ingest_recovery_restores_prior_state_then_disputes_conflict(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    old = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The API quota is 100.",
    )
    incoming = canonical_claim(
        value="200",
        source_id="source-b",
        evidence_span="The API quota is 200.",
        observed_at="2026-07-14T01:00:00Z",
    )
    old_page = store.upsert_wiki_page(
        "Old Policy",
        "Old policy.",
        metadata={"summary": "API quota", "claims": [old.to_dict()]},
    )
    old_creation = TransitionEvent(
        schema_version="librarian-memory/v2",
        event_id="old-creation",
        timestamp=old.observed_at,
        page_slug=old_page.slug,
        claim_id=old.claim_id,
        from_status=None,
        to_status=ClaimStatus.ACTIVE,
        trigger_claim_id=None,
        rule="source_grounded_claim_creation",
        relation=None,
        model="fixture",
        prompt_version="v2",
        evidence_source_ids=old.source_ids,
        evidence_spans=tuple(item.span for item in old.evidence),
        rationale="Fixture creation receipt.",
    )
    store.append_decision_event(old_creation)
    incoming_text = "The API quota is 200."
    store.stage_ingest_operation(
        source_id="source-b",
        source_hash=hashlib.sha256(incoming_text.encode("utf-8")).hexdigest(),
        observed_at="2026-07-14T01:00:00Z",
        target_slug="new-policy",
        affected_keys=[incoming.key],
        incoming_claim_ids=[incoming.claim_id],
        prior_claims=[(old_page.slug, old)],
    )
    new_page = store.upsert_wiki_page(
        "New Policy",
        "New policy.",
        metadata={"summary": "API quota", "claims": [incoming.to_dict()]},
    )
    incoming_creation = TransitionEvent(
        schema_version="librarian-memory/v2",
        event_id="incoming-creation",
        timestamp=incoming.observed_at,
        page_slug=new_page.slug,
        claim_id=incoming.claim_id,
        from_status=None,
        to_status=ClaimStatus.ACTIVE,
        trigger_claim_id=None,
        rule="source_grounded_claim_creation",
        relation=None,
        model="fixture",
        prompt_version="v2",
        evidence_source_ids=incoming.source_ids,
        evidence_spans=tuple(item.span for item in incoming.evidence),
        rationale="Fixture creation receipt.",
    )
    store.append_decision_event(incoming_creation)
    partial = _transition(
        claim=old,
        page_slug=old_page.slug,
        from_status=ClaimStatus.ACTIVE,
        to_status=ClaimStatus.SUPERSEDED,
        event_id="partial-supersession",
        rationale="Simulated first mutation of an interrupted ingest.",
    )
    store.apply_claim_transition(
        page_slug=old_page.slug,
        claim_id=old.claim_id,
        to_status=ClaimStatus.SUPERSEDED,
        event=partial,
    )

    restarted = MemoryStore(store.base)
    recovered_keys = restarted.recover_pending_ingest(prompt_version="v2")

    recovered = [
        Claim.from_dict(raw)
        for slug in (old_page.slug, new_page.slug)
        for raw in restarted.claims_for_page(slug)
    ]
    assert recovered_keys == [old.key]
    assert {claim.status for claim in recovered} == {ClaimStatus.DISPUTED}
    assert not restarted.pending_ingest_path.exists()
    rules = {str(event.get("rule")) for event in restarted.decision_events()}
    assert "ingest_recovery_restore_prior_state" in rules
    assert "ingest_recovery_fail_closed" in rules


def test_write_page_claims_rejects_invalid_canonical_claim(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"summary": "API quota", "claims": [claim.to_dict()]},
    )
    invalid = {**claim.to_dict(), "status": "not-a-status"}

    with pytest.raises(ValueError, match="status must be one of"):
        store.write_page_claims(page.slug, [invalid])

    persisted = Claim.from_dict(store.claims_for_page(page.slug)[0])
    assert persisted == claim


def test_claim_revision_chain_captures_creation_and_lifecycle_update(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    target_slug = store.slug_for("API Policy")
    store.stage_ingest_operation(
        source_id="source-a",
        source_hash=hashlib.sha256(b"source-a").hexdigest(),
        observed_at=claim.observed_at,
        target_slug=target_slug,
        affected_keys=[claim.key],
        incoming_claim_ids=[claim.claim_id],
        prior_claims=[],
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"summary": "quota", "claims": [claim.to_dict()]},
    )
    store.complete_ingest_operation()

    transition = _transition(
        claim=claim,
        page_slug=page.slug,
        from_status=ClaimStatus.ACTIVE,
        to_status=ClaimStatus.SUPERSEDED,
        event_id="revision-transition",
        rationale="A later source replaced this claim.",
    )
    store.apply_claim_transition(
        page_slug=page.slug,
        claim_id=claim.claim_id,
        to_status=ClaimStatus.SUPERSEDED,
        event=transition,
        recorded_at="2026-07-15T00:00:00Z",
    )

    revisions = store.claim_revisions()
    assert [row["ordinal"] for row in revisions] == [1, 2]
    assert [row["change_kind"] for row in revisions] == ["creation", "update"]
    assert revisions[0]["previous_revision_id"] is None
    assert revisions[1]["previous_revision_id"] == revisions[0]["revision_id"]
    assert revisions[0]["claim"]["status"] == "active"
    assert revisions[1]["claim"]["status"] == "superseded"
    assert revisions[1]["recorded_at"] == "2026-07-15T00:00:00Z"

    diagnostics = store.claim_revision_diagnostics()
    assert diagnostics == {
        "schema_version": "librarian-claim-revision/v1",
        "ledger_exists": True,
        "revision_count": 2,
        "tracked_claim_count": 1,
        "current_claim_count": 1,
        "untracked_current_claim_count": 0,
        "baseline_revision_count": 0,
        "earliest_recorded_at": "2026-07-14T00:00:00Z",
        "latest_recorded_at": "2026-07-15T00:00:00Z",
        "pending_receipt_exists": False,
    }


def test_claim_deletion_is_rejected_in_favor_of_archive_transition(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"claims": [claim.to_dict()]},
    )

    with pytest.raises(ValueError, match="transition claims to archived"):
        store.write_page_claims(page.slug, [])

    assert store.claims_for_page(page.slug) == [claim.to_dict()]


def test_claim_revision_context_rejects_blank_identity(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )

    with pytest.raises(ValueError, match="must be non-empty"):
        store.upsert_wiki_page(
            "API Policy",
            "Policy facts.",
            metadata={"claims": [claim.to_dict()]},
            revision_recorded_at=claim.observed_at,
            revision_operation_id=" ",
            revision_reason="creation",
        )

    assert store.list_wiki_pages() == []


def test_pending_claim_revision_recovers_page_write_exactly_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    target_slug = store.slug_for("API Policy")
    store.stage_ingest_operation(
        source_id="source-a",
        source_hash=hashlib.sha256(b"source-a").hexdigest(),
        observed_at=claim.observed_at,
        target_slug=target_slug,
        affected_keys=[claim.key],
        incoming_claim_ids=[claim.claim_id],
        prior_claims=[],
    )

    def fail_append(_revision):
        raise RuntimeError("simulated revision append crash")

    monkeypatch.setattr(store, "_append_claim_revision", fail_append)
    with pytest.raises(RuntimeError, match="simulated revision append crash"):
        store.upsert_wiki_page(
            "API Policy",
            "Policy facts.",
            metadata={"summary": "quota", "claims": [claim.to_dict()]},
        )

    assert store.claims_for_page(target_slug)[0]["claim_id"] == claim.claim_id
    assert store.pending_claim_revisions_path.exists()
    restarted = MemoryStore(store.base)
    assert restarted.recover_pending_claim_revisions() is True
    assert not restarted.pending_claim_revisions_path.exists()
    assert len(restarted.claim_revisions()) == 1
    assert restarted.recover_pending_claim_revisions() is False
    assert len(restarted.claim_revisions()) == 1


def test_new_mutation_recovers_pending_revision_before_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    first = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    second = canonical_claim(
        value="eu-central-1",
        source_id="source-b",
        evidence_span="The region is eu-central-1.",
        observed_at="2026-07-15T00:00:00Z",
        predicate="region",
    )
    original_append = store._append_claim_revision

    def fail_append(_revision):
        raise RuntimeError("simulated revision append crash")

    monkeypatch.setattr(store, "_append_claim_revision", fail_append)
    with pytest.raises(RuntimeError, match="simulated revision append crash"):
        store.upsert_wiki_page(
            "API Policy",
            "Policy facts.",
            slug="api-policy",
            metadata={"claims": [first.to_dict()]},
            revision_recorded_at=first.observed_at,
            revision_operation_id="create-first",
            revision_reason="first creation",
        )
    assert store.pending_claim_revisions_path.exists()

    monkeypatch.setattr(store, "_append_claim_revision", original_append)
    store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        slug="api-policy",
        metadata={"claims": [first.to_dict(), second.to_dict()]},
        revision_recorded_at=second.observed_at,
        revision_operation_id="create-second",
        revision_reason="second creation",
    )

    revisions = store.claim_revisions()
    assert [row["claim_id"] for row in revisions] == [
        first.claim_id,
        second.claim_id,
    ]
    assert [row["ordinal"] for row in revisions] == [1, 2]
    assert not store.pending_claim_revisions_path.exists()


def test_tracked_claim_mutation_requires_revision_context(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"claims": [claim.to_dict()]},
        revision_recorded_at=claim.observed_at,
        revision_operation_id="create-claim",
        revision_reason="claim creation",
    )
    disputed = Claim.from_dict({**claim.to_dict(), "status": "disputed"})

    with pytest.raises(ValueError, match="requires complete revision context"):
        store.write_page_claims(page.slug, [disputed])

    assert Claim.from_dict(store.claims_for_page(page.slug)[0]) == claim
    assert len(store.claim_revisions()) == 1


def test_claim_revision_recorded_at_cannot_move_backwards(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-15T00:00:00Z",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        metadata={"claims": [claim.to_dict()]},
        revision_recorded_at=claim.observed_at,
        revision_operation_id="create-claim",
        revision_reason="claim creation",
    )
    disputed = Claim.from_dict({**claim.to_dict(), "status": "disputed"})

    with pytest.raises(ValueError, match="cannot precede"):
        store.write_page_claims(
            page.slug,
            [disputed],
            revision_recorded_at="2026-07-14T00:00:00Z",
            revision_operation_id="backdated-update",
            revision_reason="invalid backdated update",
        )

    assert Claim.from_dict(store.claims_for_page(page.slug)[0]) == claim
    assert len(store.claim_revisions()) == 1


def test_claim_revision_ledger_repairs_only_truncated_tail(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
        observed_at="2026-07-14T00:00:00Z",
    )
    store.stage_ingest_operation(
        source_id="source-a",
        source_hash=hashlib.sha256(b"source-a").hexdigest(),
        observed_at=claim.observed_at,
        target_slug="api-policy",
        affected_keys=[claim.key],
        incoming_claim_ids=[claim.claim_id],
        prior_claims=[],
    )
    store.upsert_wiki_page(
        "API Policy",
        "Policy facts.",
        slug="api-policy",
        metadata={"claims": [claim.to_dict()]},
    )
    store.complete_ingest_operation()
    with store.claim_revisions_path.open("ab") as handle:
        handle.write(b'{"truncated":')

    assert store.repair_partial_claim_revision_tail() is True
    assert len(store.claim_revisions()) == 1
    assert store.claim_revisions_path.read_bytes().endswith(b"\n")


def test_equivalent_utc_timestamps_produce_one_claim_id() -> None:
    common = ("fact", "production", "api", "quota", "100")

    assert make_claim_id(*common, "2026-08-01T00:00:00Z") == make_claim_id(
        *common, "2026-08-01T00:00:00+00:00"
    )


def test_decision_ledger_repairs_only_a_truncated_final_record(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    store.append_decision_event({"event_id": "complete", "type": "receipt"})
    with store.decisions_path.open("a", encoding="utf-8") as handle:
        handle.write('{"event_id":"partial"')

    with pytest.raises(ValueError, match="decision ledger is corrupt"):
        store.decision_events()
    assert store.repair_partial_decision_tail() is True
    assert store.decision_events() == [{"event_id": "complete", "type": "receipt"}]


def test_decision_ledger_rejects_completed_corrupt_record(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    store.decisions_path.write_text('{"event_id":"broken"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="decision ledger is corrupt"):
        store.repair_partial_decision_tail()


def test_decision_ledger_repairs_a_valid_record_missing_final_newline(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    store.decisions_path.write_text('{"event_id":"first"}', encoding="utf-8")

    assert store.repair_partial_decision_tail() is True
    store.append_decision_event({"event_id": "second"})
    assert [event["event_id"] for event in store.decision_events()] == [
        "first",
        "second",
    ]


def test_decision_ledger_repairs_a_truncated_utf8_tail(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    store.append_decision_event({"event_id": "complete"})
    with store.decisions_path.open("ab") as handle:
        handle.write(b'{"event_id":"partial","note":"\xe2\x82')

    with pytest.raises(ValueError, match="valid UTF-8"):
        store.decision_events()
    assert store.repair_partial_decision_tail() is True
    assert store.decision_events() == [{"event_id": "complete"}]


def test_reserved_projection_title_is_namespaced(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The quota is 100.",
    )

    page = store.upsert_wiki_page(
        "index",
        "User-authored index memory.",
        metadata={"summary": "quota", "claims": [claim.to_dict()]},
    )

    assert page.slug == "page-index"
    assert page.path.name == "page-index.md"
    assert store.index_path.name == "index.md"
    assert "page-index" in store.index_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="reserved wiki page slug"):
        store.upsert_wiki_page("unsafe", "body", slug="index")


def _transition(
    *,
    claim: Claim,
    page_slug: str,
    from_status: ClaimStatus,
    to_status: ClaimStatus,
    event_id: str,
    rationale: str,
) -> TransitionEvent:
    return TransitionEvent(
        schema_version="librarian-memory/v2",
        event_id=event_id,
        timestamp="2026-07-14T01:00:00Z",
        page_slug=page_slug,
        claim_id=claim.claim_id,
        from_status=from_status,
        to_status=to_status,
        trigger_claim_id=None,
        rule="manual_audit_correction",
        relation=None,
        model=None,
        prompt_version="v2",
        evidence_source_ids=claim.source_ids,
        evidence_spans=tuple(item.span for item in claim.evidence),
        rationale=rationale,
    )

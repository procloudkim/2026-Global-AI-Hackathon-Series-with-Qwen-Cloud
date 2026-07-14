from __future__ import annotations

from datetime import datetime
from pathlib import Path

from librarian.claims import Claim, ClaimStatus
from librarian.forget import run_lint
from librarian.store import MemoryStore
from tests.support import NoCallRouter, ScriptedRouter, canonical_claim


def test_lint_repairs_projection_once_and_is_idempotent(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The API quota is 100.",
    )
    page = store.upsert_wiki_page(
        "API Policy",
        "Policy memory.",
        metadata={"summary": "API quota", "claims": [claim.to_dict()]},
    )
    store.graph_path.write_text('{"corrupt": true}', encoding="utf-8")

    first = run_lint(store=store, router=NoCallRouter(), apply_archive=True)
    first_events = store.decision_events()
    second = run_lint(store=store, router=NoCallRouter(), apply_archive=True)

    assert page.path.exists()
    assert first.repaired_projections is True
    assert any(f.finding_type == "projection_drift" and f.repaired for f in first.findings)
    assert any(f.finding_type == "audit_gap" and f.repaired for f in first.findings)
    assert store.projection_is_consistent()
    assert second.repaired_projections is False
    assert second.findings == []
    assert store.decision_events() == first_events


def test_unresolved_lint_never_archives_whole_pages(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    claim_a = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The API quota is 100.",
        status=ClaimStatus.DISPUTED,
    )
    claim_b = canonical_claim(
        value="200",
        source_id="source-b",
        evidence_span="The API quota is 200.",
        status=ClaimStatus.DISPUTED,
    )
    page_a = store.upsert_wiki_page(
        "API Policy A",
        "First disputed memory.",
        metadata={"summary": "API quota", "claims": [claim_a.to_dict()]},
    )
    page_b = store.upsert_wiki_page(
        "API Policy B",
        "Second disputed memory.",
        metadata={"summary": "API quota", "claims": [claim_b.to_dict()]},
    )
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "The supplied evidence does not establish a winner.",
    }
    router = ScriptedRouter(unresolved, unresolved)

    first = run_lint(store=store, router=router, apply_archive=True)
    events_after_first = store.decision_events()
    second = run_lint(store=store, router=router, apply_archive=True)

    assert first.archived_pages == second.archived_pages == []
    assert first.archived_claims == second.archived_claims == []
    assert first.transitioned_claims == second.transitioned_claims == []
    assert page_a.path.exists() and page_b.path.exists()
    assert not (store.archive_dir / f"{page_a.slug}.md").exists()
    assert not (store.archive_dir / f"{page_b.slug}.md").exists()
    assert store.decision_events() == events_after_first
    statuses = {
        Claim.from_dict(item).status
        for slug in (page_a.slug, page_b.slug)
        for item in store.claims_for_page(slug)
    }
    assert statuses == {ClaimStatus.DISPUTED}


def test_lint_does_not_fabricate_active_history_for_non_active_claim(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    superseded = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The old quota was 100.",
        status=ClaimStatus.SUPERSEDED,
    )
    store.upsert_wiki_page(
        "API Policy",
        "Historical policy memory.",
        metadata={"summary": "Old API quota", "claims": [superseded.to_dict()]},
    )

    result = run_lint(store=store, router=NoCallRouter(), apply_archive=True)

    assert store.decision_events() == []
    finding = next(item for item in result.findings if item.finding_type == "audit_gap")
    assert finding.claim_id == superseded.claim_id
    assert finding.repaired is False
    assert "report-only" in finding.message


def test_lint_makes_partial_active_disputed_conflict_fail_closed(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    active = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The API quota is 100.",
    )
    disputed = canonical_claim(
        value="200",
        source_id="source-b",
        evidence_span="The API quota may be 200.",
        status=ClaimStatus.DISPUTED,
    )
    store.upsert_wiki_page(
        "API Policy",
        "Partially transitioned conflict.",
        metadata={
            "summary": "API quota conflict",
            "claims": [active.to_dict(), disputed.to_dict()],
        },
    )
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "No source-grounded winner.",
    }

    result = run_lint(
        store=store,
        router=ScriptedRouter(unresolved),
        apply_archive=True,
    )

    statuses = {
        Claim.from_dict(raw).value: Claim.from_dict(raw).status
        for raw in store.claims_for_page("api-policy")
    }
    assert statuses == {
        "100": ClaimStatus.DISPUTED,
        "200": ClaimStatus.DISPUTED,
    }
    assert active.claim_id in result.transitioned_claims
    assert any(
        event.get("rule") == "lint_fail_closed_partial_conflict"
        for event in store.decision_events()
    )


def test_lint_transition_uses_repair_time_not_source_observation(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    observed = "2020-01-01T00:00:00Z"
    active = canonical_claim(
        value="100",
        source_id="source-a",
        evidence_span="The API quota is 100.",
        observed_at=observed,
    )
    disputed = canonical_claim(
        value="200",
        source_id="source-b",
        evidence_span="The API quota may be 200.",
        status=ClaimStatus.DISPUTED,
        observed_at=observed,
    )
    store.upsert_wiki_page(
        "API Policy",
        "Partially transitioned conflict.",
        metadata={"summary": "quota conflict", "claims": [active.to_dict(), disputed.to_dict()]},
    )
    unresolved = {
        "relation": "unresolved",
        "winner_claim_id": None,
        "evidence_source_ids": [],
        "evidence_spans": [],
        "rationale": "No source-grounded winner.",
    }

    run_lint(
        store=store,
        router=ScriptedRouter(unresolved),
        apply_archive=True,
    )

    event = next(
        item
        for item in store.decision_events()
        if item.get("rule") == "lint_fail_closed_partial_conflict"
    )
    assert event["timestamp"] != observed
    assert datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")) > datetime.fromisoformat(
        observed.replace("Z", "+00:00")
    )

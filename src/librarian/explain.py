"""Read-only, time-aware explanations for one canonical memory key."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .claims import Claim, canonical_timestamp, claim_key
from .query import _build_claim_view, _resolve_temporal_cutoffs
from .store import MemoryStore, WikiPage


class InvalidMemoryExplainRequest(ValueError):
    """The caller supplied an invalid key or temporal cutoff combination."""


def explain_memory(
    *,
    store: MemoryStore,
    key: str,
    as_of: str | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
) -> dict[str, Any]:
    """Explain one key from the same bitemporal projection used by queries.

    The operation never invokes a model or repairs state. If a crash-recovery
    boundary is present, it returns an explicit incomplete result instead of
    interpreting a potentially partial canonical projection.
    """
    canonical_key = _canonical_key(key)
    try:
        valid_time, knowledge_time = _resolve_temporal_cutoffs(
            as_of=as_of,
            valid_at=valid_at,
            known_at=known_at,
        )
    except ValueError as exc:
        raise InvalidMemoryExplainRequest(str(exc)) from exc

    temporal_view = {
        "valid_at": valid_time.isoformat(),
        "known_at": knowledge_time.isoformat(),
        "explicit": any(item is not None for item in (as_of, valid_at, known_at)),
    }

    with store.transaction():
        recovery_required = _recovery_boundaries(store)
        if recovery_required:
            return _incomplete_response(
                canonical_key=canonical_key,
                temporal_view=temporal_view,
                recovery_required=recovery_required,
            )

        pages = store.list_wiki_pages()
        revisions = store.claim_revisions()
        decisions = store.decision_events()
        revision_view = store.claim_revision_view(
            known_at=knowledge_time.isoformat(),
            page_slugs={page.slug for page in pages},
        )
        active, disputed, _trace, _filtered = _build_claim_view(
            pages,
            valid_time,
            known_at=knowledge_time,
            transition_events=decisions,
            revision_snapshots=revision_view.snapshots,
            revision_claim_ids=revision_view.tracked_claim_ids,
            incomplete_revision_claim_ids=revision_view.incomplete_claim_ids,
        )
        stored_claims = _stored_claims(pages, canonical_key)
        canonical_claims = _knowledge_claims(
            pages,
            canonical_key,
            revision_snapshots=revision_view.snapshots,
            tracked_claim_ids=set(revision_view.tracked_claim_ids),
            explicit=bool(temporal_view["explicit"]),
        )

    active_claims = _projected_claims(active, canonical_key)
    disputed_claims = _projected_claims(disputed, canonical_key)
    current_claims = _sort_claims([*active_claims, *disputed_claims])
    history = _history(
        revisions,
        canonical_key,
        known_at=knowledge_time,
    )
    related_claim_ids = {
        str(item["claim_id"])
        for item in [*canonical_claims, *history]
        if item.get("claim_id")
    }
    related_decisions = _related_decisions(
        decisions,
        related_claim_ids,
        known_at=knowledge_time,
    )

    strict_bitemporal = bool(temporal_view["explicit"])
    stored_claim_ids = {str(item["claim_id"]) for item in stored_claims}
    untracked = stored_claim_ids - set(revision_view.tracked_claim_ids)
    incomplete = stored_claim_ids & set(revision_view.incomplete_claim_ids)
    active_values = {str(item["normalized_value"]) for item in active_claims}

    if strict_bitemporal and (untracked or incomplete):
        resolution_status = "history_incomplete"
    elif not canonical_claims and not history:
        resolution_status = "not_found"
    elif disputed_claims:
        resolution_status = "unresolved"
    elif active_claims and len(active_values) == 1:
        resolution_status = "resolved"
    elif not active_claims:
        resolution_status = "inactive"
    else:
        resolution_status = "inconsistent"

    return {
        "status": "ok",
        "key": canonical_key,
        "resolution_status": resolution_status,
        "winner_claim_ids": [item["claim_id"] for item in active_claims]
        if resolution_status == "resolved"
        else [],
        "current_claims": current_claims,
        "canonical_claims": canonical_claims,
        "history": history,
        "decisions": related_decisions,
        "temporal_view": {
            **temporal_view,
            "history_complete": not (untracked or incomplete),
        },
        "integrity": {"status": "ok", "recovery_required": []},
        "proof_boundary": _proof_boundary(),
    }


def _canonical_key(raw_key: str) -> str:
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise InvalidMemoryExplainRequest(
            "key must be a non-empty scope::subject::predicate string"
        )
    parts = [part.strip() for part in raw_key.split("::")]
    if len(parts) != 3 or any(not part for part in parts):
        raise InvalidMemoryExplainRequest("key must use scope::subject::predicate")
    return claim_key(parts[0], parts[1], parts[2])


def _stored_claims(
    pages: list[WikiPage],
    canonical_key: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        raw_claims = page.metadata.get("claims", [])
        if not isinstance(raw_claims, list):
            continue
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            claim = Claim.from_dict(raw_claim)
            if claim.key == canonical_key:
                rows.append(_claim_view(claim, page_slug=page.slug))
    return _sort_claims(rows)


def _knowledge_claims(
    pages: list[WikiPage],
    canonical_key: str,
    *,
    revision_snapshots: dict[str, dict[str, Any]],
    tracked_claim_ids: set[str],
    explicit: bool,
) -> list[dict[str, Any]]:
    """Return only claim snapshots visible at the selected knowledge time."""
    rows: list[dict[str, Any]] = []
    for page in pages:
        raw_claims = page.metadata.get("claims", [])
        if not isinstance(raw_claims, list):
            continue
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            claim_id = str(raw_claim.get("claim_id", ""))
            if claim_id in tracked_claim_ids:
                snapshot = revision_snapshots.get(claim_id)
                if snapshot is None:
                    continue
                claim = Claim.from_dict(snapshot)
            elif explicit:
                # An untracked historical claim cannot be safely rewound.
                continue
            else:
                claim = Claim.from_dict(raw_claim)
            if claim.key == canonical_key:
                rows.append(_claim_view(claim, page_slug=page.slug))
    return _sort_claims(rows)


def _projected_claims(
    items: Iterable[Any],
    canonical_key: str,
) -> list[dict[str, Any]]:
    return [
        _claim_view(item.claim, page_slug=item.page_slug)
        for item in items
        if item.claim.key == canonical_key
    ]


def _sort_claims(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status_order = {"active": 0, "disputed": 1, "superseded": 2, "archived": 3}
    return sorted(
        rows,
        key=lambda item: (
            status_order.get(str(item["status"]), 9),
            str(item["effective_at"] or item["observed_at"]),
            str(item["claim_id"]),
        ),
    )


def _history(
    revisions: list[dict[str, Any]],
    canonical_key: str,
    *,
    known_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for revision in revisions:
        if _timestamp(str(revision["recorded_at"]), "recorded_at") > known_at:
            continue
        snapshot = revision.get("claim")
        if not isinstance(snapshot, dict):
            continue
        claim = Claim.from_dict(snapshot)
        if claim.key != canonical_key:
            continue
        rows.append(
            {
                "revision_id": str(revision["revision_id"]),
                "ordinal": int(revision["ordinal"]),
                "recorded_at": str(revision["recorded_at"]),
                "change_kind": str(revision["change_kind"]),
                "reason": str(revision["reason"]),
                **_claim_view(claim, page_slug=str(revision["page_slug"])),
            }
        )
    return sorted(rows, key=lambda item: int(item["ordinal"]))


def _related_decisions(
    events: list[dict[str, Any]],
    claim_ids: set[str],
    *,
    known_at: datetime,
) -> list[dict[str, Any]]:
    """Return only events whose claim and optional trigger stay inside the key."""
    selected: list[dict[str, Any]] = []
    for event in events:
        if _timestamp(str(event.get("timestamp", "")), "decision timestamp") > known_at:
            continue
        claim_id = str(event.get("claim_id", ""))
        trigger_id = str(event.get("trigger_claim_id") or "")
        if claim_id not in claim_ids:
            continue
        if trigger_id and trigger_id not in claim_ids:
            continue
        selected.append(_decision_view(event))
    return sorted(
        selected,
        key=lambda item: (str(item["timestamp"]), str(item["event_id"])),
    )


def _timestamp(value: str, label: str) -> datetime:
    normalized = canonical_timestamp(value, label)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _recovery_boundaries(store: MemoryStore) -> list[str]:
    paths = (
        ("pending_ingest", store.pending_ingest_path),
        ("pending_transition", store.pending_transition_path),
        ("pending_claim_revisions", store.pending_claim_revisions_path),
        ("projection_dirty", store.projection_dirty_path),
    )
    boundaries = [label for label, path in paths if path.exists()]
    for label, path in (
        ("decision_ledger_tail", store.decisions_path),
        ("claim_revision_ledger_tail", store.claim_revisions_path),
    ):
        if _has_partial_tail(path):
            boundaries.append(label)
    return boundaries


def _has_partial_tail(path: Path) -> bool:
    if not path.exists():
        return False
    raw = path.read_bytes()
    return bool(raw) and not raw.endswith(b"\n")


def _incomplete_response(
    *,
    canonical_key: str,
    temporal_view: dict[str, Any],
    recovery_required: list[str],
) -> dict[str, Any]:
    return {
        "status": "incomplete",
        "key": canonical_key,
        "resolution_status": "recovery_required",
        "winner_claim_ids": [],
        "current_claims": [],
        "canonical_claims": [],
        "history": [],
        "decisions": [],
        "temporal_view": {**temporal_view, "history_complete": False},
        "integrity": {
            "status": "recovery_required",
            "recovery_required": recovery_required,
        },
        "proof_boundary": _proof_boundary(),
    }


def _proof_boundary() -> dict[str, Any]:
    return {
        "read_only": True,
        "memory_mutations": 0,
        "provider_calls": 0,
        "interpretation": "bitemporal ledger projection, not model judgment",
    }


def _claim_view(claim: Claim, *, page_slug: str) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "page_slug": page_slug,
        "key": claim.key,
        "value": claim.value,
        "normalized_value": claim.normalized_value,
        "status": claim.status.value,
        "observed_at": claim.observed_at,
        "effective_at": claim.effective_at,
        "source_ids": list(claim.source_ids),
        "supersedes": list(claim.supersedes),
    }


def _decision_view(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(event.get("event_id", "")),
        "event_type": str(event.get("event_type", "transition")),
        "timestamp": str(event.get("timestamp", "")),
        "claim_id": str(event.get("claim_id", "")),
        "trigger_claim_id": event.get("trigger_claim_id"),
        "from_status": event.get("from_status"),
        "to_status": event.get("to_status"),
        "rule": str(event.get("rule", "")),
        "relation": event.get("relation"),
        "evidence_source_ids": list(event.get("evidence_source_ids", [])),
        "rationale": str(event.get("rationale", "")),
    }

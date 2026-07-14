"""Persistent memory store for Librarian.

Implements the Track 1 storage layer with:
- raw/ immutable sources
- wiki/ managed markdown pages (+ index.md, log.md, graph.json)
- archive/ forgotten pages
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import re
from typing import Any
import unicodedata
from uuid import uuid4
from threading import Lock, RLock, local

import yaml

from .claims import (
    Claim,
    ClaimStatus,
    Relation,
    TransitionEvent,
    canonical_timestamp,
    claim_key,
)

_FRONTMATTER_DELIM = "---"
_MEMORY_SCHEMA_VERSION = "librarian-memory/v2"
_INGEST_OPERATION_SCHEMA_VERSION = "librarian-ingest-operation/v1"
_RESERVED_PAGE_SLUGS = frozenset({"graph", "index", "log"})
_LOCK_REGISTRY_GUARD = Lock()
_LOCK_REGISTRY: dict[str, RLock] = {}
_LOCK_DEPTH = local()


@dataclass(frozen=True)
class WikiPage:
    slug: str
    title: str
    body: str
    metadata: dict[str, Any]
    path: Path


class MemoryStore:
    def __init__(self, base_path: str | Path = "memory") -> None:
        self.base = Path(base_path)
        self.raw_dir = self.base / "raw"
        self.wiki_dir = self.base / "wiki"
        self.archive_dir = self.base / "archive"
        self.claim_archive_dir = self.archive_dir / "claims"
        self.index_path = self.wiki_dir / "index.md"
        self.log_path = self.wiki_dir / "log.md"
        self.graph_path = self.wiki_dir / "graph.json"
        self.decisions_path = self.base / "decisions.jsonl"
        self.pending_transition_path = self.base / ".pending-transition.json"
        self.pending_ingest_path = self.base / ".pending-ingest.json"
        self.process_lock_path = self.base / ".memory.lock"
        self.projection_dirty_path = self.base / ".projection-dirty"
        lock_key = str(self.base.resolve())
        with _LOCK_REGISTRY_GUARD:
            self._transaction_lock = _LOCK_REGISTRY.setdefault(lock_key, RLock())
        self.ensure_layout()

    @contextmanager
    def transaction(self):
        """Serialize one read/modify/write pipeline per memory root and process set."""
        with self._transaction_lock:
            depths = getattr(_LOCK_DEPTH, "by_root", {})
            root_key = str(self.base.resolve())
            depth = int(depths.get(root_key, 0))
            lock_handle = None
            process_lock_acquired = False
            try:
                if depth == 0:
                    self.process_lock_path.parent.mkdir(parents=True, exist_ok=True)
                    lock_handle = self.process_lock_path.open("a+b")
                    lock_handle.seek(0, os.SEEK_END)
                    if lock_handle.tell() == 0:
                        lock_handle.write(b"0")
                        lock_handle.flush()
                        os.fsync(lock_handle.fileno())
                    self._acquire_process_lock(lock_handle)
                    process_lock_acquired = True
                depths[root_key] = depth + 1
                _LOCK_DEPTH.by_root = depths
                try:
                    yield
                finally:
                    depths[root_key] -= 1
                    if depths[root_key] == 0:
                        depths.pop(root_key, None)
            finally:
                if lock_handle is not None:
                    try:
                        if process_lock_acquired:
                            self._release_process_lock(lock_handle)
                    finally:
                        lock_handle.close()

    @staticmethod
    def _acquire_process_lock(handle) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _release_process_lock(handle) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def ensure_layout(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._atomic_write_text(
                self.index_path,
                "# Memory Index\n\n| slug | title | updated_at | summary |\n"
                "|---|---|---|---|\n",
            )
        if not self.log_path.exists():
            self._atomic_write_text(self.log_path, "# Memory Log\n\n")
        if not self.graph_path.exists():
            self._atomic_write_text(
                self.graph_path,
                json.dumps(self._empty_graph(), ensure_ascii=False),
            )

    def save_raw_source(self, source_id: str, content: str) -> Path:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        path = self.raw_dir / f"{self._slugify(source_id)}--{content_hash}.md"
        if not path.exists():
            self._atomic_write_text(path, content)
        return path

    def slug_for(self, text: str) -> str:
        slug = self._slugify(text)
        return f"page-{slug}" if slug in _RESERVED_PAGE_SLUGS else slug

    def upsert_wiki_page(
        self,
        title: str,
        body: str,
        *,
        slug: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WikiPage:
        if slug is None:
            page_slug = self.slug_for(title)
        else:
            page_slug = self._slugify(slug)
            if page_slug in _RESERVED_PAGE_SLUGS:
                raise ValueError(f"reserved wiki page slug: {page_slug}")
        page_path = self.wiki_dir / f"{page_slug}.md"
        now = datetime.now(UTC).isoformat()

        incoming_metadata = dict(metadata or {})
        if "claims" in incoming_metadata:
            raw_claims = incoming_metadata["claims"]
            if not isinstance(raw_claims, list):
                raise ValueError("claims metadata must be an array")
            incoming_metadata["claims"] = [
                (
                    claim.to_dict()
                    if isinstance(claim, Claim)
                    else Claim.from_dict(claim).to_dict()
                )
                for claim in raw_claims
            ]

        existing_meta: dict[str, Any] = {}
        if page_path.exists():
            existing = self.read_wiki_page(page_slug)
            existing_meta = existing.metadata

        merged = {
            **existing_meta,
            **incoming_metadata,
            "title": title,
            "slug": page_slug,
            "updated_at": now,
        }
        text = self._serialize_page(merged, body)
        self._atomic_write_text(
            self.projection_dirty_path,
            json.dumps({"page_slug": page_slug, "operation": "upsert"}),
        )
        self._atomic_write_text(page_path, text)

        page = WikiPage(
            slug=page_slug, title=title, body=body, metadata=merged, path=page_path
        )
        self.refresh_index()
        self.refresh_graph()
        self.projection_dirty_path.unlink(missing_ok=True)
        return page

    def read_wiki_page(self, slug: str) -> WikiPage:
        page_path = self.wiki_dir / f"{slug}.md"
        if not page_path.exists():
            raise FileNotFoundError(f"wiki page not found: {slug}")
        raw = page_path.read_text(encoding="utf-8")
        metadata, body = self._parse_page(raw)
        title = str(metadata.get("title", slug))
        return WikiPage(slug=slug, title=title, body=body, metadata=metadata, path=page_path)

    def archive_page(self, slug: str, reason: str) -> Path:
        del slug, reason
        raise RuntimeError(
            "whole-page archival is disabled; transition the affected claim instead"
        )

    def list_wiki_pages(self) -> list[WikiPage]:
        pages: list[WikiPage] = []
        for path in sorted(self.wiki_dir.glob("*.md")):
            if path.name in {"index.md", "log.md"}:
                continue
            pages.append(self.read_wiki_page(path.stem))
        return pages

    def append_log(self, event_type: str, detail: str) -> None:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"- [{now}] {event_type}: {detail}\n"
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)

    def append_decision_event(self, event: dict[str, Any] | Any) -> None:
        """Append one lifecycle decision without making it a second source of truth."""
        if hasattr(event, "to_dict"):
            payload = event.to_dict()
        elif isinstance(event, dict):
            payload = event
        else:
            raise TypeError("decision event must be a mapping or expose to_dict()")
        event_id = str(payload.get("event_id", "")).strip()
        if event_id and any(
            existing.get("event_id") == event_id for existing in self.decision_events()
        ):
            return
        if self.decisions_path.exists() and self.decisions_path.stat().st_size:
            with self.decisions_path.open("rb") as existing_file:
                existing_file.seek(-1, 2)
                if existing_file.read(1) != b"\n":
                    raise ValueError(
                        "decision ledger is missing its final newline; run explicit repair"
                    )
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.decisions_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def decision_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if not self.decisions_path.exists():
            return events
        try:
            raw = self.decisions_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("decision ledger is not valid UTF-8") from exc
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"decision ledger is corrupt at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(item, dict):
                raise ValueError(
                    f"decision ledger line {line_number} must be a JSON object"
                )
            events.append(item)
        return events

    def repair_partial_decision_tail(self) -> bool:
        """Drop only a crash-truncated final JSONL fragment.

        Corruption in a completed line or in the middle of the ledger is never
        guessed away.  Callers must stop and require an external audit.
        """
        if not self.decisions_path.exists():
            return False
        raw = self.decisions_path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            # A malformed newline-terminated record is not a partial append.
            self.decision_events()
            return False

        boundary = raw.rfind(b"\n")
        prefix_bytes = raw[: boundary + 1] if boundary >= 0 else b""
        tail_bytes = raw[boundary + 1 :]
        try:
            prefix = prefix_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("decision ledger has non-tail UTF-8 corruption") from exc
        prefix_lines = prefix.splitlines()
        for line_number, line in enumerate(prefix_lines, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "decision ledger has non-tail corruption at line "
                    f"{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(item, dict):
                raise ValueError(
                    f"decision ledger line {line_number} must be a JSON object"
                )
        try:
            tail_text = tail_bytes.decode("utf-8")
            tail = json.loads(tail_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._atomic_write_bytes(self.decisions_path, prefix_bytes)
            return True
        if not isinstance(tail, dict):
            raise ValueError("decision ledger final record must be a JSON object")
        self._atomic_write_bytes(self.decisions_path, raw + b"\n")
        return True

    def claims_for_page(self, page: WikiPage | str) -> list[dict[str, Any]]:
        target = self.read_wiki_page(page) if isinstance(page, str) else page
        raw_claims = target.metadata.get("claims", [])
        if not isinstance(raw_claims, list):
            return []
        return [dict(item) for item in raw_claims if isinstance(item, dict)]

    def write_page_claims(
        self,
        page_slug: str,
        claims: list[Claim | dict[str, Any]],
        *,
        metadata_updates: dict[str, Any] | None = None,
    ) -> WikiPage:
        """Atomically replace one page's canonical claim array."""
        page = self.read_wiki_page(page_slug)
        validated = [
            claim if isinstance(claim, Claim) else Claim.from_dict(dict(claim))
            for claim in claims
        ]
        serialized = [claim.to_dict() for claim in validated]
        metadata = {
            **page.metadata,
            **(metadata_updates or {}),
            "schema_version": _MEMORY_SCHEMA_VERSION,
            "claims": serialized,
        }
        return self.upsert_wiki_page(
            title=page.title,
            body=page.body,
            slug=page.slug,
            metadata=metadata,
        )

    def apply_claim_transition(
        self,
        *,
        page_slug: str,
        claim_id: str,
        to_status: ClaimStatus | str,
        event: TransitionEvent | dict[str, Any],
    ) -> Claim:
        """Apply one validated claim transition and append its audit event.

        A retry after a crash is idempotent: if the page write succeeded but the
        ledger append did not, calling this method again appends only the missing
        event.
        """
        transition = (
            event if isinstance(event, TransitionEvent) else TransitionEvent.from_dict(event)
        )
        target = ClaimStatus(to_status)
        if (
            transition.page_slug != page_slug
            or transition.claim_id != claim_id
            or transition.to_status is not target
        ):
            raise ValueError("transition event does not match requested claim transition")

        recorded_events = self.decision_events()
        exact_event_recorded = any(
            str(existing.get("event_id", "")) == transition.event_id
            for existing in recorded_events
        )
        page = self.read_wiki_page(page_slug)
        raw_claims = self.claims_for_page(page)
        for index, raw_claim in enumerate(raw_claims):
            claim = Claim.from_dict(raw_claim)
            if claim.claim_id != claim_id:
                continue
            if claim.status is target:
                if exact_event_recorded:
                    self.pending_transition_path.unlink(missing_ok=True)
                    return claim
                recorded_statuses = [
                    str(existing.get("to_status", ""))
                    for existing in recorded_events
                    if existing.get("page_slug") == page_slug
                    and existing.get("claim_id") == claim_id
                    and existing.get("to_status") is not None
                ]
                last_recorded = recorded_statuses[-1] if recorded_statuses else None
                expected_previous = (
                    None
                    if transition.from_status is None
                    else transition.from_status.value
                )
                if last_recorded is not None and last_recorded != expected_previous:
                    raise ValueError(
                        "canonical claim already reached the target through a "
                        "different recorded transition"
                    )
                self._stage_pending_transition(transition)
                self.append_decision_event(transition)
                self.pending_transition_path.unlink(missing_ok=True)
                return claim
            if transition.from_status is not claim.status:
                raise ValueError(
                    "transition event from_status does not match canonical claim state"
                )
            updated = Claim.from_dict({**claim.to_dict(), "status": target.value})
            raw_claims[index] = updated.to_dict()
            self._stage_pending_transition(transition)
            self.write_page_claims(page_slug, raw_claims)
            self.append_decision_event(transition)
            if target is ClaimStatus.ARCHIVED:
                self.archive_claim_snapshot(
                    page_slug=page_slug,
                    claim=updated.to_dict(),
                    reason=transition.rationale,
                )
            self.pending_transition_path.unlink(missing_ok=True)
            return updated
        raise KeyError(f"claim not found on page {page_slug}: {claim_id}")

    def recover_pending_transition(self) -> bool:
        """Idempotently finish a page/ledger transition interrupted by a crash."""
        if not self.pending_transition_path.exists():
            return False
        try:
            raw = json.loads(
                self.pending_transition_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("pending transition receipt is corrupt") from exc
        if not isinstance(raw, dict):
            raise ValueError("pending transition receipt must be a JSON object")
        transition = TransitionEvent.from_dict(raw)
        self.apply_claim_transition(
            page_slug=transition.page_slug,
            claim_id=transition.claim_id,
            to_status=transition.to_status,
            event=transition,
        )
        self.pending_transition_path.unlink(missing_ok=True)
        return True

    def _stage_pending_transition(self, transition: TransitionEvent) -> None:
        self._atomic_write_text(
            self.pending_transition_path,
            json.dumps(
                transition.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def stage_ingest_operation(
        self,
        *,
        source_id: str,
        source_hash: str,
        observed_at: str,
        target_slug: str,
        affected_keys: list[str],
        incoming_claim_ids: list[str],
        prior_claims: list[tuple[str, Claim]],
    ) -> None:
        """Persist the affected-key boundary before canonical ingest mutation.

        The receipt is deliberately small and deterministic.  It is not a
        second memory store: it exists only until the ingest either commits or
        is conservatively recovered.
        """
        normalized_source_id = source_id.strip()
        normalized_hash = source_hash.strip().lower()
        normalized_observed_at = canonical_timestamp(observed_at, "observed_at")
        normalized_target = self._slugify(target_slug)
        keys = sorted(set(str(key).strip() for key in affected_keys if str(key).strip()))
        claim_ids = sorted(
            set(str(claim_id).strip() for claim_id in incoming_claim_ids if str(claim_id).strip())
        )
        if not normalized_source_id:
            raise ValueError("pending ingest source_id must be non-empty")
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
            raise ValueError("pending ingest source_hash must be a SHA-256 hex digest")
        if not keys or not claim_ids:
            raise ValueError("pending ingest must contain affected keys and claim ids")
        if normalized_target in _RESERVED_PAGE_SLUGS:
            raise ValueError("pending ingest target_slug is reserved")

        serialized_prior: list[dict[str, Any]] = []
        seen_prior_ids: set[str] = set()
        for page_slug, claim in sorted(prior_claims, key=lambda item: (item[0], item[1].claim_id)):
            page_slug = self._slugify(page_slug)
            if page_slug in _RESERVED_PAGE_SLUGS:
                raise ValueError("pending ingest prior page slug is reserved")
            if claim.key not in keys:
                raise ValueError("pending ingest prior claim is outside affected keys")
            if claim.claim_id in seen_prior_ids:
                raise ValueError("pending ingest prior claim ids must be unique")
            seen_prior_ids.add(claim.claim_id)
            serialized_prior.append(
                {"page_slug": page_slug, "claim": claim.to_dict()}
            )

        operation_id = self._ingest_operation_id(
            source_hash=normalized_hash,
            observed_at=normalized_observed_at,
            target_slug=normalized_target,
            affected_keys=keys,
            incoming_claim_ids=claim_ids,
        )
        receipt = {
            "schema_version": _INGEST_OPERATION_SCHEMA_VERSION,
            "operation_id": operation_id,
            "phase": "prepared",
            "source_id": normalized_source_id,
            "source_hash": normalized_hash,
            "observed_at": normalized_observed_at,
            "target_slug": normalized_target,
            "affected_keys": keys,
            "incoming_claim_ids": claim_ids,
            "prior_claims": serialized_prior,
        }
        self._atomic_write_text(
            self.pending_ingest_path,
            json.dumps(receipt, ensure_ascii=False, sort_keys=True),
        )

    def complete_ingest_operation(self) -> None:
        """Commit and clear the prepared ingest receipt idempotently."""
        if not self.pending_ingest_path.exists():
            return
        receipt = self._read_pending_ingest_operation()
        if receipt["phase"] != "committed":
            receipt = {**receipt, "phase": "committed"}
            self._atomic_write_text(
                self.pending_ingest_path,
                json.dumps(receipt, ensure_ascii=False, sort_keys=True),
            )
        self.pending_ingest_path.unlink(missing_ok=True)

    def recover_pending_ingest(self, *, prompt_version: str) -> list[str]:
        """Recover an interrupted ingest without trusting partial arbitration.

        Previously existing claim states are restored first.  Any current-time
        value conflict created by the interrupted operation is then converted
        to ``disputed``.  A future incoming replacement is itself disputed
        while the previously current claim remains usable.  This contains both
        stale leakage and false forgetting until a source retry can reconcile
        the same key normally.
        """
        if not self.pending_ingest_path.exists():
            return []
        receipt = self._read_pending_ingest_operation()
        affected_keys = list(receipt["affected_keys"])
        if receipt["phase"] == "committed":
            self.pending_ingest_path.unlink(missing_ok=True)
            return affected_keys

        source_id = str(receipt["source_id"])
        observed_at = str(receipt["observed_at"])
        recovery_timestamp = observed_at
        cutoff = self._parse_timestamp(observed_at)
        incoming_ids = set(map(str, receipt["incoming_claim_ids"]))
        prior_entries = list(receipt["prior_claims"])
        prior_by_id: dict[str, tuple[str, Claim]] = {
            str(entry["claim"]["claim_id"]): (
                str(entry["page_slug"]),
                Claim.from_dict(entry["claim"]),
            )
            for entry in prior_entries
        }
        relevant_slugs = {
            str(receipt["target_slug"]),
            *(page_slug for page_slug, _ in prior_by_id.values()),
        }
        graph = self.read_graph()
        claim_index = graph.get("claim_index", {})
        if isinstance(claim_index, dict):
            for key in affected_keys:
                slugs = claim_index.get(key, [])
                if isinstance(slugs, list):
                    relevant_slugs.update(str(slug) for slug in slugs)

        def current_records() -> dict[str, tuple[str, Claim]]:
            records: dict[str, tuple[str, Claim]] = {}
            for page_slug in sorted(relevant_slugs):
                try:
                    raw_claims = self.claims_for_page(page_slug)
                except FileNotFoundError:
                    continue
                for raw_claim in raw_claims:
                    try:
                        claim = Claim.from_dict(raw_claim)
                    except ValueError:
                        continue
                    if claim.key in affected_keys:
                        records[claim.claim_id] = (page_slug, claim)
            return records

        records = current_records()
        decision_events = self.decision_events()
        creation_ids = {
            str(event.get("claim_id", ""))
            for event in decision_events
            if event.get("from_status") is None and event.get("to_status") == "active"
        }
        for claim_id in sorted(incoming_ids):
            record = records.get(claim_id)
            if record is None or claim_id in creation_ids:
                continue
            page_slug, claim = record
            if claim.status is not ClaimStatus.ACTIVE:
                raise ValueError(
                    "interrupted ingest claim changed state before its creation receipt"
                )
            creation = self._recovery_transition(
                page_slug=page_slug,
                claim=claim,
                from_status=None,
                to_status=ClaimStatus.ACTIVE,
                timestamp=claim.observed_at,
                rule="ingest_recovery_missing_creation",
                prompt_version=prompt_version,
                evidence_claims=(claim,),
                rationale="Recovered a source-grounded creation receipt after an interrupted ingest.",
            )
            self.append_decision_event(creation)
            decision_events.append(creation.to_dict())
            creation_ids.add(claim_id)

        provenance_receipts = {
            (str(event.get("claim_id", "")), str(event.get("source_id", "")))
            for event in decision_events
            if event.get("event_type") == "provenance_merge"
        }
        for claim_id in sorted(incoming_ids.intersection(prior_by_id)):
            current_record = records.get(claim_id)
            if current_record is None:
                continue
            page_slug, current = current_record
            _, prior = prior_by_id[claim_id]
            if (
                source_id in current.source_ids
                and source_id not in prior.source_ids
                and (claim_id, source_id) not in provenance_receipts
            ):
                merge_event = {
                    "schema_version": _MEMORY_SCHEMA_VERSION,
                    "event_id": hashlib.sha256(
                        "|".join(
                            (
                                page_slug,
                                claim_id,
                                prior.status.value,
                                prior.status.value,
                                observed_at,
                                "duplicate_provenance_merge",
                                source_id,
                            )
                        ).encode("utf-8")
                    ).hexdigest()[:24],
                    "timestamp": observed_at,
                    "event_type": "provenance_merge",
                    "page_slug": page_slug,
                    "claim_id": claim_id,
                    "source_id": source_id,
                    "rule": "exact_key_value_effective_time",
                }
                self.append_decision_event(merge_event)
                decision_events.append(merge_event)

        # Restore lifecycle state and timeline edges that existed before the
        # interrupted operation.  Provenance additions remain valid evidence.
        for claim_id, (page_slug, prior) in sorted(prior_by_id.items()):
            current_record = current_records().get(claim_id)
            if current_record is None:
                raise ValueError(
                    f"pending ingest prior claim disappeared: {claim_id}"
                )
            actual_slug, current = current_record
            if actual_slug != page_slug:
                raise ValueError(f"pending ingest prior claim moved pages: {claim_id}")
            if current.status is ClaimStatus.ARCHIVED:
                raise ValueError("interrupted ingest cannot recover an archived prior claim")
            current = self._restore_recovery_status(
                page_slug=page_slug,
                current=current,
                target=prior.status,
                timestamp=recovery_timestamp,
                prompt_version=prompt_version,
            )
            if current.supersedes != prior.supersedes:
                self._replace_claim_metadata(
                    page_slug,
                    Claim.from_dict(
                        {**current.to_dict(), "supersedes": list(prior.supersedes)}
                    ),
                )

        records = current_records()
        incoming_records = [
            records[claim_id] for claim_id in sorted(incoming_ids) if claim_id in records
        ]
        for key in affected_keys:
            key_records = [
                record
                for record in records.values()
                if record[1].key == key
                and record[1].status in {ClaimStatus.ACTIVE, ClaimStatus.DISPUTED}
            ]
            current_records_for_key = [
                record
                for record in key_records
                if record[1].effective_at is None
                or self._parse_timestamp(record[1].effective_at) <= cutoff
            ]
            incoming_for_key = [
                record for record in incoming_records if record[1].key == key
            ]
            incoming_current = [
                record
                for record in incoming_for_key
                if record[1].effective_at is None
                or self._parse_timestamp(record[1].effective_at) <= cutoff
            ]
            current_values = {
                claim.normalized_value for _, claim in current_records_for_key
            }
            targets: list[tuple[str, Claim]] = []
            if incoming_current and len(current_values) > 1:
                targets.extend(current_records_for_key)
            for record in incoming_for_key:
                claim = record[1]
                is_future = bool(
                    claim.effective_at
                    and self._parse_timestamp(claim.effective_at) > cutoff
                )
                has_prior_other_value = any(
                    prior_claim.key == key
                    and prior_claim.normalized_value != claim.normalized_value
                    for _, prior_claim in prior_by_id.values()
                )
                if is_future and has_prior_other_value:
                    targets.append(record)

            evidence_claims = tuple(claim for _, claim in key_records)
            for page_slug, claim in dict(
                (candidate.claim_id, (slug, candidate)) for slug, candidate in targets
            ).values():
                if claim.status is not ClaimStatus.ACTIVE:
                    continue
                transition = self._recovery_transition(
                    page_slug=page_slug,
                    claim=claim,
                    from_status=ClaimStatus.ACTIVE,
                    to_status=ClaimStatus.DISPUTED,
                    timestamp=recovery_timestamp,
                    rule="ingest_recovery_fail_closed",
                    prompt_version=prompt_version,
                    evidence_claims=evidence_claims or (claim,),
                    rationale=(
                        "Interrupted affected-key reconciliation left no atomic winner; "
                        "the unresolved value is held as disputed."
                    ),
                )
                self.apply_claim_transition(
                    page_slug=page_slug,
                    claim_id=claim.claim_id,
                    to_status=ClaimStatus.DISPUTED,
                    event=transition,
                )

        self.pending_ingest_path.unlink(missing_ok=True)
        return affected_keys

    def _read_pending_ingest_operation(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.pending_ingest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("pending ingest receipt is corrupt") from exc
        expected = {
            "schema_version",
            "operation_id",
            "phase",
            "source_id",
            "source_hash",
            "observed_at",
            "target_slug",
            "affected_keys",
            "incoming_claim_ids",
            "prior_claims",
        }
        if not isinstance(parsed, dict) or set(parsed) != expected:
            raise ValueError("pending ingest receipt fields do not match the contract")
        if parsed.get("schema_version") != _INGEST_OPERATION_SCHEMA_VERSION:
            raise ValueError("pending ingest receipt schema version is unsupported")
        if parsed.get("phase") not in {"prepared", "committed"}:
            raise ValueError("pending ingest receipt phase is invalid")
        for field in ("operation_id", "source_id", "source_hash", "observed_at", "target_slug"):
            if not isinstance(parsed.get(field), str) or not str(parsed[field]).strip():
                raise ValueError(f"pending ingest {field} must be a non-empty string")
        source_hash = str(parsed["source_hash"])
        if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise ValueError("pending ingest source_hash must be a SHA-256 hex digest")
        parsed["observed_at"] = canonical_timestamp(
            str(parsed["observed_at"]), "observed_at"
        )
        for field in ("affected_keys", "incoming_claim_ids"):
            values = parsed.get(field)
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)
                or values != sorted(set(values))
            ):
                raise ValueError(f"pending ingest {field} must be a sorted unique string array")
        prior_claims = parsed.get("prior_claims")
        if not isinstance(prior_claims, list):
            raise ValueError("pending ingest prior_claims must be an array")
        seen: set[str] = set()
        for entry in prior_claims:
            if not isinstance(entry, dict) or set(entry) != {"page_slug", "claim"}:
                raise ValueError("pending ingest prior claim entry is invalid")
            if not isinstance(entry.get("page_slug"), str) or not entry["page_slug"]:
                raise ValueError("pending ingest prior page_slug is invalid")
            claim = Claim.from_dict(entry.get("claim"))
            if claim.key not in parsed["affected_keys"] or claim.claim_id in seen:
                raise ValueError("pending ingest prior claim boundary is invalid")
            seen.add(claim.claim_id)
        expected_id = self._ingest_operation_id(
            source_hash=source_hash,
            observed_at=str(parsed["observed_at"]),
            target_slug=str(parsed["target_slug"]),
            affected_keys=list(parsed["affected_keys"]),
            incoming_claim_ids=list(parsed["incoming_claim_ids"]),
        )
        if parsed["operation_id"] != expected_id:
            raise ValueError("pending ingest operation_id does not match its boundary")
        return parsed

    @staticmethod
    def _ingest_operation_id(
        *,
        source_hash: str,
        observed_at: str,
        target_slug: str,
        affected_keys: list[str],
        incoming_claim_ids: list[str],
    ) -> str:
        encoded = "|".join(
            (
                source_hash,
                observed_at,
                target_slug,
                ",".join(affected_keys),
                ",".join(incoming_claim_ids),
            )
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def _restore_recovery_status(
        self,
        *,
        page_slug: str,
        current: Claim,
        target: ClaimStatus,
        timestamp: str,
        prompt_version: str,
    ) -> Claim:
        if current.status is target:
            return current
        if current.status is ClaimStatus.SUPERSEDED and target is ClaimStatus.DISPUTED:
            current = self._apply_recovery_status_step(
                page_slug=page_slug,
                current=current,
                target=ClaimStatus.ACTIVE,
                timestamp=timestamp,
                prompt_version=prompt_version,
                rule="ingest_recovery_reactivate_prior",
            )
        return self._apply_recovery_status_step(
            page_slug=page_slug,
            current=current,
            target=target,
            timestamp=timestamp,
            prompt_version=prompt_version,
            rule="ingest_recovery_restore_prior_state",
        )

    def _apply_recovery_status_step(
        self,
        *,
        page_slug: str,
        current: Claim,
        target: ClaimStatus,
        timestamp: str,
        prompt_version: str,
        rule: str,
    ) -> Claim:
        transition = self._recovery_transition(
            page_slug=page_slug,
            claim=current,
            from_status=current.status,
            to_status=target,
            timestamp=timestamp,
            rule=rule,
            prompt_version=prompt_version,
            evidence_claims=(current,),
            rationale="Restored the last durable pre-ingest lifecycle state after interruption.",
        )
        return self.apply_claim_transition(
            page_slug=page_slug,
            claim_id=current.claim_id,
            to_status=target,
            event=transition,
        )

    def _recovery_transition(
        self,
        *,
        page_slug: str,
        claim: Claim,
        from_status: ClaimStatus | None,
        to_status: ClaimStatus,
        timestamp: str,
        rule: str,
        prompt_version: str,
        evidence_claims: tuple[Claim, ...],
        rationale: str,
    ) -> TransitionEvent:
        canonical_time = canonical_timestamp(timestamp, "timestamp")
        event_id = hashlib.sha256(
            "|".join(
                (
                    page_slug,
                    claim.claim_id,
                    "new" if from_status is None else from_status.value,
                    to_status.value,
                    canonical_time,
                    rule,
                    "",
                )
            ).encode("utf-8")
        ).hexdigest()[:24]
        return TransitionEvent(
            schema_version=_MEMORY_SCHEMA_VERSION,
            event_id=event_id,
            timestamp=canonical_time,
            page_slug=page_slug,
            claim_id=claim.claim_id,
            from_status=from_status,
            to_status=to_status,
            trigger_claim_id=None,
            rule=rule,
            relation=Relation.UNRESOLVED,
            model=None,
            prompt_version=prompt_version,
            evidence_source_ids=tuple(
                dict.fromkeys(
                    source
                    for evidence_claim in evidence_claims
                    for source in evidence_claim.source_ids
                )
            ),
            evidence_spans=tuple(
                dict.fromkeys(
                    evidence.span
                    for evidence_claim in evidence_claims
                    for evidence in evidence_claim.evidence
                )
            ),
            rationale=rationale,
        )

    def _replace_claim_metadata(self, page_slug: str, replacement: Claim) -> Claim:
        raw_claims = self.claims_for_page(page_slug)
        for index, raw_claim in enumerate(raw_claims):
            if str(raw_claim.get("claim_id", "")) != replacement.claim_id:
                continue
            raw_claims[index] = replacement.to_dict()
            self.write_page_claims(page_slug, raw_claims)
            return replacement
        raise KeyError(f"claim not found on page {page_slug}: {replacement.claim_id}")

    def legacy_unindexed_pages(self) -> list[str]:
        return [
            page.slug
            for page in self.list_wiki_pages()
            if not isinstance(page.metadata.get("claims"), list)
        ]

    def find_claim_pages(self, key: str) -> list[str]:
        graph = self.read_graph()
        index = graph.get("claim_index", {})
        if not isinstance(index, dict):
            return []
        slugs = index.get(key, [])
        if not isinstance(slugs, list):
            return []
        return [str(slug) for slug in slugs]

    def archive_claim_snapshot(
        self,
        *,
        page_slug: str,
        claim: dict[str, Any],
        reason: str,
    ) -> Path:
        claim_id = str(claim.get("claim_id", "unknown"))
        target = self.claim_archive_dir / f"{self._slugify(claim_id)}.json"
        payload = {
            "schema_version": _MEMORY_SCHEMA_VERSION,
            "page_slug": page_slug,
            "reason": reason,
            "archived_at": datetime.now(UTC).isoformat(),
            "claim": claim,
        }
        self._atomic_write_text(
            target,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        )
        return target

    def refresh_index(self) -> None:
        self._atomic_write_text(self.index_path, self._render_index(self.list_wiki_pages()))

    def _render_index(self, pages: list[WikiPage]) -> str:
        lines = [
            "# Memory Index",
            "",
            "| slug | title | updated_at | summary |",
            "|---|---|---|---|",
        ]
        for page in pages:
            summary = str(page.metadata.get("summary", "")).replace("|", " ").strip()
            updated = str(page.metadata.get("updated_at", ""))
            lines.append(
                f"| {page.slug} | {page.title} | {updated} | {summary[:120]} |"
            )
        return "\n".join(lines) + "\n"

    def refresh_graph(self) -> None:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        claim_index: dict[str, list[str]] = {}
        claim_locations: dict[str, str] = {}
        scheduled_transitions: list[dict[str, Any]] = []
        term_index: dict[str, list[str]] = {}
        pages = self.list_wiki_pages()
        slugs = {p.slug for p in pages}
        for p in pages:
            summary = str(p.metadata.get("summary", ""))
            tags_raw = p.metadata.get("tags", [])
            tags = [str(tag) for tag in tags_raw] if isinstance(tags_raw, list) else []
            active_keys: list[str] = []
            disputed_keys: list[str] = []
            superseded_keys: list[str] = []
            for claim in self.claims_for_page(p):
                key = self._claim_key(claim)
                status = str(claim.get("status", "")).strip().lower()
                claim_id = str(claim.get("claim_id", "")).strip()
                if not key:
                    continue
                if claim_id:
                    claim_locations[claim_id] = p.slug
                if status == "active":
                    active_keys.append(key)
                elif status == "disputed":
                    disputed_keys.append(key)
                elif status == "superseded":
                    superseded_keys.append(key)
                if status != "archived":
                    claim_index.setdefault(key, []).append(p.slug)
                supersedes = claim.get("supersedes", [])
                effective_at = claim.get("effective_at")
                if (
                    status in {"active", "disputed"}
                    and claim_id
                    and isinstance(effective_at, str)
                    and effective_at.strip()
                    and isinstance(supersedes, list)
                    and supersedes
                ):
                    scheduled_transitions.append(
                        {
                            "page_slug": p.slug,
                            "claim_id": claim_id,
                            "key": key,
                            "status": status,
                            "effective_at": effective_at,
                            "supersedes": [str(item) for item in supersedes],
                        }
                    )

            normalized_terms = sorted(
                self._tokenize(
                    " ".join([p.slug, p.title, summary, *tags, *active_keys, *disputed_keys])
                )
            )
            for term in normalized_terms:
                term_index.setdefault(term, []).append(p.slug)
            nodes.append(
                {
                    "id": p.slug,
                    "title": p.title,
                    "summary": summary,
                    "tags": tags,
                    "updated_at": str(p.metadata.get("updated_at", "")),
                    "active_claim_keys": sorted(set(active_keys)),
                    "disputed_claim_keys": sorted(set(disputed_keys)),
                    "superseded_claim_keys": sorted(set(superseded_keys)),
                    "normalized_terms": normalized_terms,
                    "legacy_unindexed": not isinstance(p.metadata.get("claims"), list),
                }
            )
            links = p.metadata.get("links", [])
            if isinstance(links, list):
                for target in links:
                    target_slug = self.slug_for(str(target))
                    if target_slug in slugs:
                        edges.append({"from": p.slug, "to": target_slug})
        core = {
            "schema_version": _MEMORY_SCHEMA_VERSION,
            "source_page_count": len(pages),
            "nodes": nodes,
            "edges": edges,
            "claim_index": {k: sorted(set(v)) for k, v in sorted(claim_index.items())},
            "claim_locations": dict(sorted(claim_locations.items())),
            "scheduled_transitions": sorted(
                scheduled_transitions,
                key=lambda item: (item["effective_at"], item["claim_id"]),
            ),
            "term_index": {k: sorted(set(v)) for k, v in sorted(term_index.items())},
        }
        graph_obj = {
            **core,
            "source_checksum": self._page_projection_checksum(pages),
        }
        self._atomic_write_text(
            self.graph_path,
            json.dumps(graph_obj, ensure_ascii=False, sort_keys=True),
        )

    def read_graph(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_graph()
        return parsed if isinstance(parsed, dict) else self._empty_graph()

    def projection_is_consistent(self) -> bool:
        """Return whether the derived graph matches the canonical wiki pages."""
        if self.projection_dirty_path.exists():
            return False
        graph = self.read_graph()
        pages = self.list_wiki_pages()
        try:
            index_matches = self.index_path.read_text(encoding="utf-8") == self._render_index(pages)
        except OSError:
            index_matches = False
        return (
            index_matches
            and
            graph.get("schema_version") == _MEMORY_SCHEMA_VERSION
            and graph.get("source_page_count") == len(pages)
            and graph.get("source_checksum") == self._page_projection_checksum(pages)
        )

    def repair_projections(self) -> bool:
        """Rebuild both derived artifacts and report whether drift was repaired."""
        repaired = not self.projection_is_consistent()
        self.refresh_index()
        self.refresh_graph()
        self.projection_dirty_path.unlink(missing_ok=True)
        return repaired

    def repair_dirty_projection(self) -> bool:
        """Recover a projection update interrupted after a canonical write."""
        if not self.projection_dirty_path.exists():
            return False
        self.refresh_index()
        self.refresh_graph()
        self.projection_dirty_path.unlink(missing_ok=True)
        return True

    def apply_due_transitions(
        self,
        *,
        as_of: str,
        prompt_version: str,
    ) -> list[dict[str, Any]]:
        """Materialize explicitly scheduled supersessions that are now effective."""
        cutoff = self._parse_timestamp(as_of)
        graph = self.read_graph()
        scheduled = graph.get("scheduled_transitions", [])
        locations = graph.get("claim_locations", {})
        if not isinstance(scheduled, list) or not isinstance(locations, dict):
            return []
        applied: list[dict[str, Any]] = []
        for item in scheduled:
            if not isinstance(item, dict):
                continue
            effective_at = str(item.get("effective_at", ""))
            if not effective_at or self._parse_timestamp(effective_at) > cutoff:
                continue
            winner_slug = str(item.get("page_slug", ""))
            winner_id = str(item.get("claim_id", ""))
            if not winner_slug or not winner_id:
                continue
            winner = self._claim_from_page(winner_slug, winner_id)
            if winner is None or winner.status is not ClaimStatus.ACTIVE:
                continue
            if self._has_due_dispute(winner.key, cutoff, graph):
                continue
            loser_ids = item.get("supersedes", [])
            if not isinstance(loser_ids, list):
                continue
            for loser_id_raw in loser_ids:
                loser_id = str(loser_id_raw)
                loser_slug = str(locations.get(loser_id, ""))
                if not loser_slug:
                    continue
                loser = self._claim_from_page(loser_slug, loser_id)
                if loser is None or loser.status not in {
                    ClaimStatus.ACTIVE,
                    ClaimStatus.DISPUTED,
                }:
                    continue
                evidence_claims = (winner, loser)
                event = TransitionEvent(
                    schema_version=_MEMORY_SCHEMA_VERSION,
                    event_id=hashlib.sha256(
                        "|".join(
                            (
                                loser_slug,
                                loser_id,
                                loser.status.value,
                                ClaimStatus.SUPERSEDED.value,
                                effective_at,
                                "effective_time_reached",
                                winner_id,
                            )
                        ).encode("utf-8")
                    ).hexdigest()[:24],
                    timestamp=effective_at,
                    page_slug=loser_slug,
                    claim_id=loser_id,
                    from_status=loser.status,
                    to_status=ClaimStatus.SUPERSEDED,
                    trigger_claim_id=winner_id,
                    rule="effective_time_reached",
                    relation=Relation.SUPERSEDES,
                    model=None,
                    prompt_version=prompt_version,
                    evidence_source_ids=tuple(
                        dict.fromkeys(
                            source
                            for claim in evidence_claims
                            for source in claim.source_ids
                        )
                    ),
                    evidence_spans=tuple(
                        dict.fromkeys(
                            evidence.span
                            for claim in evidence_claims
                            for evidence in claim.evidence
                        )
                    ),
                    rationale="Explicit future-effective replacement reached its effective time.",
                )
                self.apply_claim_transition(
                    page_slug=loser_slug,
                    claim_id=loser_id,
                    to_status=ClaimStatus.SUPERSEDED,
                    event=event,
                )
                applied.append(event.to_dict())
        return applied

    def _has_due_dispute(
        self,
        key: str,
        cutoff: datetime,
        graph: dict[str, Any],
    ) -> bool:
        claim_index = graph.get("claim_index", {})
        if not isinstance(claim_index, dict):
            return True
        slugs = claim_index.get(key, [])
        if not isinstance(slugs, list):
            return True
        for slug in slugs:
            for raw in self.claims_for_page(str(slug)):
                try:
                    claim = Claim.from_dict(raw)
                except ValueError:
                    return True
                if claim.status is not ClaimStatus.DISPUTED:
                    continue
                if (
                    claim.effective_at is None
                    or self._parse_timestamp(claim.effective_at) <= cutoff
                ):
                    return True
        return False

    def select_graph_candidates(
        self,
        question: str,
        *,
        k: int = 5,
        as_of: str | None = None,
    ) -> tuple[list[str], dict[str, int]]:
        if k < 1:
            raise ValueError("top_k must be at least 1")
        graph = self.read_graph()
        nodes_raw = graph.get("nodes", [])
        nodes = [n for n in nodes_raw if isinstance(n, dict)] if isinstance(nodes_raw, list) else []
        query_terms = self._tokenize(question)
        question_folded = question.casefold()
        due_winner_slugs: set[str] = set()
        due_loser_slugs: set[str] = set()
        if as_of is not None:
            cutoff = self._parse_timestamp(as_of)
            scheduled = graph.get("scheduled_transitions", [])
            locations = graph.get("claim_locations", {})
            if isinstance(scheduled, list) and isinstance(locations, dict):
                due_by_key: dict[str, list[dict[str, Any]]] = {}
                for item in scheduled:
                    if not isinstance(item, dict) or item.get("status") != "active":
                        continue
                    effective_at = item.get("effective_at")
                    key = str(item.get("key", ""))
                    if (
                        not isinstance(effective_at, str)
                        or not effective_at.strip()
                        or not query_terms.intersection(self._tokenize(key))
                        or self._parse_timestamp(effective_at) > cutoff
                    ):
                        continue
                    due_by_key.setdefault(key, []).append(item)

                for due_items in due_by_key.values():
                    superseded_ids = {
                        str(claim_id)
                        for item in due_items
                        for claim_id in (
                            item.get("supersedes", [])
                            if isinstance(item.get("supersedes"), list)
                            else []
                        )
                    }
                    terminal_items = [
                        item
                        for item in due_items
                        if str(item.get("claim_id", "")) not in superseded_ids
                    ]
                    if len(terminal_items) == 1:
                        winner_slug = str(terminal_items[0].get("page_slug", ""))
                        if winner_slug:
                            due_winner_slugs.add(winner_slug)
                    # Every predecessor and every non-terminal scheduled winner
                    # is stale at this as-of. Competing terminal winners receive
                    # no boost, making the retrieval path fail closed.
                    due_loser_slugs.update(
                        str(item.get("page_slug", ""))
                        for item in due_items
                        if item not in terminal_items or len(terminal_items) != 1
                        if str(item.get("page_slug", ""))
                    )
                    for item in due_items:
                        losers = item.get("supersedes", [])
                        if not isinstance(losers, list):
                            continue
                        due_loser_slugs.update(
                            str(locations.get(str(claim_id), ""))
                            for claim_id in losers
                            if str(locations.get(str(claim_id), ""))
                        )
        scored: list[tuple[int, str, str]] = []
        for node in nodes:
            if bool(node.get("legacy_unindexed")):
                continue
            slug = str(node.get("id", ""))
            title_terms = self._tokenize(str(node.get("title", "")))
            summary_terms = self._tokenize(str(node.get("summary", "")))
            tags_raw = node.get("tags", [])
            tags_terms = self._tokenize(
                " ".join(str(x) for x in tags_raw) if isinstance(tags_raw, list) else ""
            )
            keys_raw = [
                *(node.get("active_claim_keys", []) or []),
                *(node.get("disputed_claim_keys", []) or []),
            ]
            key_terms = self._tokenize(" ".join(str(x) for x in keys_raw))
            score = 8 if slug and slug in question_folded else 0
            score += 5 * len(query_terms.intersection(key_terms))
            score += 3 * len(query_terms.intersection(title_terms))
            score += 2 * len(query_terms.intersection(tags_terms))
            score += len(query_terms.intersection(summary_terms))
            if score > 0 and slug in due_winner_slugs:
                # A due winner and its predecessor often have identical key
                # terms.  Graph metadata must break that tie before top-K page
                # loading; no canonical page is read for this decision.
                score += 1000
            if score > 0 and slug in due_loser_slugs and slug not in due_winner_slugs:
                score -= 1000
            if score > 0 and slug:
                scored.append((score, str(node.get("updated_at", "")), slug))
        scored.sort(key=lambda row: (-row[0], row[2]))
        slugs = [slug for _, _, slug in scored[:k]]
        return slugs, {
            "corpus_pages": int(graph.get("source_page_count", len(nodes))),
            "candidate_pages": len(scored),
            "loaded_pages": len(slugs),
        }

    def _serialize_page(self, metadata: dict[str, Any], body: str) -> str:
        meta_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
        return f"{_FRONTMATTER_DELIM}\n{meta_text}\n{_FRONTMATTER_DELIM}\n\n{body.rstrip()}\n"

    def _parse_page(self, content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith(f"{_FRONTMATTER_DELIM}\n"):
            return {}, content
        parts = content.split(f"\n{_FRONTMATTER_DELIM}\n", 1)
        if len(parts) != 2:
            return {}, content
        meta_raw = parts[0].replace(f"{_FRONTMATTER_DELIM}\n", "", 1)
        body = parts[1].lstrip("\n")
        loaded = yaml.safe_load(meta_raw) or {}
        if not isinstance(loaded, dict):
            loaded = {}
        return loaded, body

    def _slugify(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).casefold().strip()
        slug = "-".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))
        return slug or "untitled"

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _atomic_write_bytes(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _empty_graph(self) -> dict[str, Any]:
        return {
            "schema_version": _MEMORY_SCHEMA_VERSION,
            "source_page_count": 0,
            "nodes": [],
            "edges": [],
            "claim_index": {},
            "claim_locations": {},
            "scheduled_transitions": [],
            "term_index": {},
            "source_checksum": hashlib.sha256(b"[]").hexdigest(),
        }

    def _tokenize(self, text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold().replace("::", " ")
        return {
            token
            for token in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
            if len(token) >= 2
        }

    def _page_projection_checksum(self, pages: list[WikiPage]) -> str:
        projection_source: list[dict[str, Any]] = []
        for page in pages:
            claims = [
                {
                    "claim_id": str(claim.get("claim_id", "")),
                    "key": self._claim_key(claim),
                    "status": str(claim.get("status", "")),
                    "effective_at": claim.get("effective_at"),
                    "supersedes": claim.get("supersedes", []),
                    "source_ids": claim.get("source_ids", []),
                }
                for claim in self.claims_for_page(page)
            ]
            projection_source.append(
                {
                    "slug": page.slug,
                    "title": page.title,
                    "summary": page.metadata.get("summary", ""),
                    "tags": page.metadata.get("tags", []),
                    "links": page.metadata.get("links", []),
                    "claims": claims,
                    "updated_at": page.metadata.get("updated_at", ""),
                }
            )
        encoded = json.dumps(
            projection_source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _claim_key(self, claim: dict[str, Any]) -> str:
        explicit = str(claim.get("key", "")).strip()
        if explicit:
            return explicit
        try:
            return claim_key(
                claim.get("scope", ""),
                claim.get("subject", ""),
                claim.get("predicate", ""),
            )
        except ValueError:
            return ""

    def _claim_from_page(self, page_slug: str, claim_id: str) -> Claim | None:
        try:
            raw_claims = self.claims_for_page(page_slug)
        except FileNotFoundError:
            return None
        for raw in raw_claims:
            if str(raw.get("claim_id", "")) != claim_id:
                continue
            try:
                return Claim.from_dict(raw)
            except ValueError:
                return None
        return None

    def _parse_timestamp(self, value: str) -> datetime:
        normalized = canonical_timestamp(value)
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))

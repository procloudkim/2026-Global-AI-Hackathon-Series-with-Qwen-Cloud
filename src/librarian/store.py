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
_CLAIM_REVISION_SCHEMA_VERSION = "librarian-claim-revision/v1"
_CLAIM_REVISION_BATCH_SCHEMA_VERSION = "librarian-claim-revision-batch/v1"
_PENDING_TRANSITION_SCHEMA_VERSION = "librarian-pending-transition/v1"
_GRAPH_SCHEMA_VERSION = "librarian-graph/v3"
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


@dataclass(frozen=True)
class ClaimRevisionView:
    snapshots: dict[str, dict[str, Any]]
    tracked_claim_ids: frozenset[str]
    incomplete_claim_ids: frozenset[str]


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
        self.claim_revisions_path = self.base / "claim-revisions.jsonl"
        self.pending_transition_path = self.base / ".pending-transition.json"
        self.pending_ingest_path = self.base / ".pending-ingest.json"
        self.pending_claim_revisions_path = (
            self.base / ".pending-claim-revisions.json"
        )
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
        # Reopening an existing store must be side-effect free. In particular,
        # deployment probes instantiate MemoryStore while the previous service
        # is stopped and require the persistent-memory digest to remain stable.
        # A stale derived graph is reported by projection_is_consistent() and is
        # rebuilt by an explicit repair or the next canonical mutation.

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
        revision_recorded_at: str | None = None,
        revision_operation_id: str | None = None,
        revision_reason: str | None = None,
    ) -> WikiPage:
        # A new mutation must not overwrite an older page/revision boundary.
        self.repair_partial_claim_revision_tail()
        self.recover_pending_claim_revisions()
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
        page_existed = page_path.exists()
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
        revision_batch = self._prepare_claim_revision_batch(
            page_slug=page_slug,
            page_existed=page_existed,
            before_claims=existing_meta.get("claims", []),
            after_claims=merged.get("claims", []),
            recorded_at=revision_recorded_at,
            operation_id=revision_operation_id,
            reason=revision_reason,
        )
        if revision_batch is not None:
            self._stage_claim_revision_batch(revision_batch)
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
        if revision_batch is not None:
            self._commit_claim_revision_batch(revision_batch)
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

    def claim_revisions(self) -> list[dict[str, Any]]:
        """Read and strictly validate the append-only claim snapshot history."""
        if not self.claim_revisions_path.exists():
            return []
        try:
            raw = self.claim_revisions_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("claim revision ledger is not valid UTF-8") from exc
        revisions: list[dict[str, Any]] = []
        heads: dict[str, str] = {}
        head_recorded_at: dict[str, str] = {}
        claim_pages: dict[str, str] = {}
        seen_ids: set[str] = set()
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                revision = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "claim revision ledger is corrupt at line "
                    f"{line_number}: {exc.msg}"
                ) from exc
            self._validate_claim_revision(
                revision,
                expected_ordinal=len(revisions) + 1,
                expected_previous=heads.get(str(revision.get("claim_id", "")))
                if isinstance(revision, dict)
                else None,
                expected_previous_recorded_at=head_recorded_at.get(
                    str(revision.get("claim_id", ""))
                )
                if isinstance(revision, dict)
                else None,
            )
            revision_id = str(revision["revision_id"])
            claim_id = str(revision["claim_id"])
            page_slug = str(revision["page_slug"])
            if revision_id in seen_ids:
                raise ValueError("claim revision ledger has duplicate revision_id")
            prior_page = claim_pages.get(claim_id)
            if prior_page is not None and prior_page != page_slug:
                raise ValueError("claim revision history cannot move a claim between pages")
            seen_ids.add(revision_id)
            heads[claim_id] = revision_id
            head_recorded_at[claim_id] = str(revision["recorded_at"])
            claim_pages[claim_id] = page_slug
            revisions.append(revision)
        return revisions

    def claim_revision_diagnostics(self) -> dict[str, Any]:
        """Report whether the current projection has complete revision coverage."""
        revisions = self.claim_revisions()
        tracked_ids = {str(row["claim_id"]) for row in revisions}
        current_ids = {
            str(raw_claim["claim_id"])
            for page in self.list_wiki_pages()
            for raw_claim in self.claims_for_page(page)
        }
        recorded_times = [str(row["recorded_at"]) for row in revisions]
        return {
            "schema_version": _CLAIM_REVISION_SCHEMA_VERSION,
            "ledger_exists": self.claim_revisions_path.exists(),
            "revision_count": len(revisions),
            "tracked_claim_count": len(tracked_ids),
            "current_claim_count": len(current_ids),
            "untracked_current_claim_count": len(current_ids - tracked_ids),
            "baseline_revision_count": sum(
                row["change_kind"] == "baseline" for row in revisions
            ),
            "earliest_recorded_at": min(recorded_times, default=None),
            "latest_recorded_at": max(recorded_times, default=None),
            "pending_receipt_exists": self.pending_claim_revisions_path.exists(),
        }

    def repair_partial_claim_revision_tail(self) -> bool:
        """Repair only a crash-truncated final claim-revision record."""
        if not self.claim_revisions_path.exists():
            return False
        raw = self.claim_revisions_path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            self.claim_revisions()
            return False
        boundary = raw.rfind(b"\n")
        prefix = raw[: boundary + 1] if boundary >= 0 else b""
        tail = raw[boundary + 1 :]
        try:
            prefix.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("claim revision ledger has non-tail UTF-8 corruption") from exc
        try:
            decoded_tail = tail.decode("utf-8")
            parsed_tail = json.loads(decoded_tail)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._atomic_write_bytes(self.claim_revisions_path, prefix)
            self.claim_revisions()
            return True
        if not isinstance(parsed_tail, dict):
            raise ValueError("claim revision ledger final record must be an object")
        self._atomic_write_bytes(self.claim_revisions_path, raw + b"\n")
        self.claim_revisions()
        return True

    def recover_pending_claim_revisions(self) -> bool:
        """Finish or discard one staged page/revision batch exactly once."""
        if not self.pending_claim_revisions_path.exists():
            return False
        batch = self._read_pending_claim_revision_batch()
        page_path = self.wiki_dir / f"{batch['page_slug']}.md"
        current_digest = (
            self._claim_array_digest(self.claims_for_page(str(batch["page_slug"])))
            if page_path.exists()
            else None
        )
        before_digest = (
            str(batch["before_claims_sha256"])
            if bool(batch["before_page_exists"])
            else None
        )
        existing = {row["revision_id"]: row for row in self.claim_revisions()}
        staged_ids = [str(row["revision_id"]) for row in batch["revisions"]]
        if current_digest == str(batch["after_claims_sha256"]):
            self._commit_claim_revision_batch(batch)
            return True
        if current_digest == before_digest:
            if any(revision_id in existing for revision_id in staged_ids):
                raise ValueError(
                    "claim revision history is ahead of the canonical page"
                )
            self.pending_claim_revisions_path.unlink(missing_ok=True)
            return True
        raise ValueError(
            "pending claim revision batch matches neither page boundary"
        )

    def baseline_claim_history(self, *, recorded_at: str, reason: str) -> int:
        """Record a non-backdated migration watermark for untracked v2 claims."""
        canonical_time = canonical_timestamp(recorded_at, "recorded_at")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("claim history baseline reason must be non-empty")
        existing = self.claim_revisions()
        tracked = {str(row["claim_id"]) for row in existing}
        ordinal = len(existing) + 1
        operation_id = hashlib.sha256(
            f"baseline|{canonical_time}|{normalized_reason}".encode("utf-8")
        ).hexdigest()[:24]
        revisions: list[dict[str, Any]] = []
        for page in self.list_wiki_pages():
            for raw_claim in self.claims_for_page(page):
                claim = Claim.from_dict(raw_claim)
                if claim.claim_id in tracked:
                    continue
                revision = self._make_claim_revision(
                    ordinal=ordinal,
                    operation_id=operation_id,
                    recorded_at=canonical_time,
                    page_slug=page.slug,
                    claim_id=claim.claim_id,
                    previous_revision_id=None,
                    change_kind="baseline",
                    claim=claim.to_dict(),
                    reason=normalized_reason,
                )
                revisions.append(revision)
                tracked.add(claim.claim_id)
                ordinal += 1
        for revision in revisions:
            self._append_claim_revision(revision)
        return len(revisions)

    def claim_revision_view(
        self,
        *,
        known_at: str,
        page_slugs: set[str] | None = None,
    ) -> ClaimRevisionView:
        """Project full claim snapshots at one knowledge-time cutoff."""
        cutoff = self._parse_timestamp(
            canonical_timestamp(known_at, "known_at")
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for revision in self.claim_revisions():
            if page_slugs is not None and str(revision["page_slug"]) not in page_slugs:
                continue
            grouped.setdefault(str(revision["claim_id"]), []).append(revision)
        snapshots: dict[str, dict[str, Any]] = {}
        incomplete: set[str] = set()
        for claim_id, revisions in grouped.items():
            first = revisions[0]
            first_time = self._parse_timestamp(str(first["recorded_at"]))
            if first["change_kind"] == "baseline" and cutoff < first_time:
                incomplete.add(claim_id)
                continue
            visible = [
                revision
                for revision in revisions
                if self._parse_timestamp(str(revision["recorded_at"])) <= cutoff
            ]
            if not visible:
                continue
            snapshot = visible[-1]["claim"]
            if snapshot is not None:
                snapshots[claim_id] = dict(snapshot)
        return ClaimRevisionView(
            snapshots=snapshots,
            tracked_claim_ids=frozenset(grouped),
            incomplete_claim_ids=frozenset(incomplete),
        )

    def _prepare_claim_revision_batch(
        self,
        *,
        page_slug: str,
        page_existed: bool,
        before_claims: Any,
        after_claims: Any,
        recorded_at: str | None = None,
        operation_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        before = self._canonical_claim_array(before_claims)
        after = self._canonical_claim_array(after_claims)
        before_by_id = {str(item["claim_id"]): item for item in before}
        after_by_id = {str(item["claim_id"]): item for item in after}
        removed_ids = sorted(set(before_by_id) - set(after_by_id))
        if removed_ids:
            raise ValueError(
                "canonical claim deletion is unsupported; transition claims to "
                f"archived instead: {', '.join(removed_ids)}"
            )
        changed_ids = sorted(
            claim_id
            for claim_id in set(before_by_id) | set(after_by_id)
            if before_by_id.get(claim_id) != after_by_id.get(claim_id)
        )
        if not changed_ids:
            return None

        existing = self.claim_revisions()
        heads: dict[str, str] = {}
        head_recorded_at: dict[str, str] = {}
        for row in existing:
            claim_id = str(row["claim_id"])
            heads[claim_id] = str(row["revision_id"])
            head_recorded_at[claim_id] = str(row["recorded_at"])
        context = self._claim_revision_context(
            recorded_at=recorded_at,
            operation_id=operation_id,
            reason=reason,
        )
        if context is None:
            tracked_changes = sorted(set(changed_ids).intersection(heads))
            if tracked_changes:
                raise ValueError(
                    "tracked claim mutation requires complete revision context: "
                    f"{', '.join(tracked_changes)}"
                )
            return None
        context_time = self._parse_timestamp(context["recorded_at"])
        for claim_id in changed_ids:
            previous_time = head_recorded_at.get(claim_id)
            if previous_time is not None and context_time < self._parse_timestamp(
                previous_time
            ):
                raise ValueError(
                    "claim revision recorded_at cannot precede its previous revision: "
                    f"{claim_id}"
                )
        ordinal = len(existing) + 1
        revisions: list[dict[str, Any]] = []
        for claim_id in changed_ids:
            previous = heads.get(claim_id)
            prior = before_by_id.get(claim_id)
            current = after_by_id.get(claim_id)
            if previous is None and prior is not None:
                baseline = self._make_claim_revision(
                    ordinal=ordinal,
                    operation_id=context["operation_id"],
                    recorded_at=context["recorded_at"],
                    page_slug=page_slug,
                    claim_id=claim_id,
                    previous_revision_id=None,
                    change_kind="baseline",
                    claim=prior,
                    reason="v2 current projection baseline before first tracked mutation",
                )
                revisions.append(baseline)
                previous = str(baseline["revision_id"])
                heads[claim_id] = previous
                ordinal += 1
            change_kind = (
                "delete"
                if current is None
                else "creation"
                if prior is None and previous is None
                else "update"
            )
            revision = self._make_claim_revision(
                ordinal=ordinal,
                operation_id=context["operation_id"],
                recorded_at=context["recorded_at"],
                page_slug=page_slug,
                claim_id=claim_id,
                previous_revision_id=previous,
                change_kind=change_kind,
                claim=current,
                reason=context["reason"],
            )
            revisions.append(revision)
            heads[claim_id] = str(revision["revision_id"])
            ordinal += 1

        before_digest = self._claim_array_digest(before)
        after_digest = self._claim_array_digest(after)
        batch_id = hashlib.sha256(
            "|".join(str(row["revision_id"]) for row in revisions).encode("utf-8")
        ).hexdigest()[:24]
        return {
            "schema_version": _CLAIM_REVISION_BATCH_SCHEMA_VERSION,
            "batch_id": batch_id,
            "page_slug": page_slug,
            "before_page_exists": page_existed,
            "before_claims_sha256": before_digest,
            "after_claims_sha256": after_digest,
            "revisions": revisions,
        }

    def _claim_revision_context(
        self,
        *,
        recorded_at: str | None,
        operation_id: str | None,
        reason: str | None,
    ) -> dict[str, str] | None:
        if recorded_at is not None or operation_id is not None or reason is not None:
            if recorded_at is None or operation_id is None or reason is None:
                raise ValueError("claim revision context must be complete")
            normalized_operation_id = operation_id.strip()
            normalized_reason = reason.strip()
            if not normalized_operation_id or not normalized_reason:
                raise ValueError(
                    "claim revision operation_id and reason must be non-empty"
                )
            return {
                "recorded_at": canonical_timestamp(recorded_at, "recorded_at"),
                "operation_id": normalized_operation_id,
                "reason": normalized_reason,
            }
        if self.pending_transition_path.exists():
            transition, transition_recorded_at = self._read_pending_transition()
            return {
                "recorded_at": transition_recorded_at,
                "operation_id": transition.event_id,
                "reason": transition.rule,
            }
        if self.pending_ingest_path.exists():
            receipt = self._read_pending_ingest_operation()
            return {
                "recorded_at": str(receipt["observed_at"]),
                "operation_id": str(receipt["operation_id"]),
                "reason": "ingest claim reconciliation",
            }
        return None

    def _stage_claim_revision_batch(self, batch: dict[str, Any]) -> None:
        if self.pending_claim_revisions_path.exists():
            raise RuntimeError(
                "pending claim revision batch must be recovered before staging"
            )
        self._atomic_write_text(
            self.pending_claim_revisions_path,
            json.dumps(batch, ensure_ascii=False, sort_keys=True),
        )

    def _commit_claim_revision_batch(self, batch: dict[str, Any]) -> None:
        for revision in batch["revisions"]:
            self._append_claim_revision(revision)
        self.pending_claim_revisions_path.unlink(missing_ok=True)

    def _append_claim_revision(self, revision: dict[str, Any]) -> None:
        existing = self.claim_revisions()
        by_id = {str(row["revision_id"]): row for row in existing}
        revision_id = str(revision.get("revision_id", ""))
        if revision_id in by_id:
            if by_id[revision_id] != revision:
                raise ValueError("duplicate claim revision_id has different payload")
            return
        heads: dict[str, str] = {}
        head_recorded_at: dict[str, str] = {}
        for row in existing:
            row_claim_id = str(row["claim_id"])
            heads[row_claim_id] = str(row["revision_id"])
            head_recorded_at[row_claim_id] = str(row["recorded_at"])
        claim_id = str(revision.get("claim_id", ""))
        self._validate_claim_revision(
            revision,
            expected_ordinal=len(existing) + 1,
            expected_previous=heads.get(claim_id),
            expected_previous_recorded_at=head_recorded_at.get(claim_id),
        )
        if self.claim_revisions_path.exists() and self.claim_revisions_path.stat().st_size:
            with self.claim_revisions_path.open("rb") as handle:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    raise ValueError(
                        "claim revision ledger is missing its final newline; "
                        "run explicit repair"
                    )
        with self.claim_revisions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(revision, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _make_claim_revision(
        self,
        *,
        ordinal: int,
        operation_id: str,
        recorded_at: str,
        page_slug: str,
        claim_id: str,
        previous_revision_id: str | None,
        change_kind: str,
        claim: dict[str, Any] | None,
        reason: str,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": _CLAIM_REVISION_SCHEMA_VERSION,
            "operation_id": operation_id,
            "recorded_at": canonical_timestamp(recorded_at, "recorded_at"),
            "page_slug": page_slug,
            "claim_id": claim_id,
            "previous_revision_id": previous_revision_id,
            "change_kind": change_kind,
            "claim": claim,
            "reason": reason,
        }
        revision_id = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {**payload, "ordinal": ordinal, "revision_id": revision_id}

    def _validate_claim_revision(
        self,
        revision: Any,
        *,
        expected_ordinal: int,
        expected_previous: str | None,
        expected_previous_recorded_at: str | None,
    ) -> None:
        expected_fields = {
            "schema_version",
            "ordinal",
            "revision_id",
            "operation_id",
            "recorded_at",
            "page_slug",
            "claim_id",
            "previous_revision_id",
            "change_kind",
            "claim",
            "reason",
        }
        if not isinstance(revision, dict) or set(revision) != expected_fields:
            raise ValueError("claim revision fields do not match the contract")
        if revision.get("schema_version") != _CLAIM_REVISION_SCHEMA_VERSION:
            raise ValueError("claim revision schema version is unsupported")
        if revision.get("ordinal") != expected_ordinal:
            raise ValueError("claim revision ordinals must be contiguous")
        if revision.get("previous_revision_id") != expected_previous:
            raise ValueError("claim revision previous head does not match")
        for field in ("revision_id", "operation_id", "page_slug", "claim_id", "reason"):
            if not isinstance(revision.get(field), str) or not revision[field].strip():
                raise ValueError(f"claim revision {field} must be non-empty")
        canonical_time = canonical_timestamp(
            str(revision["recorded_at"]), "recorded_at"
        )
        if canonical_time != revision["recorded_at"]:
            raise ValueError("claim revision recorded_at must be canonical")
        if (
            expected_previous_recorded_at is not None
            and self._parse_timestamp(canonical_time)
            < self._parse_timestamp(expected_previous_recorded_at)
        ):
            raise ValueError(
                "claim revision recorded_at cannot precede its previous revision"
            )
        change_kind = revision.get("change_kind")
        if change_kind not in {"creation", "baseline", "update", "delete"}:
            raise ValueError("claim revision change_kind is invalid")
        claim = revision.get("claim")
        if change_kind == "delete":
            if claim is not None:
                raise ValueError("delete claim revision must contain a null snapshot")
        else:
            parsed_claim = Claim.from_dict(claim)
            if parsed_claim.claim_id != revision["claim_id"]:
                raise ValueError("claim revision snapshot id does not match")
        expected_id = self._make_claim_revision(
            ordinal=int(revision["ordinal"]),
            operation_id=str(revision["operation_id"]),
            recorded_at=str(revision["recorded_at"]),
            page_slug=str(revision["page_slug"]),
            claim_id=str(revision["claim_id"]),
            previous_revision_id=revision["previous_revision_id"],
            change_kind=str(revision["change_kind"]),
            claim=claim,
            reason=str(revision["reason"]),
        )["revision_id"]
        if revision["revision_id"] != expected_id:
            raise ValueError("claim revision_id does not match its payload")

    def _read_pending_claim_revision_batch(self) -> dict[str, Any]:
        try:
            batch = json.loads(
                self.pending_claim_revisions_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("pending claim revision batch is corrupt") from exc
        expected = {
            "schema_version",
            "batch_id",
            "page_slug",
            "before_page_exists",
            "before_claims_sha256",
            "after_claims_sha256",
            "revisions",
        }
        if not isinstance(batch, dict) or set(batch) != expected:
            raise ValueError("pending claim revision batch fields are invalid")
        if batch.get("schema_version") != _CLAIM_REVISION_BATCH_SCHEMA_VERSION:
            raise ValueError("pending claim revision batch schema is unsupported")
        if not isinstance(batch.get("before_page_exists"), bool):
            raise ValueError("pending revision page-existence flag is invalid")
        for field in (
            "batch_id",
            "page_slug",
            "before_claims_sha256",
            "after_claims_sha256",
        ):
            if not isinstance(batch.get(field), str) or not batch[field]:
                raise ValueError(f"pending revision {field} must be non-empty")
        revisions = batch.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            raise ValueError("pending revision batch must contain revisions")
        expected_batch_id = hashlib.sha256(
            "|".join(str(row.get("revision_id", "")) for row in revisions).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        if batch["batch_id"] != expected_batch_id:
            raise ValueError("pending revision batch_id does not match")
        return batch

    @staticmethod
    def _canonical_claim_array(raw_claims: Any) -> list[dict[str, Any]]:
        if raw_claims is None:
            return []
        if not isinstance(raw_claims, list):
            raise ValueError("claims metadata must be an array")
        canonical = [Claim.from_dict(item).to_dict() for item in raw_claims]
        ids = [str(item["claim_id"]) for item in canonical]
        if len(ids) != len(set(ids)):
            raise ValueError("claims metadata contains duplicate claim ids")
        return canonical

    @classmethod
    def _claim_array_digest(cls, raw_claims: Any) -> str:
        canonical = sorted(
            cls._canonical_claim_array(raw_claims),
            key=lambda item: str(item["claim_id"]),
        )
        return hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

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
        revision_recorded_at: str | None = None,
        revision_operation_id: str | None = None,
        revision_reason: str | None = None,
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
            revision_recorded_at=revision_recorded_at,
            revision_operation_id=revision_operation_id,
            revision_reason=revision_reason,
        )

    def apply_claim_transition(
        self,
        *,
        page_slug: str,
        claim_id: str,
        to_status: ClaimStatus | str,
        event: TransitionEvent | dict[str, Any],
        recorded_at: str | None = None,
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
        revision_recorded_at = canonical_timestamp(
            recorded_at or transition.timestamp,
            "recorded_at",
        )
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
                self._stage_pending_transition(
                    transition,
                    recorded_at=revision_recorded_at,
                )
                self.append_decision_event(transition)
                self.pending_transition_path.unlink(missing_ok=True)
                return claim
            if transition.from_status is not claim.status:
                raise ValueError(
                    "transition event from_status does not match canonical claim state"
                )
            updated = Claim.from_dict({**claim.to_dict(), "status": target.value})
            raw_claims[index] = updated.to_dict()
            self._stage_pending_transition(
                transition,
                recorded_at=revision_recorded_at,
            )
            self.write_page_claims(
                page_slug,
                raw_claims,
                revision_recorded_at=revision_recorded_at,
                revision_operation_id=transition.event_id,
                revision_reason=transition.rule,
            )
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
        transition, recorded_at = self._parse_pending_transition(raw)
        self.apply_claim_transition(
            page_slug=transition.page_slug,
            claim_id=transition.claim_id,
            to_status=transition.to_status,
            event=transition,
            recorded_at=recorded_at,
        )
        self.pending_transition_path.unlink(missing_ok=True)
        return True

    def _stage_pending_transition(
        self,
        transition: TransitionEvent,
        *,
        recorded_at: str,
    ) -> None:
        self._atomic_write_text(
            self.pending_transition_path,
            json.dumps(
                {
                    "schema_version": _PENDING_TRANSITION_SCHEMA_VERSION,
                    "recorded_at": canonical_timestamp(
                        recorded_at, "recorded_at"
                    ),
                    "event": transition.to_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def _read_pending_transition(self) -> tuple[TransitionEvent, str]:
        try:
            raw = json.loads(
                self.pending_transition_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("pending transition receipt is corrupt") from exc
        return self._parse_pending_transition(raw)

    @staticmethod
    def _parse_pending_transition(raw: Any) -> tuple[TransitionEvent, str]:
        if (
            isinstance(raw, dict)
            and raw.get("schema_version") == _PENDING_TRANSITION_SCHEMA_VERSION
        ):
            if set(raw) != {"schema_version", "recorded_at", "event"}:
                raise ValueError("pending transition receipt fields are invalid")
            transition = TransitionEvent.from_dict(raw.get("event"))
            recorded_at = canonical_timestamp(
                str(raw.get("recorded_at", "")), "recorded_at"
            )
            return transition, recorded_at
        # Backward-compatible recovery for v2 receipts staged before the
        # recorded-at wrapper existed.
        transition = TransitionEvent.from_dict(raw)
        return transition, transition.timestamp

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
        temporal_claims: list[dict[str, Any]] = []
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
                    temporal_claims.append(
                        {
                            "claim_id": claim_id,
                            "page_slug": p.slug,
                            "key": key,
                            "normalized_value": str(
                                claim.get("normalized_value", "")
                            ),
                            "status": status,
                            "observed_at": str(claim.get("observed_at", "")),
                            "effective_at": claim.get("effective_at"),
                            "supersedes": [
                                str(item)
                                for item in (
                                    claim.get("supersedes", [])
                                    if isinstance(claim.get("supersedes"), list)
                                    else []
                                )
                            ],
                        }
                    )
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
            "graph_schema_version": _GRAPH_SCHEMA_VERSION,
            "source_page_count": len(pages),
            "nodes": nodes,
            "edges": edges,
            "claim_index": {k: sorted(set(v)) for k, v in sorted(claim_index.items())},
            "claim_locations": dict(sorted(claim_locations.items())),
            "scheduled_transitions": sorted(
                scheduled_transitions,
                key=lambda item: (item["effective_at"], item["claim_id"]),
            ),
            "temporal_claims": sorted(
                temporal_claims,
                key=lambda item: (item["key"], item["claim_id"]),
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
            and graph.get("graph_schema_version") == _GRAPH_SCHEMA_VERSION
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
                    recorded_at=as_of,
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
        valid_at: str | None = None,
        known_at: str | None = None,
        transition_events: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        if k < 1:
            raise ValueError("top_k must be at least 1")
        if as_of is not None and (valid_at is not None or known_at is not None):
            raise ValueError("as_of cannot be combined with valid_at or known_at")
        if (valid_at is None) != (known_at is None):
            raise ValueError("valid_at and known_at must be provided together")
        if as_of is not None:
            valid_cutoff = known_cutoff = self._parse_timestamp(as_of)
        elif valid_at is not None and known_at is not None:
            valid_cutoff = self._parse_timestamp(valid_at)
            known_cutoff = self._parse_timestamp(known_at)
        else:
            valid_cutoff = known_cutoff = None
        graph = self.read_graph()
        nodes_raw = graph.get("nodes", [])
        nodes = [n for n in nodes_raw if isinstance(n, dict)] if isinstance(nodes_raw, list) else []
        query_terms = self._tokenize(question)
        question_folded = question.casefold()
        active_claim_ids: set[str] = set()
        active_slugs: set[str] = set()
        loser_slugs: set[str] = set()
        conflict_slugs: set[str] = set()
        temporal_keys_by_slug: dict[str, set[str]] = {}
        graph_history_incomplete = 0
        graph_history_untracked = 0
        if valid_cutoff is not None and known_cutoff is not None:
            (
                active_claim_ids,
                active_slugs,
                loser_slugs,
                conflict_slugs,
                temporal_keys_by_slug,
                graph_history_incomplete,
                graph_history_untracked,
            ) = self._project_temporal_graph(
                graph,
                valid_at=valid_cutoff,
                known_at=known_cutoff,
                transition_events=(
                    transition_events
                    if transition_events is not None
                    else self.decision_events()
                ),
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
            keys_raw = (
                sorted(temporal_keys_by_slug.get(slug, set()))
                if valid_cutoff is not None
                else [
                    *(node.get("active_claim_keys", []) or []),
                    *(node.get("disputed_claim_keys", []) or []),
                ]
            )
            key_terms = self._tokenize(" ".join(str(x) for x in keys_raw))
            score = 8 if slug and slug in question_folded else 0
            score += 5 * len(query_terms.intersection(key_terms))
            score += 3 * len(query_terms.intersection(title_terms))
            score += 2 * len(query_terms.intersection(tags_terms))
            score += len(query_terms.intersection(summary_terms))
            if score > 0 and slug in active_slugs:
                score += 1000
            if score > 0 and slug in loser_slugs and slug not in active_slugs:
                score -= 1000
            if score > 0 and slug in conflict_slugs:
                score -= 1000
            if score > 0 and slug:
                scored.append((score, str(node.get("updated_at", "")), slug))
        scored.sort(key=lambda row: (-row[0], row[2]))
        slugs = [slug for _, _, slug in scored[:k]]
        return slugs, {
            "corpus_pages": int(graph.get("source_page_count", len(nodes))),
            "candidate_pages": len(scored),
            "loaded_pages": len(slugs),
            "temporal_active_claim_ids": sorted(active_claim_ids),
            "graph_incomplete_claim_histories": graph_history_incomplete,
            "graph_untracked_claim_histories": graph_history_untracked,
        }

    def _project_temporal_graph(
        self,
        graph: dict[str, Any],
        *,
        valid_at: datetime,
        known_at: datetime,
        transition_events: list[dict[str, Any]],
    ) -> tuple[
        set[str],
        set[str],
        set[str],
        set[str],
        dict[str, set[str]],
        int,
        int,
    ]:
        raw_records = graph.get("temporal_claims", [])
        records = (
            [dict(item) for item in raw_records if isinstance(item, dict)]
            if isinstance(raw_records, list)
            else []
        )
        revision_view = self.claim_revision_view(known_at=known_at.isoformat())
        events_by_claim: dict[str, list[TransitionEvent]] = {}
        for raw_event in transition_events:
            if raw_event.get("event_type") == "provenance_merge":
                continue
            event = TransitionEvent.from_dict(raw_event)
            events_by_claim.setdefault(event.claim_id, []).append(event)

        knowledge_visible: list[dict[str, Any]] = []
        incomplete = 0
        untracked = 0
        for current in records:
            claim_id = str(current.get("claim_id", ""))
            if not claim_id:
                continue
            revision_backed = claim_id in revision_view.tracked_claim_ids
            if claim_id in revision_view.incomplete_claim_ids:
                incomplete += 1
                continue
            if revision_backed:
                snapshot = revision_view.snapshots.get(claim_id)
                if snapshot is None:
                    continue
                snapshot_claim = Claim.from_dict(snapshot)
                record = {
                    "claim_id": claim_id,
                    "page_slug": str(current.get("page_slug", "")),
                    "key": snapshot_claim.key,
                    "normalized_value": snapshot_claim.normalized_value,
                    "status": snapshot_claim.status.value,
                    "observed_at": snapshot_claim.observed_at,
                    "effective_at": snapshot_claim.effective_at,
                    "supersedes": list(snapshot_claim.supersedes),
                }
            else:
                untracked += 1
                record = current
                observed = self._parse_timestamp(str(record.get("observed_at", "")))
                if observed > known_at:
                    continue
                projected_status: ClaimStatus | None = ClaimStatus(
                    str(record.get("status", ""))
                )
                for event in reversed(events_by_claim.get(claim_id, [])):
                    if self._parse_timestamp(event.timestamp) <= known_at:
                        continue
                    if projected_status is not event.to_status:
                        raise ValueError(
                            "decision ledger cannot rewind graph claim state: "
                            f"{claim_id}"
                        )
                    projected_status = event.from_status
                if projected_status is None:
                    continue
                record = {**record, "status": projected_status.value}
            if self._parse_timestamp(str(record.get("observed_at", ""))) > known_at:
                continue
            if str(record.get("status", "")) == ClaimStatus.ARCHIVED.value:
                continue
            knowledge_visible.append(record)

        known_superseded_ids = {
            str(loser_id)
            for record in knowledge_visible
            for loser_id in (
                record.get("supersedes", [])
                if isinstance(record.get("supersedes"), list)
                else []
            )
        }
        eligible_by_key: dict[str, list[dict[str, Any]]] = {}
        disputed_by_key: dict[str, list[dict[str, Any]]] = {}
        loser_slugs: set[str] = set()
        for record in knowledge_visible:
            valid_from = self._parse_timestamp(
                str(record.get("effective_at") or record.get("observed_at", ""))
            )
            if valid_from > valid_at:
                loser_slugs.add(str(record.get("page_slug", "")))
                continue
            status = str(record.get("status", ""))
            claim_id = str(record.get("claim_id", ""))
            if status == ClaimStatus.DISPUTED.value:
                disputed_by_key.setdefault(str(record.get("key", "")), []).append(
                    record
                )
                continue
            if (
                status == ClaimStatus.SUPERSEDED.value
                and claim_id not in known_superseded_ids
            ):
                loser_slugs.add(str(record.get("page_slug", "")))
                continue
            eligible_by_key.setdefault(str(record.get("key", "")), []).append(record)

        active_claim_ids: set[str] = set()
        active_slugs: set[str] = set()
        conflict_slugs: set[str] = set()
        temporal_keys_by_slug: dict[str, set[str]] = {}
        for key in set(eligible_by_key) | set(disputed_by_key):
            group = eligible_by_key.get(key, [])
            disputed = disputed_by_key.get(key, [])
            if disputed:
                slugs = {
                    str(item.get("page_slug", ""))
                    for item in [*group, *disputed]
                }
                if len(slugs) == 1:
                    temporal_keys_by_slug.setdefault(next(iter(slugs)), set()).add(
                        key
                    )
                else:
                    conflict_slugs.update(slugs)
                continue
            group_ids = {str(item.get("claim_id", "")) for item in group}
            superseded_ids = {
                str(loser_id)
                for item in group
                for loser_id in (
                    item.get("supersedes", [])
                    if isinstance(item.get("supersedes"), list)
                    else []
                )
                if str(loser_id) in group_ids
            }
            terminals = [
                item
                for item in group
                if str(item.get("claim_id", "")) not in superseded_ids
            ]
            terminal_values = {
                str(item.get("normalized_value", "")) for item in terminals
            }
            if terminals and len(terminal_values) == 1:
                for item in terminals:
                    claim_id = str(item.get("claim_id", ""))
                    slug = str(item.get("page_slug", ""))
                    active_claim_ids.add(claim_id)
                    active_slugs.add(slug)
                    temporal_keys_by_slug.setdefault(slug, set()).add(key)
                loser_slugs.update(
                    str(item.get("page_slug", ""))
                    for item in group
                    if str(item.get("claim_id", "")) in superseded_ids
                )
                continue
            slugs = {str(item.get("page_slug", "")) for item in group}
            if len(slugs) == 1:
                temporal_keys_by_slug.setdefault(next(iter(slugs)), set()).add(key)
            else:
                conflict_slugs.update(slugs)
        return (
            active_claim_ids,
            active_slugs,
            loser_slugs,
            conflict_slugs,
            temporal_keys_by_slug,
            incomplete,
            untracked,
        )

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
            "graph_schema_version": _GRAPH_SCHEMA_VERSION,
            "source_page_count": 0,
            "nodes": [],
            "edges": [],
            "claim_index": {},
            "claim_locations": {},
            "scheduled_transitions": [],
            "temporal_claims": [],
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

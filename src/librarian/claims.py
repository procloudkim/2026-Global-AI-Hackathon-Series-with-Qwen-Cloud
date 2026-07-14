"""Canonical claim contracts for Librarian's memory lifecycle.

The model may extract claim components, but IDs and state transitions are owned
by this module.  Keeping these operations deterministic makes page metadata,
derived indexes, and evaluation receipts comparable across process restarts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import hashlib
import re
from typing import Any, Mapping, Sequence
import unicodedata


class ClaimKind(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    EPISODE = "episode"


class ClaimStatus(str, Enum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class Relation(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    UNRESOLVED = "unresolved"


_ALLOWED_TRANSITIONS: frozenset[tuple[ClaimStatus | None, ClaimStatus]] = frozenset(
    {
        (None, ClaimStatus.ACTIVE),
        (ClaimStatus.ACTIVE, ClaimStatus.DISPUTED),
        (ClaimStatus.ACTIVE, ClaimStatus.SUPERSEDED),
        (ClaimStatus.DISPUTED, ClaimStatus.ACTIVE),
        (ClaimStatus.DISPUTED, ClaimStatus.SUPERSEDED),
        (ClaimStatus.SUPERSEDED, ClaimStatus.ACTIVE),
        (ClaimStatus.SUPERSEDED, ClaimStatus.ARCHIVED),
    }
)
_EXPLICIT_SUPERSESSION = re.compile(
    r"\b(replaces?|supersedes?|replacement|corrects?|correction|retracts?|"
    r"retraction|rollback|rolls? back|restores?|reinstates?|instead of|no longer)"
    r"\b|대체|정정|철회|롤백|복원|변경",
    flags=re.IGNORECASE,
)
_NEGATED_SUPERSESSION = re.compile(
    r"\b(no|not|without)\s+(an?\s+)?"
    r"(replacement|supersession|authority\s+to\s+replace)\b|"
    r"\b(do|does|is)\s+not\s+(replace|supersede)\b|"
    r"\b(doesn't|isn't)\s+(replace|supersede|a\s+replacement)\b|대체하지\s*않",
    flags=re.IGNORECASE,
)
def normalize_component(value: object) -> str:
    """Return the canonical representation used by claim keys and IDs."""

    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _meaningful_terms(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]+", normalize_component(value), flags=re.UNICODE)
        if len(token) >= 2
    }


def _text_contains_value(normalized_text: str, normalized_value: str) -> bool:
    if re.fullmatch(r"[\w.%-]+", normalized_value, flags=re.UNICODE):
        return bool(
            re.search(
                rf"(?<!\w){re.escape(normalized_value)}(?!\w)", normalized_text
            )
        )
    return normalized_value in normalized_text


def claim_key(scope: object, subject: object, predicate: object) -> str:
    """Build the canonical comparison key ``scope::subject::predicate``."""

    parts = tuple(normalize_component(part) for part in (scope, subject, predicate))
    if any(not part for part in parts):
        raise ValueError("claim key components must be non-empty")
    return "::".join(parts)


def has_explicit_supersession(text: object) -> bool:
    """Return whether text contains non-negated replacement/rollback evidence."""
    normalized = unicodedata.normalize("NFKC", str(text))
    return bool(_EXPLICIT_SUPERSESSION.search(normalized)) and not bool(
        _NEGATED_SUPERSESSION.search(normalized)
    )


def make_claim_id(
    kind: ClaimKind | str,
    scope: object,
    subject: object,
    predicate: object,
    value: object,
    effective_at: object | None = None,
) -> str:
    """Return the stable 20-hex ID for a canonical claim value."""

    kind_value = _coerce_enum(ClaimKind, kind, "kind").value
    value_normalized = normalize_component(value)
    if not value_normalized:
        raise ValueError("claim value must be non-empty")
    effective_normalized = (
        "" if effective_at is None else canonical_timestamp(str(effective_at), "effective_at")
    )
    canonical = "|".join(
        (
            kind_value,
            claim_key(scope, subject, predicate),
            value_normalized,
            effective_normalized,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def make_evidence_id(source_hash: object, span: object) -> str:
    """Return the stable 20-hex ID for one source evidence span."""

    source_hash_normalized = normalize_component(source_hash)
    span_normalized = normalize_component(span)
    if not source_hash_normalized or not span_normalized:
        raise ValueError("source_hash and span must be non-empty")
    canonical = f"{source_hash_normalized}|{span_normalized}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def validate_transition(
    current: ClaimStatus | str | None,
    new: ClaimStatus | str,
) -> None:
    """Raise ``ValueError`` unless ``current -> new`` is lifecycle-safe.

    ``None`` and the convenience string ``"new"`` both denote creation.  A
    no-op is not a transition; callers should handle idempotency before calling
    this validator.
    """

    current_status: ClaimStatus | None
    if current is None or (isinstance(current, str) and current.strip().lower() == "new"):
        current_status = None
    else:
        current_status = _coerce_enum(ClaimStatus, current, "current status")
    new_status = _coerce_enum(ClaimStatus, new, "new status")
    if (current_status, new_status) not in _ALLOWED_TRANSITIONS:
        current_value = "new" if current_status is None else current_status.value
        raise ValueError(
            f"invalid claim transition: {current_value} -> {new_status.value}"
        )


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    source_id: str
    source_hash: str
    span: str

    def __post_init__(self) -> None:
        _require_non_empty(self.evidence_id, "evidence_id")
        _require_non_empty(self.source_id, "source_id")
        _require_non_empty(self.source_hash, "source_hash")
        _require_non_empty(self.span, "span")
        expected_id = make_evidence_id(self.source_hash, self.span)
        if self.evidence_id != expected_id:
            raise ValueError(
                f"evidence_id does not match canonical evidence: expected {expected_id}"
            )

    @classmethod
    def create(cls, *, source_id: str, source_hash: str, span: str) -> EvidenceRef:
        return cls(
            evidence_id=make_evidence_id(source_hash, span),
            source_id=source_id,
            source_hash=source_hash,
            span=span,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceRef:
        _require_mapping(data, "evidence")
        _reject_unknown_keys(
            data,
            {"evidence_id", "source_id", "source_hash", "span"},
            "evidence",
        )
        source_id = _required_string(data, "source_id")
        source_hash = _required_string(data, "source_hash")
        span = _required_string(data, "span")
        evidence_id = data.get("evidence_id")
        if evidence_id is None:
            return cls.create(source_id=source_id, source_hash=source_hash, span=span)
        if not isinstance(evidence_id, str):
            raise ValueError("evidence.evidence_id must be a string")
        return cls(
            evidence_id=evidence_id,
            source_id=source_id,
            source_hash=source_hash,
            span=span,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "span": self.span,
        }


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    kind: ClaimKind
    scope: str
    subject: str
    predicate: str
    value: str
    normalized_value: str
    observed_at: str
    effective_at: str | None
    status: ClaimStatus
    source_ids: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    supersedes: tuple[str, ...]

    def __post_init__(self) -> None:
        kind = _coerce_enum(ClaimKind, self.kind, "kind")
        status = _coerce_enum(ClaimStatus, self.status, "status")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "supersedes", tuple(self.supersedes))
        object.__setattr__(
            self,
            "observed_at",
            canonical_timestamp(self.observed_at, "observed_at"),
        )
        if self.effective_at is not None:
            object.__setattr__(
                self,
                "effective_at",
                canonical_timestamp(self.effective_at, "effective_at"),
            )

        _require_non_empty(self.scope, "scope")
        _require_non_empty(self.subject, "subject")
        _require_non_empty(self.predicate, "predicate")
        _require_non_empty(self.value, "value")
        _require_non_empty(self.observed_at, "observed_at")
        expected_normalized = normalize_component(self.value)
        if self.normalized_value != expected_normalized:
            raise ValueError(
                "normalized_value does not match canonical value: "
                f"expected {expected_normalized!r}"
            )
        expected_id = make_claim_id(
            kind,
            self.scope,
            self.subject,
            self.predicate,
            self.value,
            self.effective_at,
        )
        if self.claim_id != expected_id:
            raise ValueError(
                f"claim_id does not match canonical claim: expected {expected_id}"
            )
        _require_unique_strings(self.source_ids, "source_ids", allow_empty=False)
        _require_unique_strings(self.supersedes, "supersedes", allow_empty=True)
        if any(not isinstance(item, EvidenceRef) for item in self.evidence):
            raise ValueError("evidence must contain EvidenceRef values")
        if not self.evidence:
            raise ValueError("evidence must contain at least one item")
        evidence_sources = {item.source_id for item in self.evidence}
        if evidence_sources != set(self.source_ids):
            raise ValueError(
                "each source_id must own at least one evidence reference and "
                "each evidence source_id must appear in source_ids"
            )
        if self.claim_id in self.supersedes:
            raise ValueError("a claim cannot supersede itself")

    @property
    def key(self) -> str:
        return claim_key(self.scope, self.subject, self.predicate)

    @classmethod
    def create(
        cls,
        *,
        kind: ClaimKind | str,
        scope: str,
        subject: str,
        predicate: str,
        value: str,
        observed_at: str,
        effective_at: str | None,
        status: ClaimStatus | str = ClaimStatus.ACTIVE,
        source_ids: Sequence[str],
        evidence: Sequence[EvidenceRef],
        supersedes: Sequence[str] = (),
    ) -> Claim:
        kind_enum = _coerce_enum(ClaimKind, kind, "kind")
        return cls(
            claim_id=make_claim_id(
                kind_enum, scope, subject, predicate, value, effective_at
            ),
            kind=kind_enum,
            scope=scope,
            subject=subject,
            predicate=predicate,
            value=value,
            normalized_value=normalize_component(value),
            observed_at=observed_at,
            effective_at=effective_at,
            status=_coerce_enum(ClaimStatus, status, "status"),
            source_ids=tuple(source_ids),
            evidence=tuple(evidence),
            supersedes=tuple(supersedes),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Claim:
        _require_mapping(data, "claim")
        allowed = {
            "claim_id",
            "kind",
            "scope",
            "subject",
            "predicate",
            "value",
            "normalized_value",
            "observed_at",
            "effective_at",
            "status",
            "source_ids",
            "evidence",
            "supersedes",
        }
        _reject_unknown_keys(data, allowed, "claim")
        kind = _coerce_enum(ClaimKind, data.get("kind"), "kind")
        scope = _required_string(data, "scope")
        subject = _required_string(data, "subject")
        predicate = _required_string(data, "predicate")
        value = _required_string(data, "value")
        observed_at = _required_string(data, "observed_at")
        effective_at = _optional_string(data.get("effective_at"), "effective_at")
        status = _coerce_enum(ClaimStatus, data.get("status"), "status")
        source_ids = _string_tuple(data.get("source_ids"), "source_ids")
        evidence_raw = data.get("evidence")
        if not isinstance(evidence_raw, list):
            raise ValueError("claim.evidence must be an array")
        evidence = tuple(EvidenceRef.from_dict(item) for item in evidence_raw)
        supersedes = _string_tuple(
            data.get("supersedes", []), "supersedes", allow_empty=True
        )
        normalized_value = data.get("normalized_value", normalize_component(value))
        if not isinstance(normalized_value, str):
            raise ValueError("claim.normalized_value must be a string")
        claim_id = data.get(
            "claim_id",
            make_claim_id(kind, scope, subject, predicate, value, effective_at),
        )
        if not isinstance(claim_id, str):
            raise ValueError("claim.claim_id must be a string")
        return cls(
            claim_id=claim_id,
            kind=kind,
            scope=scope,
            subject=subject,
            predicate=predicate,
            value=value,
            normalized_value=normalized_value,
            observed_at=observed_at,
            effective_at=effective_at,
            status=status,
            source_ids=source_ids,
            evidence=evidence,
            supersedes=supersedes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind.value,
            "scope": self.scope,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "normalized_value": self.normalized_value,
            "observed_at": self.observed_at,
            "effective_at": self.effective_at,
            "status": self.status.value,
            "source_ids": list(self.source_ids),
            "evidence": [item.to_dict() for item in self.evidence],
            "supersedes": list(self.supersedes),
        }


@dataclass(frozen=True, slots=True)
class RelationDecision:
    relation: Relation
    winner_claim_id: str | None
    evidence_source_ids: tuple[str, ...]
    evidence_spans: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        relation = _coerce_enum(Relation, self.relation, "relation")
        object.__setattr__(self, "relation", relation)
        object.__setattr__(
            self, "evidence_source_ids", tuple(self.evidence_source_ids)
        )
        object.__setattr__(self, "evidence_spans", tuple(self.evidence_spans))
        _require_non_empty(self.rationale, "rationale")
        _require_unique_strings(
            self.evidence_source_ids, "evidence_source_ids", allow_empty=True
        )
        _require_unique_strings(
            self.evidence_spans, "evidence_spans", allow_empty=True
        )
        if relation is Relation.UNRESOLVED and self.winner_claim_id is not None:
            raise ValueError("unresolved relation cannot name a winner")
        if relation is Relation.SUPERSEDES and not self.winner_claim_id:
            raise ValueError("supersedes relation requires winner_claim_id")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RelationDecision:
        _require_mapping(data, "relation decision")
        expected = {
            "relation",
            "winner_claim_id",
            "evidence_source_ids",
            "evidence_spans",
            "rationale",
        }
        _reject_unknown_keys(data, expected, "relation decision")
        missing = expected.difference(data)
        if missing:
            raise ValueError(
                "relation decision missing fields: " + ", ".join(sorted(missing))
            )
        winner = data["winner_claim_id"]
        if winner is not None and not isinstance(winner, str):
            raise ValueError("winner_claim_id must be a string or null")
        return cls(
            relation=_coerce_enum(Relation, data["relation"], "relation"),
            winner_claim_id=winner.strip() if isinstance(winner, str) else None,
            evidence_source_ids=_string_tuple(
                data["evidence_source_ids"],
                "evidence_source_ids",
                allow_empty=True,
            ),
            evidence_spans=_string_tuple(
                data["evidence_spans"], "evidence_spans", allow_empty=True
            ),
            rationale=_required_string(data, "rationale"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation.value,
            "winner_claim_id": self.winner_claim_id,
            "evidence_source_ids": list(self.evidence_source_ids),
            "evidence_spans": list(self.evidence_spans),
            "rationale": self.rationale,
        }


def supersession_evidence_binds_winner(
    decision: RelationDecision,
    claims: Sequence[Claim],
) -> bool:
    """Validate that cited relation evidence is owned by and supports the winner."""

    if decision.relation is not Relation.SUPERSEDES or not decision.winner_claim_id:
        return False
    winner = next(
        (claim for claim in claims if claim.claim_id == decision.winner_claim_id),
        None,
    )
    if winner is None:
        return False
    cited_sources = set(decision.evidence_source_ids)
    cited_spans = set(decision.evidence_spans)
    subject_terms = _meaningful_terms(winner.subject)
    predicate_terms = _meaningful_terms(winner.predicate)
    loser_sources = {
        source
        for claim in claims
        if claim.claim_id != winner.claim_id
        for source in claim.source_ids
    }
    loser_values = {
        claim.normalized_value
        for claim in claims
        if claim.claim_id != winner.claim_id
    }
    for evidence in winner.evidence:
        if evidence.source_id not in cited_sources or evidence.span not in cited_spans:
            continue
        for clause in re.split(r"(?<=[.!?])\s+|[\r\n:;]+", evidence.span):
            normalized_span = normalize_component(clause)
            if not _text_contains_value(normalized_span, winner.normalized_value):
                continue
            if not all(
                terms and any(term in normalized_span for term in terms)
                for terms in (subject_terms, predicate_terms)
            ):
                continue
            if winner.effective_at and winner.effective_at[:10] in clause:
                return True
            if not has_explicit_supersession(clause):
                continue
            source_bound = any(
                normalize_component(source) in normalized_span
                for source in loser_sources
            )
            value_bound = any(
                _text_contains_value(normalized_span, value)
                for value in loser_values
            )
            prior_key_bound = any(
                re.search(
                    rf"\b(?:prior|previous|old)\s+{re.escape(term)}\b",
                    normalized_span,
                )
                for term in predicate_terms
            )
            if source_bound or value_bound or prior_key_bound:
                return True
    return False


@dataclass(frozen=True, slots=True)
class TransitionEvent:
    schema_version: str
    event_id: str
    timestamp: str
    page_slug: str
    claim_id: str
    from_status: ClaimStatus | None
    to_status: ClaimStatus
    trigger_claim_id: str | None
    rule: str
    relation: Relation | None
    model: str | None
    prompt_version: str
    evidence_source_ids: tuple[str, ...]
    evidence_spans: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        from_status = (
            None
            if self.from_status is None
            else _coerce_enum(ClaimStatus, self.from_status, "from_status")
        )
        to_status = _coerce_enum(ClaimStatus, self.to_status, "to_status")
        relation = (
            None
            if self.relation is None
            else _coerce_enum(Relation, self.relation, "relation")
        )
        object.__setattr__(self, "from_status", from_status)
        object.__setattr__(self, "to_status", to_status)
        object.__setattr__(self, "relation", relation)
        object.__setattr__(
            self,
            "timestamp",
            canonical_timestamp(self.timestamp, "timestamp"),
        )
        object.__setattr__(
            self, "evidence_source_ids", tuple(self.evidence_source_ids)
        )
        object.__setattr__(self, "evidence_spans", tuple(self.evidence_spans))
        for field_name in (
            "schema_version",
            "event_id",
            "timestamp",
            "page_slug",
            "claim_id",
            "rule",
            "prompt_version",
            "rationale",
        ):
            _require_non_empty(getattr(self, field_name), field_name)
        _validate_timestamp(self.timestamp, "timestamp")
        validate_transition(from_status, to_status)
        _require_unique_strings(
            self.evidence_source_ids, "evidence_source_ids", allow_empty=True
        )
        _require_unique_strings(
            self.evidence_spans, "evidence_spans", allow_empty=True
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TransitionEvent:
        _require_mapping(data, "transition event")
        expected = {
            "schema_version",
            "event_id",
            "timestamp",
            "page_slug",
            "claim_id",
            "from_status",
            "to_status",
            "trigger_claim_id",
            "rule",
            "relation",
            "model",
            "prompt_version",
            "evidence_source_ids",
            "evidence_spans",
            "rationale",
        }
        _reject_unknown_keys(data, expected, "transition event")
        missing = expected.difference(data)
        if missing:
            raise ValueError(
                "transition event missing fields: " + ", ".join(sorted(missing))
            )
        from_raw = data["from_status"]
        relation_raw = data["relation"]
        trigger = data["trigger_claim_id"]
        model = data["model"]
        if trigger is not None and not isinstance(trigger, str):
            raise ValueError("trigger_claim_id must be a string or null")
        if model is not None and not isinstance(model, str):
            raise ValueError("model must be a string or null")
        return cls(
            schema_version=_required_string(data, "schema_version"),
            event_id=_required_string(data, "event_id"),
            timestamp=_required_string(data, "timestamp"),
            page_slug=_required_string(data, "page_slug"),
            claim_id=_required_string(data, "claim_id"),
            from_status=(
                None
                if from_raw is None
                else _coerce_enum(ClaimStatus, from_raw, "from_status")
            ),
            to_status=_coerce_enum(ClaimStatus, data["to_status"], "to_status"),
            trigger_claim_id=trigger.strip() if isinstance(trigger, str) else None,
            rule=_required_string(data, "rule"),
            relation=(
                None
                if relation_raw is None
                else _coerce_enum(Relation, relation_raw, "relation")
            ),
            model=model.strip() if isinstance(model, str) else None,
            prompt_version=_required_string(data, "prompt_version"),
            evidence_source_ids=_string_tuple(
                data["evidence_source_ids"],
                "evidence_source_ids",
                allow_empty=True,
            ),
            evidence_spans=_string_tuple(
                data["evidence_spans"], "evidence_spans", allow_empty=True
            ),
            rationale=_required_string(data, "rationale"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "page_slug": self.page_slug,
            "claim_id": self.claim_id,
            "from_status": (
                None if self.from_status is None else self.from_status.value
            ),
            "to_status": self.to_status.value,
            "trigger_claim_id": self.trigger_claim_id,
            "rule": self.rule,
            "relation": None if self.relation is None else self.relation.value,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "evidence_source_ids": list(self.evidence_source_ids),
            "evidence_spans": list(self.evidence_spans),
            "rationale": self.rationale,
        }


def _coerce_enum(enum_type: type[Enum], value: object, label: str):
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    try:
        return enum_type(value.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{label} must be one of: {allowed}") from exc


def _require_mapping(value: object, label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")


def _reject_unknown_keys(
    data: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    unknown = set(data).difference(allowed)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string or null")
    return value.strip()


def _require_non_empty(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _string_tuple(
    value: object, label: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} must contain non-empty strings")
        result.append(item.strip())
    output = tuple(result)
    _require_unique_strings(output, label, allow_empty=allow_empty)
    return output


def _require_unique_strings(
    values: Sequence[str], label: str, *, allow_empty: bool
) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{label} must contain at least one item")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")


def canonical_timestamp(value: str, label: str = "timestamp") -> str:
    """Return one timezone-required UTC representation for persisted timestamps."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_timestamp(value: str, label: str) -> None:
    canonical_timestamp(value, label)


__all__ = [
    "Claim",
    "ClaimKind",
    "ClaimStatus",
    "EvidenceRef",
    "Relation",
    "RelationDecision",
    "TransitionEvent",
    "canonical_timestamp",
    "claim_key",
    "has_explicit_supersession",
    "make_claim_id",
    "make_evidence_id",
    "normalize_component",
    "supersession_evidence_binds_winner",
    "validate_transition",
]

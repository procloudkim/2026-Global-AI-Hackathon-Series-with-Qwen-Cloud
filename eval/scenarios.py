"""Deterministic synthetic scenario construction.

This module is used only by the dataset materializer.  The runner deliberately
does not import it.  Inputs, extraction snapshots, and oracle labels are emitted
to separate files so the runner can be invoked without a gold path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any, Callable

from .contracts import SCHEMA_VERSION


SCENARIO_TYPES = (
    "explicit_supersession",
    "future_effective",
    "correction_rollback",
    "scope_coexistence",
    "unresolved_conflict",
    "non_numeric_change",
    "distractor_retrieval",
    "duplicate_restore",
)


def _opaque(seed: str, *parts: object, prefix: str = "x") -> str:
    material = "|".join([seed, *(str(part) for part in parts)])
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _key(scope: str, subject: str, predicate: str) -> str:
    return "::".join((scope.casefold(), subject.casefold(), predicate.casefold()))


@dataclass
class _Builder:
    seed: str
    scenario_type: str
    variant: int
    base: datetime
    scenario_id: str = field(init=False)
    events: list[dict[str, Any]] = field(default_factory=list)
    extraction_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    extraction_queries: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    gold_checkpoints: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.scenario_id = _opaque(
            self.seed, self.scenario_type, self.variant, prefix="scn"
        )

    def token(self, label: str, *, prefix: str) -> str:
        return _opaque(
            self.seed, self.scenario_type, self.variant, label, prefix=prefix
        )

    def add_event(
        self,
        *,
        index: int,
        text: str,
        claims: list[dict[str, Any]],
    ) -> str:
        event_id = self.token(f"event-{index}", prefix="evt")
        source_id = self.token(f"source-{index}", prefix="src")
        observed_at = _iso(self.base + timedelta(days=index))
        event = {
            "event_id": event_id,
            "at": observed_at,
            "source_id": source_id,
            "text": text.replace("{source_id}", source_id),
        }
        self.events.append(event)
        normalized: list[dict[str, Any]] = []
        for claim in claims:
            item = dict(claim)
            item.setdefault("kind", "fact")
            item.setdefault("observed_at", observed_at)
            # Effective time is an extracted fact only when the source states
            # one.  Defaulting it to observation time silently hands lifecycle
            # ordering to the candidate.
            item.setdefault("effective_at", None)
            # Relationship labels are generator internals.  The runtime must
            # infer replacement, restoration, duplication, or ambiguity from
            # the source text and evidence span.
            item.pop("relation", None)
            item.pop("target_source_id", None)
            item["key"] = _key(item["scope"], item["subject"], item["predicate"])
            item["source_id"] = source_id
            item["evidence_span"] = event["text"]
            normalized.append(item)
        self.extraction_events[event_id] = {"source_id": source_id, "claims": normalized}
        return event_id

    def source(self, event_id: str) -> str:
        return str(self.extraction_events[event_id]["source_id"])

    def add_checkpoint(
        self,
        *,
        label: str,
        after_event: str,
        as_of: datetime,
        scope: str,
        subject: str,
        predicate: str,
        expected_facts: list[dict[str, Any]],
        forbidden_facts: list[dict[str, str]],
        expected_states: list[dict[str, Any]],
        protected_facts: list[dict[str, Any]],
        required_retrieval_sources: list[str],
        must_abstain: bool = False,
        restart: bool = False,
    ) -> None:
        checkpoint_id = self.token(f"checkpoint-{label}", prefix="cp")
        query = f"What is the current {scope} {predicate} for {subject}?"
        self.checkpoints.append(
            {
                "checkpoint_id": checkpoint_id,
                "after_event": after_event,
                "as_of": _iso(as_of),
                "query": query,
                "restart": restart,
                "top_k": 3,
                "context_budget": 4000,
            }
        )
        self.extraction_queries[checkpoint_id] = {
            "terms": [scope, subject, predicate],
        }
        # A fact may have multiple provenance sources, but a checkpoint can require
        # the source that establishes the current transition (for example, a
        # restoration receipt).  Entailment still accepts any supporting source.
        required_sources = list(required_retrieval_sources)
        self.gold_checkpoints.append(
            {
                "checkpoint_id": checkpoint_id,
                "expected_facts": expected_facts,
                "forbidden_facts": forbidden_facts,
                "required_sources": required_sources,
                "required_retrieval_sources": required_retrieval_sources,
                "expected_states": expected_states,
                "protected_facts": protected_facts,
                "must_abstain": must_abstain,
            }
        )

    def bundle(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (
            {
                "schema_version": SCHEMA_VERSION,
                "scenario_id": self.scenario_id,
                "events": self.events,
                "checkpoints": self.checkpoints,
            },
            {
                "schema_version": SCHEMA_VERSION,
                "scenario_id": self.scenario_id,
                "events": self.extraction_events,
                "queries": self.extraction_queries,
            },
            {
                "schema_version": SCHEMA_VERSION,
                "scenario_id": self.scenario_id,
                "scenario_type": self.scenario_type,
                "checkpoints": self.gold_checkpoints,
            },
        )


def _fact(key: str, value: str, *sources: str) -> dict[str, Any]:
    return {"key": key, "value": value, "supporting_sources": list(sources)}


def _forbidden(key: str, value: str) -> dict[str, str]:
    return {"key": key, "value": value}


def _state(key: str, value: str, status: str, *sources: str) -> dict[str, Any]:
    return {"key": key, "value": value, "status": status, "source_ids": list(sources)}


def _claim(
    scope: str,
    subject: str,
    predicate: str,
    value: str,
    *,
    effective_at: datetime | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "scope": scope,
        "subject": subject,
        "predicate": predicate,
        "value": value,
    }
    if effective_at is not None:
        item["effective_at"] = _iso(effective_at)
    return item


def _new_builder(seed: str, kind: str, variant: int) -> _Builder:
    return _Builder(
        seed=seed,
        scenario_type=kind,
        variant=variant,
        base=datetime(2042, 1, 1, tzinfo=UTC) + timedelta(days=variant * 40),
    )


def _explicit_supersession(seed: str, variant: int, _: int):
    b = _new_builder(seed, "explicit_supersession", variant)
    scope, predicate = "production", "quota"
    subject = b.token("entity", prefix="ent")
    old = str(100 + variant * 7)
    new = str(1000 + variant * 11)
    key = _key(scope, subject, predicate)
    e1 = b.add_event(
        index=1,
        text=f"In {scope}, {subject}'s {predicate} is {old} units per minute.",
        claims=[_claim(scope, subject, predicate, old)],
    )
    s1 = b.source(e1)
    b.add_checkpoint(
        label="initial", after_event=e1, as_of=b.base + timedelta(days=1),
        scope=scope, subject=subject, predicate=predicate,
        expected_facts=[_fact(key, old, s1)], forbidden_facts=[],
        expected_states=[_state(key, old, "active", s1)],
        protected_facts=[_state(key, old, "active", s1)],
        required_retrieval_sources=[s1],
    )
    e2 = b.add_event(
        index=2,
        text=f"Explicit replacement notice: this record replaces {s1}. In {scope}, {subject}'s {predicate} is {new} units per minute.",
        claims=[_claim(scope, subject, predicate, new)],
    )
    s2 = b.source(e2)
    states = [_state(key, old, "superseded", s1), _state(key, new, "active", s2)]
    for label, restart in (("updated", False), ("restart", True)):
        b.add_checkpoint(
            label=label, after_event=e2, as_of=b.base + timedelta(days=2),
            scope=scope, subject=subject, predicate=predicate,
            expected_facts=[_fact(key, new, s2)], forbidden_facts=[_forbidden(key, old)],
            expected_states=states, protected_facts=[_state(key, new, "active", s2)],
            required_retrieval_sources=[s2], restart=restart,
        )
    return b.bundle()


def _future_effective(seed: str, variant: int, _: int):
    b = _new_builder(seed, "future_effective", variant)
    scope, predicate = "regional", "mode"
    subject = b.token("entity", prefix="ent")
    old = b.token("old-value", prefix="val")
    new = b.token("new-value", prefix="val")
    key = _key(scope, subject, predicate)
    e1 = b.add_event(index=1, text=f"In {scope}, {subject}'s {predicate} is {old}.", claims=[_claim(scope, subject, predicate, old)])
    s1 = b.source(e1)
    b.add_checkpoint(
        label="initial", after_event=e1, as_of=b.base + timedelta(days=1), scope=scope,
        subject=subject, predicate=predicate, expected_facts=[_fact(key, old, s1)],
        forbidden_facts=[], expected_states=[_state(key, old, "active", s1)],
        protected_facts=[_state(key, old, "active", s1)], required_retrieval_sources=[s1],
    )
    future = b.base + timedelta(days=10)
    e2 = b.add_event(
        index=2,
        text=f"Scheduled replacement: this record replaces {s1} at {_iso(future)}. In {scope}, {subject}'s {predicate} becomes {new}.",
        claims=[_claim(scope, subject, predicate, new, effective_at=future)],
    )
    s2 = b.source(e2)
    b.add_checkpoint(
        label="before-effective", after_event=e2, as_of=b.base + timedelta(days=3),
        scope=scope, subject=subject, predicate=predicate,
        expected_facts=[_fact(key, old, s1)], forbidden_facts=[_forbidden(key, new)],
        expected_states=[_state(key, old, "active", s1), _state(key, new, "active", s2)],
        protected_facts=[_state(key, old, "active", s1)], required_retrieval_sources=[s1],
    )
    b.add_checkpoint(
        label="after-effective-restart", after_event=e2, as_of=b.base + timedelta(days=11),
        scope=scope, subject=subject, predicate=predicate,
        expected_facts=[_fact(key, new, s2)], forbidden_facts=[_forbidden(key, old)],
        expected_states=[_state(key, old, "superseded", s1), _state(key, new, "active", s2)],
        protected_facts=[_state(key, new, "active", s2)], required_retrieval_sources=[s2], restart=True,
    )
    return b.bundle()


def _correction_rollback(seed: str, variant: int, _: int):
    b = _new_builder(seed, "correction_rollback", variant)
    scope, predicate = "tenant", "endpoint"
    subject = b.token("entity", prefix="ent")
    a = b.token("value-a", prefix="val")
    c = b.token("value-b", prefix="val")
    key = _key(scope, subject, predicate)
    e1 = b.add_event(index=1, text=f"In {scope}, {subject}'s {predicate} is {a}.", claims=[_claim(scope, subject, predicate, a)])
    s1 = b.source(e1)
    b.add_checkpoint(label="initial", after_event=e1, as_of=b.base + timedelta(days=1), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, a, s1)], forbidden_facts=[], expected_states=[_state(key, a, "active", s1)], protected_facts=[_state(key, a, "active", s1)], required_retrieval_sources=[s1])
    e2 = b.add_event(index=2, text=f"Correction: this record replaces {s1}. In {scope}, {subject}'s {predicate} is {c}.", claims=[_claim(scope, subject, predicate, c)])
    s2 = b.source(e2)
    b.add_checkpoint(label="corrected", after_event=e2, as_of=b.base + timedelta(days=2), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, c, s2)], forbidden_facts=[_forbidden(key, a)], expected_states=[_state(key, a, "superseded", s1), _state(key, c, "active", s2)], protected_facts=[_state(key, c, "active", s2)], required_retrieval_sources=[s2])
    rollback_at = b.base + timedelta(days=3)
    e3 = b.add_event(
        index=3,
        text=(
            "Rollback as a new effective record: this record replaces "
            f"{s2} effective {_iso(rollback_at)}. In {scope}, {subject}'s "
            f"{predicate} is {a}."
        ),
        claims=[
            _claim(
                scope,
                subject,
                predicate,
                a,
                effective_at=rollback_at,
            )
        ],
    )
    s3 = b.source(e3)
    b.add_checkpoint(label="rollback-restart", after_event=e3, as_of=b.base + timedelta(days=3), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, a, s3)], forbidden_facts=[_forbidden(key, c)], expected_states=[_state(key, a, "superseded", s1), _state(key, c, "superseded", s2), _state(key, a, "active", s3)], protected_facts=[_state(key, a, "active", s3)], required_retrieval_sources=[s3], restart=True)
    return b.bundle()


def _scope_coexistence(seed: str, variant: int, _: int):
    b = _new_builder(seed, "scope_coexistence", variant)
    subject, predicate = b.token("entity", prefix="ent"), "channel"
    prod, staging = "production", "staging"
    pv, sv = b.token("prod-value", prefix="val"), b.token("stage-value", prefix="val")
    pk, sk = _key(prod, subject, predicate), _key(staging, subject, predicate)
    e1 = b.add_event(index=1, text=f"In {prod}, {subject}'s {predicate} is {pv}.", claims=[_claim(prod, subject, predicate, pv)])
    s1 = b.source(e1)
    b.add_checkpoint(label="initial", after_event=e1, as_of=b.base + timedelta(days=1), scope=prod, subject=subject, predicate=predicate, expected_facts=[_fact(pk, pv, s1)], forbidden_facts=[], expected_states=[_state(pk, pv, "active", s1)], protected_facts=[_state(pk, pv, "active", s1)], required_retrieval_sources=[s1])
    e2 = b.add_event(index=2, text=f"In {staging}, {subject}'s {predicate} is {sv}. This does not replace production.", claims=[_claim(staging, subject, predicate, sv)])
    s2 = b.source(e2)
    states = [_state(pk, pv, "active", s1), _state(sk, sv, "active", s2)]
    protected = states.copy()
    b.add_checkpoint(label="production-after-staging", after_event=e2, as_of=b.base + timedelta(days=2), scope=prod, subject=subject, predicate=predicate, expected_facts=[_fact(pk, pv, s1)], forbidden_facts=[_forbidden(pk, sv)], expected_states=states, protected_facts=protected, required_retrieval_sources=[s1])
    b.add_checkpoint(label="staging-restart", after_event=e2, as_of=b.base + timedelta(days=2), scope=staging, subject=subject, predicate=predicate, expected_facts=[_fact(sk, sv, s2)], forbidden_facts=[_forbidden(sk, pv)], expected_states=states, protected_facts=protected, required_retrieval_sources=[s2], restart=True)
    return b.bundle()


def _unresolved_conflict(seed: str, variant: int, _: int):
    b = _new_builder(seed, "unresolved_conflict", variant)
    scope, predicate = "workspace", "tier"
    subject = b.token("entity", prefix="ent")
    a, c = b.token("value-a", prefix="val"), b.token("value-b", prefix="val")
    key = _key(scope, subject, predicate)
    e1 = b.add_event(index=1, text=f"In {scope}, {subject}'s {predicate} is {a}.", claims=[_claim(scope, subject, predicate, a)])
    s1 = b.source(e1)
    b.add_checkpoint(label="initial", after_event=e1, as_of=b.base + timedelta(days=1), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, a, s1)], forbidden_facts=[], expected_states=[_state(key, a, "active", s1)], protected_facts=[_state(key, a, "active", s1)], required_retrieval_sources=[s1])
    e2 = b.add_event(index=2, text=f"Independent conflicting record, with no replacement authority: in {scope}, {subject}'s {predicate} is {c}.", claims=[_claim(scope, subject, predicate, c)])
    s2 = b.source(e2)
    states = [_state(key, a, "disputed", s1), _state(key, c, "disputed", s2)]
    forbidden = [_forbidden(key, a), _forbidden(key, c)]
    for label, restart in (("conflict", False), ("conflict-restart", True)):
        b.add_checkpoint(label=label, after_event=e2, as_of=b.base + timedelta(days=2), scope=scope, subject=subject, predicate=predicate, expected_facts=[], forbidden_facts=forbidden, expected_states=states, protected_facts=[], required_retrieval_sources=[s1, s2], must_abstain=True, restart=restart)
    return b.bundle()


def _non_numeric_change(seed: str, variant: int, _: int):
    b = _new_builder(seed, "non_numeric_change", variant)
    scope, predicate = "profile", "preferred_language"
    subject = b.token("entity", prefix="ent")
    old, new = b.token("language-a", prefix="lang"), b.token("language-b", prefix="lang")
    key = _key(scope, subject, predicate)
    e1 = b.add_event(index=1, text=f"In {scope}, {subject}'s {predicate} is {old}.", claims=[_claim(scope, subject, predicate, old)])
    s1 = b.source(e1)
    b.add_checkpoint(label="initial", after_event=e1, as_of=b.base + timedelta(days=1), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, old, s1)], forbidden_facts=[], expected_states=[_state(key, old, "active", s1)], protected_facts=[_state(key, old, "active", s1)], required_retrieval_sources=[s1])
    e2 = b.add_event(index=2, text=f"Preference update: this record replaces {s1}. In {scope}, {subject}'s {predicate} is {new}.", claims=[_claim(scope, subject, predicate, new)])
    s2 = b.source(e2)
    states = [_state(key, old, "superseded", s1), _state(key, new, "active", s2)]
    for label, restart in (("updated", False), ("restart", True)):
        b.add_checkpoint(label=label, after_event=e2, as_of=b.base + timedelta(days=2), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, new, s2)], forbidden_facts=[_forbidden(key, old)], expected_states=states, protected_facts=[_state(key, new, "active", s2)], required_retrieval_sources=[s2], restart=restart)
    return b.bundle()


def _distractor_retrieval(seed: str, variant: int, distractor_count: int):
    b = _new_builder(seed, "distractor_retrieval", variant)
    for index in range(1, distractor_count + 1):
        ds = b.token(f"distractor-subject-{index}", prefix="ent")
        dv = b.token(f"distractor-value-{index}", prefix="val")
        b.add_event(index=index, text=f"In noise, {ds}'s unrelated_property is {dv}.", claims=[_claim("noise", ds, "unrelated_property", dv)])
    scope, predicate = "fleet", "routing_code"
    subject = b.token("entity", prefix="ent")
    old, new = b.token("old-value", prefix="val"), b.token("new-value", prefix="val")
    key = _key(scope, subject, predicate)
    first_index = distractor_count + 1
    e1 = b.add_event(index=first_index, text=f"In {scope}, {subject}'s {predicate} is {old}.", claims=[_claim(scope, subject, predicate, old)])
    s1 = b.source(e1)
    b.add_checkpoint(label="initial", after_event=e1, as_of=b.base + timedelta(days=first_index), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, old, s1)], forbidden_facts=[], expected_states=[_state(key, old, "active", s1)], protected_facts=[_state(key, old, "active", s1)], required_retrieval_sources=[s1])
    e2 = b.add_event(index=first_index + 1, text=f"Explicit replacement: this record replaces {s1}. In {scope}, {subject}'s {predicate} is {new}.", claims=[_claim(scope, subject, predicate, new)])
    s2 = b.source(e2)
    states = [_state(key, old, "superseded", s1), _state(key, new, "active", s2)]
    for label, restart in (("updated", False), ("restart", True)):
        b.add_checkpoint(label=label, after_event=e2, as_of=b.base + timedelta(days=first_index + 1), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, new, s2)], forbidden_facts=[_forbidden(key, old)], expected_states=states, protected_facts=[_state(key, new, "active", s2)], required_retrieval_sources=[s2], restart=restart)
    return b.bundle()


def _duplicate_restore(seed: str, variant: int, _: int):
    b = _new_builder(seed, "duplicate_restore", variant)
    scope, predicate = "catalog", "status"
    subject = b.token("entity", prefix="ent")
    good, bad = b.token("stable-value", prefix="val"), b.token("conflict-value", prefix="val")
    key = _key(scope, subject, predicate)
    e1 = b.add_event(index=1, text=f"In {scope}, {subject}'s {predicate} is {good}.", claims=[_claim(scope, subject, predicate, good)])
    s1 = b.source(e1)
    b.add_checkpoint(label="initial", after_event=e1, as_of=b.base + timedelta(days=1), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, good, s1)], forbidden_facts=[], expected_states=[_state(key, good, "active", s1)], protected_facts=[_state(key, good, "active", s1)], required_retrieval_sources=[s1])
    e2 = b.add_event(index=2, text=f"Duplicate evidence for {s1}: in {scope}, {subject}'s {predicate} is {good}.", claims=[_claim(scope, subject, predicate, good)])
    s2 = b.source(e2)
    e3 = b.add_event(index=3, text=f"Unsupported conflict: in {scope}, {subject}'s {predicate} is {bad}.", claims=[_claim(scope, subject, predicate, bad)])
    s3 = b.source(e3)
    b.add_checkpoint(label="conflict", after_event=e3, as_of=b.base + timedelta(days=3), scope=scope, subject=subject, predicate=predicate, expected_facts=[], forbidden_facts=[_forbidden(key, good), _forbidden(key, bad)], expected_states=[_state(key, good, "disputed", s1, s2), _state(key, bad, "disputed", s3)], protected_facts=[], required_retrieval_sources=[s1, s3], must_abstain=True)
    e4 = b.add_event(index=4, text=f"Resolution restores {s1} and rejects {s3}: in {scope}, {subject}'s {predicate} is {good}.", claims=[_claim(scope, subject, predicate, good)])
    s4 = b.source(e4)
    b.add_checkpoint(label="restored-restart", after_event=e4, as_of=b.base + timedelta(days=4), scope=scope, subject=subject, predicate=predicate, expected_facts=[_fact(key, good, s1, s2, s4)], forbidden_facts=[_forbidden(key, bad)], expected_states=[_state(key, good, "active", s1, s2, s4), _state(key, bad, "superseded", s3)], protected_facts=[_state(key, good, "active", s1, s2, s4)], required_retrieval_sources=[s4], restart=True)
    return b.bundle()


_BUILDERS: dict[str, Callable[[str, int, int], tuple[dict, dict, dict]]] = {
    "explicit_supersession": _explicit_supersession,
    "future_effective": _future_effective,
    "correction_rollback": _correction_rollback,
    "scope_coexistence": _scope_coexistence,
    "unresolved_conflict": _unresolved_conflict,
    "non_numeric_change": _non_numeric_change,
    "distractor_retrieval": _distractor_retrieval,
    "duplicate_restore": _duplicate_restore,
}


def build_dataset(
    *, seed: str, variants_per_type: int, distractor_count: int = 50
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if variants_per_type < 1:
        raise ValueError("variants_per_type must be positive")
    if distractor_count < 1:
        raise ValueError("distractor_count must be positive")
    cases: list[dict[str, Any]] = []
    extractions: list[dict[str, Any]] = []
    gold: list[dict[str, Any]] = []
    for scenario_type in SCENARIO_TYPES:
        for variant in range(variants_per_type):
            case, extraction, oracle = _BUILDERS[scenario_type](
                seed, variant, distractor_count
            )
            cases.append(case)
            extractions.append(extraction)
            gold.append(oracle)
    return cases, extractions, gold

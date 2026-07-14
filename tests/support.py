from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from librarian.claims import Claim, ClaimStatus, EvidenceRef


@dataclass
class FakeResponse:
    text: str
    model: str = "fake-qwen"
    prompt_tokens: int = 11
    completion_tokens: int = 7

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ScriptedRouter:
    """Deterministic router that fails if production makes an unplanned call."""

    def __init__(self, *responses: dict[str, Any] | str) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, tier, system, user, temperature=0.3, max_tokens=None):
        self.calls.append(
            {
                "tier": tier,
                "system": system,
                "user": user,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError("production made an unexpected model call")
        response = self._responses.pop(0)
        text = response if isinstance(response, str) else json.dumps(response)
        return FakeResponse(text=text)

    @property
    def remaining(self) -> int:
        return len(self._responses)


class NoCallRouter:
    def chat(self, *args, **kwargs):
        raise AssertionError("this deterministic path must not call a model")


def extracted_claim(
    *,
    value: str,
    evidence_span: str,
    kind: str = "fact",
    scope: str = "production",
    subject: str = "api",
    predicate: str = "quota",
    effective_at: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "scope": scope,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "effective_at": effective_at,
        "evidence_spans": [evidence_span],
    }


def ingest_payload(
    *,
    title: str,
    claims: list[dict[str, Any]],
    summary: str = "Synthetic policy memory.",
    body: str = "Synthetic policy memory.",
    links: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "summary": summary,
        "body": body,
        "links": links or [],
        "tags": tags or ["policy"],
        "claims": claims,
    }


def canonical_claim(
    *,
    value: str,
    source_id: str,
    evidence_span: str,
    status: ClaimStatus | str = ClaimStatus.ACTIVE,
    scope: str = "production",
    subject: str = "api",
    predicate: str = "quota",
    observed_at: str = "2026-07-14T00:00:00Z",
    effective_at: str | None = None,
    supersedes: tuple[str, ...] = (),
) -> Claim:
    source_hash = hashlib.sha256(
        f"{source_id}\n{evidence_span}".encode("utf-8")
    ).hexdigest()
    evidence = EvidenceRef.create(
        source_id=source_id,
        source_hash=source_hash,
        span=evidence_span,
    )
    return Claim.create(
        kind="fact",
        scope=scope,
        subject=subject,
        predicate=predicate,
        value=value,
        observed_at=observed_at,
        effective_at=effective_at,
        status=status,
        source_ids=[source_id],
        evidence=[evidence],
        supersedes=supersedes,
    )


def query_answer(
    *,
    answer: str,
    key: str,
    value: str,
    claim_id: str,
    citation: str,
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "answer": answer,
        "facts": [{"key": key, "value": value, "claim_ids": [claim_id]}],
        "citations": [citation],
        "confidence": confidence,
        "abstained": False,
    }

"""Forget/Lint engine for timely memory maintenance."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Protocol

from .llm import Tier
from .store import MemoryStore, WikiPage


class SupportsChat(Protocol):
    def chat(
        self,
        tier: Tier,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ): ...


@dataclass(frozen=True)
class LintFinding:
    finding_type: str
    page: str
    message: str
    archived: bool = False


@dataclass(frozen=True)
class LintResult:
    findings: list[LintFinding]
    archived_pages: list[str]
    route_tier: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def run_lint(*, store: MemoryStore, router: SupportsChat, apply_archive: bool = True) -> LintResult:
    findings: list[LintFinding] = []
    archived: list[str] = []

    # 1) orphan detection
    orphans = _detect_orphans(store)
    for slug in orphans:
        findings.append(
            LintFinding(
                finding_type="orphan",
                page=slug,
                message=f"Page has no inbound links: {slug}",
            )
        )

    # 2) conflict detection with optional heavy arbitration
    pages = store.list_wiki_pages()
    conflict_candidates = _conflict_candidates(pages)
    prompt_tokens = 0
    completion_tokens = 0
    model = "none"
    for a, b in conflict_candidates:
        decision = _resolve_conflict(a, b, router)
        prompt_tokens += decision["prompt_tokens"]
        completion_tokens += decision["completion_tokens"]
        model = decision["model"] or model
        winner = decision["winner"]
        loser = decision["loser"]
        if loser and apply_archive:
            store.archive_page(loser, f"conflict resolved in favor of {winner}")
            archived.append(loser)
            findings.append(
                LintFinding(
                    finding_type="conflict",
                    page=loser,
                    message=f"Archived conflicting page: {loser} (winner={winner})",
                    archived=True,
                )
            )
        elif loser:
            findings.append(
                LintFinding(
                    finding_type="conflict",
                    page=loser,
                    message=f"Conflict detected: {loser} (winner={winner})",
                    archived=False,
                )
            )

    return LintResult(
        findings=findings,
        archived_pages=archived,
        route_tier=Tier.HEAVY.value if conflict_candidates else "none",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def _detect_orphans(store: MemoryStore) -> list[str]:
    pages = store.list_wiki_pages()
    inbound: dict[str, int] = {p.slug: 0 for p in pages}
    for p in pages:
        links = p.metadata.get("links", [])
        if isinstance(links, list):
            for link in links:
                s = str(link).strip().lower()
                if s in inbound:
                    inbound[s] += 1
    return sorted([slug for slug, count in inbound.items() if count == 0])


def _conflict_candidates(pages: list[WikiPage]) -> list[tuple[WikiPage, WikiPage]]:
    out: list[tuple[WikiPage, WikiPage]] = []
    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            a = pages[i]
            b = pages[j]
            if _same_topic(a, b) and _different_numeric_claim(a, b):
                out.append((a, b))
    return out


def _same_topic(a: WikiPage, b: WikiPage) -> bool:
    ta = set(_topic_terms(a.title + " " + str(a.metadata.get("summary", ""))))
    tb = set(_topic_terms(b.title + " " + str(b.metadata.get("summary", ""))))
    return len(ta.intersection(tb)) >= 2


def _topic_terms(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    stop = {"with", "from", "that", "this", "have", "will", "into", "should"}
    return [w for w in words if w not in stop]


def _different_numeric_claim(a: WikiPage, b: WikiPage) -> bool:
    nums_a = re.findall(r"\b\d+(?:\.\d+)?\b", a.body + " " + str(a.metadata.get("summary", "")))
    nums_b = re.findall(r"\b\d+(?:\.\d+)?\b", b.body + " " + str(b.metadata.get("summary", "")))
    if not nums_a or not nums_b:
        return False
    return nums_a[0] != nums_b[0]


def _resolve_conflict(a: WikiPage, b: WikiPage, router: SupportsChat) -> dict[str, str | int]:
    system = (
        "You are ConflictJudge. Decide winner between two pages.\n"
        "Return STRICT JSON: {\"winner\":\"slug-a or slug-b or none\"}."
    )
    user = (
        f"slug-a={a.slug}\nsummary-a={a.metadata.get('summary','')}\nbody-a={a.body[:1200]}\n\n"
        f"slug-b={b.slug}\nsummary-b={b.metadata.get('summary','')}\nbody-b={b.body[:1200]}\n"
    )
    resp = router.chat(
        Tier.HEAVY,
        system=system,
        user=user,
        temperature=0.0,
        max_tokens=120,
    )
    winner = _parse_winner(resp.text, a.slug, b.slug)
    loser = ""
    if winner == a.slug:
        loser = b.slug
    elif winner == b.slug:
        loser = a.slug
    return {
        "winner": winner,
        "loser": loser,
        "model": str(getattr(resp, "model", "unknown")),
        "prompt_tokens": int(getattr(resp, "prompt_tokens", 0)),
        "completion_tokens": int(getattr(resp, "completion_tokens", 0)),
    }


def _parse_winner(text: str, slug_a: str, slug_b: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return "none"
    if not isinstance(obj, dict):
        return "none"
    winner = str(obj.get("winner", "none")).strip().lower()
    if winner in {slug_a, slug_b}:
        return winner
    return "none"

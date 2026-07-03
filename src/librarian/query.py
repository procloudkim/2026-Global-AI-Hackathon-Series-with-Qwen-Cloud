"""Query pipeline: index-first retrieval -> top-K context -> cited answer."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Protocol

from .llm import Tier
from .prompts import QUERY_HEAVY_SYSTEM_PREFIX, QUERY_LIGHT_SYSTEM_PREFIX, PROMPT_VERSION
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
class QueryResult:
    answer: str
    citations: list[str]
    confidence: float
    route: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_version: str


def answer_question(
    *,
    question: str,
    store: MemoryStore,
    router: SupportsChat,
    top_k: int = 5,
    confidence_threshold: float = 0.72,
) -> QueryResult:
    selected = select_top_k_pages(store, question, k=top_k)
    if not selected:
        return QueryResult(
            answer="I do not have enough memory pages yet. Please ingest more sources.",
            citations=[],
            confidence=0.0,
            route="none",
            model="none",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_version=PROMPT_VERSION,
        )

    payload = _build_context_payload(question, selected)
    light_resp = router.chat(
        Tier.LIGHT,
        system=QUERY_LIGHT_SYSTEM_PREFIX,
        user=payload,
        temperature=0.1,
        max_tokens=500,
    )
    light_parsed = _parse_query_json(light_resp.text)
    light_conf = float(light_parsed.get("confidence", 0.0))

    if light_conf >= confidence_threshold and light_parsed["citations"]:
        return QueryResult(
            answer=light_parsed["answer"],
            citations=_clean_citations(light_parsed["citations"], selected),
            confidence=light_conf,
            route=Tier.LIGHT.value,
            model=str(getattr(light_resp, "model", "unknown")),
            prompt_tokens=int(getattr(light_resp, "prompt_tokens", 0)),
            completion_tokens=int(getattr(light_resp, "completion_tokens", 0)),
            total_tokens=int(getattr(light_resp, "total_tokens", 0)),
            prompt_version=PROMPT_VERSION,
        )

    heavy_resp = router.chat(
        Tier.HEAVY,
        system=QUERY_HEAVY_SYSTEM_PREFIX,
        user=payload,
        temperature=0.2,
        max_tokens=700,
    )
    heavy_parsed = _parse_query_json(heavy_resp.text)
    return QueryResult(
        answer=heavy_parsed["answer"],
        citations=_clean_citations(heavy_parsed["citations"], selected),
        confidence=float(heavy_parsed.get("confidence", 0.0)),
        route=f"{Tier.LIGHT.value}->{Tier.HEAVY.value}",
        model=str(getattr(heavy_resp, "model", "unknown")),
        prompt_tokens=int(getattr(light_resp, "prompt_tokens", 0))
        + int(getattr(heavy_resp, "prompt_tokens", 0)),
        completion_tokens=int(getattr(light_resp, "completion_tokens", 0))
        + int(getattr(heavy_resp, "completion_tokens", 0)),
        total_tokens=int(getattr(light_resp, "total_tokens", 0))
        + int(getattr(heavy_resp, "total_tokens", 0)),
        prompt_version=PROMPT_VERSION,
    )


def select_top_k_pages(store: MemoryStore, question: str, k: int = 5) -> list[WikiPage]:
    terms = _extract_terms(question)
    scored: list[tuple[int, WikiPage]] = []
    for page in store.list_wiki_pages():
        summary = str(page.metadata.get("summary", ""))
        haystack = f"{page.title}\n{summary}\n{page.body}".lower()
        score = 0
        for t in terms:
            if t in haystack:
                score += 3
        if page.slug in question.lower():
            score += 5
        if score > 0:
            scored.append((score, page))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:k]]


def _extract_terms(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    stop = {"what", "when", "where", "which", "that", "with", "from", "about"}
    return [w for w in words if w not in stop]


def _build_context_payload(question: str, pages: list[WikiPage]) -> str:
    sections: list[str] = [f"Question:\n{question}\n", "Context pages:"]
    for p in pages:
        summary = str(p.metadata.get("summary", ""))
        sections.append(
            f"\n[slug={p.slug}] title={p.title}\nsummary={summary}\nbody={p.body[:1500]}"
        )
    sections.append(
        "\nReturn only JSON with answer, citations, confidence. "
        "Use citation slugs from provided pages only."
    )
    return "\n".join(sections)


def _parse_query_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("query output must be JSON object")
    if "answer" not in data or not isinstance(data["answer"], str):
        raise ValueError("query output missing answer")
    citations = data.get("citations", [])
    if not isinstance(citations, list):
        raise ValueError("query output citations must be array")
    data["citations"] = [str(c) for c in citations if str(c).strip()]
    conf = data.get("confidence", 0.0)
    try:
        data["confidence"] = float(conf)
    except (ValueError, TypeError) as e:
        raise ValueError("query confidence must be numeric") from e
    return data


def _clean_citations(citations: list[str], pages: list[WikiPage]) -> list[str]:
    allowed = {p.slug for p in pages}
    out: list[str] = []
    for c in citations:
        slug = c.strip().lower()
        if slug in allowed and slug not in out:
            out.append(slug)
    return out


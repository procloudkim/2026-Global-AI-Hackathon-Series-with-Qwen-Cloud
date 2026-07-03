"""Ingest pipeline: raw source -> wiki page + index/log refresh."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from .llm import Tier
from .prompts import INGEST_SYSTEM_PREFIX, PROMPT_VERSION
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
class IngestResult:
    page: WikiPage
    source_path: str
    prompt_version: str
    route_tier: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def ingest_source(
    *,
    source_id: str,
    source_text: str,
    store: MemoryStore,
    router: SupportsChat,
) -> IngestResult:
    source_path = store.save_raw_source(source_id, source_text)
    index_snapshot = store.index_path.read_text(encoding="utf-8")
    user_payload = (
        "Source ID:\n"
        f"{source_id}\n\n"
        "Current wiki index snapshot:\n"
        f"{index_snapshot[:2000]}\n\n"
        "Raw source:\n"
        f"{source_text[:12000]}"
    )
    resp = router.chat(
        Tier.LIGHT,
        system=INGEST_SYSTEM_PREFIX,
        user=user_payload,
        temperature=0.2,
        max_tokens=700,
    )
    payload = _parse_ingest_json(resp.text)

    title = payload["title"].strip() or source_id
    summary = payload["summary"].strip()
    body = payload["body"].strip() or summary
    links = _as_string_list(payload.get("links"))
    tags = _as_string_list(payload.get("tags"))

    page = store.upsert_wiki_page(
        title=title,
        body=body,
        metadata={
            "summary": summary,
            "links": links,
            "tags": tags,
            "sources": [source_id],
            "prompt_version": PROMPT_VERSION,
        },
    )
    store.append_log("ingest", f"{source_id} -> {page.slug}")
    return IngestResult(
        page=page,
        source_path=str(source_path),
        prompt_version=PROMPT_VERSION,
        route_tier=Tier.LIGHT.value,
        model=str(getattr(resp, "model", "unknown")),
        prompt_tokens=int(getattr(resp, "prompt_tokens", 0)),
        completion_tokens=int(getattr(resp, "completion_tokens", 0)),
        total_tokens=int(getattr(resp, "total_tokens", 0)),
    )


def _parse_ingest_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"ingest output is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("ingest output must be a JSON object")
    for required in ("title", "summary", "body"):
        if required not in data or not isinstance(data[required], str):
            raise ValueError(f"missing required field: {required}")
    return data


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append(stripped)
    return out

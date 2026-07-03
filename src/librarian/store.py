"""Persistent memory store for Librarian.

Implements the Track 1 storage layer with:
- raw/ immutable sources
- wiki/ managed markdown pages (+ index.md, log.md, graph.json)
- archive/ forgotten pages
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import re
from typing import Any

import yaml

_FRONTMATTER_DELIM = "---"


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
        self.index_path = self.wiki_dir / "index.md"
        self.log_path = self.wiki_dir / "log.md"
        self.graph_path = self.wiki_dir / "graph.json"
        self.ensure_layout()

    def ensure_layout(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text(
                "# Memory Index\n\n| slug | title | updated_at | summary |\n"
                "|---|---|---|---|\n",
                encoding="utf-8",
            )
        if not self.log_path.exists():
            self.log_path.write_text("# Memory Log\n\n", encoding="utf-8")
        if not self.graph_path.exists():
            self.graph_path.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")

    def save_raw_source(self, source_id: str, content: str) -> Path:
        path = self.raw_dir / f"{self._slugify(source_id)}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def upsert_wiki_page(
        self,
        title: str,
        body: str,
        *,
        slug: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WikiPage:
        page_slug = slug or self._slugify(title)
        page_path = self.wiki_dir / f"{page_slug}.md"
        now = datetime.now(UTC).isoformat()

        existing_meta: dict[str, Any] = {}
        if page_path.exists():
            existing = self.read_wiki_page(page_slug)
            existing_meta = existing.metadata

        merged = {
            **existing_meta,
            **(metadata or {}),
            "title": title,
            "slug": page_slug,
            "updated_at": now,
        }
        text = self._serialize_page(merged, body)
        page_path.write_text(text, encoding="utf-8")

        page = WikiPage(
            slug=page_slug, title=title, body=body, metadata=merged, path=page_path
        )
        self.refresh_index()
        self.refresh_graph()
        return page

    def read_wiki_page(self, slug: str) -> WikiPage:
        page_path = self.wiki_dir / f"{slug}.md"
        if not page_path.exists():
            raise FileNotFoundError(f"wiki page not found: {slug}")
        raw = page_path.read_text(encoding="utf-8")
        metadata, body = self._parse_page(raw)
        title = str(metadata.get("title", slug))
        return WikiPage(slug=slug, title=title, body=body, metadata=metadata, path=page_path)

    def delete_wiki_page(self, slug: str) -> None:
        page_path = self.wiki_dir / f"{slug}.md"
        if page_path.exists():
            page_path.unlink()
            self.refresh_index()
            self.refresh_graph()

    def archive_page(self, slug: str, reason: str) -> Path:
        page = self.read_wiki_page(slug)
        target = self.archive_dir / f"{slug}.md"
        target.write_text(page.path.read_text(encoding="utf-8"), encoding="utf-8")
        self.delete_wiki_page(slug)
        self.append_log("forget", f"{slug} archived: {reason}")
        return target

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

    def refresh_index(self) -> None:
        lines = [
            "# Memory Index",
            "",
            "| slug | title | updated_at | summary |",
            "|---|---|---|---|",
        ]
        for page in self.list_wiki_pages():
            summary = str(page.metadata.get("summary", "")).replace("|", " ").strip()
            updated = str(page.metadata.get("updated_at", ""))
            lines.append(
                f"| {page.slug} | {page.title} | {updated} | {summary[:120]} |"
            )
        self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def refresh_graph(self) -> None:
        nodes: list[dict[str, str]] = []
        edges: list[dict[str, str]] = []
        pages = self.list_wiki_pages()
        slugs = {p.slug for p in pages}
        for p in pages:
            nodes.append({"id": p.slug, "title": p.title})
            links = p.metadata.get("links", [])
            if isinstance(links, list):
                for target in links:
                    target_slug = self._slugify(str(target))
                    if target_slug in slugs:
                        edges.append({"from": p.slug, "to": target_slug})
        graph_obj = {"nodes": nodes, "edges": edges}
        self.graph_path.write_text(json.dumps(graph_obj, ensure_ascii=False), encoding="utf-8")

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
        lowered = text.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
        return slug or "untitled"

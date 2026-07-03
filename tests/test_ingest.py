from pathlib import Path

from librarian.ingest import ingest_source
from librarian.store import MemoryStore


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeRouter:
    def chat(self, *args, **kwargs):  # compatible with SupportsChat
        return _FakeResp(
            """{
  "title": "Token Caching",
  "summary": "Prompt prefix reuse improves cache hit.",
  "body": "Keep static policy at the beginning and dynamic values at the end.",
  "links": ["memory-store"],
  "tags": ["token", "cache"]
}"""
        )


def test_ingest_source_creates_wiki_page_and_log(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    result = ingest_source(
        source_id="doc-001",
        source_text="Token caching and prompt structure research.",
        store=store,
        router=_FakeRouter(),
    )
    assert result.page.slug == "token-caching"
    assert (store.raw_dir / "doc-001.md").exists()
    page = store.read_wiki_page("token-caching")
    assert page.metadata["summary"] == "Prompt prefix reuse improves cache hit."
    assert page.metadata["links"] == ["memory-store"]
    assert page.metadata["tags"] == ["token", "cache"]
    assert page.metadata["sources"] == ["doc-001"]
    assert "doc-001 -> token-caching" in store.log_path.read_text(encoding="utf-8")

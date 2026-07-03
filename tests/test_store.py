from pathlib import Path

from librarian.store import MemoryStore


def test_store_layout_and_crud(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    assert store.raw_dir.exists()
    assert store.wiki_dir.exists()
    assert store.archive_dir.exists()
    assert store.index_path.exists()
    assert store.log_path.exists()
    assert store.graph_path.exists()

    store.save_raw_source("source 1", "# source content")
    assert (store.raw_dir / "source-1.md").exists()

    created = store.upsert_wiki_page(
        "Page A",
        "Body A",
        metadata={"summary": "summary A", "links": ["page-b"]},
    )
    assert created.slug == "page-a"
    loaded = store.read_wiki_page("page-a")
    assert loaded.title == "Page A"
    assert loaded.body.strip() == "Body A"
    assert loaded.metadata["summary"] == "summary A"

    store.upsert_wiki_page("Page B", "Body B", metadata={"summary": "summary B"})
    pages = store.list_wiki_pages()
    assert {p.slug for p in pages} == {"page-a", "page-b"}

    index_text = store.index_path.read_text(encoding="utf-8")
    assert "page-a" in index_text
    assert "summary A" in index_text
    assert "page-b" in index_text

    graph_text = store.graph_path.read_text(encoding="utf-8")
    assert '"from": "page-a"' in graph_text
    assert '"to": "page-b"' in graph_text

    archived = store.archive_page("page-a", "stale")
    assert archived.exists()
    assert not (store.wiki_dir / "page-a.md").exists()
    log_text = store.log_path.read_text(encoding="utf-8")
    assert "page-a archived: stale" in log_text


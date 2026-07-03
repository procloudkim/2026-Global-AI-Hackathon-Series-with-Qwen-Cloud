from pathlib import Path

from librarian.forget import run_lint
from librarian.store import MemoryStore


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "qwen-plus"
        self.prompt_tokens = 12
        self.completion_tokens = 6


class _Router:
    def __init__(self, winner: str) -> None:
        self.winner = winner

    def chat(self, *args, **kwargs):
        return _Resp(f'{{"winner":"{self.winner}"}}')


def _seed_conflict_store(tmp_path: Path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory")
    store.upsert_wiki_page(
        "API Price Policy Legacy",
        "The API price is 100 per month.",
        metadata={"summary": "API price policy old value 100", "links": []},
    )
    store.upsert_wiki_page(
        "API Price Policy Current",
        "The API price is 80 per month.",
        metadata={"summary": "API price policy new value 80", "links": []},
    )
    return store


def test_lint_archives_conflicting_loser(tmp_path: Path) -> None:
    store = _seed_conflict_store(tmp_path)
    result = run_lint(
        store=store,
        router=_Router("api-price-policy-current"),
        apply_archive=True,
    )
    assert "api-price-policy-legacy" in result.archived_pages
    assert (store.archive_dir / "api-price-policy-legacy.md").exists()
    assert not (store.wiki_dir / "api-price-policy-legacy.md").exists()


def test_lint_detects_without_archive_when_disabled(tmp_path: Path) -> None:
    store = _seed_conflict_store(tmp_path)
    result = run_lint(
        store=store,
        router=_Router("api-price-policy-current"),
        apply_archive=False,
    )
    assert result.archived_pages == []
    assert (store.wiki_dir / "api-price-policy-legacy.md").exists()
    assert any(f.finding_type == "conflict" for f in result.findings)


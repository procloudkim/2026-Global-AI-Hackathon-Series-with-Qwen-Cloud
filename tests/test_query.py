from pathlib import Path

from librarian.query import answer_question, select_top_k_pages
from librarian.store import MemoryStore


class _Resp:
    def __init__(
        self,
        text: str,
        model: str = "fake",
        prompt_tokens: int = 10,
        completion_tokens: int = 5,
    ) -> None:
        self.text = text
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _RouterLightOnly:
    def chat(self, *args, **kwargs):
        return _Resp(
            '{"answer":"Use static prefix for cache.","citations":["prompt-caching"],"confidence":0.9}',
            model="qwen-flash",
        )


class _RouterEscalate:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Resp(
                '{"answer":"Unsure","citations":[],"confidence":0.2}',
                model="qwen-flash",
            )
        return _Resp(
            '{"answer":"Index-first + static prefix.","citations":["prompt-caching","index-strategy"],"confidence":0.82}',
            model="qwen-plus",
            prompt_tokens=20,
            completion_tokens=8,
        )


def _seed_store(tmp_path: Path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory")
    store.upsert_wiki_page(
        "Prompt Caching",
        "Static prefix and dynamic suffix improve cache hit.",
        metadata={"summary": "Cache best practices", "links": ["index-strategy"]},
    )
    store.upsert_wiki_page(
        "Index Strategy",
        "Index-first retrieval avoids full reads.",
        metadata={"summary": "Top-k strategy"},
    )
    return store


def test_select_top_k_pages(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    selected = select_top_k_pages(store, "How to improve prompt cache with index strategy?", k=2)
    assert len(selected) == 2
    assert selected[0].slug in {"prompt-caching", "index-strategy"}


def test_query_uses_light_when_confident(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    result = answer_question(
        question="How should I optimize prompt caching?",
        store=store,
        router=_RouterLightOnly(),
        top_k=5,
    )
    assert result.route == "light"
    assert result.model == "qwen-flash"
    assert result.citations == ["prompt-caching"]


def test_query_escalates_to_heavy(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    router = _RouterEscalate()
    result = answer_question(
        question="Explain reliable token minimization strategy.",
        store=store,
        router=router,
        top_k=5,
    )
    assert result.route == "light->heavy"
    assert result.model == "qwen-plus"
    assert "index-strategy" in result.citations
    assert result.total_tokens > 0

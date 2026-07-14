from __future__ import annotations

from types import SimpleNamespace

import pytest

from librarian import llm


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
        )


class _FakeClient:
    def __init__(self, **kwargs) -> None:
        self.options = kwargs
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def _settings(**overrides):
    values = {
        "api_key": "masked-test-key",
        "base_url": "https://example.invalid/v1",
        "light_model": "qwen-light-test",
        "heavy_model": "qwen-heavy-test",
        "request_timeout_seconds": 17.0,
        "max_retries": 1,
        "max_completion_tokens": 64,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_router_applies_bounded_transport_and_completion_budget(monkeypatch) -> None:
    monkeypatch.setattr(llm, "get_settings", _settings)
    monkeypatch.setattr(llm, "OpenAI", _FakeClient)

    router = llm.ModelRouter()
    result = router.chat(
        llm.Tier.LIGHT,
        system="system",
        user="user",
        temperature=0.0,
        max_tokens=8,
    )

    assert router._client.options["timeout"] == 17.0
    assert router._client.options["max_retries"] == 1
    assert router._client.completions.calls[0]["max_tokens"] == 8
    assert result.text == "pong"
    assert result.total_tokens == 4


@pytest.mark.parametrize("max_tokens", [None, 0, 65])
def test_router_rejects_missing_or_out_of_budget_completion_cap(
    monkeypatch,
    max_tokens,
) -> None:
    monkeypatch.setattr(llm, "get_settings", _settings)
    monkeypatch.setattr(llm, "OpenAI", _FakeClient)
    router = llm.ModelRouter()

    with pytest.raises(ValueError, match="max_tokens"):
        router.chat(
            llm.Tier.LIGHT,
            system="system",
            user="user",
            max_tokens=max_tokens,
        )
    assert router._client.completions.calls == []

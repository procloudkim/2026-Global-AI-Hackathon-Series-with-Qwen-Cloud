from __future__ import annotations

import pytest

from librarian.config import get_memory_root, get_settings


def _provider_env(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "masked-test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://example.invalid/v1")


def test_provider_limits_are_bounded_and_models_are_explicit(monkeypatch) -> None:
    _provider_env(monkeypatch)
    monkeypatch.delenv("LIBRARIAN_LIGHT_MODEL", raising=False)
    monkeypatch.delenv("LIBRARIAN_HEAVY_MODEL", raising=False)
    monkeypatch.setenv("LIBRARIAN_QWEN_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("LIBRARIAN_QWEN_MAX_RETRIES", "0")
    monkeypatch.setenv("LIBRARIAN_QWEN_MAX_COMPLETION_TOKENS", "1400")

    settings = get_settings()

    assert settings.light_model == "qwen-flash"
    assert settings.heavy_model == "qwen-plus-2025-07-28"
    assert settings.request_timeout_seconds == 12.5
    assert settings.max_retries == 0
    assert settings.max_completion_tokens == 1400


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LIBRARIAN_QWEN_TIMEOUT_SECONDS", "0"),
        ("LIBRARIAN_QWEN_MAX_RETRIES", "3"),
        ("LIBRARIAN_QWEN_MAX_COMPLETION_TOKENS", "99999"),
    ],
)
def test_invalid_provider_limits_fail_before_client_creation(
    monkeypatch,
    name: str,
    value: str,
) -> None:
    _provider_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        get_settings()


def test_memory_root_does_not_require_provider_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("LIBRARIAN_MEMORY_ROOT", str(tmp_path / "persistent"))

    assert get_memory_root() == tmp_path / "persistent"

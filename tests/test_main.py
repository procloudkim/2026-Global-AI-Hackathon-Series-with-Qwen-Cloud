from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from librarian import main
from librarian.meter import RunLedger


@dataclass
class _Response:
    text: str
    model: str = "qwen-test"
    prompt_tokens: int = 3
    completion_tokens: int = 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class _Router:
    def __init__(self, text: str = "pong") -> None:
        self.text = text
        self.calls: list[dict] = []

    def chat(self, tier, system, user, temperature=0.3, max_tokens=None):
        self.calls.append(
            {
                "tier": tier,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return _Response(self.text)


def test_process_health_never_calls_qwen(monkeypatch) -> None:
    router = _Router()
    monkeypatch.setattr(main, "get_router", lambda: router)
    monkeypatch.setattr(main, "get_deployed_sha", lambda: "abc123")

    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json()["deployed_sha"] == "abc123"
    assert router.calls == []


def test_public_rate_limiter_is_bounded_and_recovers_after_window() -> None:
    limiter = main._FixedWindowRateLimiter(2, window_seconds=60.0)

    assert limiter.allow("client-a", now=100.0) is True
    assert limiter.allow("client-a", now=101.0) is True
    assert limiter.allow("client-a", now=102.0) is False
    assert limiter.allow("client-b", now=102.0) is True
    assert limiter.allow("client-a", now=161.0) is True


def test_qwen_health_is_disabled_without_release_token(monkeypatch) -> None:
    router = _Router()
    monkeypatch.setattr(main, "get_router", lambda: router)
    monkeypatch.setattr(main, "get_qwen_health_token", lambda: "")

    response = TestClient(main.app).get("/health/qwen")

    assert response.status_code == 404
    assert router.calls == []


def test_qwen_health_requires_token_and_exact_pong(monkeypatch, tmp_path) -> None:
    router = _Router()
    monkeypatch.setattr(main, "get_router", lambda: router)
    monkeypatch.setattr(main, "get_qwen_health_token", lambda: "release-token")
    monkeypatch.setattr(main, "ledger", RunLedger(tmp_path / "runs.jsonl"))
    client = TestClient(main.app)

    unauthorized = client.get("/health/qwen")
    passed = client.get(
        "/health/qwen",
        headers={"X-Librarian-Health-Token": "release-token"},
    )

    assert unauthorized.status_code == 404
    assert passed.status_code == 200
    assert passed.json()["reply"] == "pong"
    assert router.calls == [
        {"tier": main.Tier.LIGHT, "temperature": 0.0, "max_tokens": 8}
    ]


def test_qwen_health_fails_closed_on_non_exact_reply(monkeypatch, tmp_path) -> None:
    router = _Router("PONG")
    monkeypatch.setattr(main, "get_router", lambda: router)
    monkeypatch.setattr(main, "get_qwen_health_token", lambda: "release-token")
    monkeypatch.setattr(main, "ledger", RunLedger(tmp_path / "runs.jsonl"))

    response = TestClient(main.app).get(
        "/health/qwen",
        headers={"X-Librarian-Health-Token": "release-token"},
    )

    assert response.status_code == 503
    assert "exact pong" in response.json()["detail"]
    assert main.ledger.summary()["failures"] == 1

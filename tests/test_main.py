from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

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


def test_query_forwards_explicit_as_of(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_answer_question(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            answer="No supported memory.",
            facts=[],
            citations=[],
            evidence_claim_ids=[],
            evidence_source_ids=[],
            confidence=0.0,
            abstained=True,
            route="none",
            model="none",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_version="v4",
            trace={},
        )

    monkeypatch.setattr(main, "answer_question", fake_answer_question)
    monkeypatch.setattr(main, "get_router", lambda: object())
    monkeypatch.setattr(main, "ledger", RunLedger(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(main, "rate_limiter", main._FixedWindowRateLimiter(1000))

    response = TestClient(main.app).post(
        "/query",
        json={
            "question": "What was the quota?",
            "top_k": 2,
            "as_of": "2026-07-14T00:00:00Z",
        },
    )

    assert response.status_code == 200
    assert captured["as_of"] == "2026-07-14T00:00:00Z"
    assert captured["top_k"] == 2


def test_query_forwards_bitemporal_cutoffs(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_answer_question(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            answer="No supported memory.",
            facts=[],
            citations=[],
            evidence_claim_ids=[],
            evidence_source_ids=[],
            confidence=0.0,
            abstained=True,
            route="none",
            model="none",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_version="v4",
            trace={},
        )

    monkeypatch.setattr(main, "answer_question", fake_answer_question)
    monkeypatch.setattr(main, "get_router", lambda: object())
    monkeypatch.setattr(main, "ledger", RunLedger(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(main, "rate_limiter", main._FixedWindowRateLimiter(1000))

    response = TestClient(main.app).post(
        "/query",
        json={
            "question": "What was the quota?",
            "valid_at": "2026-07-05T00:00:00Z",
            "known_at": "2026-07-10T00:00:00Z",
        },
    )

    assert response.status_code == 200
    assert captured["as_of"] is None
    assert captured["valid_at"] == "2026-07-05T00:00:00Z"
    assert captured["known_at"] == "2026-07-10T00:00:00Z"


def test_query_rejects_naive_as_of_before_model_call(monkeypatch, tmp_path) -> None:
    router = _Router()
    monkeypatch.setattr(main, "get_router", lambda: router)
    monkeypatch.setattr(main, "ledger", RunLedger(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(main, "rate_limiter", main._FixedWindowRateLimiter(1000))

    response = TestClient(main.app).post(
        "/query",
        json={
            "question": "What was the quota?",
            "as_of": "2026-07-14T00:00:00",
        },
    )

    assert response.status_code == 422
    assert "timezone offset" in response.json()["detail"]
    assert router.calls == []


def test_query_rejects_partial_bitemporal_cutoff_before_model_call(
    monkeypatch, tmp_path
) -> None:
    router = _Router()
    monkeypatch.setattr(main, "get_router", lambda: router)
    monkeypatch.setattr(main, "ledger", RunLedger(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(main, "rate_limiter", main._FixedWindowRateLimiter(1000))

    response = TestClient(main.app).post(
        "/query",
        json={
            "question": "What was the quota?",
            "valid_at": "2026-07-05T00:00:00Z",
        },
    )

    assert response.status_code == 422
    assert "must be provided together" in response.json()["detail"]
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


def test_ingest_response_exposes_provider_usage(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(
        source_path="memory/raw/source-a.txt",
        page=SimpleNamespace(
            slug="api-policy",
            title="API Policy",
            path=tmp_path / "memory" / "wiki" / "api-policy.md",
        ),
        prompt_version="v4",
        route_tier="light",
        model="qwen-plus",
        prompt_tokens=41,
        completion_tokens=9,
        total_tokens=50,
        claim_ids=["claim-a"],
        transition_events=[],
        trace={"extracted_claims": 1},
    )
    monkeypatch.setattr(main, "ingest_source", lambda **_kwargs: result)
    monkeypatch.setattr(main, "get_router", lambda: object())
    monkeypatch.setattr(main, "ledger", RunLedger(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(main, "rate_limiter", main._FixedWindowRateLimiter(1000))

    response = TestClient(main.app).post(
        "/ingest",
        json={"source_id": "source-a", "text": "The quota is 100."},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "qwen-plus"
    assert response.json()["tokens"] == {
        "prompt": 41,
        "completion": 9,
        "total": 50,
    }

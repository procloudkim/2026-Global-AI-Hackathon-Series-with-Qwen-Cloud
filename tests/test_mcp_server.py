from __future__ import annotations

import importlib
from types import SimpleNamespace

import librarian.mcp_server as mcp_server
from librarian.meter import RunLedger


def test_mcp_server_module_loads_without_runtime_import() -> None:
    stats = mcp_server.memory_stats_impl()
    assert stats["status"] == "ok"
    assert "ledger" in stats
    assert stats["claim_history"]["schema_version"] == (
        "librarian-claim-revision/v1"
    )


def test_mcp_server_uses_configured_persistent_root(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "persistent"
    with monkeypatch.context() as scoped:
        scoped.setenv("LIBRARIAN_MEMORY_ROOT", str(configured))
        reloaded = importlib.reload(mcp_server)
        assert reloaded.store.base == configured
        assert reloaded.ledger.path == configured / "runs.jsonl"
    importlib.reload(mcp_server)


def test_mcp_query_forwards_explicit_as_of(monkeypatch, tmp_path) -> None:
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
            trace={},
        )

    monkeypatch.setattr(mcp_server, "answer_question", fake_answer_question)
    monkeypatch.setattr(mcp_server, "get_router", lambda: object())
    monkeypatch.setattr(
        mcp_server,
        "ledger",
        RunLedger(tmp_path / "runs.jsonl"),
    )

    result = mcp_server.memory_query_impl(
        "What was the quota?",
        top_k=2,
        as_of="2026-07-14T00:00:00Z",
    )

    assert result["status"] == "ok"
    assert captured["as_of"] == "2026-07-14T00:00:00Z"
    assert captured["top_k"] == 2


def test_mcp_query_forwards_bitemporal_cutoffs(monkeypatch, tmp_path) -> None:
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
            trace={},
        )

    monkeypatch.setattr(mcp_server, "answer_question", fake_answer_question)
    monkeypatch.setattr(mcp_server, "get_router", lambda: object())
    monkeypatch.setattr(
        mcp_server,
        "ledger",
        RunLedger(tmp_path / "runs.jsonl"),
    )

    result = mcp_server.memory_query_impl(
        "What was the quota?",
        2,
        None,
        "2026-07-05T00:00:00Z",
        "2026-07-10T00:00:00Z",
    )

    assert result["status"] == "ok"
    assert captured["as_of"] is None
    assert captured["valid_at"] == "2026-07-05T00:00:00Z"
    assert captured["known_at"] == "2026-07-10T00:00:00Z"


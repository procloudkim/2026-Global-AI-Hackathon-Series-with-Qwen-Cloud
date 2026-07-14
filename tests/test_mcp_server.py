from __future__ import annotations

import importlib

import librarian.mcp_server as mcp_server


def test_mcp_server_module_loads_without_runtime_import() -> None:
    stats = mcp_server.memory_stats_impl()
    assert stats["status"] == "ok"
    assert "ledger" in stats


def test_mcp_server_uses_configured_persistent_root(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "persistent"
    with monkeypatch.context() as scoped:
        scoped.setenv("LIBRARIAN_MEMORY_ROOT", str(configured))
        reloaded = importlib.reload(mcp_server)
        assert reloaded.store.base == configured
        assert reloaded.ledger.path == configured / "runs.jsonl"
    importlib.reload(mcp_server)


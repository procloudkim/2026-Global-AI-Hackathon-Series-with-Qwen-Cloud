import librarian.mcp_server as mcp_server


def test_mcp_server_module_loads_without_runtime_import() -> None:
    stats = mcp_server.memory_stats_impl()
    assert stats["status"] == "ok"
    assert "ledger" in stats


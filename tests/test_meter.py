from pathlib import Path

from librarian.meter import RunEvent, RunLedger, now_iso


def test_run_ledger_append_and_summary(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path / "runs.jsonl")
    ledger.append(
        RunEvent(
            ts=now_iso(),
            task_type="query",
            route_tier="light",
            model="qwen-flash",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=100,
            success=True,
        )
    )
    ledger.append(
        RunEvent(
            ts=now_iso(),
            task_type="query",
            route_tier="heavy",
            model="qwen-plus",
            prompt_tokens=20,
            completion_tokens=8,
            total_tokens=28,
            latency_ms=200,
            success=False,
            error="x",
        )
    )
    summary = ledger.summary()
    assert summary["requests"] == 2
    assert summary["successes"] == 1
    assert summary["failures"] == 1
    assert summary["tokens"]["prompt"] == 30
    assert summary["tokens"]["completion"] == 13
    assert summary["tokens"]["total"] == 43
    assert summary["avg_latency_ms"] == 150
    assert summary["by_tier"]["light"] == 1
    assert summary["by_tier"]["heavy"] == 1


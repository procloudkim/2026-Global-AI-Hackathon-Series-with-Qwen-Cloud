"""Run ledger for token/cost observability."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunEvent:
    ts: str
    task_type: str
    route_tier: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    success: bool
    error: str | None = None
    details: dict[str, Any] | None = None


class RunLedger:
    def __init__(self, path: str | Path = "memory/runs.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, event: RunEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def summary(self) -> dict[str, Any]:
        rows = list(self._rows())
        total = len(rows)
        successes = sum(1 for r in rows if bool(r.get("success")))
        prompt_tokens = sum(int(r.get("prompt_tokens", 0)) for r in rows)
        completion_tokens = sum(int(r.get("completion_tokens", 0)) for r in rows)
        total_tokens = sum(int(r.get("total_tokens", 0)) for r in rows)
        latency_ms = sum(int(r.get("latency_ms", 0)) for r in rows)
        by_tier: dict[str, int] = {}
        trace_totals: dict[str, int] = {}
        for r in rows:
            tier = str(r.get("route_tier", "unknown"))
            by_tier[tier] = by_tier.get(tier, 0) + 1
            details = r.get("details")
            if isinstance(details, dict):
                for key in (
                    "corpus_pages",
                    "candidate_pages",
                    "loaded_pages",
                    "active_claims_loaded",
                    "disputed_claims_loaded",
                    "superseded_claims_filtered",
                    "context_tokens",
                ):
                    value = details.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        trace_totals[key] = trace_totals.get(key, 0) + value
        return {
            "requests": total,
            "successes": successes,
            "failures": total - successes,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens,
            },
            "avg_latency_ms": int(latency_ms / total) if total else 0,
            "by_tier": by_tier,
            "trace_totals": trace_totals,
        }

    def _rows(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                items.append(parsed)
        return items


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


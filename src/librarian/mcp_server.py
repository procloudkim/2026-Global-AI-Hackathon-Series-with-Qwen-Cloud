"""MCP server exposing Librarian memory tools.

The mcp package is imported lazily to avoid hard import failures on
environments where native deps are unavailable until runtime.
"""
from __future__ import annotations

from time import perf_counter

from .config import get_memory_root
from .forget import run_lint
from .ingest import ingest_source
from .llm import get_router
from .meter import RunEvent, RunLedger, now_iso
from .query import answer_question
from .store import MemoryStore

memory_root = get_memory_root()
store = MemoryStore(memory_root)
ledger = RunLedger(memory_root / "runs.jsonl")


def memory_ingest_impl(source_id: str, text: str) -> dict:
    started = perf_counter()
    result = ingest_source(
        source_id=source_id,
        source_text=text,
        store=store,
        router=get_router(),
    )
    latency_ms = int((perf_counter() - started) * 1000)
    ledger.append(
        RunEvent(
            ts=now_iso(),
            task_type="mcp-ingest",
            route_tier=result.route_tier,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            latency_ms=latency_ms,
            success=True,
            details=result.trace,
        )
    )
    return {
        "status": "ok",
        "source_path": result.source_path,
        "page_slug": result.page.slug,
        "page_title": result.page.title,
        "prompt_version": result.prompt_version,
        "route_tier": result.route_tier,
        "claim_ids": result.claim_ids,
        "transitions": result.transition_events,
        "trace": result.trace,
    }


def memory_query_impl(question: str, top_k: int = 5) -> dict:
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    started = perf_counter()
    result = answer_question(
        question=question,
        store=store,
        router=get_router(),
        top_k=top_k,
    )
    latency_ms = int((perf_counter() - started) * 1000)
    ledger.append(
        RunEvent(
            ts=now_iso(),
            task_type="mcp-query",
            route_tier=result.route,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            latency_ms=latency_ms,
            success=True,
            details={**result.trace, "abstained": int(result.abstained)},
        )
    )
    return {
        "status": "ok",
        "answer": result.answer,
        "facts": result.facts,
        "citations": [f"memory/wiki/{slug}.md" for slug in result.citations],
        "evidence_claim_ids": result.evidence_claim_ids,
        "evidence_source_ids": result.evidence_source_ids,
        "confidence": result.confidence,
        "abstained": result.abstained,
        "route": result.route,
        "trace": result.trace,
        "tokens": {
            "prompt": result.prompt_tokens,
            "completion": result.completion_tokens,
            "total": result.total_tokens,
        },
    }


def memory_lint_impl(apply_archive: bool = True) -> dict:
    started = perf_counter()
    result = run_lint(store=store, router=get_router(), apply_archive=apply_archive)
    latency_ms = int((perf_counter() - started) * 1000)
    ledger.append(
        RunEvent(
            ts=now_iso(),
            task_type="mcp-lint",
            route_tier=result.route_tier,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            latency_ms=latency_ms,
            success=True,
            details={
                "findings": len(result.findings),
                "transitioned_claims": len(result.transitioned_claims),
                "repaired_projections": int(result.repaired_projections),
            },
        )
    )
    return {
        "status": "ok",
        "findings": [
            {
                "type": f.finding_type,
                "page": f.page,
                "message": f.message,
                "archived": f.archived,
                "claim_id": f.claim_id,
                "repaired": f.repaired,
            }
            for f in result.findings
        ],
        "archived_pages": result.archived_pages,
        "archived_claims": result.archived_claims,
        "transitioned_claims": result.transitioned_claims,
        "repaired_projections": result.repaired_projections,
    }


def memory_stats_impl() -> dict:
    return {
        "status": "ok",
        "ledger": ledger.summary(),
        "wiki_pages": len(store.list_wiki_pages()),
    }


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as e:
        raise RuntimeError(
            "MCP runtime import failed. "
            "Install/repair MCP dependencies and retry."
        ) from e

    mcp = FastMCP("librarian-memory")

    @mcp.tool()
    def memory_ingest(source_id: str, text: str) -> dict:
        """Ingest a source text into persistent wiki memory."""
        return memory_ingest_impl(source_id, text)

    @mcp.tool()
    def memory_query(question: str, top_k: int = 5) -> dict:
        """Query memory with index-first top-k retrieval and citations."""
        return memory_query_impl(question, top_k)

    @mcp.tool()
    def memory_lint(apply_archive: bool = True) -> dict:
        """Run lint/forget pass for stale/orphan/conflicting pages."""
        return memory_lint_impl(apply_archive)

    @mcp.tool()
    def memory_stats() -> dict:
        """Return aggregated ledger statistics for memory operations."""
        return memory_stats_impl()

    mcp.run()


if __name__ == "__main__":
    main()


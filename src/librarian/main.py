"""Librarian API — Track 1: MemoryAgent (Qwen Cloud hackathon)."""
from time import perf_counter
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import __version__
from .ingest import ingest_source
from .llm import Tier, get_router
from .meter import RunEvent, RunLedger, now_iso
from .query import answer_question
from .store import MemoryStore

app = FastAPI(title="Librarian", version=__version__)
store = MemoryStore()
ledger = RunLedger()


class IngestRequest(BaseModel):
    source_id: str
    text: str


class QueryRequest(BaseModel):
    question: str
    top_k: int = 5


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.post("/ingest")
def ingest(payload: IngestRequest) -> dict:
    started = perf_counter()
    try:
        result = ingest_source(
            source_id=payload.source_id,
            source_text=payload.text,
            store=store,
            router=get_router(),
        )
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="ingest",
                route_tier=result.route_tier,
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                latency_ms=latency_ms,
                success=True,
            )
        )
    except ValueError as e:
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="ingest",
                route_tier=Tier.LIGHT.value,
                model="unknown",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )
        )
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="ingest",
                route_tier=Tier.LIGHT.value,
                model="unknown",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )
        )
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "status": "ok",
        "source_path": result.source_path,
        "page": {
            "slug": result.page.slug,
            "title": result.page.title,
            "path": str(result.page.path),
        },
        "prompt_version": result.prompt_version,
        "route_tier": result.route_tier,
    }


@app.get("/stats")
def stats() -> dict:
    return {
        "status": "ok",
        "ledger": ledger.summary(),
        "store": {
            "raw_dir": str(store.raw_dir),
            "wiki_pages": len(store.list_wiki_pages()),
            "index_exists": store.index_path.exists(),
            "log_exists": store.log_path.exists(),
        },
    }


@app.post("/query")
def query(payload: QueryRequest) -> dict:
    started = perf_counter()
    try:
        result = answer_question(
            question=payload.question,
            store=store,
            router=get_router(),
            top_k=payload.top_k,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="query",
                route_tier=result.route,
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                latency_ms=latency_ms,
                success=True,
            )
        )
    except ValueError as e:
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="query",
                route_tier="unknown",
                model="unknown",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )
        )
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="query",
                route_tier="unknown",
                model="unknown",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )
        )
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "status": "ok",
        "answer": result.answer,
        "citations": [f"memory/wiki/{s}.md" for s in result.citations],
        "confidence": result.confidence,
        "route": result.route,
        "model": result.model,
        "tokens": {
            "prompt": result.prompt_tokens,
            "completion": result.completion_tokens,
            "total": result.total_tokens,
        },
        "prompt_version": result.prompt_version,
    }


@app.get("/health/qwen")
def health_qwen() -> dict:
    """Round-trip check against Qwen Cloud (DashScope compatible mode)."""
    started = perf_counter()
    try:
        r = get_router().chat(
            Tier.LIGHT,
            system="You are a health check. Reply with exactly: pong",
            user="ping",
        )
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="health-check",
                route_tier=Tier.LIGHT.value,
                model=r.model,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                total_tokens=r.total_tokens,
                latency_ms=latency_ms,
                success=True,
            )
        )
    except Exception as e:  # surface config/network errors clearly
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="health-check",
                route_tier=Tier.LIGHT.value,
                model="unknown",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )
        )
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "status": "ok",
        "model": r.model,
        "reply": r.text.strip()[:40],
        "tokens": {"prompt": r.prompt_tokens, "completion": r.completion_tokens},
    }
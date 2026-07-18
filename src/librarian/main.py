"""Librarian API — Track 1: MemoryAgent (Qwen Cloud hackathon)."""
from collections import deque
import secrets
from threading import Lock
from time import monotonic, perf_counter

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from .config import (
    get_deployed_sha,
    get_memory_root,
    get_qwen_health_token,
    get_rate_limit_per_minute,
)
from .demo_ui import render_demo_home
from .explain import InvalidMemoryExplainRequest, explain_memory
from .forget import run_lint
from .ingest import ingest_source
from .llm import Tier, get_router
from .meter import RunEvent, RunLedger, now_iso
from .query import answer_question
from .store import MemoryStore

app = FastAPI(title="Librarian", version=__version__)
memory_root = get_memory_root()
store = MemoryStore(memory_root)
ledger = RunLedger(memory_root / "runs.jsonl")


class _FixedWindowRateLimiter:
    """Small single-process abuse guard for the one-worker demo runtime."""

    def __init__(self, limit: int, *, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        timestamp = monotonic() if now is None else now
        cutoff = timestamp - self.window_seconds
        with self._lock:
            bucket_key = key
            if key not in self._buckets and len(self._buckets) >= 2048:
                bucket_key = "<overflow>"
            bucket = self._buckets.setdefault(bucket_key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(timestamp)
            return True


rate_limiter = _FixedWindowRateLimiter(get_rate_limit_per_minute())


@app.middleware("http")
async def bounded_public_requests(request: Request, call_next):
    if request.url.path in {"/health", "/health/qwen"}:
        return await call_next(request)
    client_key = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client_key):
        return JSONResponse(
            status_code=429,
            content={"detail": "request rate limit exceeded"},
            headers={"Retry-After": "60"},
        )
    return await call_next(request)


class IngestRequest(BaseModel):
    source_id: str
    text: str


class QueryRequest(BaseModel):
    question: str
    top_k: int = Field(default=3, ge=1, le=10)
    as_of: str | None = None
    valid_at: str | None = None
    known_at: str | None = None


class LintRequest(BaseModel):
    apply_archive: bool = True


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "deployed_sha": get_deployed_sha(),
    }


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return render_demo_home()


@app.get("/memory/explain")
def memory_explain(
    key: str,
    as_of: str | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
) -> dict:
    try:
        return explain_memory(
            store=store,
            key=key,
            as_of=as_of,
            valid_at=valid_at,
            known_at=known_at,
        )
    except InvalidMemoryExplainRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"memory integrity check failed: {exc}",
        ) from exc


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
                details=result.trace,
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
        "model": result.model,
        "tokens": {
            "prompt": result.prompt_tokens,
            "completion": result.completion_tokens,
            "total": result.total_tokens,
        },
        "claim_ids": result.claim_ids,
        "transitions": result.transition_events,
        "trace": result.trace,
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
            "decisions_exists": store.decisions_path.exists(),
            "projection_consistent": store.projection_is_consistent(),
            "claim_history": store.claim_revision_diagnostics(),
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
            as_of=payload.as_of,
            valid_at=payload.valid_at,
            known_at=payload.known_at,
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
                details={**result.trace, "abstained": int(result.abstained)},
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
        "facts": result.facts,
        "citations": [f"memory/wiki/{s}.md" for s in result.citations],
        "evidence_claim_ids": result.evidence_claim_ids,
        "evidence_source_ids": result.evidence_source_ids,
        "confidence": result.confidence,
        "abstained": result.abstained,
        "route": result.route,
        "model": result.model,
        "tokens": {
            "prompt": result.prompt_tokens,
            "completion": result.completion_tokens,
            "total": result.total_tokens,
        },
        "prompt_version": result.prompt_version,
        "trace": result.trace,
    }


@app.post("/lint")
def lint(payload: LintRequest) -> dict:
    started = perf_counter()
    try:
        result = run_lint(
            store=store,
            router=get_router(),
            apply_archive=payload.apply_archive,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="lint",
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
    except Exception as e:
        latency_ms = int((perf_counter() - started) * 1000)
        ledger.append(
            RunEvent(
                ts=now_iso(),
                task_type="lint",
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

    findings = [
        {
            "type": f.finding_type,
            "page": f.page,
            "message": f.message,
            "archived": f.archived,
            "claim_id": f.claim_id,
            "repaired": f.repaired,
        }
        for f in result.findings
    ]
    return {
        "status": "ok",
        "findings": findings,
        "archived_pages": result.archived_pages,
        "archived_claims": result.archived_claims,
        "transitioned_claims": result.transitioned_claims,
        "repaired_projections": result.repaired_projections,
        "tokens": {
            "prompt": result.prompt_tokens,
            "completion": result.completion_tokens,
            "total": result.total_tokens,
        },
        "model": result.model,
    }


@app.get("/health/qwen", include_in_schema=False)
def health_qwen(
    x_librarian_health_token: str | None = Header(
        default=None,
        alias="X-Librarian-Health-Token",
    ),
) -> dict:
    """Round-trip check against Qwen Cloud (DashScope compatible mode)."""
    expected_token = get_qwen_health_token()
    if (
        not expected_token
        or not x_librarian_health_token
        or not secrets.compare_digest(expected_token, x_librarian_health_token)
    ):
        raise HTTPException(status_code=404, detail="not found")
    started = perf_counter()
    try:
        r = get_router().chat(
            Tier.LIGHT,
            system="You are a health check. Reply with exactly: pong",
            user="ping",
            temperature=0.0,
            max_tokens=8,
        )
        reply = r.text.strip()
        if reply != "pong":
            raise ValueError("Qwen health response was not exact pong")
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
        "reply": reply,
        "tokens": {"prompt": r.prompt_tokens, "completion": r.completion_tokens},
    }

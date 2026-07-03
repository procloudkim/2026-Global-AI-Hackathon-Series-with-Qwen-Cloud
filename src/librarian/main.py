"""Librarian API — Track 1: MemoryAgent (Qwen Cloud hackathon)."""
from fastapi import FastAPI, HTTPException

from . import __version__
from .llm import Tier, get_router

app = FastAPI(title="Librarian", version=__version__)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/health/qwen")
def health_qwen() -> dict:
    """Round-trip check against Qwen Cloud (DashScope compatible mode)."""
    try:
        r = get_router().chat(
            Tier.LIGHT,
            system="You are a health check. Reply with exactly: pong",
            user="ping",
        )
    except Exception as e:  # surface config/network errors clearly
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "status": "ok",
        "model": r.model,
        "reply": r.text.strip()[:40],
        "tokens": {"prompt": r.prompt_tokens, "completion": r.completion_tokens},
    }
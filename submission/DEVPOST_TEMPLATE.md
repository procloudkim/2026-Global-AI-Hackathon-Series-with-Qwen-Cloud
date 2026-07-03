# Devpost Submission Template (Track 1: MemoryAgent)

## Project Title
Librarian — The Memory Agent that Maintains Memory

## Elevator Pitch
Librarian is a Qwen-powered memory agent that does not just retrieve context; it continuously maintains a persistent wiki memory, detects stale or conflicting claims, and archives outdated memory with explicit rationale.

## What it does
- Ingests raw sources into persistent wiki pages (`/ingest`)
- Answers with index-first top-k retrieval and citations (`/query`)
- Runs timely forgetting via lint engine (`/lint`)
- Tracks token usage and route behavior in a run ledger (`/stats`, `memory/runs.jsonl`)
- Exposes MCP tools (`memory_ingest`, `memory_query`, `memory_lint`, `memory_stats`)

## How we built it
- Qwen Cloud via DashScope OpenAI-compatible API (`src/librarian/llm.py`)
- FastAPI backend (`src/librarian/main.py`)
- Persistent markdown memory store (`src/librarian/store.py`)
- AI-DLC workflow docs (`aidlc-docs/`)

## Why it is different
- Treats memory as a maintained artifact (LLM Wiki pattern), not one-shot retrieval
- Explicit forget/lint workflow with archive trail
- Token minimization measured via A/B harness (`bench/run_ab.py`), reported in `BENCHMARK.md`:
  - **−47.1%** tokens-per-successful-answer vs full-context reads (L-E1)
  - **−51.3%** vs freeform prompting without output contract (L-E3)
  - **−17.2%** vs heavy-model-only, despite +30% raw tokens — honest reporting of routing overhead (L-E2)
  - Strict success criterion: valid JSON + citations resolving to real wiki pages (candidate 11/12 vs baselines 6–7/12)

## Alibaba Cloud deployment proof
- Deployment scripts: `deploy/setup.sh`, `deploy/deploy.sh`
- Container deployment: `Dockerfile`, `docker-compose.yml`
- Qwen API integration path: `src/librarian/llm.py`
- Live URL: [ADD YOUR ECS/FC URL]

## Architecture diagram
- Source: `submission/architecture.mmd` (details: `aidlc-docs/construction/architecture.md`)
- Image URL: https://raw.githubusercontent.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/main/submission/architecture.png

## Open source repository
- Repo URL: https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud
- License: MIT

## Demo video (under 3 min)
- Script: `submission/VIDEO_SCRIPT.md`
- URL: [ADD YOUTUBE/VIMEO/YOUKU URL — record per script, must show forget/lint demo]

## Optional blog/social post
- URL: [ADD BLOG POST URL]

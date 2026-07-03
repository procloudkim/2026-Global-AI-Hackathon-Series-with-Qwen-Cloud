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
- Token minimization measured via A/B harness (`bench/run_ab.py`) instead of claims

## Alibaba Cloud deployment proof
- Deployment scripts: `deploy/setup.sh`, `deploy/deploy.sh`
- Qwen API integration path: `src/librarian/llm.py`
- Live URL: [ADD YOUR ECS/FC URL]

## Architecture diagram
- Source: `aidlc-docs/construction/architecture.md`
- Image URL: [ADD EXPORTED PNG URL]

## Open source repository
- Repo URL: https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud
- License: MIT

## Demo video (under 3 min)
- URL: [ADD YOUTUBE/VIMEO/YOUKU URL]

## Optional blog/social post
- URL: [ADD BLOG POST URL]


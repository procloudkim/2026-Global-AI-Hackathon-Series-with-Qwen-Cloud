# 2026 Global AI Hackathon Series with Qwen Cloud — Librarian

> **Track 1: MemoryAgent** — An agent that *maintains* its memory instead of merely retrieving it.
> "Writes like a wiki, forgets like a librarian."

## What is this?

Librarian is a persistent-memory agent built on Qwen Cloud. Instead of RAG-style
re-discovery on every query, it incrementally builds and maintains a structured,
interlinked markdown wiki — updating entity pages, flagging contradictions,
and **actively forgetting** stale information via a lint engine.

Key capabilities (Track 1 requirements):
1. **Efficient storage & retrieval** — compounding wiki + link graph, index-first search
2. **Timely forgetting** — lint engine detects stale claims, contradictions, orphans; archives with rationale
3. **Recall within limited context** — surgical context injection (top-K pages only), token metering included

## Status

🚧 In development for the hackathon (deadline 2026-07-09 PT). See `aidlc-docs/` for the
AI-DLC design documents (requirements → units of work → architecture → operations).

## Development methodology

This project is developed following [AI-DLC](https://github.com/awslabs/aidlc-workflows)
(AI-Driven Development Life Cycle), with design docs in `aidlc-docs/`.

Conceptual foundations: [karpathy/llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
[karpathy/autoresearch](https://github.com/karpathy/autoresearch),
[safishamsi/graphify](https://github.com/safishamsi/graphify),
[colbymchenry/codegraph](https://github.com/colbymchenry/codegraph).

## License

MIT

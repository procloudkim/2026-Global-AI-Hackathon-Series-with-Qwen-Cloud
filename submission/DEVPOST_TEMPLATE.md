# Devpost final submission source — Track 1 MemoryAgent

Live project: https://devpost.com/software/librarian-evidence-backed-agent-memory

This file is the durable source for the editable Devpost project. Official field
requirements remain canonical in [`hackathon-contract.json`](hackathon-contract.json),
and proof status remains canonical in
[`evidence-manifest.json`](evidence-manifest.json).

## Project title

Librarian — Evidence-Backed Agent Memory

## Elevator pitch

A Qwen-powered MemoryAgent that preserves sources, supersedes stale claims, and
explains why memory changed.

## Inspiration

AI agents often remember a fact after it stops being true. A larger context
window does not tell a user which source replaced that fact, why the answer
changed, or whether the older evidence was preserved. Librarian treats memory
as an evidence lifecycle instead of a bag of text.

## What it does

Librarian is a **MemoryAgent powered by Qwen Cloud** and deployed on
**Alibaba Cloud ECS**.

A user can:

1. ingest an original source,
2. ingest a correction that explicitly replaces it,
3. query the current memory with bounded retrieval, and
4. inspect an explanation showing the active claim, superseded claim, source
   citations, and transition history.

The guided demo uses an isolated namespace for each run. Sources remain
immutable while claim state changes explicitly, so the system can answer with
the current value without erasing how that value changed.

## How we built it

- Qwen Cloud models through the DashScope OpenAI-compatible API
- FastAPI application on Alibaba Cloud ECS
- Caddy HTTPS gateway with Basic Auth and a 64 KB request-body limit
- persistent Markdown, JSON, and JSONL memory outside immutable application releases
- graph-first bounded retrieval
- systemd services and exact-commit GitHub Actions deployment
- candidate-bound health and restart-persistence receipts

Only bounded ingest and query requests cross the Qwen boundary. The explanation
view is reconstructed locally from the stored ledger and does not make another
model call.

## Challenges

The hardest part was making “memory changed” auditable. We had to preserve
source bytes, represent supersession rather than overwrite history, keep
retrieval bounded, and prove that deployment and restart did not silently alter
persistent memory. The deployment pipeline fails closed when the candidate SHA,
live health, or memory digest does not match.

## Accomplishments

- a working browser-guided correction demo
- live Qwen Cloud calls with model and token receipts
- source-backed answers with explicit claim IDs
- visible active and superseded memory states
- exact-SHA deployment on Alibaba Cloud ECS
- restart-persistence proof bound to the deployed candidate

## What we learned

Useful agent memory needs more than storage. It needs evidence, state
transitions, bounded recall, and recovery proof. We also learned to keep the
Qwen transmission boundary small and to separate model-assisted ingest and
query from deterministic explanation and deployment verification.

## What's next

Next we would add user-managed retention policies, encrypted tenant isolation,
and broader evaluation across real correction-heavy workflows while preserving
the same evidence-first contract.

## Built with

Qwen Cloud, Alibaba Cloud ECS, DashScope, qwen-flash,
qwen-plus-2025-07-28, Python, FastAPI, Caddy, systemd, GitHub Actions,
MCP, Markdown, JSON, and JSONL.

## Public links

- Repository: https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud
- Live demo: https://43.106.13.57.sslip.io
- Qwen integration proof:
  https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/main/src/librarian/llm.py

## Judge testing instructions

Use the Basic Auth credentials supplied privately in the Devpost judge
instructions. Credentials are intentionally absent from the public repository.

1. Open the live demo and authenticate.
2. Keep the generated isolated demo namespace.
3. Ingest Source A with quota `100`.
4. Load and ingest Source B, which explicitly replaces Source A with quota
   `1000`.
5. Query memory and verify that the answer is `1000`, cites Source B, and does
   not include standalone stale value `100`.
6. Open **Explain memory** and verify that `1000` is active, `100` is
   superseded, and the replacement transition is preserved.
7. Inspect `proof/deployments/restart-persistence.json` and
   `proof/deployments/release-finalization.json`; both bind deployed runtime
   commit `c1ee50907c2bebbab2f2f85e7d08a4ae0ccf22db`.

## AI tools disclosure

Qwen Cloud models power runtime extraction and answering. OpenAI Codex assisted
software engineering, repository auditing, test design, security review,
deployment verification, and submission preparation.

## Required custom-field values

The following values are evidence-backed and ready:

- Track: **MemoryAgent**
- Public repository: the repository URL above
- Alibaba deployment proof code: the `src/librarian/llm.py` URL above
- Architecture upload: `submission/architecture.png`
- Workbench screenshot upload:
  `submission/evidence/workbench-deployment.png`
- AI tools: the disclosure above
- Learning level: **Significant learning**

Submitter type, country, new or existing project provenance, age of majority,
eligible jurisdiction, and sponsor or government employment are personal facts
that require the owner’s explicit confirmation before final submission.

## Proof boundary

Deployed candidate `c1ee50907c2bebbab2f2f85e7d08a4ae0ccf22db`
passed deterministic CI, the bounded two-case live Qwen gate, exact-SHA Alibaba
deployment, and restart-persistence verification. The live release slice is not
an independent private-holdout promotion:
`promotion_status` remains `HOLD`. No winning, comparative-superiority, or
general production-readiness claim is made.

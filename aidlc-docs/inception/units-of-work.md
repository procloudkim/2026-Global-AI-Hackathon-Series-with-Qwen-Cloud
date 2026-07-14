# Units of Work — Librarian

This document defines stable implementation boundaries and dependencies. Official dates and submission fields live only in [`submission/hackathon-contract.json`](../../submission/hackathon-contract.json); current evidence status lives only in [`submission/evidence-manifest.json`](../../submission/evidence-manifest.json).

## Core units

### U1. Runtime and Qwen contract

- FastAPI runtime and DashScope OpenAI-compatible client
- explicit light/heavy model IDs, timeout, retry, completion-token cap, and usage ledger
- no-Qwen `/health` separated from authenticated bounded `/health/qwen`
- DoD: configuration fails closed and every Qwen call has a bounded token budget

### U2. Persistent memory store

- immutable raw sources, canonical claims, wiki projections, graph/index, and append-only decisions
- atomic replace, fsync, process lock, pending-transition recovery, and projection repair
- DoD: concurrent writers serialize and restart restores the same canonical state

### U3. Evidence-grounded ingest

- versioned strict-JSON extraction and relation prompts
- complete-sentence evidence validation before any lifecycle transition
- DoD: explicit replacement supersedes the old claim, activates the new claim, preserves unrelated claims, and records source-bound evidence

### U4. Limited-context query

- graph metadata scoring before top-K page reads
- active/disputed claim filtering, citation entailment, and fail-closed abstention
- DoD: answers expose claim/source/context traces and never select superseded evidence

### U5. Safe forgetting and repair

- claim-level transition, audit, and projection repair; whole-page automatic deletion is disabled
- DoD: false forgetting, illegal transitions, and source/decision drift are detected without erasing audit history

### U6. Independent evaluation

- public B0/B1/B2/C comparison and separate C-only production conformance
- private 24-case holdout with external signed aggregate attestation and no Qwen-as-judge
- DoD: [`eval/policy.json`](../../eval/policy.json) gates the exact frozen candidate; implementation changes invalidate the holdout

### U7. Exact-candidate Alibaba release

- immutable `/opt/librarian/releases/<sha>` application releases separated from `/var/lib/librarian/memory`
- candidate-bound cloud approval, capped live-Qwen gate, host readiness, restart proof, finalization, and rollback
- DoD: the public service, receipts, and Workbench evidence bind the same SHA and persistent-memory digest

### U8. Submission synchronization

- public repository and license, code URL, English architecture image, Workbench screenshot, judge URL, sub-180-second video, testing instructions, and disclosures
- DoD: `scripts/preflight.ps1 -Mode submit` passes for the deployed candidate before human submission approval

## Optional interfaces

MCP and the minimal demo UI remain thin interfaces over the same memory contract. They must not delay or weaken U1–U8 and do not create an independent promotion claim.

## Dependency chain

`U1 → U2 → (U3, U4, U5) → U6 → U7 → U8`

Any failed live-Qwen, persistence, holdout, cost, or official-contract gate stops promotion and deployment expansion.

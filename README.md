# 2026 Global AI Hackathon Series with Qwen Cloud — Librarian

> **Track 1: MemoryAgent** — An agent that *maintains* its memory instead of merely retrieving it.
> "Writes like a wiki, forgets like a librarian."

## What is this?

Librarian is a persistent-memory agent built on Qwen Cloud. Instead of RAG-style
re-discovery on every query, it maintains atomic, evidence-backed claims inside a
Markdown wiki. Superseded claims remain auditable but are removed from answer
context; unresolved conflicts stay disputed instead of being guessed away.

Core capabilities:
1. **Persistent accumulation** — content-hashed immutable raw sources, canonical wiki claims, append-only decision receipts with crash-recovery outbox
2. **Safe, timely forgetting** — evidence-validated claim transitions; no whole-page auto archive
3. **Limited-context recall** — score `graph.json` metadata first, then load at most top-K wiki pages
4. **Cited structured answers** — fact/claim/source receipts and fail-closed citation validation

## Status

The frozen runtime tree passed deterministic CI, a bounded live-Qwen two-case
gate, exact-SHA deployment to Alibaba Cloud, and an authenticated
restart-persistence proof. Runtime commit
`c1ee50907c2bebbab2f2f85e7d08a4ae0ccf22db` is `RELEASE_VERIFIED`; the public
health response is bound to that SHA. It has not been promoted by an
independent private holdout, and submission completeness is tracked separately
from runtime proof. No winning or general production-ready claim is made.

Current proof and contract status:

- `submission/hackathon-contract.json` is the canonical official-contract SOT.
- `submission/evidence-manifest.json` keeps local, live, deployed, holdout, and
  submission evidence levels separate.
- `proof/runs/release-live-qwen/receipt.json` records the capped live-Qwen gate;
  it remains `promotion_status: HOLD` because it is only a two-case release
  slice without an independent external verifier or fair live B2 comparison.
- `proof/deployments/` contains masked infrastructure, deployment, restart, and
  finalization receipts for the exact deployed SHA.
- `submission/evidence/cloud-approval-receipt.json` binds verified compute
  eligibility to an approved maximum spend of USD 0 and scheduled resource
  release containment.
- `scripts/preflight.ps1 -Mode ci` passes while submit mode still fails closed
  on current-contract freshness and missing human/form/media evidence.

## Local run

```bash
uv sync --frozen
uv run --frozen uvicorn --app-dir src librarian.main:app --reload
```

- API docs: `http://127.0.0.1:8000/docs`
- Demo UI: `http://127.0.0.1:8000/`

## Docker run (portable)

```bash
cp .env.example .env
docker compose up --build -d
```

On PowerShell, use `Copy-Item .env.example .env` for the first command. Keep
`.env` local and add a real `DASHSCOPE_API_KEY` only for an explicitly approved
live-Qwen run; `/health` itself does not call Qwen.

- App: `http://127.0.0.1:8080/`
- API docs: `http://127.0.0.1:8080/docs`
- Memory volume: `./memory` (persisted on host)

To stop:

```bash
docker compose down
```

## Development methodology

This project is developed following [AI-DLC](https://github.com/awslabs/aidlc-workflows)
(AI-Driven Development Life Cycle), with design docs in `aidlc-docs/`.

Design rationale and research references live in
[`wiki/concepts/foundations.md`](wiki/concepts/foundations.md).

## License

MIT

## Alibaba Cloud release tooling (deployment verified)

Deployment tooling supports an approved Ubuntu 22.04/24.04 Alibaba ECS or
Simple Application Server host. It does not activate a trial, create a
resource, attach a payment method, or spend credit.

- `deploy/setup.sh` installs the non-login service account, persistent paths,
  checksum-pinned uv artifact, systemd unit, and Caddy configuration. It
  intentionally does not deploy or start the application.
- `deploy/deploy.sh` accepts a reviewed Git archive plus candidate and receipt
  digests, installs `/opt/librarian/releases/<sha>`, atomically switches
  `/opt/librarian/current`, and never replaces
  `/var/lib/librarian/memory`.
- `deploy/rollback.sh` restores only a release with a hash-valid
  `RELEASE_VERIFIED` finalization receipt and stops the service if the
  persistent-memory digest changes.
- `deploy/verify-restart-persistence.sh` runs the approval-gated Track 1
  100→1000 vertical slice in a unique namespace and writes an append-only proof
  receipt.
- `deploy/continue-quarantined-restart-proof.py` can promote only a complete,
  hash-bound quarantined artifact set after revalidating exact-SHA health,
  memory, semantics, and budgets; it performs no provider calls.
- `.github/workflows/ci.yml` uses no Qwen key. The manual production workflow
  performs the bounded no-gold live gate before host deployment.
- `Dockerfile` and `docker-compose.yml` remain a loopback-bound portable local
  runtime path.

The public `/health` route is a no-Qwen process/SHA check. `/health/qwen` is a
token-authenticated, exact-`pong`, bounded release check and is denied by the
public Caddy proxy. It must not be used as a load-balancer probe.

Qwen Cloud API integration code path:

- `src/librarian/llm.py` (DashScope OpenAI-compatible API client)

## MCP tools

Librarian exposes MCP tools for agent integration:

- `memory_ingest(source_id, text)`
- `memory_query(question, top_k=5)`
- `memory_lint(apply_archive=true)` (`apply_archive` is a legacy name for safe repairs; pages are never auto-archived)
- `memory_stats()`

Run MCP server (stdio):

```bash
PYTHONPATH=src uv run --frozen python -m librarian.mcp_server
```

PowerShell equivalent:

```powershell
$env:PYTHONPATH = "src"
uv run --frozen python -m librarian.mcp_server
```

## Legacy token smoke benchmark

Run token minimization experiments (L-E1~L-E3):

```bash
uv run python bench/run_ab.py
```

This command checks formatting/token plumbing only. It is not answer-correctness or
winning evidence.

## Independent memory-policy evaluation

The evaluator never passes gold labels to the runner and never uses Qwen as
judge. Public development comparison, production conformance, and independent
private promotion are separate lanes. Production conformance replays the
transition ledger, verifies append-only prefixes and source-bound evidence, and
requires the replayed state to equal canonical memory.

```powershell
uv run --frozen pytest tests eval/tests -q
```

See [`eval/README.md`](eval/README.md) for reproducible public runs, the
secret-seeded 24-scenario holdout, and promotion boundaries.

## Submission helpers

- Canonical contract: `submission/hackathon-contract.json`
- Human contract projection: `submission/HACKATHON_CONTRACT.md`
- Evidence manifest: `submission/evidence-manifest.json`
- Masked account/credit audit: `submission/account-credit-audit.json`
- Devpost template: `submission/DEVPOST_TEMPLATE.md`
- Tiered preflight: `scripts/preflight.ps1`

Run the local structural lane with:

```powershell
pwsh -File scripts/preflight.ps1 -Mode ci -RunTests
```

Deploy and submit modes require a clean frozen candidate and their respective
external receipts. A pass at one level never substitutes for another.

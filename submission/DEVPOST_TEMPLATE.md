# Devpost Submission Draft — Track 1 MemoryAgent

This is an English drafting surface, not a release receipt. Official field
requirements come only from
[`hackathon-contract.json`](hackathon-contract.json), and every URL, upload, or
claim used at submission must be `verified` in
[`evidence-manifest.json`](evidence-manifest.json). Do not copy this draft into
Devpost while `scripts/preflight.ps1 -Mode submit` fails.

## Project title

Librarian — Persistent Memory That Safely Forgets

## Elevator pitch

Librarian is a Qwen-powered memory agent that maintains a persistent,
evidence-grounded knowledge base. It preserves immutable source records,
supersedes stale claims instead of silently deleting them, retrieves only a
limited top-K context, and restores the same active memory state after a
process restart.

## What it does

- Ingests source text into immutable raw records and evidence-grounded claims.
- Tracks claim lifecycle transitions so a newer explicit replacement can
  supersede an older value without deleting its audit trail.
- Retrieves active claims through a graph-first, top-K path and returns source
  citations.
- Persists decisions, wiki state, graph data, and indexes outside the
  application release directory.
- Exposes process health without calling Qwen and keeps any paid live-provider
  check bounded and release-gated.

## Track 1 thesis

Under the same update stream, answer model, answer prompt, top-K, and context
budget, Librarian must cite current facts more accurately than a strong
latest-write-wins baseline, reduce both stale leakage and false forgetting,
and restore the same active memory state after process restart.

This thesis is not considered proven by documentation, structure checks, or a
deployment screenshot. Promotion requires deterministic behavior, a bounded
live Qwen contract, restart persistence on Alibaba Cloud, and an independently
sealed private holdout for the exact deployed candidate.

## How we built it

- Qwen Cloud through the DashScope OpenAI-compatible endpoint.
- FastAPI for the service API.
- Markdown, JSON, and JSONL memory artifacts with atomic replacement,
  filesystem synchronization, process locking, and recovery.
- Immutable application releases separated from persistent memory.
- Deterministic CI followed by a manually approved live-Qwen and Alibaba Cloud
  release gate.

## Deployment and evidence fields

Resolve these fields from `submission/evidence-manifest.json` only after their
status is `verified`:

| Devpost field | Evidence manifest item |
|---|---|
| Track selection | `selected_track_receipt` |
| Public repository URL | `public_repository` |
| Alibaba Cloud proof code URL | `alibaba_code_proof` |
| Architecture diagram upload | `architecture_diagram` |
| Workbench deployment screenshot | `workbench_deployment_screenshot` |
| Free judge-access URL | `public_demo_url` |
| Public video under three minutes | `public_demo_video` |
| English project description | `english_description` |
| English-language review | `english_language_review` |
| New/existing project answer | `existing_project_update` |
| Testing instructions | `testing_instructions` |
| AI tools used | `ai_tools_disclosure` |
| Learning summary | `learning_summary` |
| Eligibility confirmations | `eligibility_confirmations` |

No live URL, video URL, Workbench screenshot, or successful Alibaba deployment
is claimed in this draft yet.

## New or existing project

Pending human confirmation. Repository history alone does not establish
whether the underlying project existed before 2026-05-26. Before submission,
the owner must confirm the project's provenance in the current form and, if it
is an existing project, provide an accurate English summary of meaningful
changes made after 2026-05-26.

## Judge testing instructions draft

After the public endpoint and candidate receipt are verified, the final form
instructions will direct judges to:

1. Open the free judge-access URL recorded in the evidence manifest.
2. Ingest an initial source that sets the production quota to 100.
3. Ingest a second source that explicitly replaces it with 1000.
4. Query the current production quota and verify the answer is 1000, cites the
   second source, and excludes 100 from both the answer and selected context.
5. Inspect the public restart-persistence receipt for the same deployed commit
   SHA and memory-state digest before and after service restart.

The exact endpoint, authentication instructions, request bodies, expected
responses, and receipt URLs must be inserted only after deployed smoke tests
pass. Until then this section remains a draft even though it contains no
synthetic placeholder URL.

## AI tools disclosure draft

Qwen Cloud models power runtime extraction and answering. OpenAI Codex assisted
software engineering, repository auditing, test design, and release planning.
The final disclosure must be reviewed by a human against the complete tool
history before submission.

## Learning summary draft

The central lesson was that persistent memory is a lifecycle and release
contract, not a retrieval feature. Evidence spans must bind every claim to the
source text; explicit replacements must supersede stale values without erasing
the audit trail; unrelated facts must survive forgetting; and restart must
restore the same canonical state. We also learned to keep deterministic tests,
live-provider behavior, deployed persistence, independent holdout promotion,
and submission completeness as separate proof levels.

## Proof boundary

No live Qwen behavioral, Alibaba restart-persistence, or private-promotion
receipt is verified for the current candidate. Legacy token benchmarks and
pre-candidate runs are not submission evidence. Only candidate-bound artifacts
marked `verified` in the evidence manifest may be used in the final submission.

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

The live URL, bounded Qwen receipt, masked Alibaba infrastructure receipt,
exact-SHA deployment manifest, restart proof, and finalization receipt are now
verified in the evidence manifest. The video, architecture refresh, Workbench
screenshot, form receipts, and human confirmations remain incomplete.

## New or existing project

Pending human confirmation. Repository history alone does not establish
whether the underlying project existed before 2026-05-26. Before submission,
the owner must confirm the project's provenance in the current form and, if it
is an existing project, provide an accurate English summary of meaningful
changes made after 2026-05-26.

## Judge testing instructions draft

Use the Basic Auth username and password supplied privately in the Devpost
testing field; credentials are intentionally absent from the public repository.
Then:

1. Open `https://43.106.13.57.sslip.io` and authenticate.
2. `POST /ingest` with
   `{"source_id":"judge-source-a","text":"In judge-demo, librarian's production-quota is 100 units per minute."}`.
3. `POST /ingest` with
   `{"source_id":"judge-source-b","text":"This record explicitly replaces judge-source-a. In judge-demo, librarian's production-quota is 1000 units per minute."}`.
4. `POST /query` with
   `{"question":"What is librarian's current production-quota in judge-demo?","top_k":3}`.
5. Verify the answer selects `1000`, cites `judge-source-b`, and does not place
   standalone `100` in the answer or selected facts.
6. Inspect `proof/deployments/restart-persistence.json` and
   `proof/deployments/release-finalization.json`; both bind runtime commit
   `5dee1dbae5e350c4b2a1466f0002596168bbe15e` and matching memory digests.

The endpoint and request bodies are verified. The repository must never contain
the Basic Auth secret; copy it only into the judge-visible testing field before
submission.

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

The current candidate has verified live-Qwen, Alibaba deployment,
restart-persistence, and release-finalization receipts. The live gate is a
two-case release slice, not an independent private-holdout promotion:
`promotion_status` remains `HOLD`. Legacy token benchmarks and pre-candidate
runs are not submission evidence. Only candidate-bound artifacts marked
`verified` in the evidence manifest may be used in the final submission.

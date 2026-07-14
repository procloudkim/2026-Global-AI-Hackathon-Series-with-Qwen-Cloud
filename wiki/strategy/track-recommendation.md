# Track 1 selection decision

Target: Track 1 MemoryAgent.

The decision remains valid because Librarian's load-bearing behavior is memory
lifecycle correctness: it accumulates source-backed claims, supersedes stale
facts, preserves unrelated facts, retrieves a bounded active context, and
restores the same canonical state after restart. The release thesis and AND
gates are canonical in
[`submission/hackathon-contract.json`](../../submission/hackathon-contract.json).

## Why this remains the narrowest fit

- Existing file-backed wiki, graph/index, and decision ledger directly exercise
  accumulation, forgetting, and limited-context recall.
- A latest-write-wins baseline makes the improvement falsifiable without a new
  vector database, embedding stack, or generalized knowledge graph.
- Restart persistence can be demonstrated on Alibaba persistent disk with the
  current storage contract.
- MCP is retained as an optional interface, not treated as a Track 1 contract or
  automatic scoring bonus.

## Rejected expansion

The release does not add video-generation infrastructure, a multi-agent
society, edge hardware, Kubernetes, a new IaC framework, or a Function Compute
migration. Those would enlarge the proof surface without strengthening the
Track 1 thesis.

## Current execution reference

- [`aidlc-docs/operations/operations-plan.md`](../../aidlc-docs/operations/operations-plan.md)
- [`submission/evidence-manifest.json`](../../submission/evidence-manifest.json)
- [`eval/README.md`](../../eval/README.md)

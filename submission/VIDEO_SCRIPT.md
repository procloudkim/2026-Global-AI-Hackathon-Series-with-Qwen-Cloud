# Librarian public demo video script (target: 2:45)

Do not record this script until the live-Qwen gate, Alibaba restart proof, and
private promotion attestation all bind to the same candidate SHA. Replace every
`<EVIDENCE>` marker with the verified receipt value; never narrate a pending gate
as a pass.

## 0:00-0:20 - Problem and thesis

Screen: title, then the English architecture diagram.

Narration:

> Agents do not only need more memory. They need current memory. Librarian is a
> Qwen-powered MemoryAgent that keeps immutable source evidence, marks replaced
> claims stale, and retrieves only the active facts that fit a limited context.

## 0:20-0:45 - Real Alibaba Cloud runtime

Screen: Alibaba Cloud Workbench connected to the running instance. Show the
timestamp, `/health`, and deployed commit SHA without revealing account IDs,
addresses that should remain private, or secrets.

Narration:

> This FastAPI backend is running on Alibaba Cloud at commit `<EVIDENCE_SHA>`.
> Its Markdown memory is on a persistent system disk outside the application
> release, while bounded model calls go to Qwen Cloud.

## 0:45-1:25 - Latest-write replacement with evidence

Screen: ingest the two approved vertical-slice sources and show the structured
receipts.

1. Source A: `The production API quota is 100.`
2. Source B: `This replaces the prior production API quota. The production API quota is 1000.`

Narration:

> Source A establishes a quota of one hundred. Source B explicitly replaces it
> with one thousand. Qwen extracts claims, but deterministic evidence and
> lifecycle validators decide whether state may change. The old claim is now
> superseded, the new claim is active, and unrelated memory remains intact.

## 1:25-1:55 - Restart persistence

Screen: show the pre-restart state hash, restart the service or container in
Workbench, then show the post-restart state hash and `/health` SHA.

Narration:

> I restart the application. The release changes independently from the memory
> directory, so the canonical state and decision history survive. The before
> and after memory hashes are identical: `<EVIDENCE_MEMORY_HASH>`.

## 1:55-2:20 - Limited-context answer

Screen: query the current production quota. Keep the answer, citation, selected
context trace, top-K count, and token receipt visible.

Narration:

> The answer is one thousand and cites Source B. The stale value one hundred is
> absent from both the answer and selected context. Librarian scores its graph
> first and reads only the top-K pages, shown here as `<EVIDENCE_TOP_K>`.

## 2:20-2:35 - Independent evaluation

Screen: public holdout attestation only. Do not show private gold or the seed.

Narration:

> Promotion is an AND gate, not a model-graded score. An isolated evaluator ran
> twenty-four private cases three times against the same candidate SHA. The
> public attestation records hashes, aggregate metrics, and the final decision:
> `<EVIDENCE_PROMOTION_DECISION>`.

## 2:35-2:45 - Close

Screen: public repository, MIT license, test URL.

Narration:

> Librarian: persistent memory that remembers the current truth and preserves
> the evidence for how it changed.

## Recording and publication checklist

- [ ] Total duration is strictly less than 180 seconds.
- [ ] English narration or complete English subtitles are present.
- [ ] Video is publicly accessible without login on an allowed host.
- [ ] Workbench, public demo, receipts, and repository all show the same SHA.
- [ ] Alibaba deployment and restart are recorded live, not simulated locally.
- [ ] Source B citation, top-K trace, token usage, and absence of stale `100` are visible.
- [ ] No API key, health token, SSH key, account number, coupon code, or private IP is visible.
- [ ] No private holdout gold, raw seed, or evaluator-only path is visible.
- [ ] All `<EVIDENCE_...>` markers have been replaced from fresh receipts.
- [ ] Title, description, repository, diagram, video, and running demo describe the same behavior.

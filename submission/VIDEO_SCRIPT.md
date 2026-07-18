# Librarian demo video script

Target: 2:45–2:55
Deployed commit: `d5ca972b74688eab1c5e3eee63bb89306b55d6a0`

## 0:00–0:18 — Problem

Screen: Title, then the final architecture diagram.

Narration:

> AI agents can remember a fact after it stops being true. A larger context
> window still cannot explain which source replaced it. Librarian is a
> Qwen-powered MemoryAgent that preserves sources, changes claim state
> explicitly, and answers from current evidence.

## 0:18–0:36 — Architecture

Screen: Follow the diagram from the browser to Alibaba Cloud and Qwen Cloud.

Narration:

> The browser reaches Caddy over HTTPS and Basic Auth, then FastAPI on Alibaba
> Cloud ECS. Claims, source files, and append-only decisions live on a persistent
> system disk. In this demo, only bounded ingest and query calls cross to Qwen
> Cloud; explanation is a local ledger read.

## 0:36–0:54 — Live Alibaba backend

Screen: Alibaba Cloud console, showing the successful Cloud Assistant remote-command result with sensitive account information hidden:

```bash
date -u '+%Y-%m-%dT%H:%M:%SZ'
systemctl is-active librarian.service
systemctl is-active caddy.service
curl -fsS http://127.0.0.1:8080/health
readlink -f /opt/librarian/current
```

Narration:

> This Alibaba Cloud Assistant result is from the live backend, not a local mock.
> Librarian and Caddy are both active. Health and the current release path agree
> on commit d5ca972.

## 0:54–1:18 — Source A: original fact

Screen: Open the live demo. Source A is already loaded with quota `100`. Click
**Ingest source** and wait for the receipt.

Narration:

> The browser creates a unique demo namespace so earlier runs cannot contaminate
> this result. Source A says the quota is one hundred units per minute. I ingest
> it. The receipt identifies the stored claim, the Qwen model route, and actual
> token usage.

## 1:18–1:42 — Source B: explicit replacement

Screen: Click **Load correction**. Pause over the text showing that it explicitly
replaces Source A and changes the quota to `1000`. Click **Ingest source**.

Narration:

> Now I load Source B. Notice that it explicitly names Source A as the record it
> replaces and changes the quota to one thousand. After ingest, the old source
> remains immutable; the claim lifecycle changes instead of deleting its
> history.

## 1:42–2:02 — Current answer and citation

Screen: Keep **Maximum pages** at `5`. Click **Query memory**. Show the answer,
verified fact, Source B citation, claim ID, and token receipt.

Narration:

> The question is scoped to this run, with at most five pages available. Query
> returns one thousand, cites the stored Source B page and claim ID, and shows
> token usage. The consistency check confirms that the standalone stale value
> one hundred is absent from the answer.

## 2:02–2:28 — Explain why memory changed

Screen: Click **Explain memory**. Show `Resolution · resolved`, the active `1000`
claim, superseded `100` claim, transition, revision history, and passed
replacement proof.

Narration:

> Explain memory makes no Qwen call. It projects the ledger for the same
> canonical key: one thousand is active, one hundred is superseded, and the
> transition links Source B to the old claim. Revision history remains visible,
> and the replacement proof passes without hiding either version.

## 2:28–2:43 — Persistence receipt

Screen: Open `proof/deployments/restart-persistence.json`. Highlight
`candidate_sha`, `status: PASS`, and equal `memory_sha256_before_restart` and
`memory_sha256_after_restart` values.

Narration:

> This deployment receipt is bound to the same candidate SHA, reports PASS, and
> records identical memory digests before and after restart. I am showing the
> existing receipt, not restarting the service during this demo.

## 2:43–2:55 — Public deliverable

Screen: Public GitHub repository root, `LICENSE`, then README references to Qwen
Cloud and Alibaba Cloud.

Narration:

> The code is public under MIT. The repository documents its Qwen Cloud
> integration and Alibaba Cloud deployment. Librarian remembers the current
> truth and preserves the evidence for how it changed.

## Recording checks

- [ ] Keep the finished video under three minutes.
- [ ] Use English narration or complete English subtitles.
- [ ] Show Workbench branding, both active services, `/health`, and the release path.
- [ ] Use only the new receipt bound to `d5ca972`.
- [ ] Do not expose credentials, account identifiers, tokens, private IPs, or shell history.
- [ ] Do not mention private holdouts, independent promotion, or simulated evaluation.
- [ ] Publish on YouTube or Vimeo so it plays without login.

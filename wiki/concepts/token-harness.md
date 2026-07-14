# Token and context measurement

Source research: [`Deep-research-token.md`](../../Deep-research-token.md). This page keeps only the conclusions implemented by Librarian.

## Principles

1. Token count and token price are separate levers.
2. Optimize `cost_per_success`, not raw token reduction.
3. Measure before optimizing; a cheaper incorrect answer is a failure.
4. Keep static policy and output contracts in versioned prompt prefixes and put dynamic context last.
5. Score graph/index metadata first and load only the selected top-K pages.

## Runtime contract

- `meter.py` records model, route, prompt/completion tokens, latency, success, and retrieval trace in `runs.jsonl`.
- `prompts.py` owns versioned static prefixes and strict JSON contracts.
- `llm.py` requires a per-call completion cap and bounded timeout/retry configuration.
- query traces expose corpus pages, candidate pages, loaded pages, filtered stale claims, and context tokens.

## Evidence lanes

`bench/run_ab.py` is a legacy formatting/token smoke benchmark. It does not score answer correctness and cannot support promotion.

The deterministic evaluator in [`eval/`](../../eval/) compares B0/B1/B2/C under the same frozen update stream, answer serializer, top-K, and context budget. Adoption requires correctness gates to remain satisfied while tokens per correct current answer or cost per success improves.

## Guardrails

- Do not attach the full wiki to every request.
- Do not treat vendor “up to” savings as project evidence.
- Do not use Qwen as the correctness judge.
- Do not add multi-agent or embedding infrastructure without a measured Track 1 benefit.
- Do not copy judging labels, weights, or deadlines here; their canonical source is [`submission/hackathon-contract.json`](../../submission/hackathon-contract.json).

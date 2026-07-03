# Librarian Token-Efficiency Benchmark (U8)

Reproducible A/B evidence that Librarian's memory harness reduces **cost per successful answer**, not just raw tokens.

## Method

- **Dataset**: 12 questions (`bench/questions.txt`) covering the wiki knowledge base built by `/ingest`.
- **Models**: Qwen via DashScope OpenAI-compatible API — LIGHT = `qwen-flash`, HEAVY = `qwen-plus`.
- **Candidate (all experiments)**: Librarian surgical pipeline — index-first top-3 page selection, static prompt prefix, strict JSON contract, LIGHT-first with escalate-on-fail.
- **Success criterion (strict)**: response must be parseable JSON with a non-empty `answer` AND at least one citation matching a real wiki page slug. Merely producing text does not count.
- **KPI**: `tokens_per_success = total_tokens / successful_answers` (proxy for cost_per_success; exact cost requires per-tier pricing via `BENCH_INPUT_PRICE_PER_1M` / `BENCH_OUTPUT_PRICE_PER_1M`).

## Experiments

| ID | Baseline | Tests |
|---|---|---|
| L-E1 | HEAVY model reads the **entire wiki** as context | Does surgical retrieval beat full-context reads? |
| L-E2 | HEAVY model with same top-5 retrieval | Does LIGHT-first routing beat always-heavy? |
| L-E3 | LIGHT model, **freeform** prompt, no contract | Does the strict JSON contract beat freeform? |

## Results (n = 12 per condition)

| Experiment | Baseline tokens | Candidate tokens | Raw token Δ | Baseline success | Candidate success | Baseline tok/success | Candidate tok/success | **tok/success Δ** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| L-E1 full-read vs surgical | 6,861 | 6,658 | −3.0% | 6/12 | 11/12 | 1,143.5 | 605.3 | **−47.1%** |
| L-E2 heavy-only vs small-first | 5,149 | 6,701 | +30.1% | 7/12 | 11/12 | 735.6 | 609.2 | **−17.2%** |
| L-E3 freeform vs contract | 14,939 | 6,669 | −55.4% | 12/12* | 11/12 | 1,244.9 | 606.3 | **−51.3%** |

\* L-E3 baseline "success" only requires non-empty text (freeform has no citations to validate), so it is trivially high while producing ~2.2× the tokens with zero verifiable grounding.

## Honest interpretation

- **L-E2 raw tokens are worse (+30%)** for the candidate: LIGHT-first routing sometimes escalates, adding a second call. We report this openly. The point is that per *successful, citation-grounded* answer, small-first still wins (−17%) because heavy-only fails the strict contract more often (7/12 vs 11/12).
- **Quality dominates**: baselines that dump more context (L-E1) or skip the contract (L-E3) look fine on raw output but fail citation validation. Optimizing raw tokens alone is the wrong objective; `cost_per_success` is the KPI Librarian is engineered around.
- The one candidate failure (11/12) was a question whose answer spans pages outside the top-3 window — a known trade-off of aggressive context pruning.

## Limitations

- n = 12 questions on a small wiki (~10 pages); effect sizes on large corpora are expected to grow for L-E1 (full-read cost scales with corpus size) and shrink routing overhead for L-E2.
- Single run per condition; LLM nondeterminism means ±1 success variance is possible.
- tokens_per_success treats LIGHT and HEAVY tokens equally; with real per-tier pricing (qwen-flash ≈ 8–10× cheaper than qwen-plus), the candidate advantage in L-E1/L-E2 **increases** further.

## Reproduce

```powershell
# from repo root, with DASHSCOPE_API_KEY in .env and wiki populated via /ingest
$env:PYTHONPATH='src'
uv run python bench/run_ab.py
# outputs: bench/results.md, bench/results.json
```

"""Run the legacy U8 token/format smoke benchmark.

Its success predicate does not score answer correctness or stale leakage. Use
``eval/`` for promotion evidence; never cite this script as a winning receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, "src")

from librarian.llm import Tier, get_router  # noqa: E402
from librarian.prompts import QUERY_LIGHT_SYSTEM_PREFIX  # noqa: E402
from librarian.query import answer_question, select_top_k_pages  # noqa: E402
from librarian.store import MemoryStore  # noqa: E402


@dataclass(frozen=True)
class RunMetric:
    total_tokens: int
    estimated_cost: float
    success: bool


def main() -> None:
    store = MemoryStore()
    router = get_router()
    questions = load_questions(Path("bench/questions.txt"))
    if not questions:
        raise SystemExit("No questions found in bench/questions.txt")
    if not store.list_wiki_pages():
        raise SystemExit("No wiki pages found. Run /ingest first.")

    rows: list[dict] = []
    experiments = {
        "L-E1_surgical_vs_fullread": (
            lambda q: baseline_full_read(q, store, router),
            lambda q: candidate_surgical(q, store, router),
        ),
        "L-E2_smallfirst_vs_heavyonly": (
            lambda q: baseline_heavy_only(q, store, router),
            lambda q: candidate_surgical(q, store, router),
        ),
        "L-E3_contract_vs_freeform": (
            lambda q: baseline_freeform(q, store, router),
            lambda q: candidate_surgical(q, store, router),
        ),
    }

    for exp_id, (baseline_fn, candidate_fn) in experiments.items():
        b = [baseline_fn(q) for q in questions]
        c = [candidate_fn(q) for q in questions]
        b_tokens = sum(x.total_tokens for x in b)
        c_tokens = sum(x.total_tokens for x in c)
        b_cost = sum(x.estimated_cost for x in b)
        c_cost = sum(x.estimated_cost for x in c)
        b_success = sum(1 for x in b if x.success)
        c_success = sum(1 for x in c if x.success)
        b_cps = b_cost / b_success if b_success else float("inf")
        c_cps = c_cost / c_success if c_success else float("inf")

        rows.append(
            {
                "experiment_id": exp_id,
                "baseline_tokens": b_tokens,
                "candidate_tokens": c_tokens,
                "token_reduction_rate": safe_rate(b_tokens, c_tokens),
                "baseline_cost_est": round(b_cost, 6),
                "candidate_cost_est": round(c_cost, 6),
                "baseline_cost_per_success": round(b_cps, 6) if b_success else None,
                "candidate_cost_per_success": round(c_cps, 6) if c_success else None,
                "cost_per_success_reduction_rate": safe_rate_value(b_cps, c_cps),
                "baseline_successes": b_success,
                "candidate_successes": c_success,
            }
        )

    Path("bench/results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = render_markdown_table(rows)
    Path("bench/results.md").write_text(md, encoding="utf-8")
    print(md)


def valid_slugs(store: MemoryStore) -> set[str]:
    return {p.slug for p in store.list_wiki_pages()}


def judge_json_answer(text: str, store: MemoryStore) -> bool:
    """Strict success: parseable JSON, non-empty answer, and >=1 citation matching a real wiki slug."""
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return False
        obj = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return False
    answer = str(obj.get("answer") or "").strip()
    citations = obj.get("citations") or []
    if not answer or not isinstance(citations, list):
        return False
    slugs = valid_slugs(store)
    return any(str(c).strip() in slugs for c in citations)


def load_questions(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def baseline_full_read(question: str, store: MemoryStore, router) -> RunMetric:
    pages = store.list_wiki_pages()
    context = "\n\n".join(
        f"[{p.slug}] {p.title}\n{p.body[:1800]}" for p in pages
    )[:24000]
    user = (
        f"Question:\n{question}\n\n"
        f"Use all memory pages and answer with strict JSON "
        f"{{\"answer\":string,\"citations\":[...],\"confidence\":number}}.\n\n"
        f"Context:\n{context}"
    )
    resp = router.chat(Tier.HEAVY, system=QUERY_LIGHT_SYSTEM_PREFIX, user=user, max_tokens=900)
    success = judge_json_answer(resp.text or "", store)
    return metric_from_usage(resp.prompt_tokens, resp.completion_tokens, success)


def baseline_heavy_only(question: str, store: MemoryStore, router) -> RunMetric:
    pages = select_top_k_pages(store, question, k=5)
    context = "\n\n".join(f"[{p.slug}] {p.title}\n{p.body[:1400]}" for p in pages)
    user = (
        f"Question:\n{question}\n\n"
        "Answer from context with JSON {\"answer\":string,\"citations\":[...],\"confidence\":number}.\n\n"
        f"Context:\n{context}"
    )
    resp = router.chat(Tier.HEAVY, system=QUERY_LIGHT_SYSTEM_PREFIX, user=user, max_tokens=700)
    success = judge_json_answer(resp.text or "", store)
    return metric_from_usage(resp.prompt_tokens, resp.completion_tokens, success)


def baseline_freeform(question: str, store: MemoryStore, router) -> RunMetric:
    pages = select_top_k_pages(store, question, k=5)
    context = "\n\n".join(f"[{p.slug}] {p.title}\n{p.body[:1200]}" for p in pages)
    user = f"Question: {question}\n\nContext:\n{context}\n\nGive a detailed answer."
    resp = router.chat(Tier.LIGHT, system="You are helpful.", user=user, max_tokens=1000)
    success = bool((resp.text or "").strip())
    return metric_from_usage(resp.prompt_tokens, resp.completion_tokens, success)


def candidate_surgical(question: str, store: MemoryStore, router) -> RunMetric:
    result = answer_question(question=question, store=store, router=router, top_k=3)
    slugs = valid_slugs(store)
    success = bool(result.answer.strip()) and any(str(c).strip() in slugs for c in result.citations)
    return metric_from_usage(result.prompt_tokens, result.completion_tokens, success)


def metric_from_usage(prompt_tokens: int, completion_tokens: int, success: bool) -> RunMetric:
    inp = float(os.getenv("BENCH_INPUT_PRICE_PER_1M", "0"))
    out = float(os.getenv("BENCH_OUTPUT_PRICE_PER_1M", "0"))
    estimated = (prompt_tokens / 1_000_000.0) * inp + (completion_tokens / 1_000_000.0) * out
    return RunMetric(
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost=estimated,
        success=success,
    )


def safe_rate(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(1 - (candidate / baseline), 4)


def safe_rate_value(baseline: float, candidate: float) -> float | None:
    if baseline <= 0 or baseline == float("inf") or candidate == float("inf"):
        return None
    return round(1 - (candidate / baseline), 4)


def render_markdown_table(rows: list[dict]) -> str:
    lines = [
        "# U8 A/B Results",
        "",
        "| experiment_id | baseline_tokens | candidate_tokens | token_reduction_rate | baseline_successes | candidate_successes | cps_reduction_rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['experiment_id']} | {r['baseline_tokens']} | {r['candidate_tokens']} | "
            f"{r['token_reduction_rate']} | {r['baseline_successes']} | {r['candidate_successes']} | "
            f"{r['cost_per_success_reduction_rate']} |"
        )
    lines.append("")
    lines.append(
        "> Note: estimated cost fields require BENCH_INPUT_PRICE_PER_1M / BENCH_OUTPUT_PRICE_PER_1M env vars."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()

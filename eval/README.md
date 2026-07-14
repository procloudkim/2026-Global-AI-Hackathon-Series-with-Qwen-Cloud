# Librarian Track 1 Evaluation Harness

This directory is an offline, deterministic proof lane for memory-policy behavior.
It does **not** claim live Qwen quality. The runner and evaluator are separate on
purpose: the runner receives `cases.jsonl` and `extractions.jsonl`, while only the
evaluator receives `gold.jsonl`.

## Proof boundary

- `B0`: append-only + full read
- `B1`: append-only + lexical top-K
- `B2`: scope/subject/predicate latest-write-wins + top-K
- `C`: claim lifecycle + index-first retrieval
- The primary production-policy-comparison lane runs built-in B0/B1/B2 against
  the allowlisted real-code C adapter with the same frozen atomic extraction and
  shared deterministic answer serializer. A built-in-C comparison remains a
  harness self-test, not evidence about Librarian's implementation.
- Frozen extraction contains no lifecycle relation label, replacement target, or
  hidden query key. Policies infer lifecycle from raw source evidence.
- The production-conformance lane runs only C through real store/ingest/query code.
  It is not mixed with baseline deltas or token-efficiency claims. Its ledger is
  strict-schema validated, source/span bound, checked as an append-only checkpoint
  prefix, replayed from creation, and compared exactly with canonical memory state.
- Both production lanes bind the allowlisted module hash and adapter class to a
  digest of the actual C output rows in `candidate_execution`.
- Scenario is the analysis unit. Checkpoints and three repeats are not counted as
  independent samples.
- The committed dev recipe is public. A holdout is valid only when generated from a
  high-entropy user-owned `HOLDOUT_SEED` after candidate freeze.

The harness addresses the main leakage risks as follows:

| Leakage pattern | Control |
|---|---|
| Recall, not reason | Opaque synthetic entities and values derived from a seed |
| Shared hallucination / tautology | Deterministic policy oracle; no model grader |
| Verifier = designer | Public policy, hashes, receipts, user-owned holdout seed commitment |
| Frame injection | Opaque scenario IDs; scenario type appears only in gold |
| Demand characteristics | Runner inputs contain no expected/forbidden labels or gates |

This is still a hackathon promotion gate, not population-level scientific proof.
Independent human review of `policy.json` remains the strongest upgrade.
Offline and synthetic receipts always return product decision `HOLD` and
`promoted: false`, even when the deterministic policy gate passes.

## Offline dev run

From the repository root:

```powershell
uv run python -m eval.generate --split dev --output-dir eval/runs/dev-dataset
uv run python -m eval.run `
  --cases eval/runs/dev-dataset/runner-inputs/cases.jsonl `
  --extractions eval/runs/dev-dataset/runner-inputs/extractions.jsonl `
  --output eval/runs/dev-run/outputs.jsonl `
  --repeats 3 `
  --candidate-factory src.librarian.eval_adapter:create_adapter `
  --dataset-manifest eval/runs/dev-dataset/dataset-manifest.json
uv run python -m eval.evaluate `
  --cases eval/runs/dev-dataset/runner-inputs/cases.jsonl `
  --gold eval/runs/dev-dataset/evaluator-only/gold.jsonl `
  --outputs eval/runs/dev-run/outputs.jsonl `
  --run-manifest eval/runs/dev-run/run-manifest.json `
  --dataset-manifest eval/runs/dev-dataset/dataset-manifest.json `
  --output-dir eval/runs/dev-report
```

Dev has eight scenarios, so its gate status must remain
`NOT_ELIGIBLE_DEV_OR_MISSING_REPEATS` even when C passes all behavior checks.

## Repository-generated diagnostic holdout

<code>eval.generate --split holdout</code> remains available for hidden-value
regression work, but it is not private-promotion evidence. It changes values through
<code>HOLDOUT_SEED</code> while using the same repository-owned scenario builders
that produce dev inputs and gold. Its manifest therefore records:

- <code>evidence_role: same_builder_diagnostic_only</code>
- <code>promotion_eligible: false</code>
- <code>collection_provenance: repository_scenario_builders_v1</code>

The local runner also records <code>runner_process_isolation: false</code>. Neither a
secret seed nor editing that boolean can make a local result eligible.

## Independent private promotion v2

Promotion requires an external evaluator and the frozen contract in
<code>eval/policy.json</code>:

- 8 scenario types × naturalistic/adversarial pools × 24 scenarios = 384
- two author pools separate from the candidate team
- independent double annotation and third-party adjudication
- final candidate outputs hidden while cases are collected
- B2 and C evaluated on the same scenario
- scenario, not checkpoint or repeat, is the statistical unit
- Qwen is the answer model only in the 24-case live subset, never the oracle or judge

The private evaluator stores one paired row per scenario using
<code>eval/private-paired-results.schema.json</code>. <code>scenario_success</code>
is deliberately absent; <code>eval.private_promotion</code> derives it from answer,
citation, stale-context, preservation, retrieval, transition, abstention, ledger, and
wire-citation checks.

The external evaluator computes aggregate-only evidence without publishing rows:

~~~powershell
uv run python -m eval.private_promotion --paired-results <private-paired-results.jsonl> --dataset-manifest-sha256 <frozen-private-manifest-sha256> --candidate-tree-sha256 <frozen-candidate-tree-sha256> --policy eval/policy.json --output <aggregate-metrics.json>
~~~

The scorer rejects missing cells, duplicate scenario IDs, extra labels, and any matrix
other than 384 scenarios. It computes the paired B2/C table, exact two-sided McNemar
p-value, and 10,000-sample stratified paired bootstrap interval. Deterministic reruns
may demonstrate reproducibility but never increase the statistical sample count.

### Public promotion attestation v2

The external evaluator publishes only a signed aggregate receipt conforming to
<code>eval/attestation.schema.json</code>. The receipt contains no seed, gold, case
text, or scenario-level result. It binds:

- independent collection and process-isolation claims
- protocol, annotation guide, role-separation, dataset, inputs, paired-result, and aggregate hashes
- exact candidate/deployed SHA and current B2/C/answer-contract source hashes
- 384-case paired statistics and all 16 cell summaries
- a precommitted 24-case live-Qwen subset capped at 144 calls
- raw provider-response and token-usage receipt hashes

Legacy schema v1 and a top-level <code>repeats</code> field are rejected. A trusted
OpenSSH <code>ssh-rsa</code> key and attestor identity must be supplied out of band.

~~~powershell
uv run python -m eval.attestation payload --attestation <unsigned-or-signed-attestation-v2.json> > payload.json

uv run python -m eval.attestation verify --attestation <signed-attestation-v2.json> --trusted-public-key <independent-attestor.pub> --deployed-sha <exact-deployment-sha> --dataset-manifest-sha256 <frozen-private-manifest-sha256> --attestor <trusted-attestor-identity>
~~~

Verification recomputes all public arithmetic, policy gates, McNemar significance,
cell/pool aggregation, live-Qwen limits, source hashes, repository tree, and
evaluated/deployed SHA equality. It cannot establish independence by itself; that
claim is accepted only from the pre-trusted external signer. Until such a real signed
receipt exists, <code>promotion_status</code> remains <code>HOLD</code>.

## Production candidate adapter

`eval.run` can exercise real Librarian code either in a C-only conformance receipt or
against all three fair baselines:

```powershell
uv run python -m eval.run ... --policies C `
  --candidate-factory src.librarian.eval_adapter:create_adapter `
  --dataset-manifest <dataset>/dataset-manifest.json

uv run python -m eval.run ... --policies B0 B1 B2 C `
  --candidate-factory src.librarian.eval_adapter:create_adapter `
  --dataset-manifest <dataset>/dataset-manifest.json
```

The factory is called as:

```python
create_adapter(policy_id="C", policy_config={...})
```

It returns an object with:

```python
run_case(*, case: dict, extraction: dict, repeat: int) -> list[dict]
```

Each checkpoint result must contain:

- `scenario_id`, `checkpoint_id`, `answer`, `abstained`
- `facts`: `{key, value, claim_ids}` records
- `citations`: source IDs
- `memory_state`: `{key, value, status, source_ids}` records
- `transitions`: auditable state-transition records
- `trace`: at least `loaded_source_ids`, `context_tokens`, `prompt_tokens`,
  `completion_tokens`, and `total_tokens`. Production C must also expose
  `wire_page_citations` and `wire_evidence_source_ids` at every checkpoint;
  the evaluator requires full receipt coverage and exact source-ID fidelity.

The included adapter creates a fresh temporary memory root per scenario, exercises the
real ingest/store/query lifecycle, and reconstructs its store on checkpoints marked
`restart`. It uses `checkpoint.as_of` instead of wall clock time and receives no gold
path or promotion thresholds. Its frozen router supplies only atomic extraction,
returns `unresolved` for ambiguous relation arbitration, and selects claims from
prompt-visible question text. Actual Qwen routing and tokens belong in a separate live
lane.

Production conformance requires `transition_ledger_integrity == 1.0` and zero
transition-ledger violations in addition to exact lifecycle state, answer,
abstention, citation, retrieval, and false-forget gates.

## Self-tests

```powershell
uv run pytest eval/tests -q
```

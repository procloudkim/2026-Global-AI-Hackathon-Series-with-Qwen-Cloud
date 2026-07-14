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

## Private holdout

Use a high-entropy secret with at least 16 characters. Never place it in a command
argument, file, issue, or log.

```powershell
$env:HOLDOUT_SEED = '<user-owned-high-entropy-secret>'
uv run python -m eval.generate --split holdout --commitment-only
uv run python -m eval.generate --split holdout --output-dir eval/private/holdout-v1
Remove-Item Env:HOLDOUT_SEED
```

The manifest stores only the SHA-256 commitment, artifact hashes, current Git commit,
and a candidate tree hash. `eval/private/` is ignored by Git. Materializing a holdout
before candidate freeze, editing candidate code afterward, or changing policy/gates
invalidates the holdout.

Run and evaluate the policy-comparison lane with the same commands as dev, pointing
at the private files and passing
`--candidate-factory src.librarian.eval_adapter:create_adapter`.
Do not pass `gold.jsonl` to `eval.run`; its CLI has no gold argument and rejects known
oracle fields if they appear under another filename.

For a valid holdout boundary, copy only `runner-inputs/` plus candidate code into a
separate runner environment and keep `evaluator-only/` outside that environment. The
allowlisted in-process factory and its module hash prevent an arbitrary adapter from
entering a receipt. The included local runner is not an operating-system sandbox: its
manifest records `runner_process_isolation: false`, so every local holdout result is
`NOT_ELIGIBLE_GOLD_NOT_ISOLATED` regardless of its metric values.
The evaluator rejects a local manifest whose boolean is edited to `true`; runner
self-attestation is never accepted as isolation evidence. A future externally
attested lane must bind its trusted receipt to the dataset, outputs, candidate tree,
and run-manifest hashes and use a separate verifier.

### Public promotion attestation

An independent evaluator may publish only an aggregate receipt conforming to
`eval/attestation.schema.json`. The receipt contains no seed, seed commitment, oracle
rows, oracle digest, or scenario-level result. It binds the frozen dataset manifest,
runner inputs, outputs, policy, aggregate metrics, evaluated candidate tree, evaluated
Git SHA, and deployed Git SHA. Qwen is explicitly excluded from both oracle generation
and pass/fail judging.

The attestor signs the canonical output of `payload` with a pre-registered RSA key.
The trusted OpenSSH `ssh-rsa` public key is supplied to the verifier out of band; a key
embedded only in the receipt is not trusted.

```powershell
uv run python -m eval.attestation payload `
  --attestation <unsigned-or-signed-attestation.json> > payload.json

uv run python -m eval.attestation verify `
  --attestation <signed-attestation.json> `
  --trusted-public-key <independent-attestor.pub> `
  --deployed-sha <exact-deployment-sha> `
  --dataset-manifest-sha256 <frozen-private-manifest-sha256> `
  --attestor <trusted-attestor-identity>
```

Verification recomputes every repeat decision using the existing production
comparison gates and kill rules, requires exactly three repeats and a two-of-three
promotion result, compares the current candidate tree and repository HEAD, and
requires the evaluated SHA to equal the deployed SHA. A code change invalidates the
tree digest. Missing process isolation yields
`NOT_ELIGIBLE_GOLD_NOT_ISOLATED`; no attestation boolean can upgrade a local receipt.

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

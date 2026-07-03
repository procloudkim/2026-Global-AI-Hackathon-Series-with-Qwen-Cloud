# U8 A/B Results

| experiment_id | baseline_tokens | candidate_tokens | token_reduction_rate | baseline_successes | candidate_successes | cps_reduction_rate |
|---|---:|---:|---:|---:|---:|---:|
| L-E1_surgical_vs_fullread | 1821 | 1643 | 0.0977 | 3 | 3 | None |
| L-E2_smallfirst_vs_heavyonly | 1439 | 1654 | -0.1494 | 3 | 3 | None |
| L-E3_contract_vs_freeform | 3855 | 1655 | 0.5707 | 3 | 3 | None |

> Note: estimated cost fields require BENCH_INPUT_PRICE_PER_1M / BENCH_OUTPUT_PRICE_PER_1M env vars.
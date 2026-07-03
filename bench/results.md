# U8 A/B Results

| experiment_id | baseline_tokens | candidate_tokens | token_reduction_rate | baseline_successes | candidate_successes | cps_reduction_rate |
|---|---:|---:|---:|---:|---:|---:|
| L-E1_surgical_vs_fullread | 6861 | 6658 | 0.0296 | 6 | 11 | None |
| L-E2_smallfirst_vs_heavyonly | 5149 | 6701 | -0.3014 | 7 | 11 | None |
| L-E3_contract_vs_freeform | 14939 | 6669 | 0.5536 | 12 | 11 | None |

> Note: estimated cost fields require BENCH_INPUT_PRICE_PER_1M / BENCH_OUTPUT_PRICE_PER_1M env vars.
# ADR-13: 독립 private holdout과 통계적 promotion gate

- 상태: Proposed — local enforcement implemented, external evidence pending
- 작성일: 2026-07-14
- 리서치 확인일: 2026-07-14
- 결정 범위: Track 1 Librarian의 B2 대비 기술 우위와 promotion 판정
- 결정권자: repository owner
- 독립 평가 책임자: 미지정
- 선행 결정: ADR-8, ADR-9, ADR-12
- 대체 대상: 현재 24-case same-builder holdout의 promotion 자격

## 1. 결정 요약

현재 공개 dev 8-case 결과는 Librarian의 lifecycle 계약을 빠르게 깨뜨리는
회귀 검증으로 유지한다. 이 결과와 동일 데이터의 deterministic replay는
promotion 근거로 사용하지 않는다.

Track 1 우위 주장은 다음 조건을 모두 충족한 경우에만 허용한다.

1. 프로젝트 구현자가 만들지 않은 384-case sealed holdout
2. naturalistic pool과 adversarial/metamorphic pool의 분리
3. 독립 작성자, 독립 gold adjudicator, 독립 실행자
4. B2와 C의 동일 입력·추출·answer 조건 및 scenario-level paired 비교
5. 사전 등록된 exact McNemar 검정, effect-size, confidence interval
6. stale leakage, false forgetting, citation, transition, ledger의 AND gate
7. 실제 Qwen을 사용하는 사전 고정 24-case live subset
8. 외부 서명 attestation과 candidate/deployed SHA 동일성

위 조건이 갖춰지기 전 상태는 다음과 같다.

> promotion_status = HOLD

## 2. 현재 증거와 결함

### 2.1 관찰된 결과

로컬 receipt는
[eval/runs/deepcheck-dev-report/metrics.json](../../eval/runs/deepcheck-dev-report/metrics.json)에
있다.

| 항목 | 현재 관찰값 | 증명 가능한 범위 |
|---|---:|---|
| split | dev | 공개 회귀 데이터 |
| scenario | 8 | type당 1 variant |
| C scenario success | 8/8, 1.0 | 공개 synthetic behavior |
| B2 scenario success | 5/8, 0.625 | 같은 공개 synthetic behavior |
| C−B2 delta | +0.375 | 이 8개 사례에 한정 |
| C stale leakage | 0 | 이 8개 사례에 한정 |
| C false forgetting | 0 | 이 8개 사례에 한정 |
| repeat 0, 1, 2 | 완전히 동일 | deterministic reproducibility |
| gate | NOT_ELIGIBLE_DEV_OR_MISSING_REPEATS | promotion 불가 |
| promotion | HOLD | 올바른 현재 판정 |

scenario가 분석 단위다. 각 scenario의 checkpoint 24개를 독립 표본 24개로
계산하지 않는다.

### 2.2 구조적 원인

- [eval/generate.py](../../eval/generate.py)는 dev와 holdout 모두
  [eval/scenarios.py](../../eval/scenarios.py)의 동일한 build_dataset 함수를
  호출한다.
- build_dataset은 동일한 scenario builder에서 case, frozen extraction,
  oracle gold를 한 번에 만든다.
- [eval/policy.json](../../eval/policy.json)의 holdout은 seed와 variant 수만
  바꾼다. seed 비공개는 값 노출을 막지만 collection recipe 독립성을 만들지
  못한다.
- 현재 3회 replay는 같은 입력과 deterministic adapter를 반복한다.
  재현성은 증명하지만 독립 표본이나 분산 추정치는 아니다.
- [eval/attestation.py](../../eval/attestation.py)는 외부 서명을 검증할 수
  있으나, 실제 독립 evaluator·외부 gold·서명된 promotion receipt는 아직 없다.

## 3. 독립성 누수 감사

Mandela 8-pattern taxonomy 중 실제로 발화한 패턴만 기록한다.

| 발화 패턴 | 현재 증거 | 독립성 수정 |
|---|---|---|
| Tautology | 동일 builder가 case와 oracle을 함께 만든다. runner가 gold를 직접 보지는 않지만 평가 bucket과 정답 recipe가 프로젝트 내부에서 닫혀 있다. | 외부 작성자가 input stream을 만들고 별도 adjudicator가 gold를 확정한다. scorer는 확정된 외부 gold만 읽는다. |
| Verifier = designer | 프로젝트가 taxonomy, generator, oracle, scorer, gate를 모두 소유한다. | 구현 소유권이 없는 evaluator가 dataset, gold, 실행 환경, signing key를 소유한다. |
| Shared-pool bias | dev와 holdout이 동일한 8개 builder family에서 나온다. | 기존 builder를 사용하지 않는 두 외부 provenance pool을 만든다. |

Shared hallucination은 현재 발화하지 않았다. Qwen은 gold 생성자나 judge가
아니며 deterministic scorer가 사용된다. 이 장점은 유지한다.

## 4. 공식 논문에서 추출한 설계 근거

아래의 “논문 확인 사실”과 “프로젝트 적용”을 구분한다. 논문이 Librarian의
우수성을 보증한다는 뜻이 아니며, 프로젝트 적용은 이 ADR의 설계 추론이다.

| 출처 | 논문 확인 사실 | 프로젝트 적용 |
|---|---|---|
| Ribeiro et al., CheckList, ACL 2020 Best Overall Paper | held-out accuracy만으로는 행동 결함을 놓칠 수 있으며 capability × MFT/INV/DIR matrix를 제안한다. | 8개 lifecycle capability를 aggregate score 아래 숨기지 않고 naturalistic·MFT·invariance·directional cell로 보고한다. |
| Recht et al., ICML 2019 | 오래 재사용된 benchmark를 원래 수집 절차에 가깝게 새로 구성했을 때 성능 하락을 관찰했다. 논문은 그 하락을 단순 adaptivity가 아니라 새 표본 난이도 차이와 연결했다. | 공개 dev generator의 seed만 바꾸지 않고, 독립 작성자가 새 표본을 구성한다. 분포 차이도 결과에 함께 보고한다. |
| Wu et al., LongMemEval, ICLR 2025 | 500개 curated question으로 extraction, multi-session reasoning, temporal reasoning, knowledge update, abstention의 다섯 능력을 평가한다. | 현재 8개 lifecycle type에 multi-session, temporal, update, abstention coverage tag를 사전 등록한다. |
| Maharana et al., LoCoMo, ACL 2024 | temporal event graph에 grounded된 장기 대화를 만들고 human annotator가 long-range consistency와 grounding을 검증·수정했다. | naturalistic pool은 event timeline과 source provenance를 먼저 만들고, 사람이 stream·gold의 장기 일관성을 검수한다. |
| Li et al., LoCoMo-Plus, ACL 2026 | surface factual recall만으로는 cue–trigger semantic disconnect와 latent constraint 적용 실패를 잡기 어렵다고 보고한다. | non-numeric change에는 명시적 숫자 교체뿐 아니라 preference, goal, exception, latent constraint를 포함한다. |
| Cheng et al., AMemGym, ICLR 2026 Poster | static off-policy context만의 평가 한계를 지적하고 structured state evolution을 포함한 interactive evaluation을 제안한다. | holdout의 일부는 query가 다음 update와 state transition을 유발하는 on-policy vertical slice로 만든다. |
| Kiela et al., Dynabench, NAACL 2021 | human-and-model-in-the-loop 방식으로 현재 모델이 실패하지만 사람이 유효하다고 판단하는 사례를 동적으로 수집한다. | adversarial 작성자는 sacrificial shadow policy나 이전 candidate를 공격하되 최종 frozen B2/C 출력은 보지 않는다. 만들어진 case는 최종 실행 전에 봉인한다. |
| Bowman and Dahl, NAACL 2021 | adversarial/OOD만으로는 측정하려는 능력을 흐릴 수 있으며 annotation reliability, dataset size, benchmark design이 중요하다고 주장한다. | adversarial-only를 채택하지 않고 naturalistic pool과 균형을 맞추며, 이중 작성과 제3자 adjudication을 둔다. |
| Bouthillier et al., MLSys 2021 | data sampling, initialization, hyperparameter choice 등 여러 변동원을 고정한 반복은 benchmark variance를 충분히 반영하지 못할 수 있다. | 동일 deterministic replay 3회를 독립 n으로 계산하지 않는다. scenario sampling과 provider variability를 별도 층으로 보고한다. |
| Dror et al., ACL 2018 | McNemar test는 같은 사례에 대한 두 시스템의 paired binary outcome에 맞는 검정이라고 정리한다. | strict scenario success의 C/B2 paired table에 exact two-sided McNemar test를 사용한다. |
| Koehn, EMNLP 2004 | 같은 test item에서 시스템 차이를 평가하기 위한 bootstrap resampling 방법을 제시한다. | C−B2 effect size의 95% interval은 capability×pool stratified paired bootstrap으로 보고한다. |

## 5. 결정한 평가 설계

### 5.1 네 개 증거 층

| 층 | 목적 | 표본 | promotion 기여 |
|---|---|---:|---|
| D: public dev regression | 빠른 contract 회귀 | 현재 8 | 없음 |
| P: sealed policy superiority | B2 대비 C의 독립 우위 | 384 | primary |
| Q: live Qwen robustness | 실제 Qwen answer behavior | P에서 사전 고정한 24 | AND gate |
| R: deployed restart persistence | Alibaba restart 후 같은 active memory | 별도 vertical slice | AND gate |

D의 PASS를 P, Q, R의 PASS로 표현하지 않는다.

### 5.2 sealed holdout 구성

현재 8개 scenario type을 유지한다.

1. explicit_supersession
2. future_effective
3. correction_rollback
4. scope_coexistence
5. unresolved_conflict
6. non_numeric_change
7. distractor_retrieval
8. duplicate_restore

각 type은 다음 두 provenance pool에서 24개씩 가져온다.

- N pool: 외부 사람이 작성한 naturalistic multi-session update stream
- A pool: 별도 외부 사람이 작성한 adversarial/metamorphic stream

총 표본 수는 8 types × 2 pools × 24 scenarios = 384다.

각 scenario는 다음을 가진다.

- 최소 4개 session과 시간 순서가 있는 update stream
- immutable source ID와 evidence span
- 하나 이상의 current-fact query
- stale candidate와 preserved unrelated claim
- expected active/superseded/conflicted state
- expected citation source
- expected decision-ledger transition
- MFT, INV, DIR 중 해당 test-type tag
- temporal, multi-session, latent-constraint, abstention 중 해당 capability tag

N과 A의 작성자 pool을 분리한다. 각 case의 gold는 원 작성자가 아닌 두 명이
독립 작성하고, 불일치는 세 번째 adjudicator가 해결한다. adjudicator에게는
B2/C 이름, 출력, repository 구현 설명을 제공하지 않는다.

### 5.3 동결과 격리 순서

1. evaluator가 공개 가능한 taxonomy, annotation guide, acceptance rule의
   hash를 먼저 고정한다.
2. 프로젝트가 candidate commit SHA, tree hash, B2/C implementation hash,
   extraction snapshot version, answer prompt hash, model ID, top-K=3,
   context budget=4000을 고정한다.
3. evaluator가 private dataset과 gold를 materialize하고 commitment를
   타임스탬프와 함께 봉인한다.
4. candidate에는 random case ID, update stream, query, allowed source만
   read-only로 제공한다.
5. gold, scenario type, capability tag, thresholds, adjudication log, seed,
   signing key는 별도 scorer process에만 둔다.
6. B2와 C는 무작위 순서와 blind policy ID로 같은 scenario를 실행한다.
7. scorer가 output을 결합하고 공개 가능한 aggregate receipt에 서명한다.
8. P와 Q lane이 모두 끝날 때까지 중간 score, failure, aggregate를
   프로젝트 팀에 공개하지 않는다.
9. 결과를 확인한 뒤 구현이 바뀌면 해당 holdout은 그 candidate에 대해서만
   효력을 잃는다. 다음 candidate에는 새 case pool 또는 미사용 sealed tranche가
   필요하다.

### 5.4 공정 비교 계약

P lane은 version/hash가 고정된 deterministic structured answer adapter를 사용해
memory policy 차이를 격리한다. Q lane만 실제 Qwen answer model을 사용한다.
두 lane의 결과는 합산하지 않으며, 각 lane 안에서 B2와 C의 answer component가
동일해야 한다.

B2와 C 사이에서 다음을 동일하게 고정한다.

- update stream과 immutable source
- frozen atomic extraction
- lane-specific answer component
- answer prompt와 system prompt
- temperature와 sampling parameter
- top-K
- context budget
- timeout과 retry
- citation validator
- scenario scorer

달라질 수 있는 것은 memory policy, canonical state, retrieval-selected context뿐이다.

## 6. 통계 계약

### 6.1 분석 단위와 primary endpoint

분석 단위는 scenario다. checkpoint, claim, token, deterministic repeat를 별도
표본으로 부풀리지 않는다.

scenario_success는 다음이 모두 참인 binary outcome이다.

- current answer가 gold와 일치
- citation이 정답 source를 가리킴
- stale claim이 answer와 selected context에 없음
- 보존해야 할 unrelated claim이 유지됨
- expected lifecycle state와 transition이 일치
- decision ledger가 evidence와 transition을 포함

paired table을 다음과 같이 정의한다.

- n10: C success, B2 failure
- n01: C failure, B2 success
- delta = (n10 − n01) / N

primary null은 p10 = p01이다. alpha=0.05의 exact two-sided McNemar test를
사용한다.

### 6.2 표본 수 근거

기존 minimum effect delta=0.15를 유지한다. 최대 discordance planning
case인 p10=0.575, p01=0.425에서 exact McNemar power를 계산하면 다음과 같다.

| N | 계산 power |
|---:|---:|
| 24 | 0.062 |
| 96 | 0.249 |
| 192 | 0.496 |
| 384 | 0.832 |

이 값은 프로젝트의 사전 power 계산이며 논문에서 가져온 수치가 아니다.
384는 현재 dev delta=0.375를 재사용하지 않고 minimum delta=0.15에 맞춘
보수적 planning bound다. 독립 evaluator가 다른 nuisance assumption이나
sequential design을 제안하려면 dataset materialization 전에 ADR amendment,
계산 코드, alpha-spending rule을 공개해야 한다.

### 6.3 promotion AND gate

P lane은 다음을 모두 만족해야 PROMOTE 후보가 된다.

- N=384 및 16개 type×pool cell 완전성
- C scenario success rate ≥ 0.875
- C−B2 delta ≥ 0.15
- exact McNemar p ≤ 0.05
- 10,000회 capability×pool stratified paired bootstrap의 95% delta lower bound > 0
- N pool과 A pool 각각 C−B2 delta > 0
- 16개 cell 중 C−B2 delta < 0인 cell이 없음
- stale leakage rate ≤ 0.05
- valid-claim false-forget count = 0
- scope false-forget count = 0
- citation entailment ≥ 0.95
- retrieval recall@K ≥ 0.90
- state-transition accuracy = 1.0, violation count = 0
- abstention accuracy = 1.0
- transition-ledger integrity = 1.0, violation count = 0
- candidate SHA/tree, dataset commitment, policy hash, prompt/model hash 일치
- runner_and_candidate_isolated_from_oracle
- 외부 trusted key의 유효한 signature

B0/B1과 token efficiency는 계속 보고하되 primary superiority 판정에는 넣지
않는다. 여러 metric을 가중합한 composite score도 만들지 않는다.

### 6.4 HOLD와 KILL

다음은 HOLD다.

- 독립 evaluator 또는 외부 gold가 없음
- N<384 또는 cell 누락
- p-value/CI/effect 중 하나라도 primary gate 미달
- 한 provenance pool 또는 한 cell에서 C가 B2보다 나쁨
- sample-size rule, threshold, prompt, candidate를 unblinding 후 변경
- gold 또는 case recipe가 candidate/team/log에 노출
- live Qwen Q lane만 실패하고 안전성 kill은 발화하지 않음
- receipt signature 또는 SHA chain을 검증할 수 없음

다음은 KILL이다.

- C≤B2 overall
- stale leakage >0.10
- valid claim false-forget count >0
- citation·transition·ledger를 조작하거나 oracle에 접근한 증거
- 배포 candidate와 평가 candidate가 다름

KILL 이후 같은 case를 보고 수정한 candidate는 해당 holdout으로 재평가하지
않는다.

## 7. live Qwen subset

P dataset을 봉인할 때 type당 3개, 총 24개를 Q lane으로 사전 고정한다.
N/A pool을 균형 배정한다.

- B2와 C 각각 동일 Qwen model, prompt, top-K, context budget 사용
- 24 scenarios × 2 policies × 3 provider executions = maximum 144 calls
- max output tokens = 256/call
- timeout = 30 seconds
- retry = 0
- raw provider response, usage, latency, request hash 보존
- Qwen은 answer model일 뿐 gold generator나 pass/fail judge가 아님
- 세 실행은 provider robustness 분포이며 N=72로 계산하지 않음
- 최소 2/3 run-level gate가 일치해야 Q lane PASS
- 무료 quota와 사용 승인이 확인되지 않으면 호출하지 않고 HOLD

Q lane의 24-case 결과로 delta의 통계적 우위를 주장하지 않는다. 우위 추론은
P lane, 실제 provider behavior는 Q lane이 담당한다.

## 8. 공개·비공개 artifact 계약

### 8.1 외부 evaluator만 보유

- raw scenario와 unused tranche
- gold, scenario type, capability/test tag
- author identity와 adjudication log
- seed와 materialization recipe
- scorer private key
- scenario-level output과 failure detail

### 8.2 candidate runner가 받는 것

- random case ID
- update stream과 immutable source
- query
- allowed public execution configuration

### 8.3 public repository에 공개 가능한 것

- protocol hash와 dataset commitment
- candidate commit/tree hash
- B2/C implementation hash
- answer prompt/model/top-K/context hash
- pool/type별 aggregate count와 metric
- n10, n01, delta, exact p-value, bootstrap interval
- safety-gate aggregate
- Qwen call/token/latency aggregate
- isolation statement
- promotion decision
- evaluator public-key ID와 signature

seed, gold, case text, unused tranche, private key는 public repository에 넣지 않는다.

## 9. 거부한 대안

| 대안 | 거부 이유 |
|---|---|
| 현재 builder를 24→384로 확대 | volume은 늘지만 Tautology와 Shared-pool bias가 남는다. |
| secret seed만 교체 | 값은 숨겨도 collection recipe는 동일하다. |
| 동일 deterministic run 3회 중 2회 PASS | 재현성이지 독립 표본이 아니다. |
| public benchmark만 사용 | Librarian의 claim lifecycle·citation·ledger contract와 정확히 맞지 않고 공개 오염 가능성도 있다. |
| adversarial-only | 실제 분포의 능력을 흐릴 수 있어 naturalistic pool과 함께 사용해야 한다. |
| Qwen-as-a-judge | 시스템 구성 요소가 gold를 만들고 판정하는 순환을 만든다. |
| aggregate success 하나만 공개 | 특정 capability나 provenance pool의 실패를 숨긴다. |
| weighted composite score | load-bearing AND gate 하나의 실패를 다른 점수로 상쇄한다. |

## 10. 구현 매핑

2026-07-14에 local enforcement까지 구현했다. 이것은 독립 평가 결과를 만든
것이 아니라, 독립 증거가 없으면 promotion할 수 없도록 public scorer와 verifier를
고정한 것이다.

| 위치 | 구현 상태 |
|---|---|
| eval/policy.json | 구현: schema 2.0, P/Q lane, N=384, paired statistic, HOLD/KILL rule |
| eval/generate.py | 구현: repository-generated holdout을 same_builder_diagnostic_only와 promotion_eligible=false로 고정 |
| eval/private-paired-results.schema.json | 구현: 외부 evaluator의 paired B2/C scenario outcome 계약 |
| eval/private_promotion.py | 구현: strict scenario success 파생, 16-cell 검증, exact McNemar, stratified paired bootstrap |
| eval/attestation.schema.json | 구현: v2 aggregate-only public receipt, repeats field 제거 |
| eval/attestation.py | 구현: v1 폐기, 외부 role 분리, source/SHA/statistics/live-Qwen 검증 |
| eval/README.md | 구현: diagnostic holdout과 external sealed promotion 경계 분리 |
| eval/tests/ | 구현: same-builder, repeat inflation, statistic tamper, SHA/signature/Qwen boundary negative tests |
| external evaluator workspace | 미구현: private authoring, adjudication, scorer execution, signing key |
| 실제 384-case 및 live-Qwen receipt | 미실행: 독립 evaluator와 별도 승인 필요 |

## 11. ADR acceptance gate

Proposed에서 Accepted로 바꾸기 전에 다음이 필요하다.

1. 구현 소유권이 없는 independent evaluator 실명 또는 기관 지정
2. 두 author pool과 adjudicator 지정
3. 384-case 제작 시간·비용 승인
4. power 계산 코드의 독립 재현
5. annotation guide와 gold disagreement rule 승인
6. 외부 storage, scorer process, signing key 격리 증명
7. 사용하지 않을 sacrificial dataset으로 end-to-end attestation dry run
8. live Qwen 144-call 최대 budget과 free-quota 확인

하나라도 없으면 이 ADR은 Proposed이며 promotion_status는 HOLD다.

## 12. 결과와 트레이드오프

### 긍정

- 현재 완벽한 8/8을 과장하지 않고 실제 일반화 우위를 검정한다.
- B2와 C의 paired design으로 사례 난이도를 통제한다.
- naturalistic와 adversarial 실패를 분리해 진단한다.
- Qwen provider 변동과 scenario sampling을 혼동하지 않는다.
- 외부 서명과 SHA chain으로 평가 candidate와 배포 candidate를 연결한다.

### 비용

- 384개 case의 외부 작성과 adjudication이 필요하다.
- live subset은 최대 144회의 Qwen 호출을 요구한다.
- 결과를 본 뒤 수정하면 새 sealed tranche가 필요하다.
- 독립 evaluator가 없으면 기술적으로 완성돼도 promotion은 HOLD다.

## 13. 참고문헌

모든 링크는 2026-07-14에 공식 proceedings 또는 공식 conference page에서
확인했다.

1. Ribeiro et al. “Beyond Accuracy: Behavioral Testing of NLP Models with CheckList.”
   ACL 2020. https://aclanthology.org/2020.acl-main.442/
2. Recht et al. “Do ImageNet Classifiers Generalize to ImageNet?”
   ICML 2019. https://proceedings.mlr.press/v97/recht19a.html
3. Wu et al. “LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory.”
   ICLR 2025. https://openreview.net/pdf?id=pZiyCaVuti
4. Maharana et al. “Evaluating Very Long-Term Conversational Memory of LLM Agents.”
   ACL 2024. https://aclanthology.org/2024.acl-long.747/
5. Li et al. “Locomo-Plus: Beyond-Factual Cognitive Memory Evaluation Framework for LLM Agents.”
   ACL 2026. https://aclanthology.org/2026.acl-long.1150/
6. Cheng et al. “AMemGym: Interactive Memory Benchmarking for Assistants in Long-Horizon Conversations.”
   ICLR 2026 Poster. https://openreview.net/forum?id=sfrVLzsmlf
7. Kiela et al. “Dynabench: Rethinking Benchmarking in NLP.”
   NAACL 2021. https://aclanthology.org/2021.naacl-main.324/
8. Bowman and Dahl. “What Will it Take to Fix Benchmarking in Natural Language Understanding?”
   NAACL 2021. https://aclanthology.org/2021.naacl-main.385/
9. Bouthillier et al. “Accounting for Variance in Machine Learning Benchmarks.”
   MLSys 2021. https://proceedings.mlsys.org/paper_files/paper/2021/hash/0184b0cd3cfb185989f858a1d9f5c1eb-Abstract.html
10. Dror et al. “The Hitchhiker’s Guide to Testing Statistical Significance in Natural Language Processing.”
    ACL 2018. https://aclanthology.org/P18-1128/
11. Koehn. “Statistical Significance Tests for Machine Translation Evaluation.”
    EMNLP 2004. https://aclanthology.org/W04-3250/

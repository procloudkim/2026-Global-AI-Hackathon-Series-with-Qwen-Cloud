# 토큰 비용과 지연을 함께 줄이는 하네스 엔지니어링 보고서

## 핵심 요약

이 보고서의 결론은 단순합니다. **토큰 수를 줄이는 일**과 **토큰 단가를 낮추는 일**은 다른 문제이며, 에이전틱 코딩 하네스에서는 둘을 동시에 설계해야 가장 큰 효과가 납니다. 현재 공식 문서와 고신뢰 연구를 종합하면, 가장 재현성이 높은 절감 레버는 다음 일곱 가지입니다. **정적 prefix를 앞에 두어 prompt caching 적중률을 높이는 것**, **작은 모델→큰 모델 escalation 라우팅**, **전체 파일 투입 대신 diff/symbol/path 중심 retrieval**, **장기 세션 compaction 및 체크포인트**, **출력 계약과 hard cap으로 output token 억제**, **tool/search budget과 batched reads**, **cost-per-success를 기준으로 한 eval-driven retry**입니다. OpenAI, Anthropic, Azure, AWS, Google 모두 반복 prefix 재사용·비동기 배치·컨텍스트 관리·토큰 사전계산을 주요 비용 최적화 수단으로 문서화하고 있습니다. citeturn18view1turn23view2turn19view0turn18view2turn18view4turn19view11turn18view13turn18view14

가장 먼저 적용할 우선순위는 **로그와 측정 체계**입니다. 이유는 간단합니다. `previous_response_id`를 써도 OpenAI는 체인상의 이전 입력 토큰을 계속 과금하며, Anthropic의 thinking 표시 생략도 비용이 아니라 지연만 줄이고, 프롬프트 캐싱도 최소 길이·동일 prefix·요청 속도 조건을 만족해야 의미가 있기 때문입니다. 즉, “좋아 보이는 기법”을 넣는 것보다 **실제 hit ratio, cached input ratio, output cap 초과율, tool call 분포, 성공당 비용**을 먼저 계측해야 합니다. citeturn24view0turn28view7turn23view2turn22view1

단기적으로는 프롬프트 구조 재배치, 출력 제한, diff-only 컨텍스트, 실패 케이스만 재시도, 배치 평가 전환이 가장 빠른 ROI를 냅니다. 중기적으로는 라우터, cache key 체계, compaction, tool budgeter가 필요합니다. 장기적으로는 eval harness, 지속 A/B, semantic/result caching, 제한적 distillation 또는 vendor-managed 최적화가 대상입니다. OpenAI는 Batch API에 50% 할인, Flex를 Batch와 같은 가격대의 저우선 처리로 제공하고, Anthropic은 prompt caching과 auto-compact, effort 제어를 문서화하며, Google과 AWS는 context/prompt caching·batch inference·token counting을 제공하고 있습니다. 다만 벤더가 말하는 “up to X% 절감”은 **vendor claim**으로 취급해야 하며, 실제 절감률은 프롬프트 재사용률·세션 형태·작업 난이도에 따라 크게 달라집니다. citeturn19view0turn18view2turn18view1turn18view9turn20view0turn18view13turn18view12

아래 표는 지금 당장 적용할 행동 우선순위를 요약한 것입니다.

| 기간 | 우선 적용 항목 | 기대 효과 | 핵심 리스크 | 검증 지표 |
|---|---|---:|---|---|
| 단기 | 정적 prefix 선배치 + prompt caching 관측 | 높음 | prefix 변동으로 hit 저하 | cached input ratio, TTFT, request cost citeturn23view2turn23view3turn30view3 |
| 단기 | diff/symbol/path 기반 context packer | 높음 | 필요한 맥락 누락 | pass rate, retry count, context size/turn citeturn38view2turn38view4 |
| 단기 | strict JSON / max output / reviewer budget | 중간~높음 | 과도한 제약으로 품질 저하 | output tokens, validation fail rate citeturn36view1turn20view6 |
| 단기 | failing-test only debug loop | 높음 | 재현 정보 부족 | cost per successful fix, retry count |
| 중기 | small-first model routing | 높음 | 잘못된 triage | accuracy by task class, escalation rate citeturn33search2turn29view1turn12search9turn13search0 |
| 중기 | compaction + checkpoint summary | 높음 | 요약 손실 | post-compact success rate, stale-summary incidents citeturn18view4turn20view9turn19view11 |
| 중기 | tool/search budget + batched reads | 중간 | 너무 이른 stop | tool calls/session, needless reads rate citeturn38view1turn36view3turn34view2 |
| 장기 | semantic/result caching | 중간 | stale/approximate mismatch | semantic hit precision, stale-hit rate citeturn13search16turn13search5 |
| 장기 | Batch/Flex offline evaluation | 높음 | 사용자 facing 부적합 | eval dollar/run, queue delay citeturn19view0turn18view2turn19view1turn19view2 |
| 장기 | fine-tuning or distillation | 불확실 | 지원 상태 변동 | cost amortization, maintenance load citeturn32view0 |

## 비용 모델과 관측 지표

LLM API 비용은 보통 **입력 토큰**, **캐시된 입력 토큰**, **출력 토큰**, 그리고 도구 호출이나 내장도구 사용에 따른 **별도 수수료/세션 비용**의 합으로 계산됩니다. OpenAI 가격표는 flagship 모델에 대해 `Input / Cached input / Output` 단가를 분리해 제시하고, 웹 검색·파일 검색·컨테이너 같은 도구는 별도 line item으로 과금합니다. AWS Bedrock은 CUR에서 `input`, `output`, `cache read`, `cache write`를 별도 토큰 타입으로 정산하라고 명시합니다. Anthropic은 tool use의 추가 토큰이 `tools` 스키마, `tool_use`, `tool_result` 블록, 그리고 자동 삽입되는 도구 시스템 프롬프트에서 발생한다고 설명합니다. citeturn32view0turn27view2turn19view8turn27view4

OpenAI reasoning 모델의 경우 응답 객체는 `completion_tokens_details.reasoning_tokens`를 노출하지만, 가격표는 별도의 reasoning 단가 열을 두지 않고 `output` 단가만 제시합니다. 따라서 실무에서는 **reasoning tokens를 “출력 계정 하위의 비용 driver”로 기록**하는 편이 안전합니다. Anthropic은 더 명확합니다. thinking을 `display: "omitted"`로 감춰도 **thinking tokens 전체에 대해 과금**된다고 분명히 밝히며, summarized thinking 역시 보이는 토큰 수가 아니라 원래 thinking 과정 기준으로 청구됩니다. citeturn23view0turn18view5turn32view0turn28view7

`previous_response_id`는 **개발 편의와 상태 관리**에는 도움이 되지만, OpenAI 공식 문서는 이것이 과금 토큰을 줄여준다고 말하지 않습니다. 오히려 체인 전체의 이전 입력 토큰이 계속 입력 토큰으로 과금된다고 명시합니다. 즉, 이것은 “메모리 API”이지 “무료 문맥 유지 기능”이 아닙니다. 토큰 비용을 줄이려면 `previous_response_id` 자체보다 **compaction, trimming, summary checkpoint, retrieval gating**이 필요합니다. citeturn24view0turn18view4turn20view9

아래 공식은 하네스에서 써야 할 실전 비용식입니다.

```text
request_cost
≈ (uncached_input_tokens × input_price)
 + (cached_input_tokens × cached_input_price)
 + (output_tokens × output_price)
 + (tool_call_count × per_tool_fee if applicable)
 + container/session fees
 + storage/search fees
 + embeddings_cost
```

이 식은 OpenAI·AWS·Anthropic·Google 모두에 맞게 확장 가능합니다. OpenAI는 웹 검색/파일 검색/컨테이너가 별도 항목이며, AWS는 cache read·write를 독립 line item으로 정산해야 하고, Google은 context cache 객체와 배치 예측을 별도 운영 개념으로 둡니다. citeturn32view0turn27view2turn18view13turn19view2

### 토큰 수 절감과 토큰 단가 절감의 차이

**토큰 수 절감**은 프롬프트와 응답을 더 작게 만드는 일입니다. 예를 들어 diff-only context, summary compaction, max output, tool schema 축소가 여기에 들어갑니다. **토큰 단가 절감**은 같은 토큰이라도 더 싼 가격표에서 처리하는 일입니다. 예를 들어 OpenAI Batch/Flex, Azure Standard vs Priority, Anthropic cached input, Google implicit/explicit cache, AWS batch inference와 service tier 선택이 여기에 해당합니다. 둘은 서로 독립적이므로, 가장 좋은 전략은 “먼저 토큰 수 자체를 깎고, 남은 토큰을 더 싼 티어에서 처리”하는 것입니다. citeturn19view0turn18view2turn27view1turn19view7turn18view13turn27view2

### 최소 관측 지표

하네스는 최소한 아래 항목을 turn 단위와 run 단위로 모두 남겨야 합니다. OpenAI Usage/Cost API는 input/output/cached tokens, request count, service tier, tool usage류를 집계할 수 있고, AWS는 CUR와 invocation logs를 결합하라고 권장합니다. Anthropic과 Google도 token counting과 비용 관리 문서를 통해 사전 카운트와 운영 측정을 지원합니다. citeturn22view1turn22view0turn27view2turn29view0turn18view14

| 메트릭 | 왜 필요한가 | 수집 위치 |
|---|---|---|
| total input tokens | 세션 팽창 추적 | request usage / gateway logs citeturn22view1turn18view6 |
| cached input tokens | 캐시 전략 검증 | `prompt_tokens_details.cached_tokens` 또는 vendor-equivalent citeturn23view1turn30view3 |
| output tokens | 장황한 출력 탐지 | request usage citeturn22view1 |
| reasoning tokens | reasoning effort 비용 driver | completion/output breakdown citeturn23view1turn28view7 |
| tool call count | agent 낭비 탐지 | orchestration layer + usage logs citeturn32view0turn27view4 |
| wall-clock latency | 사용자 체감 속도 | harness timer citeturn19view3 |
| TTFT | 캐시/priority/omitted thinking 효과 측정 | stream events citeturn28view7turn27view1 |
| cost per successful task | 진짜 KPI | ledger join |
| pass/fail rate | 품질 보존 확인 | eval runner citeturn20view7turn20view8 |
| retry count | 루프 과다 여부 | orchestrator |
| cache hit ratio | caching 투자 검증 | ledger aggregation |
| context growth per turn | long-run 폭주 사전 경보 | session state logs |

## 근거 기반 방법론 분류

공식 문서와 연구를 함께 보면, **가장 신뢰할 수 있는 절감 전략은 “prefix 캐시 친화적 구조 + 컨텍스트 축소 + 라우팅 + 평가 기반 제어”**입니다. 반대로 “모든 응답을 자세히 설명하게 하기”, “모든 파일을 매 turn 첨부하기”, “도구를 전부 항상 노출하기”, “처음부터 가장 비싼 모델만 쓰기”는 비용과 지연을 함께 악화시키는 패턴입니다. OpenAI는 shorter outcome-first prompts, dynamic suffix 후배치, fewer requests, filtering context를 권장하고, Anthropic은 context window가 커질수록 recall이 나빠지는 context rot를 언급하며 compaction을 1차 전략으로 둡니다. Codex 가이드는 batched reads·parallel reads·tool response truncation을 권장합니다. citeturn20view6turn38view3turn19view11turn38view1

### 출처 신뢰도 기준

| Tier | 기준 | 이 보고서에서의 사용 원칙 |
|---|---|---|
| Tier 0 | OpenAI/Anthropic/Microsoft/AWS/Google 공식 문서, 공식 API 레퍼런스, 공식 Cookbook | 핵심 주장과 최신 가격·기능 확인에 우선 사용 |
| Tier 1 | peer-reviewed paper, ACL/NAACL/ICLR/NeurIPS/ACM/OpenReview 고신뢰 논문, 공식 GitHub | 공식 문서가 덜 구체적인 설계 원칙 보강 |
| Tier 2 | 엔지니어링 블로그, 벤치마크 리포트, 오픈소스 이슈 | 실무 가설로만 사용 |
| Tier 3 | 커뮤니티 추측, SEO 블로그 | 본문 핵심 주장에 사용하지 않음 |

### 핵심 증거 표

| Claim | Mechanism | Source | Source Tier | Vendor Claim 여부 | 적용 가능성 | 주의점 |
|---|---|---|---|---|---|---|
| OpenAI prompt caching은 1024+ 토큰의 동일 prefix에서 작동한다 | static prefix 재사용으로 prefill 계산 재사용 | OpenAI Prompt Caching docs citeturn23view0turn23view2 | Tier 0 | 부분적으로 예, “up to 80/90%”는 vendor claim citeturn18view1 | 매우 높음 | 1024 미만이면 cached_tokens=0 |
| 정적 내용은 앞, 동적 내용은 뒤에 둬야 cache hit가 난다 | exact prefix match 필요 | OpenAI Prompt Caching docs citeturn23view2turn23view3 | Tier 0 | 아니오 | 매우 높음 | prefix 1글자 차이도 miss 가능 |
| `previous_response_id`는 입력 과금을 없애지 않는다 | 상태 참조와 비용 절감은 별개 | OpenAI Conversation State docs citeturn24view0 | Tier 0 | 아니오 | 매우 높음 | context compaction이 별도 필요 |
| OpenAI Batch API는 50% 저렴하다 | 비동기 처리로 단가 인하 | OpenAI Batch docs citeturn19view0turn32view0 | Tier 0 | 아니오 | 높음 | 24시간 turnaround, 실시간 작업 부적합 |
| OpenAI Flex는 Batch와 같은 할인 가격이지만 느리고 가용성 제약이 있다 | 낮은 우선순위 처리 | OpenAI Flex docs citeturn18view2turn32view0 | Tier 0 | 아니오 | 높음 | user-facing 트래픽 부적합 |
| Anthropic prompt caching은 자동/명시적 둘 다 지원한다 | prefix 재사용 | Anthropic docs citeturn28view2turn19view7 | Tier 0 | 아니오 | 높음 | TTL·모델별 길이 조건 확인 필요 |
| Anthropic cached input은 기본 입력 단가의 10%로 청구된다 | cache read가 저렴 | Anthropic rate limits/pricing docs citeturn28view4turn19view7 | Tier 0 | 아니오 | 높음 | 일부 모델 예외 표식 존재 |
| Anthropic thinking을 숨겨도 비용은 그대로다 | omitted thinking은 latency만 절감 | Anthropic Extended Thinking docs citeturn28view7 | Tier 0 | 아니오 | 매우 높음 | “생각 표시 생략=비용 절감” 오해 금지 |
| Claude Code는 auto-compact를 기본 활성화하고 긴 세션에서 context approaching limit 시 compaction을 사용한다 | 장기 세션 문맥 축소 | Claude Code settings/model docs citeturn20view0turn20view1 | Tier 0 | 아니오 | 높음 | 과도 compaction thrash 주의 |
| OpenAI는 긴 컨텍스트 입력에서 trimming/compression을 권장한다 | redundant history·noisy retrieval 제거 | OpenAI session memory/latency docs citeturn38view2turn38view3 | Tier 0 | 아니오 | 매우 높음 | 과도 trimming은 사실 손실 가능 |
| 도구 스키마와 결과는 실제 토큰 비용을 만든다 | tool schema + tool blocks | Anthropic pricing/tool docs citeturn19view8turn19view9turn27view4 | Tier 0 | 아니오 | 매우 높음 | 모든 툴을 항상 노출하지 말 것 |
| OpenAI `allowed_tools`는 전체 tools 목록 변경 없이 호출 가능한 subset만 노출해 prompt caching 절약에 유리하다 | 상수 tools prefix 유지 | OpenAI Function Calling docs citeturn36view3 | Tier 0 | 아니오 | 높음 | tool subset 설계 필요 |
| JSON은 coding 맥락에 친숙하지만 verbose하며 long-context에서는 XML보다 불리할 수 있다 | 구조화 이점 vs 토큰 오버헤드 | GPT-4.1 Prompting Guide citeturn38view0 | Tier 0 | 아니오 | 높음 | 스키마가 짧고 안정적이면 JSON이 유리할 수도 있음 |
| multi-agent는 specialization 이점이 있지만 nondeterminism과 복잡도를 늘린다 | triage/handoff overhead | OpenAI eval best practices citeturn34view0turn34view2 | Tier 0 | 아니오 | 높음 | eval 없이 도입하면 낭비 가능성 큼 |
| 모델 라우팅/캐스케이드는 비용-품질 trade-off를 개선할 수 있다 | 쉬운 요청은 저가 모델, 어려운 요청만 고가 모델 | FrugalGPT, RouteLLM, routing survey citeturn13search0turn12search9turn14search11 | Tier 1 | 아니오 | 높음 | 2023~2026 연구 결과를 현재 API 가격에 그대로 이식하면 안 됨 |
| prompt compression 연구는 벤치마크에서 유의미한 압축을 보였지만 exact-code 편집에는 위험할 수 있다 | 정보 밀도 향상 | LLMLingua/LongLLMLingua/LLMLingua-2 citeturn14search3turn12search0turn12search4 | Tier 1 | 아니오 | 중간 | 코드·스키마·에러 로그는 손실 민감 |
| speculative decoding은 latency 최적화이며 품질을 바꾸지 않는 방향의 서버 측 기법이다 | draft+verify 병렬화 | Leviathan et al. speculative decoding citeturn12search3turn12search10 | Tier 1 | 아니오 | 낮음 | 대부분 managed API에선 직접 제어 불가 |
| OpenAI Predicted Outputs는 latency를 줄일 수 있으나 rejected prediction tokens는 비용을 늘릴 수 있다 | known output reuse | OpenAI Predicted Outputs docs citeturn27view0 | Tier 0 | 아니오 | 중간 | token saver가 아니라 latency lever로 분류 |

### 토큰 절감 방법 Taxonomy

| Category | Method | Expected Impact | Risk | Best Use Case | Not Suitable When |
|---|---|---:|---|---|---|
| Prompt structure | 정적 system/developer/tool schema를 앞쪽에 고정 | 매우 높음 | prefix drift | 반복되는 세션/하네스 공통 템플릿 | 요청마다 정책이 크게 바뀔 때 citeturn23view2turn30view3 |
| Output control | strict JSON, 필드 수 제한, max output, “필요한 것만” 계약 | 높음 | 정보 누락 | reviewer, router, summarizer | 자유 서술형 리서치 최종본 citeturn36view1turn20view6 |
| Context pruning | diff/symbol/path only, failing logs only | 매우 높음 | root cause 누락 | 코드 수정, 디버깅 | 아키텍처 재설계, cross-file semantic issue |
| Compaction | server-side compaction, summary checkpoints | 높음 | 요약 왜곡 | 장기 에이전트 세션 | 법률/금융처럼 wording exactness가 절대적일 때 citeturn18view4turn19view11turn20view9 |
| Retrieval gating | 필요한 문서/파일만 검색하고 상위 k 축소 | 높음 | recall 저하 | RAG, repo search | query 자체가 애매하고 넓을 때 citeturn38view3turn37search12 |
| Prompt caching | 동일 prefix 재사용, prompt_cache_key | 매우 높음 | cache miss, overflow | 공통 하네스, 고정 schema | prefix가 매우 짧거나 매번 다를 때 citeturn23view2turn23view3turn28view3 |
| Semantic/result caching | 동일·유사 질의 결과 재사용 | 중간 | stale/semantic mismatch | 반복 triage, FAQ | 최신성·정확성 임계치가 높을 때 citeturn13search16turn13search5 |
| Model routing | small-first, escalate-on-fail | 높음 | 잘못된 분기 | triage, 요약, simple edits | high-stakes one-shot outputs citeturn29view1turn33search2turn12search9 |
| Reasoning effort control | low/medium 기본, high는 필요 시만 | 중간~높음 | 과소추론 | 코딩 triage, 분류 | 수학·보안·복잡 설계 검토 citeturn18view5turn29view1turn38view4 |
| Batch/Flex processing | offline eval·embedding·bulk jobs 비동기화 | 매우 높음 | 응답 지연 | 대량 eval, 리포트, nightly jobs | 사용자 interactive path citeturn19view0turn18view2turn19view1turn19view2 |
| Tool-call minimization | allowed_tools, search budget, batched reads, truncation | 높음 | too few tools | repo 탐색, bugfix | 도구 없이는 해결 불가한 정보수집 citeturn36view3turn38view1turn34view2 |
| Eval-driven retry | 실패 case만 재시도, 전체 재실행 금지 | 높음 | 실패 분류 오류 | regression suite | 상태 누수로 부분 재실행이 왜곡될 때 citeturn20view7turn20view8turn20view11 |
| Fine-tuning or distillation | 반복 패턴 내재화 | 불확실 | 운영복잡도 | 고정 포맷 대량 처리 | 현재 OpenAI 신규 fine-tuning 사용 불가 환경 등 citeturn32view0 |

### 언제 효과가 없고, 어떻게 적용하고, 어떻게 검증할 것인가

프롬프트를 짧게 만드는 것만으로는 충분하지 않습니다. **짧지만 캐시가 깨지는 프롬프트**보다, **조금 길어도 대부분이 캐시되는 프롬프트**가 실제 비용이 더 낮을 수 있습니다. OpenAI와 Azure는 모두 동일한 시작 prefix가 핵심이라고 설명하며, Azure는 첫 1,024 토큰 동일성과 이후 128토큰 단위 hit를 명시합니다. 따라서 하네스 템플릿은 `정책/출력계약/툴스키마/예시`를 고정 prefix에, `repo path·commit hash·diff·테스트 로그`를 suffix에 두는 구조가 기본이어야 합니다. citeturn23view2turn30view1

“structured output/JSON schema가 언제 토큰을 줄이고 언제 늘리느냐”에 대한 답은 이렇습니다. **짧고 안정적인 검사형 작업**에서는 JSON schema가 후처리 실패와 재시도를 줄여 총비용을 낮출 수 있습니다. 하지만 schema가 크고 nested object가 깊어지면 schema 자체가 입력 오버헤드를 만듭니다. Anthropic은 복잡한 tool examples가 100~200 tokens를 더할 수 있다고 수치까지 제시하고, OpenAI GPT-4.1 가이드는 JSON이 verbose하고 escaping overhead가 있다고 밝힙니다. 따라서 **router/reviewer/debugger에는 compact JSON**, **대규모 문서/context wrapping에는 XML 또는 더 단순한 구획 포맷**이 대체로 유리합니다. citeturn19view9turn38view0turn36view1

“간결하게 답하라”보다 강한 출력 제한 패턴은 **정확한 필드 수**, **최대 항목 수**, **설명 금지**, **근거가 없으면 `unknown`**, **stop criteria**를 함께 넣는 것입니다. OpenAI와 Anthropic 모두 system/developer messages와 구조화 출력을 강하게 지원하고, 최신 GPT/Claude 가이드는 process-heavy stack보다 outcome-first 지시와 명시적 validation rule을 권장합니다. visible chain-of-thought를 요구하거나 중간 산출물을 과하게 남기면 reasoning/output tokens가 증가하므로, 기본 정책은 “최종 산출물 중심, 중간 reasoning은 비노출·비보존”이어야 합니다. 단, tool-heavy reasoning 모델은 내부 reasoning item을 올바르게 round-trip해야 오히려 더 token-efficient할 수 있으므로, **사용자에게 보여주는 사고과정**과 **모델이 내부적으로 유지하는 reasoning state**를 분리해야 합니다. citeturn20view6turn35view2turn35view1turn28view7

코드베이스 작업에서는 전체 파일 투입보다 **diff/symbol/path retrieval**이 기본값이 되어야 합니다. OpenAI Codex와 IDE 문서는 열린 파일·선택 영역·태그된 파일이 있을 때 더 짧은 프롬프트로 더 관련성 높은 결과를 얻는다고 설명하고, Codex Prompting Guide는 필요한 파일을 먼저 계획한 뒤 병렬로 묶어서 읽고, 도구 응답은 10k tokens 정도로 절단하라고 권장합니다. 이것은 곧 **“필요한 문맥만 읽고, 읽은 결과도 절단”**이 공식에 가까운 코딩 하네스 패턴이라는 뜻입니다. citeturn38view4turn38view1

모델 라우팅은 학술 연구와 공식 모델 선택 가이드가 같은 방향을 가리킵니다. Anthropic은 fast/cost-effective 모델로 시작해 필요 시 업그레이드하라고 권장하고, OpenAI는 reasoning 모델을 planner, GPT 계열을 workhorse로 배치하는 하이브리드 패턴을 설명합니다. FrugalGPT와 RouteLLM도 같은 원리를 보여줍니다. 다만 논문의 절감률은 과거 모델 가격표에 기반하므로 그대로 목표값으로 삼아서는 안 됩니다. 이 보고서의 권고는 “논문을 절감률의 근거가 아니라 **구조적 설계 원리의 근거**로만 사용”하는 것입니다. citeturn29view1turn33search2turn13search0turn12search9

## 하네스 아키텍처 청사진

권장 아키텍처는 “한 번의 큰 에이전트”가 아니라 **작은 정책 컴포넌트 묶음**입니다. 공식 문서가 공통으로 강조하는 것은 prompt versioning, context management, selective tool exposure, evals, cost observability입니다. 따라서 하네스의 최소 단위는 모델보다 **정책 객체**여야 합니다. 그 정책 객체가 prompt template, token budget, model routing, compaction, cache key, retry rule을 독립적으로 바꿀 수 있어야 A/B가 가능합니다. citeturn25view3turn20view7turn18view4turn18view9turn38view1

### 권장 컴포넌트 설계

| Component | 책임 | 입력 | 출력 | 로그 메트릭 | 실패 모드 | 최소 구현 | 확장 구현 |
|---|---|---|---|---|---|---|---|
| PromptTemplateRegistry | task별 prompt/version 관리 | task type, prompt version, variables | rendered prefix/suffix | template id, version, prompt bytes | 잘못된 버전 혼합 | YAML/JSON registry | typed template compiler + lint |
| TokenBudgetPolicy | 입력/출력/도구 budget 산정 | task class, model grade, repo size | caps, truncation plan | planned vs actual tokens | 과소 cap/과대 cap | static thresholds | adaptive budget by historical quantiles |
| ModelRouter | 모델·effort·service tier 결정 | task type, risk, size, urgency | model, effort, tier | escalation rate, success by route | 쉬운 작업에 고가 모델 사용 | rule-based router | classifier/router model + eval gate |
| ContextPacker | 필요한 문맥만 패킹 | repo metadata, diff, symbols, logs | compact context payload | context tokens, source counts | 핵심 파일 누락 | diff/log only | semantic symbol graph + targeted retrieval |
| CacheKeyBuilder | 캐시 재사용 키 설계 | repo path, commit hash, file hash, prompt ver, tool schema ver, model | cache key | hit/miss by key | stale cache | concatenated stable string | hierarchical key + TTL classes |
| RunLedger | run 단위 재현/회계 장부 | all requests/events | immutable ledger rows | cost, tokens, latency, tool counts | 누락 로깅 | JSONL append | OLAP sink + dashboards |
| EvalRunner | A/B 및 회귀 검증 | baseline run, candidate run, fixtures | pass/fail + score | pass rate, context recall, cost/success | flaky evals | local fixture runner | CI-triggered continuous evals |
| CostReporter | 비용 집계·분석 | ledger, vendor usage APIs | daily/weekly reports | cost by model/tool/task | CUR/API mismatch | local aggregation | vendor API + CUR reconciliation |
| RetryPolicy | 실패 시 재시도 범위 결정 | error class, eval outcome | retry action | retry count, recovered rate | 전체 재실행 남용 | retry only failed stage | learned recovery policy |
| CompactionPolicy | 세션 압축 타이밍/형태 결정 | context growth, turn count | compact/trim/summarize action | pre/post compact success | 요약 손실, compaction thrash | threshold summary | multiple summary tiers + checkpoints |
| ToolCallBudgeter | tool/search/read 예산 집행 | task type, remaining budget | allowed tools, max calls | tool count, duplicate queries | 너무 이른 stop | static hard cap | dynamic budget by utility estimate |
| OutputContractEnforcer | 출력 형식/길이/validation 보장 | raw model output | validated artifact / repair request | schema fail, repair rate | endless repair loop | JSON schema + one repair | constrained decoding + per-field repair |

### 캐시 키 원칙

캐시 키는 최소한 `repo_path + git_commit_hash + prompt_version + tool_schema_version + task_type + model_name`를 포함해야 합니다. 파일 내용이 직접 문맥에 들어가는 경우에는 `file_hash`까지 포함해야 stale cache를 피할 수 있습니다. 세션 요약 캐시는 별도로 `summary_version + checkpoint_turn`를 붙이십시오. **절대 이전 run의 hidden state나 불명확한 대화 ID만 키로 쓰면 안 됩니다.** OpenAI와 Azure는 `prompt_cache_key`가 동일 prefix 요청의 routing/hit 개선에 도움을 준다고 설명하며, Anthropic/Google도 TTL과 cache object를 명시적으로 관리합니다. citeturn23view3turn30view1turn31search2

### 보존해야 할 정보와 버려도 되는 정보

하네스는 다음 정보는 **절대 보존**해야 합니다. 현재 사용자 목표, acceptance criteria, 변경된 파일 목록, 현재 diff, 실패 테스트 이름과 핵심 로그, 실행한 도구와 그 핵심 결과, 다음 turn에서 필요한 unresolved TODO, 안전/권한 제약입니다. 반대로 장황한 intermediate explanation, 동일한 검색 결과의 중복 본문, 이미 해결된 서브태스크의 전체 transcript, 대용량 raw tool output은 요약하거나 폐기해도 됩니다. 이는 OpenAI session memory/compaction, Anthropic compaction docs, Codex truncation 가이드와 부합합니다. citeturn38view2turn18view4turn19view11turn38view1

## 실전 프롬프트 템플릿

아래 템플릿은 **짧은 outcome-first 지시**, **정적 prefix 앞배치**, **엄격한 출력 계약**, **budget 및 stop criteria 명시**라는 공통 원칙을 따릅니다. 이 원칙은 OpenAI GPT-5.x/GPT-4.1 가이드, Codex Prompting Guide, OpenAI function calling/structured output 문서, Anthropic 비용 관리 및 tool docs에 정렬됩니다. citeturn20view6turn38view0turn38view1turn36view3turn18view9

### low-token code inspection prompt

**목적**: 파일 전체 설명이 아니라 수정 후보 위치만 찾는다.  
**모델 등급**: small or mid workhorse.  
**예상 절감 포인트**: 전체 repo 요약 금지, 최대 후보 수 제한, 근거를 path/line 단위로만 받음.  
**output schema**: JSON.  
**stop criteria**: 후보 3개 이내 찾으면 종료.  
**validation rule**: 각 후보는 `path`, `symbol_or_line`, `why_relevant`만 포함.

```text
[STATIC PREFIX]
You are a code inspection agent.
Goal: locate the minimum set of code locations relevant to the issue.
Rules:
- Do not explain the whole codebase.
- Return at most 3 candidate locations.
- Prefer file path + symbol name + exact failing interface over narrative.
- If evidence is insufficient, return {"status":"need_more_context"}.

[VARIABLE SUFFIX]
Task:
{issue_summary}

Available context:
- changed_files: {changed_files}
- failing_tests: {failing_tests}
- search_hits: {search_hits}

Return JSON:
{
  "status": "ok|need_more_context",
  "candidates": [
    {
      "path": "...",
      "symbol_or_line": "...",
      "why_relevant": "..."
    }
  ],
  "next_reads": ["..."]
}
```

이 템플릿은 **짧고 목적 중심의 프롬프트**, **작은 출력 스키마**, **검색 후보 상한**을 통해 input과 output을 동시에 줄입니다. Codex와 OpenAI 가이드는 필요한 파일을 먼저 계획하고 배치로 읽으라고 권장합니다. citeturn20view6turn38view1

### diff-only remediation prompt

**목적**: 전체 파일 재작성 대신 수정 patch 초안만 생성한다.  
**모델 등급**: mid 이상 coding model.  
**예상 절감 포인트**: unchanged code 재생성 금지.  
**output schema**: unified diff or structured patch JSON.  
**stop criteria**: acceptance criteria를 만족하는 최소 patch 1개.  
**validation rule**: 변경 안 된 파일은 언급 금지.

```text
You are a remediation agent.
Produce the smallest safe fix.
Return only a patch plan for the listed targets.
Do not rewrite unchanged code.
Do not summarize the repository.

Issue:
{issue}

Targets:
{candidate_paths}

Acceptance criteria:
{acceptance_criteria}

Return JSON:
{
  "files": [
    {
      "path": "...",
      "change_type": "edit|add|delete",
      "patch_summary": "...",
      "risk": "low|medium|high"
    }
  ],
  "needs_test_update": true|false
}
```

Predicted Outputs 같은 기능은 “대부분 동일하고 일부만 바뀌는 출력”에서 지연을 줄일 수 있지만, rejected prediction tokens는 비용을 늘릴 수 있으므로 하네스 차원에서는 기본을 **diff-only 출력**으로 설계하는 편이 더 안전합니다. citeturn27view0turn37search3

### failing-test focused debugging prompt

**목적**: 실패 테스트와 핵심 로그만 사용해 root cause를 좁힌다.  
**모델 등급**: mid reasoning 또는 coding model.  
**예상 절감 포인트**: 전체 CI 로그 대신 실패 로그만 사용.  
**output schema**: JSON.  
**stop criteria**: 원인 가설 2개 이내.  
**validation rule**: 가설마다 “테스트와의 연결 근거” 필수.

```text
You are a debugging agent.
Use only the failing tests and attached error logs.
Do not discuss passing tests unless they constrain the diagnosis.
Return at most 2 hypotheses.

Failing tests:
{failing_tests}

Relevant logs:
{trimmed_error_logs}

Recent diff:
{recent_diff}

Return JSON:
{
  "hypotheses": [
    {
      "root_cause": "...",
      "evidence": ["..."],
      "best_next_action": "..."
    }
  ]
}
```

이 템플릿은 noisy retrieval와 긴 history를 줄여 tool-call accuracy와 cost를 개선하는 컨텍스트 관리 원칙에 맞습니다. citeturn38view2turn38view3

### compact session summary prompt

**목적**: 장기 세션을 다음 turn용 checkpoint로 압축한다.  
**모델 등급**: small workhorse.  
**예상 절감 포인트**: 장황한 대화 히스토리를 요약 상태로 대체.  
**output schema**: JSON.  
**stop criteria**: unresolved items와 hard facts 정리 완료.  
**validation rule**: 추측 금지, 확인된 사실/미해결 항목 분리.

```text
Summarize this session for future continuation.
Keep only what is necessary for correct next-step execution.

Preserve:
- current user goal
- acceptance criteria
- changed files and key diff facts
- failing tests and current status
- unresolved blockers
- constraints and permissions

Discard:
- repeated explanations
- obsolete plans
- verbose tool outputs

Return JSON:
{
  "goal": "...",
  "acceptance_criteria": ["..."],
  "changed_files": ["..."],
  "verified_facts": ["..."],
  "open_issues": ["..."],
  "next_best_action": "..."
}
```

OpenAI와 Anthropic 모두 장기 세션에서 compaction/summary를 핵심 전략으로 제시합니다. citeturn18view4turn19view11turn20view9

### reviewer prompt with strict budget

**목적**: 리뷰를 “우선순위 높은 finding만” 출력하게 한다.  
**모델 등급**: small or mid workhorse.  
**예상 절감 포인트**: 무한 리뷰 코멘트 방지.  
**output schema**: JSON.  
**stop criteria**: 최대 5개 finding.  
**validation rule**: 각 finding은 severity와 file path 필수, praise 금지.

```text
Review the patch with a strict token budget.
Return only materially important findings.
Do not praise. Do not restate the diff.

Patch:
{diff}

Return JSON:
{
  "findings": [
    {
      "severity": "high|medium|low",
      "path": "...",
      "issue": "...",
      "why_it_matters": "...",
      "suggested_check": "..."
    }
  ]
}
Rules:
- At most 5 findings.
- Omit low-confidence nits.
- If no material issue exists, return {"findings":[]}.
```

Codex CLI의 review 기능 또한 diff 중심 리뷰를 기본으로 삼습니다. reviewer는 full repo가 아니라 **현재 diff만** 보는 것이 비용-효율적입니다. citeturn37search7

### research prompt with search budget

**목적**: 리서치 에이전트의 검색/읽기 횟수를 제한한다.  
**모델 등급**: reasoning model for research, but with low/medium effort default.  
**예상 절감 포인트**: 중복 검색과 과도한 follow-up read 방지.  
**output schema**: JSON plan then final summary.  
**stop criteria**: 고신뢰 출처 5개 확보 또는 search budget 소진.  
**validation rule**: source tier 표시 필수.

```text
You are a research agent with a strict budget.

Budget:
- max_search_queries: 6
- max_page_opens: 10
- max_followup_searches_per_branch: 1

Prioritize:
1. official docs
2. API references
3. peer-reviewed or benchmark-backed papers

Stop when:
- you have enough evidence to answer, or
- the budget is exhausted

Return JSON:
{
  "research_plan": [
    {"query":"...", "why":"...", "tier_target":"Tier 0|Tier 1"}
  ],
  "expected_answer_shape": ["..."]
}
```

OpenAI Deep Research와 agentic eval 문서가 강조하는 것은 “잘 형성된 입력과 명시적 계획/평가”입니다. budget을 지시하면 도구 낭비를 줄일 수 있습니다. citeturn21search3turn20view11

### model-router decision prompt

**목적**: task difficulty를 분류해 모델 escalation 여부를 결정한다.  
**모델 등급**: small classifier.  
**예상 절감 포인트**: 쉬운 작업을 저가 모델에서 처리.  
**output schema**: compact JSON.  
**stop criteria**: route 1개 결정.  
**validation rule**: reasoning prose 금지, label only.

```text
Classify the task for routing.
Choose the cheapest model grade likely to succeed.

Task:
{task_summary}

Signals:
- number_of_files: {n_files}
- diff_size: {diff_size}
- failing_tests: {n_tests}
- requires_math_or_security_review: {flag}
- ambiguity_level: {low|medium|high}

Return JSON:
{
  "task_class": "classification|summary|code_locate|code_edit|test_debug|architecture|security|math_reasoning|long_research",
  "difficulty": "low|medium|high",
  "recommended_model_grade": "small|mid|large_reasoning",
  "effort": "minimal|low|medium|high",
  "needs_escalation_if_fail": true
}
```

OpenAI와 Anthropic 모두 low-cost model로 시작하고 필요 시 escalation하는 접근을 권장합니다. citeturn33search2turn29view1

### final-report compression prompt

**목적**: 긴 내부 결과를 짧은 외부 보고서로 압축한다.  
**모델 등급**: small or mid summarizer.  
**예상 절감 포인트**: 중간 산출물을 최종본에 반복 출력하지 않음.  
**output schema**: markdown with fixed sections.  
**stop criteria**: 정해진 섹션만 채움.  
**validation rule**: 새 사실 추가 금지.

```text
Compress the internal run artifacts into a final user-facing report.

Include only:
- decision
- evidence
- concrete next actions
- risks

Do not include:
- hidden internal deliberation
- repeated logs
- abandoned hypotheses unless still relevant

Format:
## Decision
## Evidence
## Risks
## Next actions
```

최종 보고 압축은 output token 절감을 위해 특히 중요합니다. reasoning/agent 시스템은 내부 중간 상태가 많기 때문에, 최종 사용자용 보고서는 별도 compression 단계가 필요합니다. citeturn20view11turn28view7

## 실험 매트릭스와 검증 설계

실험은 반드시 A/B로 해야 합니다. baseline에는 현재 하네스의 기본 프롬프트/모델/컨텍스트 정책을 고정하고, 단 하나의 변수만 바꿔야 합니다. 지표는 **토큰 절감률**과 **성공당 비용 절감률**을 분리해 봐야 합니다. 이유는 캐시나 배치처럼 토큰 수는 크게 안 줄여도 단가를 대폭 내릴 수 있는 전략이 있고, 반대로 reasoning effort를 낮춰 토큰은 줄었지만 실패율이 올라 총비용이 늘 수도 있기 때문입니다. OpenAI eval 문서는 architecture별로 nondeterminism이 생기는 지점을 식별해 그 지점에 eval을 두라고 권장합니다. citeturn20view7turn34view2

토큰 절감률과 성공당 비용 절감률은 아래처럼 분리하십시오.

```text
token_reduction_rate
= 1 - (candidate_total_tokens / baseline_total_tokens)

cost_per_success
= total_cost / successful_tasks

cost_per_success_reduction_rate
= 1 - (candidate_cost_per_success / baseline_cost_per_success)
```

### 추천 실험표

| Experiment ID | Hypothesis | Baseline | Change | Metrics | Success Criteria | Rollback Criteria |
|---|---|---|---|---|---|---|
| E001 | static prefix 앞배치가 cached input ratio를 높인다 | 혼합 순서 prompt | 정적 prefix를 맨 앞에 고정 | cached ratio, TTFT, cost/request | cached ratio +20%p 이상 | hit 개선 미미 + 품질 하락 |
| E002 | reviewer를 strict JSON + max 5 findings로 제한하면 output tokens가 줄어든다 | 자유 서술 리뷰 | compact JSON schema | output tokens, schema fail | output -30% 이상, pass 동일 | material finding 누락 증가 |
| E003 | full-file 대신 diff/symbol context가 총비용을 줄인다 | 전체 파일 첨부 | diff+symbol only | input tokens, fix pass rate | cost/success 감소 | fix success 하락 >3%p |
| E004 | failing tests만 재실행·전달하면 디버그 비용이 준다 | 전체 test suite/로그 | fail-only logs | input tokens, retries | cost/success 감소 | root cause miss 증가 |
| E005 | small model triage 후 high model escalation이 frontier-only보다 싸다 | 항상 large_reasoning | small-first router | escalation rate, accuracy, cps | cps 감소, accuracy 유지 | escalation 과다로 latency 악화 |
| E006 | compaction threshold 도입이 장기 세션 비용을 줄인다 | raw long transcript | checkpoint summary every threshold | context growth, pass rate | context slope 감소 | post-compact failure 증가 |
| E007 | tool-call budget이 불필요한 도구 호출을 줄인다 | unrestricted tools | max tool/search budget | tool calls/session, success | calls 감소, success 유지 | early stop로 fail 증가 |
| E008 | duplicate search query 제거가 research run 비용을 줄인다 | free-form search | normalized query dedupe | searches/run, citations quality | searches 감소 | source coverage 악화 |
| E009 | nightly eval을 Batch/Flex로 보내면 단가가 줄어든다 | synchronous standard eval | batch/flex eval | dollars/eval run | 비용 절감 | SLA 위반 |
| E010 | repeated task result cache가 triage 비용을 줄인다 | no result cache | semantic/result cache for repeated issues | cache hit precision, cps | cps 감소, precision 유지 | stale-hit incident 발생 |
| E011 | max output hard cap이 장황한 응답을 줄인다 | soft “be concise” | max output + exact fields | output tokens, truncation fail | output 감소 | truncation-induced failure |
| E012 | acceptance criteria 기반 early stop가 불필요한 review loop를 줄인다 | fixed planner→reviewer→critic chain | success check after each stage | turns/run, cps | turns 감소 | latent bug 누락 |
| E013 | allowed_tools subset가 tools prefix 캐시와 tool NDCG를 개선한다 | all tools always exposed | allowed_tools per task | cached tokens, tool accuracy | 비용 감소 + tool accuracy 유지 | 필요한 툴 누락 |
| E014 | tool response truncation 10k가 긴 로그 입력 비용을 줄인다 | raw tool output | head/tail truncation | input tokens, diagnosis qual | tokens 감소 | 핵심 로그 손실 |
| E015 | reasoning effort low/medium default가 easy task cost를 줄인다 | high default | adaptive low/medium | reasoning tokens, accuracy | easy task cps 감소 | hard task miss 증가 |

### 작업 분류와 추천 모델 등급

정확한 모델명은 매우 빨리 변하므로, 하네스 레벨에서는 **모델 등급**으로 설계하고 벤더 매핑을 별도 설정 파일에서 관리하는 것이 좋습니다. OpenAI와 Anthropic 모두 effort 조정과 small-first 접근을 명시적으로 권장합니다. citeturn18view5turn29view1turn38view4

| Task Type | 추천 모델 등급 | 기본 reasoning effort | 기본 max output | 캐시 전략 | 비고 |
|---|---|---|---:|---|---|
| 단순 분류 | small | minimal/low | 매우 작게 | result cache 우선 | JSON only |
| 요약 | small~mid | low | 작게 | static prefix + summary cache | 긴 원문은 chunk 후 compress |
| 코드 위치 탐색 | small~mid | low | 작게 | prompt cache + search result cache | path/symbol 중심 |
| 코드 수정 | mid | medium | 중간 | diff cache + static tool prefix | full-file 재생성 금지 |
| 테스트 실패 분석 | mid 또는 large_reasoning | medium | 작게 | failing-log only | high는 어려운 케이스만 |
| 아키텍처 설계 | large_reasoning | medium~high | 중간 | summary checkpoints | 품질 우선 |
| 보안 검토 | large_reasoning | high | 중간 | none or limited cache | false negative 비용 큼 |
| 수학/추론 | large_reasoning | high | 작게 | cache보다 정확도 우선 | visible CoT 요구 금지 |
| 장문 리서치 | large_reasoning + budget | medium | 중간 | search/result cache | query budget 필수 |

“처음부터 고성능 모델” 전략은 구현이 단순하고 품질 ceiling이 높지만, 쉬운 요청에도 비싼 비용을 지불합니다. 반면 “작은 모델 선처리 후 큰 모델 escalation”은 더 싸지만 triage 오류 가능성이 있습니다. 공식 가이드와 연구를 합치면, **초기 프로토타입에서는 고성능 모델로 정답 기준선을 만든 뒤**, 하네스가 안정화되면 **small-first router로 다운그레이드**하는 순서가 가장 안전합니다. citeturn29view1turn33search2turn13search0turn12search9

## 실행 로드맵과 최종 권고

### 구현 로드맵

**최소 구현 단계**에서는 로그 수집, 토큰 측정, prompt template 분리, output budget, run ledger만 먼저 만드십시오. OpenAI/Anthropic/Google/AWS 모두 token counting과 usage/cost 추적을 지원하므로, 여기서부터 실제 비용 지도(cost map)를 그릴 수 있습니다. prompt 변경도 코드 관리형(versioned prompts)로 옮겨야 합니다. citeturn18view6turn29view0turn18view14turn19view6turn25view3

**비용 최적화 단계**에서는 model routing, cache key, context packer, compaction, tool-call budget를 넣으십시오. 이 단계부터는 “토큰 감소”와 “단가 감소”가 함께 작동합니다. prompt caching, compaction, selective tool exposure, allowed_tools, smaller models, effort tuning이 핵심입니다. citeturn23view2turn18view4turn36view3turn18view5turn29view1

**자동 실험 단계**에서는 A/B, eval harness, cost per success, regression guard, dashboard를 붙이십시오. OpenAI eval 문서가 강조하듯 multi-agent나 tool-heavy 설계는 복잡도와 nondeterminism을 늘리므로, 자동 평가 없이는 최적화가 아니라 “비용만 큰 추측”이 되기 쉽습니다. citeturn20view7turn20view8turn34view0

### Do / Do Not

| Do | Why |
|---|---|
| 정적 prefix를 맨 앞에 둔다 | caching의 전제다. citeturn23view2turn30view3 |
| 프롬프트·컨텍스트·출력을 분리 설계한다 | 어떤 레버가 비용을 만들었는지 알 수 있다. citeturn38view2turn20view7 |
| diff/symbol/path 우선으로 읽는다 | 전체 파일 투입보다 저렴하고 관련성이 높다. citeturn38view4turn38view1 |
| tool schema를 task별 subset으로 제한한다 | tool tokens와 caching 손실을 줄인다. citeturn36view3turn19view8 |
| 실패 케이스만 재시도한다 | 전체 재실행보다 성공당 비용이 낮아진다. citeturn20view7turn20view8 |
| default effort를 낮게 시작하고 어려운 작업만 올린다 | reasoning tokens를 통제할 수 있다. citeturn18view5turn29view1turn38view4 |
| Batch/Flex는 offline에만 쓴다 | live path에 쓰면 UX가 나빠진다. citeturn19view0turn18view2turn27view1 |

| Do Not | Why |
|---|---|
| `previous_response_id`를 비용 절감 수단으로 착각하지 말 것 | 과금은 계속된다. citeturn24view0 |
| “간결하게”만 쓰고 구체적 cap을 안 둘 것 | 출력 팽창을 막기 어렵다. citeturn20view6turn36view1 |
| 모든 툴을 항상 노출하지 말 것 | schema 자체가 토큰 비용이다. citeturn19view8turn19view9 |
| visible chain-of-thought를 기본으로 요구하지 말 것 | 비용과 지연이 늘어난다. citeturn28view7turn18view5 |
| 긴 raw tool output을 그대로 다음 turn에 넘기지 말 것 | context poisoning과 비용 증가를 부른다. citeturn38view2turn38view1 |
| multi-agent를 먼저 도입하지 말 것 | eval 없이 쓰면 복잡도만 늘어난다. citeturn34view0turn34view2 |
| vendor의 “up to X% 절감”을 실측치처럼 계획하지 말 것 | 재사용률과 workload에 따라 달라진다. citeturn18view1turn31search3 |

### 당신 상황에 맞춘 최종 권고

당신의 전제는 **비전공자에 가깝지만 엔지니어가 되고 싶고**, **Windows/PowerShell 환경**, **Codex/Claude/기타 에이전틱 코딩 세션 활용**, **해커톤·개인 프로젝트에서 바로 쓸 수 있는 실용성**입니다. 이 경우 가장 중요한 것은 이론적으로 완벽한 하네스가 아니라 **재현 가능한 작은 루프**입니다. 즉, “프롬프트를 바꿨더니 토큰이 줄었다”가 아니라, **같은 문제셋 30개에 대해 성공당 비용이 얼마나 줄었는지**를 바로 보는 구조가 먼저입니다. 공식 가이드도 prompt versioning, eval, session memory, compaction, tool discipline을 한 세트로 다룹니다. citeturn25view3turn20view7turn38view2turn38view1

오늘 바로 할 일은 다섯 가지입니다.

- `runs.jsonl` 형식의 **RunLedger**를 만들고, 각 호출마다 `model, effort, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, tool_calls, latency_ms, success`를 기록하십시오. citeturn22view1turn23view1turn28view7
- 현재 사용하는 공통 system/developer prompt와 tool schema를 분리해 **정적 prefix 파일**로 빼십시오. 그리고 동적 값은 suffix로만 삽입하십시오. citeturn23view2turn30view3
- 코드 수정 루프를 `inspect → patch-plan → apply → test → review → summarize`로 나누고, 각 단계에 **max output**과 **JSON schema**를 붙이십시오. citeturn36view1turn20view6
- 디버깅 경로에서 전체 테스트 로그 전달을 중단하고, **실패 테스트명 + 핵심 stack trace + recent diff**만 넘기십시오. citeturn38view2turn38view3
- nightly/대량 평가가 있다면 **Batch 또는 Flex** 전환 후보로 분리하십시오. interactive path와 분리하는 것이 핵심입니다. citeturn19view0turn18view2

이번 주 할 일도 다섯 가지입니다.

- `ModelRouter.ps1` 또는 `model_router.py`를 만들어 task class별로 `small|mid|large_reasoning`을 선택하게 하십시오. 실패 시만 escalation 하십시오. citeturn29view1turn33search2
- `ContextPacker`를 만들어 full-file 대신 `changed files + tagged files + symbols + failing logs`만 모아 전달하십시오. citeturn38view4turn38view1
- `CompactionPolicy`를 넣고 turn 수 또는 context size 임계치에서 checkpoint summary를 생성하십시오. citeturn18view4turn20view9turn20view0
- `ToolCallBudgeter`를 넣고 단계별 `max_reads`, `max_searches`, `allowed_tools`, `parallel_tool_calls` 정책을 적용하십시오. citeturn36view3turn38view1
- 동일한 20~50개 작업셋으로 E001~E006을 먼저 A/B 하십시오. **pass rate가 유지되는 절감**만 채택하십시오. citeturn20view7turn20view8

하네스에 추가할 파일/모듈 이름은 아래처럼 제안합니다.

| 파일/모듈 | 용도 |
|---|---|
| `prompt_registry.yaml` | task별 prompt/version 관리 |
| `token_budget_policy.py` | task/model별 input-output cap |
| `model_router.py` | small/mid/large_reasoning 선택 |
| `context_packer.py` | diff/symbol/log 컨텍스트 패킹 |
| `cache_key_builder.py` | repo/commit/prompt/schema 기반 키 |
| `run_ledger.py` | JSONL 기록 및 집계 |
| `eval_runner.py` | A/B 실행과 pass/fail 판정 |
| `cost_reporter.py` | 일별/주별 비용 보고 |
| `retry_policy.py` | fail-only retry |
| `compaction_policy.py` | summary/checkpoint 생성 |
| `tool_budgeter.py` | tool/search/read 예산 제어 |
| `output_contracts.py` | JSON schema 검증/repair |
| `scripts/run-ab.ps1` | Windows/PowerShell 실험 실행 |
| `scripts/report-costs.ps1` | 로컬 리포트 출력 |

가장 먼저 측정해야 할 메트릭은 **cost per successful task**입니다. 두 번째는 **cached input ratio**, 세 번째는 **context size growth per turn**입니다. 이 세 개가 잡히면 “언제 고가 모델이 필요한지”, “캐시가 실제로 먹는지”, “세션이 언제 폭주하는지”가 바로 보입니다. citeturn22view1turn23view1turn38view2

가장 먼저 제거해야 할 토큰 낭비 패턴은 다섯 가지입니다. **매 turn 공통 지침 전체 재서술**, **전체 파일 본문 재첨부**, **모든 툴/스키마 상시 노출**, **자유 서술 reviewer**, **전체 로그/전체 테스트 재주입**입니다. 이것들은 공식 문서가 권장하는 caching·context管理·tool discipline과 정반대입니다. citeturn23view2turn38view1turn19view8turn38view2

### Open questions / limitations

이 보고서는 2026-07-03 기준 공식 문서와 일부 고신뢰 논문을 바탕으로 작성했습니다. 다만 몇 가지는 여전히 **불확실**합니다. 첫째, “OMo류”가 특정 제품군을 뜻하는지 여부는 공식 출처가 없어 일반적 에이전틱 코딩 루프로 해석했습니다. 둘째, Google Gemini의 cached token 정확한 할인율은 공식 블로그에 10% 단가 언급이 있으나, 본 보고서 핵심 판단에는 공식 product docs 중심으로만 반영했습니다. 셋째, fine-tuning/distillation은 벤더별 지원 상태가 빠르게 변하므로 현재는 **후순위**가 안전합니다. 넷째, 연구 논문(FrugalGPT, RouteLLM, LLMLingua)의 정량 수치는 현재 2026 API 가격과 모델군에 직접 이식하지 말고, **구조 원리만 채택**해야 합니다. citeturn31search3turn32view0turn13search0turn12search9turn14search3
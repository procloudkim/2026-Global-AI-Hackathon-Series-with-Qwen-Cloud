# Case Study: Librarian

## Executive verdict

이 프로젝트에서 만든 것은 **근거가 있는 사실을 불변 원천에서 추출하고, 오래된 주장을 대체하며, 현재 답과 변경 이유를 인용과 함께 반환하는 Qwen 기반 memory engine**이다. FastAPI, MCP, guided web demo, deterministic evaluator, bounded live-Qwen run, Alibaba ECS 배포와 exact-SHA release proof까지 만들었다.

만들지 못한 것은 다음 문장에 대한 증거다.

> 특정 사용자가 반복 업무에서 Librarian을 사용하면 기존 memory/RAG 또는 수작업보다 오류·시간·비용이 유의미하게 줄고, 다시 사용할 이유가 생긴다.

따라서 결론은 “아무것도 하지 않았다”가 아니다. **제품은 P1을 통과하지 못했지만 선택한 기술 thesis의 engineering·proof surface는 후속 단계 깊이까지 확장했다.** 이 판정의 정본은 [CLM-008](./06_EVIDENCE_REGISTER.md#clm-008)과 [CLM-012](./06_EVIDENCE_REGISTER.md#clm-012)다.

## 구현된 demo flow

최종 guided demo가 전달하는 흐름은 명확하다.

1. 사용자가 원래 사실을 담은 Source A를 ingest한다.
2. 같은 사실을 교정하는 Source B를 ingest한다.
3. 현재 질문을 하면 교정된 active claim만 답에 사용한다.
4. explanation을 열면 어떤 claim이 왜 superseded됐는지 원천·전이·시점과 함께 확인한다.
5. token과 model 사용량, citation, claim history를 심사자가 볼 수 있다.

이는 “잘못된 기억을 조용히 덮어쓰지 않고 왜 바뀌었는지 설명한다”는 좁은 문제에 대한 완결된 vertical slice다. 다만 flow는 judge용 quota 100→1000 예제이며 실제 agent developer, 감사 담당자, 지식 운영자 중 누구의 일상 업무인지 정해지지 않았다. [CLM-010](./06_EVIDENCE_REGISTER.md#clm-010)

## 시간선

| 시점 | 저장소에서 확인되는 사건 | 제품설계 의미 |
|---|---|---|
| 7/3 16:12 | first commit | 기회 탐색 이전 기록은 Unknown |
| 7/3 16:14 | requirements, units, architecture, operations 문서 | 해법과 Track이 이미 고정됨 |
| 7/3 16:28 | FastAPI와 Qwen router | 저장소 안의 user evidence 없이 construction 진입 |
| 7/3 17:13~17:16 | store, ingest, query, lint/forget | 핵심 engine을 약 1시간 안에 구현 |
| 7/3 18:56 | Alibaba deployment scripts | 사용자 test 전에 release surface 확장 |
| 7/3 19:11~19:54 | MCP, benchmark, UI, Docker, submission | 제출 산출물을 같은 날 완성 |
| 7/3 20:00 | 기술 report | 첫 commit부터 약 3시간 48분 |
| 7/14~15 | independent eval, security, restart, rollback, bitemporal proof | 기술 thesis와 release proof를 강화 |
| 7/18 | memory explanation, guided UI, final deployment evidence | 심사 흐름과 설명 가능성을 강화 |
| 제출 후 | 제품 수준에 대한 냉정한 감사 | persona·사용자 outcome·adoption gap을 뒤늦게 명시 |

7월 3일 14개 commit 뒤에 32개 commit이 추가됐다. 후반 작업이 쓸모없다는 뜻은 아니다. 오히려 정확성·증명 자산은 강해졌다. 문제는 그 증명의 대상이 **사용자 결과가 아니라 이미 선택된 memory lifecycle architecture**였다는 점이다. [CLM-005](./06_EVIDENCE_REGISTER.md#clm-005)

## 다축 판정

### 1. 문제·수요 검증: FAIL

초기 요구사항에는 외부 user evidence citation 없이 RAG의 축적·모순·stale 문제와 wiki-memory architecture·API가 이미 함께 고정돼 있다. 사용자 역할, 현재 workflow, 오류 빈도, 손실, 기존 대안, 전환 동기는 없다. U1~U10에도 discovery 작업이 없다. [CLM-001](./06_EVIDENCE_REGISTER.md#clm-001), [CLM-002](./06_EVIDENCE_REGISTER.md#clm-002)

이 판정은 “그 문제가 현실에 없다”는 뜻이 아니다. **이 프로젝트가 그 문제의 우선순위와 대상 사용자를 증명하지 않았다**는 뜻이다. 저장소 밖 인터뷰 여부는 Unknown이다.

### 2. MemoryAgent Track 적합: PARTIAL

다음 항목은 강하다.

- persistent file-backed memory
- active/superseded/disputed lifecycle
- outdated fact의 제한과 감사 가능한 forgetting
- bounded context retrieval
- cross-session persistence와 restart proof

다음 항목은 제품 수준으로 약하다.

- 사용자 선호를 실제 interaction에서 자율 축적
- 경험을 이용해 이후 decision이 더 정확해지는 feedback loop
- 대표적인 multi-turn·cross-session 사용자 workflow

초기 전략은 increasing accuracy를 Autoresearch와 연결했지만 requirements는 그 loop를 out of scope로 뒀다. [CLM-004](./06_EVIDENCE_REGISTER.md#clm-004)

### 3. 엔지니어링·증명: PASS

보존할 자산은 분명하다.

- 불변 source와 evidence-bound atomic claim
- deterministic state transition과 fail-closed validation
- bitemporal query·explanation
- process/file locking과 crash recovery
- independent proof boundary와 honest HOLD
- bounded provider calls와 zero-unapproved-spend gate
- exact-SHA Alibaba release, restart, rollback receipts

최종 release는 RELEASE_VERIFIED이고 live-Qwen gate도 통과했다. 동시에 receipt는 promotion을 HOLD로 남겨 증명 범위를 과장하지 않는다. [CLM-009](./06_EVIDENCE_REGISTER.md#clm-009)

### 4. 제출·운영: PARTIAL

심사용 single-operator demo로는 강하다. 공개 HTTPS, Basic Auth, guided testing flow, architecture·video script·repo evidence package가 준비됐다. Human은 제출 완료를 보고했지만 공개 video와 외부 form의 최종 binding은 저장소 snapshot만으로 독립 검증하지 않는다. [EV-026](./06_EVIDENCE_REGISTER.md#conversation-evidence)

hosted multi-user product로는 다음이 남아 있다.

- tenant와 사용자별 데이터 격리
- app-level authn/authz
- 개인정보 동의·retention·실제 삭제·export
- backup/restore와 RPO/RTO
- queue, backpressure, provider 장애 격리
- product telemetry, feedback, support, incident loop

이 격차는 해커톤 demo의 실패가 아니라 production claim의 경계다. [CLM-011](./06_EVIDENCE_REGISTER.md#clm-011)

### 5. 수상·채택·사업 성과: UNKNOWN

Human이 보고한 제출 완료는 delivery milestone이다. 수상, 반복 사용자, retention, willingness-to-pay, 업무 성과는 별도 evidence가 필요하다. [CLM-019](./06_EVIDENCE_REGISTER.md#clm-019), [EV-026](./06_EVIDENCE_REGISTER.md#conversation-evidence)

## Working assets와 misleading progress

### 다음 프로젝트로 가져갈 것

| 자산 | 가져갈 이유 |
|---|---|
| 계약과 증거를 분리한 구조 | 공식 요구·구현·runtime proof가 뒤섞이지 않는다. |
| fail-closed state transition | 모델 출력을 곧바로 canonical state로 쓰지 않는다. |
| exact-SHA·restart·rollback receipt | 배포됐다는 말을 재현 가능한 증거로 바꾼다. |
| bounded cost와 authority gate | cloud·provider 사용의 비용과 권한을 드러낸다. |
| independent evaluator와 honest HOLD | test 통과를 과장된 promotion으로 바꾸지 않는다. |
| explanation UI | 내부 기술 상태를 사용자가 이해할 수 있는 결과로 번역한다. |

### 다음에는 진행률로 착각하지 말 것

| 겉보기 진행 | 실제 한계 |
|---|---|
| MECE requirements 문서 | 분류가 깔끔해도 원래 가정이 검증되지는 않는다. |
| 많은 Unit과 빠른 commit | build throughput이지 problem validation이 아니다. |
| MCP·API·UI 표면 수 | distribution과 user adoption을 증명하지 않는다. |
| test·eval·receipt 수 | 선택한 architecture의 correctness를 증명할 뿐 사용자 가치를 자동 증명하지 않는다. |
| cloud deployment | production readiness나 product-market fit가 아니다. |
| submission 완료 | 수상·채택·사업 성과가 아니다. |

제품 Python 8,806줄보다 assurance Python 14,277줄이 더 큰 사실은 품질 집착의 증거인 동시에 proof-surface inversion의 신호다. 줄 수 자체는 나쁘지 않다. **그 전에 어떤 outcome을 증명할지 정했는가**가 중요하다. [CLM-006](./06_EVIDENCE_REGISTER.md#clm-006)

## 왜 이런 결과가 나왔는가

인과 사슬은 다음처럼 해석하는 것이 가장 단순하다.

1. 짧은 해커톤 마감과 수상 목표가 있었다.
2. 저장소의 최초 기록에는 Track과 solution architecture가 이미 결합돼 있었다. 그 이전 Human·AI의 제안 순서는 Unknown이다.
3. 이후 작업은 동작하는 제출물을 향해 빠르게 진행됐지만 최초 선택의 소유권은 현재 증거만으로 단정하지 않는다.
4. AI-DLC 문서가 가정을 requirements·units·architecture로 정교하게 만들었다.
5. 사용자 가치 Gate가 없어서 construction이 멈출 이유가 없었다.
6. 냉정한 기술 평가 요구가 “어떤 사용자가 원하는가?”보다 “이 memory policy가 baseline보다 강한가?”로 번역됐다.
7. proof가 강화될수록 이미 투자한 thesis를 더 증명하는 편이 쉬워졌다.
8. 제출 후에야 product-level audit가 수행돼 desirability gap이 드러났다.

여기서 책임을 “사용자가 잘못 선택했다” 또는 “AI가 잘못 만들었다” 하나로 환원하면 재발 방지가 어렵다.

| 주체 | 실제 역할 | 다음 교정 |
|---|---|---|
| Human | 목표·시간·권한·최종 가치 판단 제공 | Gate를 통과시키거나 중단시키는 승인자 역할을 명시 |
| AI | 대안·계획·코드·proof를 매우 빠르게 생성 | assumption을 fact로 승격하지 않고 가장 싼 falsifier를 먼저 제안 |
| Process Gate | 당시 부재 | user evidence, AI fit, mutation, budget, risk depth를 강제로 확인 |

## 같은 예산의 반사실적 경로

사후확신을 피하기 위해 “완벽한 discovery를 했어야 한다”고 요구하지 않는다. 동일한 마감 7일 전·solo-builder 조건에서도 가능한 최소 대안은 다음이었다.

### 첫 90분

- official Track contract를 사용자 행동 문장으로 분해한다.
- 후보 actor 3개를 적는다: agent developer, 감사 가능한 지식 운영자, 개인 memory 사용자.
- 각 actor의 대표 workflow와 현재 대안을 한 줄씩 적는다.
- 3개 중 증거를 가장 빨리 얻을 수 있는 actor 하나를 선택한다.
- 실제 사용자에게 닿을 수 없다면 “product demand unverified”를 명시하고 수상용 technical prototype으로 scope를 정직하게 낮춘다.

### 다음 90분

- 코드 없이 correction→query→explain flow를 화면·CLI mock으로 만든다.
- 3~5명 또는 최소 3개의 실제 workflow 사례로 무엇이 이해되지 않는지 본다.
- stale-answer 감소, 출처 확인 시간, correction 성공 같은 outcome 하나를 선택한다.
- no-memory, latest-write-wins, simple structured database를 baseline으로 둔다.

### 이후 build

- 한 actor, 한 workflow, 한 outcome만 vertical slice로 구현한다.
- 실제 사례에서 나온 golden set을 먼저 만든다.
- evaluator가 통과해야 하는 것은 claim FSM뿐 아니라 사용자의 과업 성공이다.
- public demo에 필요한 최소 보안·배포를 하고, scale hardening은 G4 전까지 보류한다.

### 제출 전

- judge가 아니라 target user가 설명 없이 workflow를 끝내는지 확인한다.
- 가장 강한 반대 근거 하나를 먼저 받는다.
- 그 objection이 가치 thesis를 무너뜨리면 architecture가 아니라 problem/segment를 바꾼다.

이 경로가 반드시 우승했을 것이라는 주장은 하지 않는다. 다만 **잘못된 제품을 정교하게 만드는 비용을 더 일찍 제한했을 가능성**은 높다.

## 이 사이클의 진짜 산출물

1. 좁지만 실제로 동작하는 evidence-backed memory engine
2. 매우 강한 eval·release·deployment proof discipline
3. 사용자 가치 검증 없는 AI-DLC가 가정을 빠르게 specification으로 세탁할 수 있다는 negative corpus
4. 다음 프로젝트에서 사용할 [AI-native Product OS](./04_AI_NATIVE_PRODUCT_OS.md)
5. 같은 오류를 프롬프트 수준에서 막는 [Prompt Library](./05_PROMPT_LIBRARY.md)

즉, 이 프로젝트의 최종 가치는 코드만이 아니다. **빠른 AI 실행력을 어디에서 멈추고 현실 증거를 요구해야 하는지 보여준 사례**다.

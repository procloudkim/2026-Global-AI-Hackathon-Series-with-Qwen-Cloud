# AI-native Product OS

이 문서는 다음 프로젝트의 Gate 전이·상태·행동 규칙, 위험 등급, 증거 계층에 대한 유일한 정본이다.

## Operating thesis

1. Reality before artifacts: 사용자·현장·데이터가 AI가 만든 문서보다 우선한다.
2. Falsifier before builder: 가장 싼 반증 실험을 가장 먼저 한다.
3. One actor, one task, one outcome: 0→1에서는 vertical slice를 넓히지 않는다.
4. Simplest sufficient system: deterministic→single call→workflow→agent→multi-agent 순으로 필요성을 증명한다.
5. Two proofs, both required: technical correctness와 user outcome은 서로 대체할 수 없다.
6. Risk-proportional depth: 위험·비용·비가역성이 커질 때만 조사와 guardrail을 깊게 한다.
7. Human owns consequence: 목표, 위험 허용도, 외부 쓰기, 비용, 비가역 결정은 Human이 승인한다.
8. AI owns acceleration, not truth: AI는 가설·artifact·실험을 만들지만 실제 pain과 adoption을 발명하지 않는다.

## Proof Layers

| Layer | 이름 | 증명하는 것 | 증명하지 않는 것 |
|---|---|---|---|
| L0 | Structural | 파일·schema·config·문서가 존재하고 정적 계약이 맞음 | 실제 동작 |
| L1 | Behavioral | local test·offline eval에서 동작 | live provider, deployment |
| L2 | Live Model | 실제 model/provider가 bounded case에서 동작 | deployment, general quality |
| L3 | Deployed | exact candidate가 target runtime에서 동작·복구 | 사용자 가치, adoption |
| L4 | Product | 실제 target user가 representative task를 더 잘 완료 | 반복 채택, economics |
| L5 | Adoption & Business | activation·retention·cost per success·risk가 지속 가능 | 영구적 우위 |

### Proof rule

- 각 claim은 자신이 요구하는 layer 이상을 가져야 한다.
- 상위 layer가 하위 layer를 자동 포함하지 않는다. 예를 들어 한 live demo가 full regression을 대체하지 않는다.
- L0~L3가 모두 PASS여도 L4는 UNKNOWN일 수 있다.
- 제출 완료는 delivery status이고 L4 또는 L5가 아니다.

## 전체 흐름

~~~text
G0 Opportunity Contract
  ↓
G1 Problem Evidence
  ↓
G2 Alternatives & AI Fit
  ↓
G3 Thin Slice & Eval Contract
  ↓
G4 User Product Proof
  ↓
G5 Preproduction
  ↓
G6 Scale

Cross-cutting:
X1 Mode & Authority
X2 Risk & Budget
X3 External State
~~~

Gate가 FAIL이면 다음 단계 artifact를 더 만드는 것이 아니라 reframe, simplify, pivot 또는 kill한다.

### Canonical Gate output

모든 G0~G6 판정은 사실 상태와 후속 행동을 분리한다.

~~~text
GATE_STATUS: PASS | PARTIAL | FAIL | UNKNOWN
NEXT_ACTION: PROCEED | ITERATE | SIMPLIFY | PIVOT | HOLD | KILL
~~~

- `GATE_STATUS`는 현재 증거가 Gate 계약을 얼마나 충족하는지 나타낸다.
- `NEXT_ACTION`은 그 상태에서 무엇을 할지 나타낸다.
- `PROCEED`는 `PASS`일 때만 허용한다. `PARTIAL`은 다음 Gate로 진입하는 우회로가 아니다.
- Prompt Library의 invocation guardrail은 한 번의 실행을 안전하게 제한할 뿐 이 schema나 Gate 전이를 재정의하지 않는다.

## G0 — Opportunity Contract

### 반드시 답할 질문

- Actor는 누구인가?
- 언제 어떤 workflow를 수행하는가?
- 현재 무엇을 사용하며 어디서 실패하는가?
- failure의 빈도·시간·비용·위험은 무엇인가?
- 원하는 outcome은 무엇이며 어떻게 측정하는가?
- 왜 지금 해결해야 하는가?
- 어떤 증거가 나오면 이 idea를 중단하는가?

### Required artifact

한 페이지 Opportunity Brief. 모든 문장은 다음 중 하나로 표시한다.

- FACT: 외부 증거가 있음
- ASSUMPTION: 검증 전
- CONSTRAINT: 대회·법·예산·시간
- DECISION: Human이 선택

### Pass

- actor 하나와 representative workflow 하나가 명시된다.
- current alternative와 baseline이 있다.
- product outcome과 contest/compliance outcome을 별도로 쓴다.
- 가장 위험한 assumption과 kill rule이 있다.
- evidence source가 AI-generated document만으로 닫히지 않는다.

### Allowed before pass

- 공식 계약·시장·대안 조사
- 사용자 모집·인터뷰·관찰
- throwaway sketch와 Wizard-of-Oz
- read-only technical feasibility probe

### Prohibited before pass

- production architecture 확정
- multi-agent·MCP·vector DB 같은 solution surface 확장
- product success claim

### Fail action

actor 또는 pain을 다시 선택한다. 외부 증거를 얻을 수 없다면 “technical exploration only”로 scope를 낮추고 product validation을 주장하지 않는다.

## G1 — Problem Evidence

### Required evidence

저위험 초기 프로젝트의 기본값:

- 서로 독립적인 실제 사례 최소 3개
- 가능하면 target user 3~5명의 인터뷰 또는 workflow 관찰
- current journey와 반복 pain ledger
- 시간·오류·재작업·위험 중 최소 하나의 baseline
- 현재 대안이 왜 충분하지 않은지에 대한 사용자 근거

숫자는 보편 법칙이 아니라 최소 falsifier 기본값이다. niche·고위험·enterprise 상황은 X2에 따라 더 깊게 한다.

### Pass

- 같은 actor·workflow에서 pain이 반복된다.
- pain의 크기와 원하는 outcome을 사용자 언어로 설명할 수 있다.
- 최소 하나의 current alternative와 switching barrier가 있다.
- “흥미롭다”가 아니라 행동·손실·우선순위 증거가 있다.
- 인터뷰 질문이 특정 solution을 유도하지 않았다.

### Fail action

- pain이 드물다: kill 또는 다른 workflow 선택
- actor마다 문제가 다르다: segment 분리
- current alternative가 충분하다: G2에서 simpler option 채택
- 사용자 접근 불가: product claim 금지, prototype learning으로 한정

## G2 — Alternatives & AI Fit

### 비교할 최소 대안

1. Do nothing / 현재 방식
2. 수작업 또는 process 변경
3. deterministic rules·structured database
4. search/RAG 또는 single LLM call
5. fixed workflow with tools
6. autonomous agent
7. multi-agent system

각 대안은 같은 representative task와 outcome으로 비교한다.

### AI가 필요한 신호

- 입력이 비정형이고 규칙이 유지 불가능하다.
- 예외·문맥·모호성에 대한 판단이 핵심이다.
- tool 사용 순서를 사전에 고정하기 어렵다.
- feedback을 이용한 adaptive behavior가 실제 outcome을 개선한다.

### Pass

- 가장 단순한 대안이 baseline으로 구현 또는 simulated test됐다.
- AI option이 사전 정의한 outcome에서 의미 있는 개선을 보인다.
- quality·latency·cost·privacy trade-off가 허용 범위다.
- agent autonomy가 필요하지 않으면 workflow 또는 deterministic solution을 선택한다.
- 선택 이유와 reject 이유가 Decision Record에 남는다.

### Fail action

AI가 이기지 못하면 AI를 빼거나 보조 기능으로 낮춘다. agent가 필요 없으면 agent라고 부르지 않는다.

## G3 — Thin Slice & Eval Contract

### Scope

- actor 1명
- task 1개
- end-to-end outcome 1개
- interface 1개
- primary model path 1개
- rollback·abstention path 1개

### Eval Contract

저위험 prototype의 기본값:

- 실제 사례에서 파생한 distinct case 최소 10개 또는 부족 사유 기록
- no-AI 또는 simpler baseline
- capability, regression, adversarial/failure case 분리
- deterministic grader가 가능한 항목은 deterministic하게 평가
- human/domain judgement가 필요한 항목은 rubric과 adjudication 기록
- quality, task success, latency, token/cost, safety threshold
- seed·model·prompt·tool·data version
- leakage 방지: hidden set을 본 뒤 candidate를 고치면 새 holdout으로 교체

### Pass

- 대표 task가 end-to-end 완료된다.
- user-facing outcome과 technical invariant가 모두 threshold를 통과한다.
- failure 시 abstain, handoff 또는 rollback이 작동한다.
- cost·latency가 사전 budget 안이다.
- 다음 feature가 아니라 가장 빈번한 failure를 알 수 있다.

### Fail action

feature를 추가하지 않는다. error taxonomy에서 가장 큰 failure 하나만 수정하고 재평가한다. 반복 실패하면 G2로 돌아간다.

## G4 — User Product Proof

### Required evidence

저위험 early product의 기본값:

- target user 3~10명 또는 동등한 수의 실제 representative workflow run
- 설명을 최소화한 task-based usability
- baseline과 비교한 completion, time, error 또는 rework
- capability·limit 이해와 잘못된 신뢰 여부
- 다시 사용할 이유, 거부 이유, current alternative로 돌아가는 이유
- 실패 transcript가 eval corpus로 돌아가는 경로

### Pass

project가 미리 정한 threshold를 모두 충족한다.

- blocking task failure가 없다.
- 핵심 outcome이 baseline보다 개선된다.
- 사용자가 결과를 이해하고 오류에서 회복할 수 있다.
- 관찰된 반복 사용 또는 실제 행동이 수반된 adoption commitment가 있다.
- cost per successful task가 허용 범위다.

Threshold는 실험 전에 정한다. 결과를 본 뒤 PASS 기준을 낮추지 않는다.
“다시 쓰고 싶다”는 stated intent는 보조 증거일 뿐, 실제 두 번째 사용·workflow 예약·data integration·조직 승인 같은 행동 증거 없이 이 Gate를 통과시키지 않는다.

### Fail action

- usability failure: UX 수정 후 재시험
- outcome failure: G2로 돌아가 solution 변경
- adoption failure: actor·workflow·positioning 변경
- safety/trust failure: autonomy 축소 또는 Human handoff 강화

### Hard rule

G4 전에는 해당 risk tier의 최소 보안·배포를 넘는 scale hardening을 하지 않는다. 대회에서 deployment가 필수라면 “judge demo infrastructure”로 한정하고 production claim을 금지한다.

## G5 — Preproduction

### Required

- code+prompt+model+tool+data+eval version bundle
- environment·secret·provider data boundary
- authn/authz와 tenant isolation
- consent, retention, deletion, export, audit policy
- prompt injection·tool misuse·output validation controls
- human handoff와 reversible action
- rate, concurrency, timeout, retry, circuit breaker, spend limit
- backup, restore, migration, rollback rehearsal
- logs, traces, user feedback, SLO, alerts
- staged release, owner, incident and support path

### Pass

- X2 risk tier의 필수 control이 모두 증거와 연결된다.
- realistic staging에서 representative load·failure·recovery를 통과한다.
- high-risk·irreversible tool에는 Human approval이 있다.
- monitoring이 technical metric과 user outcome을 함께 본다.
- release·rollback·decommission owner가 명시된다.

### Fail action

autonomy·data scope·user cohort를 줄이거나 release를 HOLD한다. 문서만 존재하고 rehearsal이 없으면 PASS가 아니다.

## G6 — Scale

### Required

- activation, repeated use, retention, task success
- cost per successful task와 unit economics
- cohort·segment별 quality와 negative impact
- canary/A-B/staged rollout
- provider·model·prompt drift monitoring
- incident, support, abuse, compliance cadence
- data·eval freshness와 feedback flywheel
- migration·sunset·decommission plan

### Pass

- 반복 가치가 scale 비용과 위험을 정당화한다.
- technical quality와 user/business metric이 함께 안정적이다.
- 새로운 failure가 trace→taxonomy→eval→change로 돌아간다.
- rollback·support·governance capacity가 growth를 따라간다.

### Fail action

traffic을 늘리지 않는다. cohort를 줄이고 원인을 분리한다. retention이나 economics가 성립하지 않으면 scale engineering을 중단한다.

## X1 — Mode & Authority Gate

### Mode

| Mode | 허용 행동 |
|---|---|
| ASSESS | 읽기, 검색, 진단, verdict. 수정 금지 |
| PLAN | 읽기와 decision-complete plan. 실행 변경 금지 |
| IMPLEMENT | 승인된 repo scope 안에서 수정·검증 |
| MONITOR | 승인된 대상 관찰·대기. 새 mutation 금지 |

Mode가 바뀌면 변경 이유와 scope를 Human에게 알린다. Review 요청은 IMPLEMENT 권한이 아니다.

### Authority classes

| Class | 예 | 기본 규칙 |
|---|---|---|
| A0 Read-only | 파일·로그·공식 문서 읽기 | 진행 가능 |
| A1 Repo write | 승인된 폴더의 코드·문서 수정 | 사용자 요청 범위에서 가능 |
| A2 External write | issue, email, browser form, cloud config | 명시적 목적과 target 확인 |
| A3 Paid action | provider call, cloud resource, purchase | budget·payment source·stop limit 확인 |
| A4 Irreversible/high impact | submit, delete, publish, production release | Human final approval와 receipt 필요 |

한 class의 승인은 더 높은 class를 포함하지 않는다.

## X2 — Risk & Budget Gate

| Tier | 조건 | 기본 검증 깊이 |
|---|---|---|
| R0 | local read-only, synthetic data, no external state | focused inspection과 최소 falsifier |
| R1 | reversible local write, low-risk prototype | focused tests, diff review, rollback |
| R2 | public deployment, external write, paid API, personal/sensitive data | threat model, auth/privacy, bounded budget, staging, Human approval |
| R3 | regulated/high-stakes decision, material money, safety impact, irreversible action | formal domain·security·privacy review, independent validation, restricted rollout |

### Escalation rule

표준 검토에서 plausible critical/high-severity attack path, sensitive-data flow, irreversible tool, unexplained loss가 발견될 때만 Deep Scan으로 승격한다.

### Timebox contract

모든 broad research·scan 전에 다음을 쓴다.

- expected deliverable
- maximum wall-clock 또는 compute budget
- progress checkpoint
- stop condition
- escalation condition

대기와 orchestration도 wall-clock budget에 포함한다. 결과 없이 timebox가 끝나면 scope를 줄이거나 HOLD한다.

## X3 — External-State Gate

Browser form, cloud console, deployment, submission에서 적용한다.

### Before action

- 현재 page·account·project·environment 확인
- 각 field를 public, sensitive, secret로 분류
- 공개 가능한 unsaved value만 durable text로 복사하고 sensitive value는 최소화·마스킹한 private artifact에 둠
- password, token, session cookie, recovery code, payment detail은 대화·공개 repo·screenshot에 저장하지 않고 승인된 secret store 또는 원래 입력 화면에만 둠
- save 여부와 last-known state 확인
- refresh·navigation·submit·delete의 영향 확인
- rollback 또는 복구 경로 확인

### During action

- 한 번에 하나의 state change
- 변경 직후 receipt·마스킹된 screenshot·ID 확인
- unsaved 상태에서 reload·back·tab close 금지
- 예상과 다른 UI면 중단하고 재확인

### After action

- live state를 다시 읽는다.
- durable receipt와 timestamp를 남긴다.
- repo projection과 external truth를 구분한다.
- irreversible action 뒤에는 자동 후속 변경을 하지 않는다.

## AI application version contract

AI product candidate는 Git SHA 하나만으로 충분하지 않다.

| Component | 고정할 값 |
|---|---|
| Code | repository SHA / candidate tree |
| Prompt | prompt ID·content digest |
| Model | provider·model ID·parameters |
| Tools | schema·permission·version |
| Data | source·consent·dataset digest |
| Eval | case set·policy·grader·seed |
| Runtime | environment·config·dependency lock |
| Decision | pass/hold/kill·approver·timestamp |

하나가 바뀌면 기존 evidence가 여전히 유효한지 재판정한다.

## Human–AI division of responsibility

| Decision | AI role | Human role |
|---|---|---|
| Problem candidates | 조사·가설·반증 질문 생성 | 실제 우선순위와 접근 가능한 사용자 결정 |
| User evidence | 정리·코딩·패턴 제안 | 실제 사용자 접촉과 해석 승인 |
| Solution options | baseline·prototype·trade-off 생성 | 가치·위험·budget 선택 |
| Eval | case·grader 초안과 실행 | outcome·rubric·acceptable error 승인 |
| Implementation | plan·code·test·review | scope와 architecture decision 승인 |
| External action | preflight·receipt 준비 | 비용·publish·submit·production 승인 |
| Release | evidence synthesis | risk acceptance와 final go/no-go |

AI가 인터뷰 transcript, adoption, 실제 pain을 가상으로 만들어 evidence 칸을 채우면 Gate FAIL이다.

## Embedded templates

### Opportunity Brief

~~~text
Project:
Date / owner:
Mode:
Risk tier:

Actor:
Representative workflow:
Trigger and frequency:
Current alternative:
Observed pain and evidence:
Desired user outcome:
Baseline:
Why now:
Contest/compliance outcome:

FACT:
ASSUMPTION:
CONSTRAINT:
DECISION:

Riskiest assumption:
Cheapest falsifier:
Kill / pivot rule:
GATE_STATUS:
NEXT_ACTION:
~~~

### User Evidence Ledger

~~~text
Evidence ID:
Actor / context:
Source type: interview | observation | telemetry | document
Observed behavior:
Pain frequency / consequence:
Current workaround:
Desired outcome:
Direct evidence:
Interpretation:
Contradicting evidence:
Confidence:
Privacy / consent boundary:
Related assumption:
~~~

### Experiment Card

~~~text
Experiment ID:
Gate:
Question:
Hypothesis:
Baseline:
Candidate:
Representative cases / users:
Primary metric:
Guardrail metrics:
Cost / time budget:
Pass threshold:
Kill threshold:
Evaluator:
Leakage controls:
Result:
GATE_STATUS:
NEXT_ACTION:
~~~

### Eval Contract

~~~text
Candidate bundle:
Target user workflow:
Golden-set source:
Case classes:
Failure taxonomy:
Baseline:
Grader types:
Human rubric:
Quality threshold:
Task-success threshold:
Latency threshold:
Cost threshold:
Safety / privacy threshold:
Seeds / repetitions:
Holdout policy:
Result and receipt:
Known limitations:
~~~

### Decision Record

~~~text
Decision ID:
Date:
Owner:
Available evidence:
Constraints:
Options:
Selected option:
Rejected options and why:
Riskiest assumption:
Equal-budget counterfactual:
Required Gate:
Review date / invalidation trigger:
~~~

### Release Card

~~~text
Candidate bundle:
Target environment / cohort:
Proof layers achieved:
Product outcome evidence:
Risk tier:
Auth / privacy / data lifecycle:
Budget and spend cap:
Rollback / restore:
Monitoring / feedback:
Approver:
Decision: release | hold | rollback | kill
Receipt:
~~~

## Default next-best action

현재 Gate를 모르면 build하지 않는다.

1. 가장 최근 evidence와 decision을 읽는다.
2. 통과하지 못한 가장 낮은 Gate를 찾는다.
3. 그 Gate의 가장 싼 falsifier 하나만 수행한다.
4. 결과를 `GATE_STATUS`로 기록하고 canonical `NEXT_ACTION` 하나를 선택한다.

메뉴를 늘리는 것보다 **낮은 Gate의 불확실성 하나를 줄이는 것**이 기본 행동이다.

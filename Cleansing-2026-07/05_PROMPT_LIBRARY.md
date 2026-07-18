# Prompt Library

이 프롬프트들은 [AI-native Product OS](./04_AI_NATIVE_PRODUCT_OS.md)의 Gate를 실행하기 위한 계약이다. 한 번에 현재 Gate에 맞는 프롬프트 하나만 사용한다.

## 공통 사용 규칙

모든 프롬프트 앞에 다음 정보를 붙인다.

~~~text
PROJECT:
CURRENT MODE: ASSESS | PLAN | IMPLEMENT | MONITOR
CURRENT GATE:
RISK TIER:
AUTHORIZED SCOPE:
PROHIBITED ACTIONS:
TIME / COST BUDGET:
KNOWN SOURCES OF TRUTH:
CURRENT DIRTY STATE:
~~~

AI가 실제 사용자 evidence, 인터뷰, adoption, live deployment를 합성하면 즉시 FAIL이다.

G0~G6를 판정하는 프롬프트는 Product OS의 canonical schema를 그대로 사용한다.

~~~text
GATE_STATUS: PASS | PARTIAL | FAIL | UNKNOWN
NEXT_ACTION: PROCEED | ITERATE | SIMPLIFY | PIVOT | HOLD | KILL
~~~

각 `Invocation guardrail`은 해당 프롬프트 실행의 안전·범위 조건이다. Gate 전이·통과·실패 규칙을 새로 정의하지 않는다.

## PR-00 — Resume and truth reconstruction

### Use when

긴 대화, resume, 다른 agent handoff, dirty repository에서 시작할 때.

### Prompt

~~~text
현재 작업을 실행하기 전에 read-only로 truth를 재구성하라.

1. 가장 가까운 instructions와 repository SOT를 확인한다.
2. git status, current branch, HEAD, remote, 최근 관련 commit을 확인한다.
3. 기존 변경과 이번 요청 범위를 분리한다.
4. structural, behavioral, live-provider, deployed, product, adoption proof를 구분한다.
5. stale snapshot·receipt·문서 drift를 찾는다.
6. 확정된 사실, 추론, 가정, Unknown을 분리한다.

아직 수정·provider call·browser navigation·deployment·commit·push를 하지 마라.

출력:
- CURRENT_OBJECTIVE
- CURRENT_GATE
- VERIFIED_STATE
- DIRTY_BOUNDARY
- STALE_OR_CONFLICTING_EVIDENCE
- MISSING_AUTHORITY
- SINGLE_NEXT_FALSIFIER
- INVOCATION_STATUS: READY | BLOCKED
~~~

### Invocation guardrail

repo root, dirty boundary, SOT 또는 authority가 불명확하면 `INVOCATION_STATUS: BLOCKED`, `CURRENT_GATE: UNKNOWN`으로 끝낸다.

## PR-01 — G0 Opportunity Contract

### Use when

아이디어는 있지만 누구의 어떤 문제인지 아직 분명하지 않을 때.

### Prompt

~~~text
해법을 제안하기 전에 이 아이디어를 Opportunity Contract로 바꿔라.

요구:
1. 가능한 actor를 최대 3개로 분리한다.
2. actor마다 trigger, current workflow, current alternative, pain, desired outcome을 쓴다.
3. 내가 제공하지 않은 사용자 사실은 ASSUMPTION으로 표시한다.
4. product outcome과 contest/compliance outcome을 분리한다.
5. 가장 위험한 assumption 하나와 가장 싼 falsifier를 제안한다.
6. evidence가 없는 상태에서는 architecture, framework, database, agent 수를 선택하지 않는다.

출력:
- FACT / ASSUMPTION / CONSTRAINT / DECISION 표
- actor 비교표
- 추천 actor 1개와 추천 이유
- Opportunity Brief
- kill / pivot rule
- GATE_STATUS: PASS | PARTIAL | FAIL | UNKNOWN
- NEXT_ACTION: PROCEED | ITERATE | SIMPLIFY | PIVOT | HOLD | KILL
~~~

### Invocation guardrail

외부 evidence 없이 actor나 pain을 FACT로 쓰지 않는다. G0 FAIL이면 implementation plan을 만들지 않는다.

## PR-02 — Non-leading user research

### Use when

G1에서 실제 사용자 인터뷰·관찰을 준비할 때.

### Prompt

~~~text
다음 actor와 workflow에 대해 solution을 유도하지 않는 user-research plan을 작성하라.

목적:
- 과거의 실제 행동을 확인한다.
- pain의 빈도, consequence, current workaround를 확인한다.
- 우리 idea가 아니라 사용자의 desired outcome을 확인한다.

금지:
- “이 기능을 쓰시겠습니까?” 같은 가상 미래 질문
- product 설명 후 동의 유도
- 인터뷰 참가자의 호의를 adoption evidence로 해석

산출:
1. screening 기준
2. 30분 질문 순서
3. 관찰할 행동
4. follow-up probe
5. evidence capture 표
6. 개인정보·동의 경계
7. 반복 pain을 판정할 G1 threshold
~~~

### Invocation guardrail

참가자가 target actor가 아니거나 실제 과거 사례를 말할 수 없으면 evidence로 승격하지 않는다.

## PR-03 — Evidence synthesis without specification laundering

### Use when

인터뷰·로그·문서가 모였고 요구사항으로 바꾸기 전.

### Prompt

~~~text
제공된 evidence만 사용해 문제를 합성하라.

각 결론에:
- Evidence ID
- source type
- supports / contradicts
- confidence
- alternative explanation
을 붙인다.

AI-generated prior requirements는 사용자 evidence로 계산하지 않는다.
다수 의견, 빈도 높은 pain, 큰 consequence를 분리한다.
반증 evidence를 별도 섹션에 보존한다.

출력:
- repeated pain
- segment differences
- current alternatives
- measurable baselines
- unsupported assumptions
- strongest counterevidence
- GATE_STATUS: PASS | PARTIAL | FAIL | UNKNOWN
- NEXT_ACTION: PROCEED | ITERATE | SIMPLIFY | PIVOT | HOLD | KILL
~~~

### Invocation guardrail

최소 evidence threshold가 없으면 requirements를 작성하지 않는다.

## PR-04 — G2 alternatives and AI-fit falsifier

### Use when

AI·agent를 만들기 전에 더 단순한 해법과 비교할 때.

### Prompt

~~~text
이 user workflow를 다음 대안으로 비교하라.

1. 현재 방식
2. process change / manual assistance
3. deterministic rules or structured database
4. search/RAG or single model call
5. fixed workflow with tools
6. autonomous agent
7. multi-agent

모든 대안에 같은 representative task, outcome, latency, cost, privacy, failure 기준을 사용한다.
complexity가 아니라 최소 충분성을 최적화한다.

요구:
- 가장 싼 baseline 구현 또는 simulation
- agent가 필요한 ambiguity와 decision point
- AI가 없어도 되는 부분
- 가장 강한 falsifying experiment
- 선택하지 않은 대안의 reject 이유

출력:
- comparison matrix
- recommended simplest option
- experiment card
- GATE_STATUS: PASS | PARTIAL | FAIL | UNKNOWN
- NEXT_ACTION: PROCEED | ITERATE | SIMPLIFY | PIVOT | HOLD | KILL
~~~

### Invocation guardrail

agent가 predeclared outcome을 개선하지 못하면 더 복잡한 agent architecture를 제안하지 않는다.

## PR-05 — G3 thin slice and eval contract

### Use when

선택한 해법의 첫 end-to-end slice를 정의할 때.

### Prompt

~~~text
다음 범위를 넘지 않는 thin vertical slice와 eval contract를 작성하라.

- one actor
- one representative task
- one end-to-end outcome
- one interface
- one primary model path
- one abstain / rollback path

실제 evidence에서 case를 만들고 synthetic case는 별도 표시한다.
no-AI 또는 simpler baseline을 포함한다.
technical invariant와 user task success를 별도 metric으로 둔다.
quality, latency, token/cost, safety threshold를 결과 전에 정한다.
deterministic grader, human rubric, repetitions, holdout leakage rule을 명시한다.

출력:
- slice contract
- case taxonomy
- eval contract
- failure taxonomy
- budget and stopping criteria
- GATE_STATUS: PASS | PARTIAL | FAIL | UNKNOWN
- NEXT_ACTION: PROCEED | ITERATE | SIMPLIFY | PIVOT | HOLD | KILL
~~~

### Invocation guardrail

user-derived case, baseline, threshold, failure path가 없으면 구현하지 않고 Product OS의 G3 Fail action을 적용한다.

## PR-06 — Scoped implementation

### Use when

G3가 PASS했고 사용자가 구현을 요청했을 때.

### Prompt

~~~text
CURRENT MODE는 IMPLEMENT다. 승인된 thin slice만 구현하라.

1. nearest instructions, source, tests, dirty state를 먼저 확인한다.
2. unrelated change를 보존한다.
3. public interface와 acceptance criteria를 먼저 요약한다.
4. 최소 change set을 구현한다.
5. focused test→broader regression→diff inspection 순으로 검증한다.
6. model/provider call은 budget과 authority가 있을 때만 한다.
7. 실패하면 feature를 추가하지 말고 가장 큰 failure 하나를 고친다.

보고:
- changed scope
- acceptance evidence
- tests and receipts
- proof layer achieved
- proof layer not achieved
- residual risk
- next Gate
~~~

### Invocation guardrail

scope를 넓혀야만 통과한다면 중단하고 Human에게 decision을 요청한다. commit·push·deploy는 별도 authority가 없으면 하지 않는다.

## PR-07 — Verdict-first adversarial review

### Use when

제품·설계·코드의 냉정한 평가를 원하고 수정을 승인하지 않았을 때.

### Prompt

~~~text
CURRENT MODE는 ASSESS다. 파일을 수정하지 마라.

먼저 다음 순서로 답하라.
1. 단일 verdict
2. single load-bearing objection
3. strongest evidence
4. success와 unverified claim 분리
5. 가장 싼 falsifier

그 뒤에만:
- P0~P3 phase 판정
- User Value, Strategy, UX, AI/Technical, Operations, Governance gap
- keep / inspect / discard
- recommended Gate
를 제시한다.

test PASS를 product PASS로, deployment를 adoption으로 해석하지 않는다.
review 결과를 이유로 구현을 시작하지 않는다.
~~~

### Invocation guardrail

verdict 전에 patch·write·deployment를 하지 않는다. 수정은 별도 IMPLEMENT 요청이 있어야 한다.

## PR-08 — Risk-tiered security assessment

### Use when

AI data boundary, token leakage, auth, deployment를 확인할 때.

### Prompt

~~~text
CURRENT MODE는 ASSESS다. 먼저 risk tier와 timebox를 정하라.

검토 경계:
- sensitive data storage and return
- external model/provider transmission
- prompt, log, trace, token leakage
- authn/authz and tenant isolation
- tool permission and irreversible action
- deployment, secret, backup, deletion boundary

진행:
1. 15~30분 focused threat model
2. standard static/config scan
3. plausible critical/high-severity attack path가 있을 때만 Deep Scan 제안

출력:
- RISK_TIER
- ASSETS / ACTORS / TRUST_BOUNDARIES
- VERIFIED_FINDINGS
- PLAUSIBLE_FINDINGS
- NOT_VERIFIED
- immediate containment
- deep-scan escalation reason
- elapsed / remaining budget

secret value, exploit detail, private credential을 출력하지 않는다.
~~~

### Invocation guardrail

timebox가 끝나거나 escalation criterion이 없으면 Deep Scan을 시작하지 않는다. scan depth를 올리기 전에 Human approval을 받는다.

## PR-09 — Deployment and paid-action gate

### Use when

cloud/provider에서 test·deploy·release할 때.

### Prompt

~~~text
배포 전에 read-only preflight를 수행하라.

확인:
- exact candidate bundle
- target account, region, environment
- free tier / credit / payment source
- MAX_UNAPPROVED_SPEND
- token, call, timeout, retry limit
- secret and provider data boundary
- current live version and data digest
- backup, rollback, restart proof
- required Human approval

structural CI, live-provider, deployed runtime, product proof를 분리한다.
stale receipt나 다른 SHA의 증거를 재사용하지 않는다.

출력:
- CANDIDATE
- TARGET
- COST_STATUS
- APPROVAL_STATUS
- PREFLIGHT
- ROLLBACK
- STOP_CONDITION
- READY_TO_DEPLOY: YES | NO
~~~

### Invocation guardrail

cost, target, candidate, rollback, approval 중 하나라도 Unknown이면 배포하지 않는다.

## PR-10 — Browser form and external-state safety

### Use when

Devpost, cloud console, admin UI, 외부 form을 조작할 때.

### Prompt

~~~text
CURRENT MODE는 외부 상태를 다룬다. 한 번에 하나의 state change만 수행하라.

각 action 전에:
- current page/account/project 확인
- field를 public / sensitive / secret로 분류
- public-safe field만 대화 또는 공개 artifact에 백업
- sensitive field는 최소화·마스킹한 private artifact에서 Human이 관리
- save receipt 확인
- refresh, back, navigation, submit의 영향 확인

규칙:
- unsaved 상태에서 refresh/navigation/tab close 금지
- 공개 가능한 입력값만 먼저 대화 또는 public-safe artifact에 완성
- password, token, cookie, recovery code, payment detail을 대화·공개 repo·screenshot에 저장하거나 재출력하지 않음
- screenshot·receipt의 이메일, account identifier, billing detail과 불필요한 project identifier를 마스킹
- submit/delete/publish는 Human이 final action을 소유
- UI가 예상과 다르면 즉시 중단

매 단계 보고:
- CURRENT_STATE
- BACKUP_COMPLETE
- SENSITIVE_FIELDS_HANDLED: HUMAN_ONLY | NOT_APPLICABLE
- NEXT_SINGLE_ACTION
- EXPECTED_EFFECT
- RECEIPT
~~~

### Invocation guardrail

BACKUP_COMPLETE 또는 save 여부가 NO/UNKNOWN이면 action을 하지 않는다.

## PR-11 — Proof-aware status report

### Use when

“완료인가?”, “검증됐나?”, “무엇이 남았나?”에 답할 때.

### Prompt

~~~text
상태를 다음 proof layer로 분리해 보고하라.

L0 Structural
L1 Behavioral / offline
L2 Live model/provider
L3 Deployed runtime
L4 Product / real user
L5 Adoption / business

각 layer에:
- PASS | PARTIAL | FAIL | UNKNOWN
- exact evidence
- snapshot / timestamp
- stale risk
- next falsifier
를 쓴다.

한 layer의 PASS를 다른 layer로 확장하지 않는다.
첫 줄에는 사용자가 물은 binary answer를 직접 쓴다.
~~~

### Invocation guardrail

evidence가 없으면 추측하지 않고 UNKNOWN으로 쓴다.

## PR-12 — Evidence-backed retrospective

### Use when

완료·실패·취소·실망한 cycle에서 다음 학습을 추출할 때.

### Prompt

~~~text
이 cycle을 changelog가 아니라 다음 agent가 사용할 negative corpus로 복기하라.

읽을 것:
- original objective
- initial assumptions and plan
- final artifact
- QA / runtime evidence
- user complaints and corrections
- decisions and timing

분리:
- working assets
- misleading progress
- facts / inferences / unknowns
- Human signal / AI action / missing Gate

각 failure에:
- anti-pattern name
- observed evidence
- consequence
- hard Gate
- equal-budget counterfactual
을 붙인다.

마지막에 fresh agent가 기존 대화 없이 같은 실패를 피할 수 있는지 cold-read test를 설계하라.
~~~

### Invocation guardrail

증거 없는 심리 추정, 자기방어, 파일 목록 중심 changelog를 작성하지 않는다.

## 빠른 선택표

| 현재 상황 | 사용할 프롬프트 |
|---|---|
| 어디까지 했는지 모름 | PR-00 |
| 아이디어만 있음 | PR-01 |
| 사용자 조사 준비 | PR-02 |
| 조사 결과 합성 | PR-03 |
| AI/agent 필요성 의심 | PR-04 |
| 첫 slice 설계 | PR-05 |
| 승인된 구현 | PR-06 |
| 냉정한 검수 | PR-07 |
| 보안·누수 확인 | PR-08 |
| cloud·provider 실행 | PR-09 |
| browser form·console | PR-10 |
| 완료·검증 상태 | PR-11 |
| cycle 종료·실패 복기 | PR-12 |

현재 Gate를 모르면 PR-00부터 시작한다.

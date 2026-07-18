# 0→1→100 Gap Analysis

## 핵심

AI-native 제품 개발은 전통적인 제품 발견을 건너뛰는 방법이 아니다.

> 전통 제품 기반<br>
> + 확률적 동작을 다루는 eval·trace·feedback<br>
> + 모델·프롬프트·데이터·도구의 version contract<br>
> + 인간 감독과 위험 기반 autonomy<br>
> = AI-native product development

Librarian은 오른쪽의 AI engineering 요소를 강하게 만들었지만 왼쪽의 problem evidence를 생략했다. [CLM-013](./06_EVIDENCE_REGISTER.md#clm-013)

## MECE 분류 규칙

각 finding은 하나의 Primary Phase와 하나의 Primary Domain만 소유한다. 다른 관련성은 tag로만 붙인다.

### Phase

| Code | Phase | 질문 |
|---|---|---|
| P0 | Opportunity evidence | 누구의 어떤 문제가 해결할 가치가 있는가? |
| P1 | Solution evidence, 0→1 | 가장 작은 해법이 대표 과업을 더 잘 끝내게 하는가? |
| P2 | Product evidence, 1→10 | 반복 사용 가능하고 이해·통제·운영할 수 있는가? |
| P3 | Scale evidence, 10→100 | 채택과 경제성이 규모의 비용·위험을 정당화하는가? |

### Domain

| Code | Primary Domain | 소유 질문 |
|---|---|---|
| UV | User Value | 누가, 어떤 결과를, 왜 원하는가? |
| SV | Strategy & Viability | 대안·차별화·채택·사업성이 성립하는가? |
| UX | Experience & Trust | 사용자가 이해·완료·통제·회복할 수 있는가? |
| AT | AI & Technical | AI가 필요한가, 품질·데이터·architecture가 가능한가? |
| OP | Delivery & Operations | 안전하게 배포·운영·복구할 수 있는가? |
| LG | Learning & Governance | 어떤 증거로 결정하고 언제 멈추는가? |

## P0 — Opportunity evidence

| ID | Domain | Present | Missing or mistimed | Gap status / timing | Evidence |
|---|---|---|---|---|---|
| P0-UV-01 | UV | stale·contradiction이라는 기술 문제 서술 | actor, JTBD, current journey, pain 빈도·손실, 인터뷰·관찰 | Missing | CLM-001, CLM-007 |
| P0-SV-01 | SV | Track 비교와 수상 포지셔닝 | 사용자 대안, 경쟁 제품의 실제 switch reason, 채택 경로, willingness | Missing | CLM-003, CLM-019 |
| P0-UX-01 | UX | 최종 interaction의 기술적 상상 | paper prototype, user mental model, capability expectation, error-recovery test | Missing | CLM-010 |
| P0-AT-01 | AT | wiki+graph+Qwen+MCP architecture hypothesis | deterministic DB, structured state, latest-write-wins, simple RAG 대비 AI 필요성 검증 | Too early | CLM-001, CLM-014 |
| P0-OP-01 | OP | free-tier·Alibaba·public repo 제약을 초기 NFR에 기록 | 제품 risk와 demo risk를 분리한 운영 깊이 결정 | Partial | EV-027 |
| P0-LG-01 | LG | MECE requirements와 Unit DoD | assumption ledger, product kill rule, evidence owner, user outcome definition | Missing | CLM-002, CLM-003 |

P0의 실패는 아이디어가 나빴다는 판정이 아니다. **아이디어를 problem fact로 승격하기 전에 필요한 외부 증거가 없었다**는 판정이다.

## P1 — Solution evidence, 0→1

| ID | Domain | Present | Missing or mistimed | Gap status / timing | Evidence |
|---|---|---|---|---|---|
| P1-UV-01 | UV | correction→current answer라는 명확한 기술 결과 | 대표 사용자의 실제 task completion, 시간·오류 baseline | Missing | CLM-010 |
| P1-SV-01 | SV | file-based memory와 evidence lifecycle 차별화 | 사용자가 그 차이를 선택할 이유, integration wedge, adoption hypothesis | Missing | CLM-007 |
| P1-UX-01 | UX | demo UI를 최종적으로 구현 | UI가 P2·영상용으로 늦게 왔고 low-fidelity user test는 없었다 | Too late | D-004, D-007, CLM-007 |
| P1-AT-01 | AT | ingest/query/forget thin slice, deterministic transitions, technical baselines | no-AI baseline과 실제 user-derived golden set을 이용한 AI-fit 판정 | Partial | CLM-009, CLM-015 |
| P1-OP-01 | OP | deployment, auth, spend cap을 빠르게 구성 | 가치가설 실패 시 infrastructure 투자를 멈추는 budget gate | Too early | D-005 |
| P1-LG-01 | LG | 정교한 eval contract와 honest HOLD | eval objective가 user outcome보다 memory lifecycle thesis에 포획됨 | Partial | CLM-006, CLM-009 |

Librarian은 “1”의 절반을 만들었다. vertical slice는 동작했지만 그 slice의 user value가 검증되지 않았다.

## P2 — Product evidence, 1→10

| ID | Domain | Present | Missing or mistimed | Gap status / timing | Evidence |
|---|---|---|---|---|---|
| P2-UV-01 | UV | judge가 따라 할 수 있는 complete demo | pilot users, activation, repeated use, user feedback corpus | Missing | CLM-007, CLM-019 |
| P2-SV-01 | SV | public repo와 MCP/API integration surface | onboarding funnel, client example, design partner, OSS adoption loop, cost per success | Missing | CLM-019 |
| P2-UX-01 | UX | citation, transition, explanation, model/token visibility | 실제 업무의 disputed·historical·multi-source path와 failure recovery usability | Partial | CLM-010 |
| P2-AT-01 | AT | code·prompt·policy·candidate·receipt binding과 bitemporal correctness | provider/model drift에 대한 user-outcome monitoring | Strong partial | CLM-009 |
| P2-OP-01 | OP | TLS, shared Basic Auth, bounded calls, restart·rollback proof | tenant isolation, user authz, deletion·retention, backup/DR, backpressure | Missing | CLM-011 |
| P2-LG-01 | LG | proof layer 분리와 promotion HOLD | product feedback→error taxonomy→eval set으로 돌아오는 loop | Missing | CLM-009, CLM-015 |

Engineering evidence는 P2 수준의 일부 자산을 갖췄지만 Product evidence는 P1을 통과하지 않았으므로 전체 단계가 P2가 되는 것은 아니다.

## P3 — Scale evidence, 10→100

| ID | Domain | Present | Missing | Gap status / timing | Evidence |
|---|---|---|---|---|---|
| P3-UV-01 | UV | 없음 | segment별 retention, longitudinal outcome, negative impact | UNKNOWN | CLM-019 |
| P3-SV-01 | SV | 없음 | pricing, unit economics, channel efficiency, competitive durability | UNKNOWN | CLM-019 |
| P3-UX-01 | UX | 없음 | large-cohort trust calibration, support burden, accessibility outcome | UNKNOWN | CLM-019 |
| P3-AT-01 | AT | local proof kernel | representative load, model migration, large-corpus quality, drift | UNKNOWN | CLM-009, CLM-011 |
| P3-OP-01 | OP | single-ECS release discipline | multi-tenant scale, SLO, incident program, DR rehearsal, decommission | UNKNOWN | CLM-011 |
| P3-LG-01 | LG | eval·receipt vocabulary | online experiment, canary, adoption/business dashboard, governance cadence | UNKNOWN | CLM-006, CLM-009, CLM-015, CLM-019 |

배포됐다는 이유로 P3에 진입했다고 말할 수 없다. Scale은 traffic이 아니라 반복 가치와 통제 가능한 economics에 대한 증거다.

## 전통 개발과 AI-native 개발의 관계

| Product concern | 전통 제품 개발에서 유지할 기반 | AI-native에서 추가할 계약 | Librarian 판정 |
|---|---|---|---|
| 문제 발견 | 사용자 조사, journey, JTBD, pain baseline | AI가 적합한 ambiguity·unstructured-data 지점 구분 | 기반 누락 |
| 대안 선택 | 경쟁·수작업·규칙 기반 대안 비교 | no-AI, deterministic, single-call, workflow, agent 순으로 복잡성 증명 | 해법 선결정 |
| 요구사항 | user outcome과 acceptance criteria | representative cases, distribution, abstention, failure taxonomy | technical contract 강함, user contract 약함 |
| Prototype | throwaway mock과 usability test | Wizard-of-Oz AI, simulated failure, trust·control test | demo가 너무 늦음 |
| QA | functional, integration, security test | stochastic eval, repeated trial, human/domain grader, leakage 방지 | 매우 강하지만 objective가 좁음 |
| Release | versioning, staging, rollback | code+prompt+model+data+eval bundle, token·latency·quality budget | 강함 |
| UX | onboarding, feedback, recovery | capability·limit disclosure, confidence, human handoff, recourse | explanation은 강하고 user test는 없음 |
| Operations | auth, privacy, SLO, backup, incident | provider boundary, prompt injection, model drift, cost per successful task | demo는 강하고 production은 약함 |
| Improvement | analytics, research, experiment | traces→failure corpus→eval→prompt/model/tool change loop | technical loop만 존재 |

공식·1차 출처 근거와 scope caveat는 [CLM-013~018](./06_EVIDENCE_REGISTER.md#clm-013)에 있다.

## Anti-pattern register

### AP-01 — Solution-first Inception

사용자와 문제를 조사하기 전에 product noun과 architecture를 확정한다.

- Signal: 요구사항 첫 문장에 해법이 이미 들어 있다.
- Cost: 이후 모든 discovery가 선택한 해법을 정당화하는 방향으로 흐른다.
- Caught by: G0, G1
- Case: D-001

### AP-02 — Specification Laundering

검증되지 않은 가정이 AI가 만든 MECE 문서·ADR·Unit을 거치며 검증된 사실처럼 보인다.

- Signal: 각 요구의 evidence source가 사용자·데이터가 아니라 이전 AI 문서다.
- Cost: 문서 품질이 의사결정 품질로 오인된다.
- Caught by: G0 evidence class, G1 external evidence
- Case: D-001, D-004

### AP-03 — Contest-metric Substitution

심사·compliance·demo metric을 product outcome과 같은 것으로 취급한다.

- Signal: 성공 기준이 제출물과 기술 지표뿐이다.
- Cost: 제출은 성공해도 user value가 Unknown으로 남는다.
- Caught by: G0 outcome split, G4
- Case: D-002, D-009

### AP-04 — Technical-thesis Capture

사용자 문제 해결이 특정 data structure나 algorithm의 우월성을 증명하는 연구로 변한다.

- Signal: product question보다 architecture invariant가 roadmap을 소유한다.
- Cost: 실제 사용자가 원하는 outcome 변화 없이 proof만 깊어진다.
- Caught by: G2, G4 proof budget
- Case: D-003, D-005

### AP-05 — Proof-surface Inversion

가치가설보다 검증 가능한 내부 surface에 더 많은 예산이 들어간다.

- Signal: assurance surface가 커지는데 user evidence count는 0이다.
- Cost: 틀린 방향을 더 안전하고 재현 가능하게 만든다.
- Caught by: G4 checkpoint, X2 budget gate
- Case: D-005

### AP-06 — Offline-eval Absolutism

golden set·test·holdout이 실제 사용자 결과를 대신한다고 본다.

- Signal: eval PASS가 activation·task success·trust를 증명하는 문장으로 확장된다.
- Cost: production에서 처음 보는 failure와 adoption gap을 놓친다.
- Caught by: Proof Layers, G4
- Case: D-005, D-010

### AP-07 — Agentic Wrong-way Acceleration

AI가 빠른 것을 이용해 가정을 반증하기보다 가정 위의 artifact를 빠르게 늘린다.

- Signal: 한 시간에 requirements→architecture→implementation이 진행된다.
- Cost: sunk cost와 context가 이후 선택을 포획한다.
- Caught by: G0 approval, smallest falsifier first
- Case: D-004

### AP-08 — Risk-depth Mismatch

위험과 시간·비용 상한을 정하지 않고 가장 깊은 조사·scan부터 시작한다.

- Signal: expected deliverable과 timebox 없이 multi-pass orchestration이 열린다.
- Cost: 결과 전에 지연·취소가 발생한다.
- Caught by: X2 risk tier
- Case: D-006

### AP-09 — Review-to-Write Drift

진단 요청을 구현 승인으로 해석한다.

- Signal: verdict 전 diff가 생긴다.
- Cost: 판단 지연, 권한 위반, unrelated change 위험.
- Caught by: X1 mode lock
- Case: D-007

### AP-10 — Unsaved External-state Mutation

browser form이나 cloud console의 미저장 상태에서 refresh·navigation·destructive action을 수행한다.

- Signal: durable copy와 save receipt가 없다.
- Cost: 사용자 입력과 신뢰 손실.
- Caught by: X3 external-state checkpoint
- Case: D-008

## 단일 load-bearing lesson

> AI 실행 속도가 빨라질수록 build를 시작하는 기준은 느슨해지면 안 된다. 더 빨리 만들 수 있기 때문에 더 빨리 반증해야 한다.

구체적인 통과 기준과 stop rule은 [04_AI_NATIVE_PRODUCT_OS.md](./04_AI_NATIVE_PRODUCT_OS.md)가 정본이다.

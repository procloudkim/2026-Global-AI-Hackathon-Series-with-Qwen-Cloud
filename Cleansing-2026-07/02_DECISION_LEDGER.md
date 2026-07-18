# Decision Ledger

이 문서는 이 cycle의 주요 의사결정에 대한 유일한 정본이다. 목적은 책임 추궁이 아니라 **같은 입력에서 다음에는 다른 결정을 내릴 수 있게 하는 것**이다.

## 읽는 법

- Human signal은 사용자의 목표·질문·제약이다.
- AI action은 Codex가 제안·설계·실행한 선택이다.
- Missing Gate는 Human과 AI 사이에서 결정을 멈췄어야 할 장치다.
- 당시 증거와 현재 해석을 분리한다.
- 직접 대화가 남지 않은 초기 항목은 저장소 문서에서 추론했으며 원문 인용으로 취급하지 않는다.
- 짧은 정제 문장 외의 대화, 인증정보, 개인 자격정보는 보존하지 않는다.

## Decision summary

| ID | 결정 | 당시 결과 | 현재 판정 |
|---|---|---|---|
| D-001 | 사용자보다 해법을 먼저 확정 | 빠른 architecture lock | 잘못된 순서 |
| D-002 | 수상 조건을 제품 성공으로 사용 | 제출 서사 명확 | 제품 metric 대체 |
| D-003 | Track 핵심 후보를 범위 제외 | scope 축소 | Track fit PARTIAL |
| D-004 | discovery 없는 U1~U10 | 4시간 내 end-to-end | solution lock-in |
| D-005 | product proof보다 proof kernel 확장 | 강한 assurance | proof-surface inversion |
| D-006 | 위험도·timebox 없는 Deep Scan | 장시간 orchestration 후 취소 | risk-depth mismatch |
| D-007 | 검수를 구현 승인으로 확장 | guided UI 개선 | authority drift |
| D-008 | 미저장 외부 폼에서 refresh | 입력 손실 | external-state failure |
| D-009 | 통합 제품 감사보다 제출 완료 우선 | submission 완료 | audit timing failure |
| D-010 | 올바른 제품 판정을 사후 수행 | 핵심 학습 확보 | 내용은 옳고 시점은 늦음 |

## D-001 — 사용자보다 해법을 먼저 확정

| Field | Record |
|---|---|
| Date / Phase | 2026-07-03 / 0 |
| Human signal | Track 1용 MemoryAgent를 짧은 일정에 설계·구현한다. 직접 초기 대화는 보존되지 않아 문서에서 추론했다. |
| Evidence available then | Track 설명, 마감 7일 전 일정, LLM Wiki·Graphify·Autoresearch 아이디어 |
| AI action | UNKNOWN. 직접 초기 대화가 없어 AI 귀속은 할 수 없다. 저장소에 남은 최초 Inception artifact에는 RAG의 무축적·모순·stale 문제와 file-wiki Librarian이 이미 결합돼 있다. |
| Missing Gate | G0 Opportunity Contract, G1 Problem Evidence |
| Observed consequence | 기능 요구는 상세했지만 actor, JTBD, 현재 workflow, pain frequency, adoption evidence가 생기지 않았다. |
| Current verdict / confidence | FAIL / HIGH |
| Equal-budget counterfactual | 첫 60~90분에 actor 3개와 pain 3개를 비교하고 실제 증거가 가장 빠른 한 workflow만 선택한다. |
| Sources | [CLM-001](./06_EVIDENCE_REGISTER.md#clm-001), [CLM-007](./06_EVIDENCE_REGISTER.md#clm-007) |

## D-002 — 수상 조건을 제품 성공 조건으로 사용

| Field | Record |
|---|---|
| Date / Phase | 2026-07-03 / 0 |
| Human signal | 심사에서 설명 가능하고 동작하는 출품물이 필요하다. |
| Evidence available then | 제출·심사 요구와 기술 아이디어 |
| AI action | 성공을 Qwen demo, 망각 장면, 토큰 절감, MCP 통합으로 정의했다. |
| Missing Gate | G0 Opportunity Contract, G4 User Product Proof |
| Observed consequence | 심사 증거는 강화됐지만 누가 어떤 결과를 더 잘 얻는지와 재사용 이유는 미검증으로 남았다. |
| Current verdict / confidence | FAIL / HIGH |
| Equal-budget counterfactual | Product outcome과 contest compliance를 별도 표로 두고 둘 다 통과해야 성공으로 판단한다. |
| Sources | [CLM-003](./06_EVIDENCE_REGISTER.md#clm-003) |

## D-003 — Track 차별화의 핵심 후보를 범위에서 제외

| Field | Record |
|---|---|
| Date / Phase | 2026-07-03 / 0→1 |
| Human signal | 제한된 일정 안에서 저장·검색·망각 demo를 완성한다. |
| Evidence available then | Track 문구와 Autoresearch 기반 self-improvement 아이디어 |
| AI action | 전체 자기개선 loop를 out of scope로 두고 explicit source ingest와 lifecycle correctness에 집중했다. |
| Missing Gate | G2 Alternatives & AI Fit, G3 Thin Slice & Eval Contract |
| Observed consequence | persistence·forgetting·audit는 강해졌지만 autonomous experience accumulation과 increasingly accurate decisions는 증명하지 못했다. preference claim과 explicit update는 존재하므로 total failure는 아니다. |
| Current verdict / confidence | PARTIAL / HIGH |
| Equal-budget counterfactual | 전체 연구 loop 대신 사용자 feedback 하나가 다음 decision을 바꾸고 holdout outcome을 개선하는 단일 폐루프를 먼저 만든다. |
| Sources | [CLM-004](./06_EVIDENCE_REGISTER.md#clm-004) |

## D-004 — Discovery 없이 구현 작업만 직렬화

| Field | Record |
|---|---|
| Date / Phase | 2026-07-03 / 0→1 |
| Human signal | 마감 7일 전부터 end-to-end 결과물을 만든다. |
| Evidence available then | requirements·units·architecture 초안 |
| AI action | scaffold→store→ingest→query→forget→deploy→MCP→benchmark→UI→submission으로 U1~U10을 구성했다. |
| Missing Gate | G1 Problem Evidence |
| Observed consequence | 첫 commit부터 report까지 약 3시간 48분의 기록 구간에 전체 해법이 고정됐다. 이후 작업은 선택한 해법의 정교화가 됐다. |
| Current verdict / confidence | FAIL / HIGH |
| Equal-budget counterfactual | 첫 4시간을 계약 확인, 문제 비교, workflow 증거, throwaway prototype, kill/pivot 결정에 사용한다. |
| Sources | [CLM-002](./06_EVIDENCE_REGISTER.md#clm-002), [CLM-005](./06_EVIDENCE_REGISTER.md#clm-005) |

## D-005 — 제품 증거보다 proof kernel을 크게 확장

| Field | Record |
|---|---|
| Date / Phase | 2026-07-13~18 / engineering·proof expansion; product P1 not passed |
| Human signal | 절차보다 기술력과 Track 적합성에 집중해 우승 가능성을 증명할 설계를 원했다. |
| Evidence available then | 동작하는 memory engine, 초기 benchmark, 공식 Track contract |
| AI action | claim lifecycle, bitemporal invariants, independent holdout, baselines, promotion gates, exact-SHA deployment·restart·rollback proof를 확대했다. |
| Missing Gate | G4 User Product Proof, X2 Risk & Budget Gate |
| Observed consequence | 공학 신뢰성과 정직한 proof boundary는 크게 높아졌으나 사용자 검증 artifact는 생기지 않았다. |
| Current verdict / confidence | PARTIAL / HIGH |
| Equal-budget counterfactual | 가장 싼 falsifying baseline과 live scenario 뒤에는 proof 확대를 멈추고 실제 사용자 과업·오류 이해·재사용 의향에 예산을 전환한다. |
| Sources | [CLM-006](./06_EVIDENCE_REGISTER.md#clm-006), [CLM-009](./06_EVIDENCE_REGISTER.md#clm-009) |

## D-006 — 위험도와 timebox 없이 Deep Scan 재개

| Field | Record |
|---|---|
| Date / Phase | 2026-07-18 / security |
| Human signal | 민감정보 저장·반환, Qwen 전송, 로그·토큰, 인증·배포 경계를 증거 기반으로 깊게 확인한다. |
| Evidence available then | public demo, synthetic judge flow, provider boundary, 기존 security concern |
| AI action | 전체 repository multi-pass Deep Scan과 여러 하위 작업을 시작했다. |
| Missing Gate | X2 Risk & Budget Gate |
| Observed consequence | 분석 결과보다 orchestration·대기 시간이 앞서 사용자가 필요성과 지연을 문제 삼고 취소했다. |
| Current verdict / confidence | FAIL / HIGH |
| Equal-budget counterfactual | 30분 focused threat model과 표준 scan을 먼저 수행하고 critical/high-severity attack path가 있을 때만 승인된 timebox로 Deep Scan한다. |
| Sources | [CLM-018](./06_EVIDENCE_REGISTER.md#clm-018), [EV-030](./06_EVIDENCE_REGISTER.md#conversation-evidence) |

## D-007 — 검수 요청을 구현 승인으로 확장

| Field | Record |
|---|---|
| Date / Phase | 2026-07-17~18 / review |
| Human signal | 냉철한 adversarial Track·product 검수를 원했다. |
| Evidence available then | current repo, official Track, local proof |
| AI action | judge verdict를 먼저 끝내지 않고 memory explanation과 guided demo 구현으로 넘어갔다. |
| Missing Gate | X1 Mode & Authority Gate |
| Observed consequence | UI와 local validation은 좋아졌지만 요청한 판단이 늦어지고 review-only와 write 권한이 섞였다. |
| Current verdict / confidence | FAIL / HIGH |
| Equal-budget counterfactual | 첫 산출물은 verdict, 단일 load-bearing objection, 증거만 제공한다. 구현은 별도 승인 뒤 독립 change set으로 수행한다. |
| Sources | [CLM-010](./06_EVIDENCE_REGISTER.md#clm-010), [EV-029](./06_EVIDENCE_REGISTER.md#conversation-evidence) |

## D-008 — 저장되지 않은 외부 폼에서 새로고침

| Field | Record |
|---|---|
| Date / Phase | 2026-07-18 / submission UI |
| Human signal | Devpost 각 단계에 입력할 값을 안내·지원한다. |
| Evidence available then | 사용자가 작성 중인 browser form과 repo submission text |
| AI action | 상태 재확인 과정에서 저장되지 않은 form을 refresh 또는 재탐색했다. |
| Missing Gate | X3 External-State Gate |
| Observed consequence | Testing Instructions 등 입력 내용이 사라져 재입력이 필요했고 시간과 신뢰가 손실됐다. |
| Current verdict / confidence | FAIL / HIGH |
| Equal-budget counterfactual | 모든 field 값을 먼저 durable text로 완성한다. unsaved 상태에서는 navigate·reload하지 않고 저장 receipt를 확인한 뒤 이동한다. |
| Sources | [EV-028](./06_EVIDENCE_REGISTER.md#conversation-evidence) |

## D-009 — 통합 제품 감사보다 제출 완료를 먼저 달성

| Field | Record |
|---|---|
| Date / Phase | 2026-07-18 / release |
| Human signal | 제출은 Human이 직접 수행하고 남은 checklist를 완결한다. |
| Evidence available then | verified deployment, submission copy, video·architecture assets |
| AI action | 배포 증거, form 값, submission checklist를 우선 마무리했다. Human이 최종 submit 권한을 유지한 것은 올바른 경계였다. |
| Missing Gate | G4 User Product Proof before G5 Preproduction |
| Observed consequence | 제출은 완료됐지만 제품 수준 감사가 뒤에 와 근본 방향 수정에는 늦었다. |
| Current verdict / confidence | PARTIAL / HIGH |
| Equal-budget counterfactual | submit 전 45분 fatal-objection audit를 실행하고 수정이 어렵다면 claim과 demo를 실제 evidence 수준에 맞춘다. |
| Sources | [CLM-008](./06_EVIDENCE_REGISTER.md#clm-008), [CLM-019](./06_EVIDENCE_REGISTER.md#clm-019), [EV-026](./06_EVIDENCE_REGISTER.md#conversation-evidence) |

## D-010 — 올바른 제품 판정을 사후에 수행

| Field | Record |
|---|---|
| Date / Phase | 2026-07-18 / retro |
| Human signal | “현재 product 수준은 무엇인가?”, “결국 무엇을 만든 것인가, 계획 실패인가?” |
| Evidence available then | 전체 repo, deployment receipts, submitted narrative, conversation history |
| AI action | engineering-assured MVP이지만 product value와 production은 미증명이라고 재분류했다. |
| Missing Gate | 판단 내용이 아니라 배치 시점. 같은 audit가 G0과 G4에 없었다. |
| Observed consequence | 실패가 코드 품질보다 0→1을 건너뛴 순서와 최적화 대상에 있다는 학습이 명확해졌다. |
| Current verdict / confidence | PARTIAL / HIGH |
| Interpretation | Content PASS; timing FAIL. |
| Equal-budget counterfactual | G0에서는 concept falsifier로, G4에서는 release claim audit로 같은 질문을 반복한다. |
| Sources | [CLM-012](./06_EVIDENCE_REGISTER.md#clm-012), [EV-015](./06_EVIDENCE_REGISTER.md#conversation-evidence) |

## Causal chain

> 문제 미검증 → 수상 지표로 성공 정의 → solution-first 구현 → 기술 proof 확대 → 운영·제출 최적화 → 제품 감사가 사후 도착

이 사슬은 현재 가장 단순한 설명이지만 실험으로 입증된 인과 법칙은 아니다. 새로운 당시 기록이 나오면 D-ID를 덮어쓰지 말고 반증·수정 note를 추가한다.

## 재사용할 좋은 결정

실패만 보존하면 다음 프로젝트가 반대 방향으로 과잉 반응한다. 다음은 유지한다.

- 최종 submit과 외부 irreversible action을 Human이 소유했다.
- 더 강한 감사가 기존 receipt를 무효화했을 때 stale receipt를 폐기하고 다시 생성했다.
- local, live, deployed, promotion, submission proof를 구분했다.
- 비용·provider call을 bounded contract로 만들었다.
- promotion HOLD와 production 경계를 숨기지 않았다.

다음 cycle의 목표는 이 discipline을 버리는 것이 아니라 **G0~G4 뒤에 배치하는 것**이다.

# Cleansing 2026-07

> Librarian 해커톤 사이클을 공개 가능한 증거로 복기하고, 다음 프로젝트에서 같은 순서 오류를 막기 위한 AI-native Product OS로 일반화한 문서 팩이다.

## 한 문장 판정

Librarian은 실패한 코드가 아니라 **공학적 증명과 제출 완성도는 깊게 확장됐지만 제품은 P1을 통과하지 못한 engineering-assured hackathon MVP**다. 무엇을 만들었는지는 분명하지만, 누가 반복해서 왜 써야 하는지와 실제 성과는 입증하지 못했다. 근거는 [CLM-008](./06_EVIDENCE_REGISTER.md#clm-008)에 고정한다.

## Librarian 한눈에 보기

Librarian은 source 문서를 입력받아 근거가 붙은 atomic claim을 저장하고, 교정 source가 들어오면 오래된 claim을 대체 상태로 전환한다. 사용자는 현재 유효한 답과 citation을 받고, explanation에서 무엇이 왜 바뀌었는지 확인한다. FastAPI·MCP·guided web demo와 Qwen eval·Alibaba 배포까지 구현했지만, 이 흐름은 **single-operator judge demo**로 검증됐을 뿐 target user와 실제 업무 성과는 검증되지 않았다. [CLM-010](./06_EVIDENCE_REGISTER.md#clm-010)

## 이 문서가 답하는 질문

- 실제로 무엇을 만들었고, 무엇은 만들지 못했는가?
- 전통적인 제품 개발의 0→1 과정에서 무엇을 건너뛰었는가?
- AI를 사용하면서 새로 필요해진 설계·평가·안전·운영 원칙은 무엇인가?
- 다음 프로젝트에서 어떤 Gate를 통과하기 전에는 구현하면 안 되는가?
- Codex에 어떤 프롬프트를 주면 같은 과잉 구현과 권한 드리프트를 줄일 수 있는가?

## 0, 1, 100의 정의

| 단계 | 의미 | 증거 |
|---|---|---|
| 0 | 기회 상태. 사용자, 반복 과업, pain, 현재 대안, 원하는 결과가 아직 가설이다. | 실제 사용자·현장·기존 데이터 |
| 1 | 한 명의 명확한 사용자가 대표 과업 하나를 더 잘 완료한다는 thin vertical slice가 증명됐다. | 사용자 과업 결과 + 기술 eval |
| 100 | 반복 채택과 운영이 가능하고 품질·비용·보안·조직적 지원이 규모에 맞게 통제된다. | 사용·유지·경제성·운영 증거 |

이 팩은 실행상 필요한 중간 단계를 0→1, 1→10, 10→100으로 나눈다. 배포나 테스트 통과만으로 단계가 올라가지는 않는다.

`P0~P3`는 [Gap Analysis](./03_0_1_100_GAP_ANALYSIS.md#phase)의 제품 증거 단계다. 이 문서의 `P1 미통과`는 **대표 사용자의 solution evidence가 없었다**는 뜻이다. `G0~G6`는 별도 namespace인 [Product OS의 실행 Gate](./04_AI_NATIVE_PRODUCT_OS.md#전체-흐름)다.

## 문서 지도

| 문서 | 역할 | 갱신 성격 |
|---|---|---|
| [01_CASE_STUDY.md](./01_CASE_STUDY.md) | 시간선, 실제 산출물, 다축 최종 판정, 반사실적 경로 | Frozen |
| [02_DECISION_LEDGER.md](./02_DECISION_LEDGER.md) | 전환점별 Human·AI·부재한 Gate와 결과 | Frozen |
| [03_0_1_100_GAP_ANALYSIS.md](./03_0_1_100_GAP_ANALYSIS.md) | 단계×제품 도메인 MECE 갭 분석과 안티패턴 | Frozen |
| [04_AI_NATIVE_PRODUCT_OS.md](./04_AI_NATIVE_PRODUCT_OS.md) | 다음 프로젝트의 Gate, 위험 등급, 증거 계층, 템플릿 | Living |
| [05_PROMPT_LIBRARY.md](./05_PROMPT_LIBRARY.md) | 바로 복사해 쓰는 단계별 Codex 프롬프트 | Living |
| [06_EVIDENCE_REGISTER.md](./06_EVIDENCE_REGISTER.md) | Claim·Evidence의 유일한 정본 | Frozen |

## 독자별 시작점

| 독자 | 첫 행동 |
|---|---|
| 이번 사이클을 복기하는 사람 | Case Study → Decision Ledger → Gap Analysis 순서로 읽는다. |
| 판정을 검증하는 사람 | 아래 판정표의 CLM-ID → Evidence Register의 EV-ID → 원천 순서로 역추적한다. |
| 새 프로젝트를 시작하는 사람 | Product OS의 [G0 Opportunity Contract](./04_AI_NATIVE_PRODUCT_OS.md#g0--opportunity-contract)부터 작성한다. |
| Codex로 현재 Gate를 실행하는 사람 | Prompt Library에서 해당 PR-ID 하나만 사용한다. Gate를 모르면 [PR-00](./05_PROMPT_LIBRARY.md#pr-00--resume-and-truth-reconstruction)부터 시작한다. |

## 핵심 용어

| 용어 | 이 팩에서의 뜻 |
|---|---|
| engineering-assured | L0 structural부터 제한된 L3 deployed proof까지 강하다는 뜻이다. L4 product·L5 adoption PASS를 포함하지 않는다. |
| HOLD | bounded eval은 통과했지만 더 넓은 promotion을 승인할 증거는 부족하다는 release decision이다. |
| claim lifecycle | active, superseded, disputed 같은 상태로 기억의 현재성·충돌·대체 이력을 관리하는 방식이다. |
| 최소 안전 기준 | Product OS의 [X1 권한](./04_AI_NATIVE_PRODUCT_OS.md#x1--mode--authority-gate), [X2 위험·예산](./04_AI_NATIVE_PRODUCT_OS.md#x2--risk--budget-gate), [X3 외부 상태](./04_AI_NATIVE_PRODUCT_OS.md#x3--external-state-gate) 중 현재 risk tier와 행동에 필요한 최소 통제다. |
| 권한 드리프트 | ASSESS 요청을 승인 없는 IMPLEMENT나 외부 쓰기로 넓히는 오류다. |

## 최종 판정표

| 축 | 판정 | 뜻 | 근거 |
|---|---|---|---|
| 문제·수요 검증 | FAIL | 저장소에서 persona, 실제 workflow 관찰, 사용자 인터뷰, 채택 의향 또는 사용자 outcome baseline을 찾지 못했다. 저장소 밖 활동 여부는 Unknown이다. | [CLM-007](./06_EVIDENCE_REGISTER.md#clm-007) |
| MemoryAgent Track 적합 | PARTIAL | 저장·검색·망각·제한 컨텍스트는 강하지만 자율 경험 축적, 선호 기억, 시간에 따른 의사결정 정확도 향상은 제품 수준으로 증명하지 못했다. | [CLM-004](./06_EVIDENCE_REGISTER.md#clm-004) |
| 엔지니어링·증명 | PASS | 경계가 좁은 PASS다. 불변 원천, claim lifecycle, 결정적 검증, exact-SHA Alibaba 배포와 재시작 증거가 있지만 live-Qwen gate는 2-case이고 promotion은 HOLD다. | [CLM-009](./06_EVIDENCE_REGISTER.md#clm-009) |
| 제출·운영 | PARTIAL | 심사용 배포와 제출 패키지는 강하다. 다중 사용자 인증·격리, 개인정보 lifecycle, backup/DR, 사용자 피드백 운영은 production 수준이 아니다. | [CLM-011](./06_EVIDENCE_REGISTER.md#clm-011), [EV-026](./06_EVIDENCE_REGISTER.md#conversation-evidence) |
| 수상·채택·사업 성과 | UNKNOWN | 결과 발표, 반복 사용자, retention, 지불 의향 자료가 이 snapshot에 없다. | [CLM-019](./06_EVIDENCE_REGISTER.md#clm-019) |

이 표는 단일 성공/실패 점수를 금지한다. 공학 PASS가 제품 PASS를 대체하지 않고, 제품 미검증이 이미 만든 공학 자산을 무효화하지도 않는다.

## Snapshot과 공개 경계

판정에 사용한 SHA와 역할은 Evidence Register의 [frozen Snapshot](./06_EVIDENCE_REGISTER.md#snapshot)이 정본이다. 후속 evidence snapshot이 배포 runtime의 receipt를 보존하므로 두 SHA가 다르며 상세 관계는 [Known drift](./06_EVIDENCE_REGISTER.md#known-drift)에 기록한다.

- 분석 기준일: 2026-07-18 KST
- 현재 working tree의 미커밋 작업은 판정 근거에 포함하지 않는다.

이 폴더는 공개 저장소 안에 있으므로 다음을 포함하지 않는다.

- 대화 전체 원문, 로컬 memory·session 경로, 비공개 thread 접근정보
- 아이디·비밀번호·토큰·이메일·개인 자격정보
- 공격 재현에 불필요한 비공개 보안 세부
- 확인되지 않은 수상·채택·production 주장

대화는 결정 전환점을 설명하는 짧은 정제 문장만 사용하며, 저장소에서 독립 검증할 수 없는 경우 Evidence Register에 그 한계를 표시한다.

## SSOT 규칙

상세한 정본 지도와 known drift는 [Evidence Register의 SSOT audit](./06_EVIDENCE_REGISTER.md#ssot-and-drift-audit)만이 정본이다. 이 README는 그 내용을 복제하지 않는다. 회고 주장은 CLM·EV-ID, 결정은 D-ID로 추적하고, 다음 프로젝트의 Gate 전이·상태·행동 규칙은 [AI-native Product OS](./04_AI_NATIVE_PRODUCT_OS.md)에만 정의한다.

`Frozen`은 2026-07-18 snapshot을 현재 상태처럼 갱신하지 않는다는 뜻이다. 새 증거·오류가 생기면 기존 판단을 조용히 덮어쓰지 않고 날짜가 있는 correction·새 CLM과 snapshot을 추가한다. `Living` 문서는 프로젝트 owner가 재사용 중 발견한 Gate 실패나 새로운 근거가 있을 때 갱신하되 Gate ID를 유지하고 변경 이유를 남긴다.

## 가장 중요한 다음 규칙

> 사용자 가치 Gate를 통과하기 전에는 최소 안전 기준을 넘는 1→100 hardening을 시작하지 않는다. 기술 eval PASS, 배포 PASS, 제출 PASS, 사용자 제품 PASS를 서로 대체하지 않는다.

# Evidence Register

이 문서는 회고의 주장과 근거에 대한 유일한 정본이다. 다른 문서는 CLM-ID와 EV-ID를 참조한다.

## 판정 언어

| 필드 | 허용값 |
|---|---|
| Kind | FACT, INFERENCE, RECOMMENDATION |
| Verdict | PASS, PARTIAL, FAIL, UNKNOWN |
| Confidence | HIGH, MEDIUM, LOW |
| Source class | REPO, GIT, CONVERSATION, OFFICIAL-GUIDANCE |

FACT는 원천에서 직접 확인한 사실이다. INFERENCE는 여러 사실을 결합한 해석이다. RECOMMENDATION은 다음 사이클의 규칙이다. 저장소에서 찾지 못했다는 사실은 저장소 밖에서 존재하지 않았다는 증명이 아니므로 한계를 함께 적는다.

## Snapshot

| 이름 | 값 |
|---|---|
| Initial design | e710bbaa682186d0ca2afd30b450dfdcc35435e2 |
| Submission-evidence repository snapshot | 608a79c327c6b17d04309f3b1585a433d95eb3e7 |
| Verified deployed runtime | c1ee50907c2bebbab2f2f85e7d08a4ae0ccf22db |
| External guidance last checked | 2026-07-18 |

## Claim register

### CLM-001

| Field | Value |
|---|---|
| Claim | 저장소의 초기 Inception 문서에는 외부 user evidence citation 없이 wiki-memory agent라는 해법이 이미 고정돼 있다. |
| Kind / Verdict / Confidence | FACT / FAIL / HIGH |
| Evidence | EV-001, EV-002 |
| Limit | 저장소 생성 전의 구두 탐색은 확인할 수 없다. |

### CLM-002

| Field | Value |
|---|---|
| Claim | 초기 U1~U10에는 사용자 조사·문제 검증·prototype test 단위가 없고 모두 구현·배포·벤치마크·제출 작업이다. |
| Kind / Verdict / Confidence | FACT / FAIL / HIGH |
| Evidence | EV-003 |
| Limit | 저장소 밖에서 수행한 활동은 포함하지 않는다. |

### CLM-003

| Field | Value |
|---|---|
| Claim | 초기 성공 기준은 사용자 outcome이 아니라 Qwen demo, 망각 장면, 토큰 절감, MCP와 수상 관점으로 정의됐다. |
| Kind / Verdict / Confidence | FACT / FAIL / HIGH |
| Evidence | EV-002 |
| Limit | 해커톤 제출 기준 자체는 정당한 제약이지만 제품 가치 증거를 대체하지 않는다. |

### CLM-004

| Field | Value |
|---|---|
| Claim | Track 1은 자율 경험 축적, 사용자 선호 기억, cross-session 의사결정 정확도 향상을 요구했다. 구현은 preference·episode claim과 명시적 갱신을 지원하지만 초기 범위는 자기개선 루프를 제외했다. |
| Kind / Verdict / Confidence | FACT / PARTIAL / HIGH |
| Evidence | EV-004, EV-005, EV-025 |
| Limit | 저장·검색·망각·제한 컨텍스트 항목은 별도로 강하게 구현됐다. |

### CLM-005

| Field | Value |
|---|---|
| Claim | 7월 3일 첫 commit부터 보고서까지 약 3시간 48분에 solution, API, MCP, UI, 배포, 제출 패키지가 만들어졌고 이후 32개 commit이 주로 hardening과 proof에 투입됐다. |
| Kind / Verdict / Confidence | FACT / PASS / HIGH |
| Evidence | EV-006 |
| Limit | commit timestamp는 작업시간 전체가 아니라 저장소에 기록된 하한선이다. |

### CLM-006

| Field | Value |
|---|---|
| Claim | 608a79c에서 제품 Python은 15개·8,806줄이고 테스트·eval·proof·deploy Python은 39개·14,277줄로 약 1.62배다. |
| Kind / Verdict / Confidence | FACT / PASS / HIGH |
| Evidence | EV-007 |
| Limit | 줄 수는 노력·품질·가치의 직접 척도가 아니라 투자 비대칭을 보여주는 보조지표다. |

### CLM-007

| Field | Value |
|---|---|
| Claim | 제출 snapshot에서 실제 persona, JTBD, 사용자 인터뷰, design partner, pilot, 사용자 task outcome 또는 반복 사용 증거를 찾지 못했다. |
| Kind / Verdict / Confidence | INFERENCE / FAIL / HIGH |
| Evidence | EV-001, EV-003, EV-008 |
| Limit | absence-of-evidence 판정이다. 저장소 밖에서 수행했다면 별도 증거가 필요하다. |

### CLM-008

| Field | Value |
|---|---|
| Claim | Librarian의 가장 정확한 성숙도 표현은 engineering-assured hackathon MVP / product-value-unvalidated다. |
| Kind / Verdict / Confidence | INFERENCE / PARTIAL / HIGH |
| Evidence | CLM-004, CLM-006, CLM-007, EV-009, EV-010, EV-011, EV-012 |
| Limit | 해커톤 결과와 실제 adoption이 추가되면 재평가해야 한다. |

### CLM-009

| Field | Value |
|---|---|
| Claim | 엔지니어링·release proof는 강하다. exact-SHA Alibaba 배포는 RELEASE_VERIFIED이고 bounded live-Qwen gate가 통과했다. |
| Kind / Verdict / Confidence | FACT / PASS / HIGH |
| Evidence | EV-009, EV-010 |
| Limit | live gate는 두 case뿐이며 receipt 자체가 promotion HOLD를 명시한다. |

### CLM-010

| Field | Value |
|---|---|
| Claim | 심사용 guided UI는 교정→현재 답변→변경 설명을 전달하지만 한 quota 예제와 judge namespace에 맞춘 demo이지 실제 업무 adoption proof가 아니다. |
| Kind / Verdict / Confidence | INFERENCE / PARTIAL / HIGH |
| Evidence | EV-011, EV-012 |
| Limit | UX가 나쁘다는 뜻이 아니라 검증 범위가 좁다는 뜻이다. |

### CLM-011

| Field | Value |
|---|---|
| Claim | 공개 다중 사용자 제품에 필요한 tenant isolation, 사용자별 auth, 개인정보 삭제·retention, backup/DR은 구현 완료로 증명되지 않았다. |
| Kind / Verdict / Confidence | FACT / PARTIAL / HIGH |
| Evidence | EV-013, EV-014 |
| Limit | 해커톤 단일 심사 demo에는 동일 수준의 production 설계가 필수는 아니다. |

### CLM-012

| Field | Value |
|---|---|
| Claim | 이 cycle의 주된 실패는 아무것도 만들지 못한 것이 아니라 최적화 대상과 작업 순서가 어긋난 것이다. |
| Kind / Verdict / Confidence | INFERENCE / PARTIAL / HIGH |
| Evidence | CLM-001, CLM-002, CLM-005, CLM-008, EV-015 |
| Limit | 원인 해석이며 인과를 실험으로 증명한 것은 아니다. |

### CLM-013

| Field | Value |
|---|---|
| Claim | 전통적인 사용자 문제 발견, prototype, 성공 지표, 운영 원칙은 AI-native 개발에서도 생략할 수 없다. |
| Kind / Verdict / Confidence | RECOMMENDATION / PASS / HIGH |
| Evidence | EV-016, EV-017, EV-018 |
| Limit | GOV.UK는 공공 서비스 지침이고 Google PAIR는 practitioner guide다. 보편 법칙이 아니라 강한 설계 기준으로 사용한다. |

### CLM-014

| Field | Value |
|---|---|
| Claim | Agent는 deterministic solution이나 단순 workflow가 부족하다는 증거가 있을 때만 복잡성을 늘려야 한다. |
| Kind / Verdict / Confidence | RECOMMENDATION / PASS / HIGH |
| Evidence | EV-019, EV-020 |
| Limit | OpenAI와 Anthropic의 실전 지침이며 산업 표준은 아니다. |

### CLM-015

| Field | Value |
|---|---|
| Claim | AI eval은 business·user workflow에 맞는 성공 조건과 실제 조건을 사용해야 하며 offline technical eval만으로 제품 성과를 대신할 수 없다. |
| Kind / Verdict / Confidence | RECOMMENDATION / PASS / HIGH |
| Evidence | EV-021, EV-022 |
| Limit | 구체적인 sample size와 grader 구성은 위험도·도메인마다 달라진다. |

### CLM-016

| Field | Value |
|---|---|
| Claim | AI-native UX는 capability·limit 고지, 사용자 control, feedback, 오류 회복과 인간 handoff를 제품 기능으로 설계해야 한다. |
| Kind / Verdict / Confidence | RECOMMENDATION / PASS / HIGH |
| Evidence | EV-018, EV-019, EV-023 |
| Limit | 모든 제품에 같은 UI 패턴을 강제하지 않는다. |

### CLM-017

| Field | Value |
|---|---|
| Claim | AI-DLC는 AI가 계획과 실행을 가속하되 중요한 결정은 인간이 승인하고 context를 저장소에 유지하는 방식으로 읽어야 한다. |
| Kind / Verdict / Confidence | RECOMMENDATION / PASS / HIGH |
| Evidence | EV-024 |
| Limit | AWS가 제안한 vendor methodology이며 외부 사용자 증거를 AI가 대신 생성해도 된다는 뜻이 아니다. |

### CLM-018

| Field | Value |
|---|---|
| Claim | 조사·보안·검증 깊이는 데이터 민감도, 외부 상태 변경, 비가역성, 비용, 사용자 위해 가능성에 비례해야 한다. |
| Kind / Verdict / Confidence | RECOMMENDATION / PASS / HIGH |
| Evidence | EV-023, EV-030 |
| Limit | 위험 등급은 법률·규제 자문을 대체하지 않는다. |

### CLM-019

| Field | Value |
|---|---|
| Claim | 수상 여부, 반복 사용자, retention, willingness-to-pay와 사업 성과는 이 snapshot에서 Unknown이다. |
| Kind / Verdict / Confidence | FACT / UNKNOWN / HIGH |
| Evidence | EV-008 |
| Limit | 향후 외부 증거가 생기면 별도 시점의 claim으로 추가한다. |

## Evidence entries

### Repository and Git evidence

| ID | Source class | Public source | Supports | Note |
|---|---|---|---|---|
| EV-001 | REPO | [requirements@e710bba L4-L35](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/e710bbaa682186d0ca2afd30b450dfdcc35435e2/aidlc-docs/inception/requirements.md#L4-L35) | CLM-001, CLM-007 | 문제 문장과 solution 요구가 같은 최초 문서에 이미 결합돼 있다. |
| EV-002 | REPO | [requirements@e710bba L45-L56](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/e710bbaa682186d0ca2afd30b450dfdcc35435e2/aidlc-docs/inception/requirements.md#L45-L56) | CLM-001, CLM-003, CLM-004 | 자기개선 범위 제외와 수상 관점 성공 기준. |
| EV-003 | REPO | [units@e710bba L6-L60](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/e710bbaa682186d0ca2afd30b450dfdcc35435e2/aidlc-docs/inception/units-of-work.md#L6-L60) | CLM-002, CLM-007 | U1~U10과 cut line 전체. |
| EV-004 | REPO | [Track contract@608a79c L245-L253](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/submission/hackathon-contract.json#L245-L253) | CLM-004 | Track 1 behavior contract의 저장소 mirror. 공식 live page가 최상위 정본이다. |
| EV-005 | REPO | [initial track strategy@e710bba L28-L43](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/e710bbaa682186d0ca2afd30b450dfdcc35435e2/wiki/strategy/track-recommendation.md#L28-L43) | CLM-004 | increasing accuracy를 Autoresearch로 연결했으나 구현 우선순위는 다른 항목으로 이동했다. |
| EV-006 | GIT | [history through 608a79c](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/commits/608a79c327c6b17d04309f3b1585a433d95eb3e7/) | CLM-005 | frozen snapshot까지 2026-07-03 16:12 첫 commit, 20:00 report. 7/3 14 commits, 이후 32 commits. |
| EV-007 | GIT | [snapshot 608a79c](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/tree/608a79c327c6b17d04309f3b1585a433d95eb3e7) | CLM-006 | git ls-tree와 git show로 Python 줄 수를 재계산했다. 아래 재현 명령 참조. |
| EV-008 | REPO | [repository snapshot](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/tree/608a79c327c6b17d04309f3b1585a433d95eb3e7) | CLM-007, CLM-019 | persona, interview, JTBD, target customer, design partner, pilot user 등으로 git grep과 파일 목록·요구사항을 교차 확인했다. 외부 활동은 Unknown. |
| EV-009 | REPO | [release finalization@608a79c](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/proof/deployments/release-finalization.json) | CLM-008, CLM-009 | exact deployed SHA와 RELEASE_VERIFIED. |
| EV-010 | REPO | [live Qwen receipt@608a79c](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/proof/runs/release-live-qwen/receipt.json) | CLM-008, CLM-009 | two-case gate 통과, promotion_status HOLD. |
| EV-011 | REPO | [guided demo@608a79c L175-L300](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/src/librarian/demo_ui.py#L175-L300) | CLM-008, CLM-010 | 교정·질의·설명 judge flow와 unique namespace. |
| EV-012 | REPO | [submission narrative@608a79c L60-L85](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/submission/DEVPOST_TEMPLATE.md#L60-L85) | CLM-008, CLM-010, CLM-011 | 현재 가치 주장과 명시된 next steps. |
| EV-013 | REPO | [app state@608a79c L20-L72](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/src/librarian/main.py#L20-L72) | CLM-011 | 하나의 MemoryStore와 single-process demo limiter. |
| EV-014 | REPO | [production gaps@608a79c L81-L85](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/submission/DEVPOST_TEMPLATE.md#L81-L85) | CLM-011 | retention, encrypted tenant isolation, broader evaluation이 next로 남아 있다. |
| EV-025 | REPO | [claim types@608a79c L18-L21](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/src/librarian/claims.py#L18-L21), [preference scenario@608a79c L369-L383](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/608a79c327c6b17d04309f3b1585a433d95eb3e7/eval/scenarios.py#L369-L383) | CLM-004 | preference·episode type과 explicit preference update eval은 존재한다. autonomous accumulation·accuracy improvement 증거와는 구분한다. |
| EV-027 | REPO | [initial NFR@e710bba L37-L43](https://github.com/procloudkim/2026-Global-AI-Hackathon-Series-with-Qwen-Cloud/blob/e710bbaa682186d0ca2afd30b450dfdcc35435e2/aidlc-docs/inception/requirements.md#L37-L43) | P0-OP-01 | Qwen, Alibaba Cloud, free-tier, public repo 제약이 초기 NFR에 기록돼 있다. 운영 깊이의 적합성까지 증명하지는 않는다. |

### Conversation evidence

| ID | Source class | Public-safe excerpt | Supports | Caveat |
|---|---|---|---|---|
| EV-015 | CONVERSATION | 2026-07-18의 정제 신호: “무엇을 만들고 어떤 결과물이 사용자에게 도달하는가?”, “이 작업이 필요했나?”, “나는 무엇을 한 것인가, 계획 설계 실패 아닌가?” | CLM-012 | 현재 Codex 대화에서 확인했지만 저장소만으로 독립 재현할 수 없다. 원문 전체와 개인·인증정보는 보존하지 않는다. |
| EV-026 | CONVERSATION | 2026-07-18 Human이 Devpost 제출 완료를 보고했고 최종 submit 행동을 직접 소유했다. | D-009 | 현재 Codex 대화에서 확인했지만 저장소 evidence manifest의 외부 form receipt는 갱신되지 않았다. 제출 이후 수상·채택과는 별개다. |
| EV-028 | CONVERSATION | 2026-07-18 Human은 browser refresh 뒤 Testing Instructions 등 저장되지 않은 입력이 사라졌다고 보고했다. | D-008 | 손실 사실과 Human의 귀속 보고를 정제한 증거다. 원문 전체, 계정, 입력값은 보존하지 않는다. |
| EV-029 | CONVERSATION | 2026-07-17~18의 review 요청에서 최종 verdict보다 guided explanation·demo UI 구현이 먼저 진행됐다. | D-007 | 대화 순서와 관련 repository change를 함께 요약한 정제 관찰이다. 원문 전체는 보존하지 않는다. |
| EV-030 | CONVERSATION | 2026-07-18 Human은 Deep Scan 재개를 승인한 뒤 결과 전 장시간 지연으로 취소하고 작업 필요성과 지연 원인을 물었다. | D-006, CLM-018 | wall-clock 전체를 유효 분석 시간으로 해석하지 않는다. private task·thread 식별자와 내부 로그는 보존하지 않는다. |

### Official guidance evidence

| ID | Source class | Source | Supports | Use and caveat |
|---|---|---|---|---|
| EV-016 | OFFICIAL-GUIDANCE | [GOV.UK: Understand users and their needs](https://www.gov.uk/service-manual/service-standard/point-1-understand-user-needs) | CLM-013 | solution보다 사용자와 문제에 집중하고 가정을 조기에 시험하라고 명시한다. 공공 서비스 지침이다. |
| EV-017 | OFFICIAL-GUIDANCE | [GOV.UK: Define success and publish performance data](https://www.gov.uk/service-manual/service-standard/point-10-define-success-publish-performance-data) | CLM-013 | 서비스가 문제를 푸는지 알려주는 metric과 user research 결합을 요구한다. |
| EV-018 | OFFICIAL-GUIDANCE | [Google PAIR People + AI Guidebook](https://pair.withgoogle.com/guidebook-v2/), [User needs chapter](https://pair.withgoogle.com/guidebook-v2/chapter/user-needs/) | CLM-013, CLM-016 | human-centered AI, trust calibration, control, failure recovery의 설계 패턴을 제공한다. 2019년 공개·2021년 갱신된 practitioner guide다. |
| EV-019 | OFFICIAL-GUIDANCE | [OpenAI: A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) | CLM-014, CLM-016 | agent use case를 먼저 검증하고 deterministic solution 가능성을 배제하지 않으며 real-user validation과 HITL을 권한다. 페이지의 model 예시는 변할 수 있어 날짜에 덜 민감한 원칙만 사용한다. |
| EV-020 | OFFICIAL-GUIDANCE | [Anthropic: Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents) | CLM-014 | 가장 단순한 해법에서 시작하고 outcome 개선이 증명될 때만 복잡성을 추가하라고 권한다. Vendor guidance다. |
| EV-021 | OFFICIAL-GUIDANCE | [OpenAI: Specify, Measure, Improve](https://openai.com/index/evals-drive-next-chapter-of-ai/) | CLM-015 | business workflow별 contextual eval과 real-world condition을 강조한다. definitive standard가 아님을 원문도 명시한다. |
| EV-022 | OFFICIAL-GUIDANCE | [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | CLM-015 | agent eval을 lifecycle 전반의 feedback·production signal과 연결한다. Vendor guidance다. |
| EV-023 | OFFICIAL-GUIDANCE | [NIST AI RMF Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence), [DOI](https://doi.org/10.6028/NIST.AI.600-1) | CLM-016, CLM-018 | lifecycle 전반의 govern, map, measure, manage와 pre-deployment testing, incident disclosure를 다룬다. 자발적 공공 프레임워크이지 certification이 아니다. |
| EV-024 | OFFICIAL-GUIDANCE | [AWS: AI-Driven Development Life Cycle](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/) | CLM-017 | AI execution, human oversight, Inception·Construction·Operations context persistence를 설명한다. AWS vendor methodology이지 보편 표준은 아니다. |

## SSOT and drift audit

| Truth | Canonical home | Non-canonical occurrences | Action |
|---|---|---|---|
| 공식 대회 계약의 저장소 mirror | [submission/hackathon-contract.json](../submission/hackathon-contract.json) | [HACKATHON_CONTRACT.md](../submission/HACKATHON_CONTRACT.md), README, requirements, submission prose | 정본을 참조하고 재정의하지 않는다. Live Devpost가 최상위 외부 권위다. |
| candidate·evidence 상태 | [submission/evidence-manifest.json](../submission/evidence-manifest.json) | README, [DEVPOST_TEMPLATE.md](../submission/DEVPOST_TEMPLATE.md), screenshots | manifest를 registry로 사용하고 외부 form truth와 구분한다. |
| eval threshold·kill·promotion | [eval/policy.json](../eval/policy.json) | eval README, requirements, receipts | policy를 source하고 receipt는 관찰 결과만 기록한다. |
| runtime 사실 | [proof/runs](../proof/runs) 및 [proof/deployments](../proof/deployments)의 개별 receipt | manifest와 README summary | 개별 receipt를 canonical observation으로, manifest를 index로 사용한다. |
| 회고 주장 | 이 파일의 CLM·EV | README, Case Study, Gap Analysis | 다른 문서는 CLM-ID를 참조한다. |
| 의사결정 | [02_DECISION_LEDGER.md](./02_DECISION_LEDGER.md) | Case Study narrative | D-ID를 참조한다. |
| Gate 전이·상태·행동 schema | [04_AI_NATIVE_PRODUCT_OS.md](./04_AI_NATIVE_PRODUCT_OS.md) | Gap Analysis, Prompt Library | formal Gate 규칙은 OS에서만 정의하고 다른 문서는 적용한다. |
| Prompt invocation guardrail | [05_PROMPT_LIBRARY.md](./05_PROMPT_LIBRARY.md) | 복사된 개별 prompt | 한 번의 실행 범위·안전만 제한하며 Gate 상태나 전이를 재정의하지 않는다. |

### Known drift

- c1ee509은 배포된 runtime이지만 그 commit 자체의 문서·receipt는 이전 candidate를 가리킨다. c1 배포 증명은 후속 evidence commit 608a79c의 receipt를 사용한다.
- 608a79c의 architecture 문서에는 ECS를 conditional trial candidate로 부르는 과거 표현이 남아 있지만 같은 snapshot의 deployment receipt는 실제 c1 배포를 증명한다.
- 608a79c evidence manifest의 track·video 관련 pending은 외부 form receipt가 저장소에 없다는 뜻이다. 현재 대화의 제출 완료 주장으로 manifest를 소급 변경하지 않으며 snapshot 상태는 Unknown으로 유지한다.
- 초기 requirements가 요약한 Track 요구는 storage·forgetting·limited context에 집중해 autonomous experience·preference·increasing accuracy를 충분히 드러내지 못했다. 회고는 608a79c의 canonical contract mirror를 사용한다.

## Reproduction notes

Commit 분포:

~~~powershell
git log 608a79c327c6b17d04309f3b1585a433d95eb3e7 --date=format:'%Y-%m-%d' --format='%ad' |
  Group-Object |
  Sort-Object Name |
  ForEach-Object { '{0}|{1}' -f $_.Name, $_.Count }
~~~

초기 의도와 현재 계약:

~~~powershell
git show e710bba:aidlc-docs/inception/requirements.md
git show e710bba:aidlc-docs/inception/units-of-work.md
git show 608a79c:submission/hackathon-contract.json
~~~

줄 수 계산 원칙:

1. git ls-tree -r --name-only 608a79c로 snapshot 파일을 열거한다.
2. 제품은 src/librarian 아래 Python으로 분류한다.
3. assurance는 tests, eval, proof, deploy 아래 Python으로 분류한다.
4. 각 blob을 git show 608a79c:path로 읽고 line count를 합산한다.

이 계산은 repository size나 개발자 생산성을 평가하지 않는다. 어떤 종류의 증명에 더 큰 표면이 생겼는지를 보는 보조 증거다.

## Known unknowns

- 저장소 생성 전 아이디어 탐색과 사용자 대화
- 저장소 밖에서 수행한 인터뷰·pilot
- 최종 수상 결과
- 공개 이후 clone·usage·retention
- willingness-to-use 또는 willingness-to-pay
- 실제 사용자 workflow에서 stale-answer 감소, 시간 절감, 오류 회피 효과

Unknown은 실패로 바꾸지 않는다. 새 증거가 생기면 기존 frozen claim을 덮어쓰지 말고 날짜가 다른 claim을 추가한다.

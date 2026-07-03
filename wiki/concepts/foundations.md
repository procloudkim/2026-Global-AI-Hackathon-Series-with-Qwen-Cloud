# 기반 개념 5+1 — 프로덕트 설계의 이론적 토대

> 제출 프로덕트가 따르는 개념들. 각 개념의 핵심, 차용할 요소, 상호 관계를 정리.

## 1. LLM Wiki (Karpathy)

> 출처: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

**핵심 아이디어**: RAG는 매 질문마다 지식을 처음부터 재발견하지만, LLM Wiki는 **LLM이 영속적·복리적(compounding) 위키를 점진적으로 구축·유지**한다. 지식은 한 번 컴파일되고 계속 최신으로 유지된다.

**3계층 구조**:
1. **Raw sources** — 불변의 원본 문서 (LLM은 읽기만 함)
2. **The wiki** — LLM이 전적으로 소유·작성하는 마크다운 페이지들 (엔티티/개념/요약/종합)
3. **The schema** — LLM에게 위키 구조·컨벤션·워크플로우를 지시하는 설정 문서 (CLAUDE.md/AGENTS.md) — *"이것이 LLM을 규율 있는 위키 관리자로 만든다"*

**3대 워크플로우**:
- **Ingest**: 소스 투입 → 요약 작성 → 인덱스 갱신 → 관련 페이지 10~15개 크로스업데이트 → 로그 기록
- **Query**: 인덱스 우선 탐색 → 페이지 종합 → 인용 포함 답변 → **좋은 답변은 위키에 재수록(복리화)**
- **Lint**: 모순·낡은 주장·고아 페이지·누락 크로스레퍼런스 정기 점검

**특수 파일**: `index.md`(내용 카탈로그, 매 ingest마다 갱신), `log.md`(append-only 연대기, grep 가능한 prefix)

**차용 포인트**: 에이전트 메모리를 "청크 검색"이 아닌 **구조화된 위키 아티팩트**로 설계. 망각 = lint의 stale claim 제거.

## 2. Autoresearch (Karpathy)

> 출처: https://github.com/karpathy/autoresearch

**핵심 아이디어**: AI 에이전트에게 작지만 실제인 실험 루프를 주고 **밤새 자율 실험**시킨다. 코드 수정 → 5분 학습 → 지표(val_bpb) 확인 → 채택/폐기 → 반복.

**설계 원칙**:
- **단일 수정 파일** (`train.py`) — 스코프 관리 + 리뷰 가능한 diff
- **고정 시간 예산** (5분) — 실험 간 공정 비교, 시간당 ~12 실험
- **단일 지표** — 개선 여부를 기계가 판정
- **`program.md`** — 인간이 편집하는 에이전트 지침 = "연구 조직 코드". 초경량 skill

**차용 포인트**: 에이전트의 자기개선 루프 설계 — *수정 대상 격리 + 고정 예산 + 단일 지표 + 인간은 program(하네스)만 편집*. 메모리 품질을 지표화하면 메모리 정책의 자율 실험도 가능.

## 3. Graphify (Safi Shamsi)

> 출처: https://github.com/safishamsi/graphify

**핵심 아이디어**: `/graphify` 한 번으로 프로젝트 전체(코드·문서·PDF·이미지·영상)를 **지식 그래프**로 매핑. grep 대신 그래프를 질의.

**산출물**: `graph.html`(인터랙티브 시각화) · `GRAPH_REPORT.md`(핵심 개념·의외의 연결·추천 질문) · `graph.json`(재독해 없이 질의 가능한 전체 그래프)

**특징**: 멀티 에이전트 플랫폼 지원(Claude Code, Copilot, Cursor 등), skill 형태로 배포, Mermaid call-flow export.

**차용 포인트**: 위키(마크다운)와 상보적인 **그래프 뷰 레이어**. 멀티모달 소스의 통합 인덱싱, "의외의 연결" 발견은 메모리 에이전트의 능동 제안 기능과 직결.

## 4. CodeGraph (Colby McHenry)

> 출처: https://github.com/colbymchenry/codegraph

**핵심 아이디어**: 코드베이스의 모든 심볼·호출 엣지·의존성을 **사전 구축된 그래프**로 만들어, 에이전트가 grep/read 크롤링 대신 **한 번의 질의로 정확한 컨텍스트**를 받는다.

**검증된 효과**: 툴 콜 58% 감소 · 22% 빠른 응답 · 파일 읽기 ~0. 토큰 절약은 규모 의존적(대형 코드베이스에서 유의미).

**운영 특징**: MCP 서버로 에이전트에 연결, 파일 변경 시 **자동 동기화(인덱스가 stale하지 않음)**, blast radius(변경 영향 범위) 질의.

**차용 포인트**: **토큰 예산 관리의 핵심 기법** — 제한된 컨텍스트 윈도우에서 회상 정확도를 높이는 "surgical context" 패턴. 자동 동기화 = 메모리 신선도 유지 메커니즘.

## 5. AI-DLC (AWS Labs)

> 출처: https://github.com/awslabs/aidlc-workflows

**핵심 아이디어**: AI-Driven Development Life Cycle. AI 에이전트를 **검증 가능하고 자기교정하는 엔지니어링 워크플로우**로 전환한다 (2.0 사양). 룰 파일(steering/rules)을 프로젝트에 심어 에이전트의 개발 프로세스를 규율한다.

**구조**: `aws-aidlc-rules/`(코어 워크플로우 룰) + `aws-aidlc-rule-details/`(조건부 참조 상세 룰). Kiro/Amazon Q/Cursor 등 룰 시스템에 설치.

**차용 포인트**: **개발 방법론 자체** — 이 프로젝트를 AI-DLC 워크플로우로 개발하고, 그 과정을 블로그로 문서화하면 Blog Post Prize와 Presentation 점수에 직결.

## +1. 하네스 엔지니어링 (Harness Engineering)

**정의(이 프로젝트 맥락)**: 모델 자체가 아니라 **모델을 둘러싼 실행 환경(하네스)** — 지침 파일, 도구, 인덱스, 검증 루프, 예산 — 을 설계하는 엔지니어링.

**5개 개념과의 관계**: 위 개념들은 모두 하네스 엔지니어링의 사례다.

| 개념 | 하네스 요소 |
|---|---|
| LLM Wiki | schema(CLAUDE.md/AGENTS.md) = 행동 규율 하네스 |
| Autoresearch | program.md + 고정 예산 + 단일 지표 = 실험 하네스 |
| Graphify | 지식 그래프 = 멀티모달 인지 하네스 |
| CodeGraph | 심볼 그래프 MCP = 토큰 효율 하네스 |
| AI-DLC | 룰 기반 워크플로우 = 개발 프로세스 하네스 |

## 개념 통합도 (프로덕트 아키텍처 관점)

```
                 ┌─ 개발 방법론: AI-DLC (프로세스 하네스)
                 │
  Raw Sources ──▶ Ingest ──▶ Wiki (LLM Wiki: 영속·복리 메모리)
                 │              │
                 │              ├─ Graph 레이어 (Graphify: 연결·발견)
                 │              ├─ 토큰 절약 질의 (CodeGraph 패턴: surgical context)
                 │              └─ index.md / log.md (탐색·연대기)
                 │
                 └─ 자기개선 루프 (Autoresearch: 지표 기반 메모리 정책 실험)
```

## 관련 페이지
- [../strategy/track-recommendation.md](../strategy/track-recommendation.md) — 이 개념 조합이 어느 트랙에 맞는가

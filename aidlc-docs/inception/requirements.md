# Requirements — Librarian (working title)

> AI-DLC Inception 산출물 1/3. Track 1: MemoryAgent 제출용.
> 한 줄 정의: "기억을 검색하지 않고 유지보수하는 에이전트 — 위키처럼 쓰고, 사서처럼 잊는다."

## 1. 문제 정의 (Problem Statement)

기존 RAG/메모리 시스템은 매 질문마다 지식을 재발견한다. 축적이 없고, 모순이 방치되며,
오래된 정보가 최신 정보를 오염시킨다. Track 1의 공식 요구인 (1) 효율적 저장·검색,
(2) 적시 망각, (3) 제한 컨텍스트 내 회상을 모두 만족하는 시스템이 필요하다.

## 2. 기능 요구사항 (MECE)

### FR-1. Ingest (기억 형성)
- FR-1.1 사용자가 소스(마크다운/텍스트/URL 텍스트)를 투입하면 에이전트가 읽고 요약 페이지 생성
- FR-1.2 기존 위키 페이지(엔티티/개념)에 교차 갱신 수행
- FR-1.3 index.md 카탈로그 갱신, log.md에 append-only 기록
- FR-1.4 신규 정보가 기존 주장과 모순되면 충돌 플래그 생성

### FR-2. Query (기억 회상)
- FR-2.1 index 우선 탐색 → 관련 페이지만 선별 읽기 (전체 재읽기 금지)
- FR-2.2 인용(페이지 링크) 포함 답변 생성
- FR-2.3 가치 있는 답변은 위키에 재수록 (복리화)
- FR-2.4 요청당 토큰 사용량 계측·기록

### FR-3. Forget (적시 망각) — 차별화 코어
- FR-3.1 Lint 패스: stale claim 탐지 (최신 소스가 대체한 주장)
- FR-3.2 모순 페이지 쌍 탐지 및 해소(상위 모델 판정)
- FR-3.3 orphan 페이지 탐지 → 병합 또는 아카이브
- FR-3.4 망각은 삭제가 아닌 아카이브 (archive/ 이동 + 근거 로그)

### FR-4. Interface
- FR-4.1 REST API (FastAPI): /ingest, /query, /lint, /stats
- FR-4.2 MCP 서버: memory_ingest, memory_query, memory_lint 툴 노출
- FR-4.3 최소 웹 UI 또는 CLI 데모 (영상 촬영용)

## 3. 비기능 요구사항

- NFR-1 Qwen 모델(DashScope OpenAI 호환 API) 사용 필수
- NFR-2 백엔드는 Alibaba Cloud에서 구동 (제출 증빙: 리포 내 코드 파일)
- NFR-3 Free Tier 예산: 모델 이원화 (경량=qwen-flash/turbo 계열, 판정=qwen-plus/max 계열)
- NFR-4 토큰 절약 증명: naive full-read 대비 절감률 수치 산출
- NFR-5 공개 리포 + MIT 라이선스

## 4. 범위 제외 (Out of Scope, D-7 현실)

- Autoresearch식 자기개선 루프 전체 구현 (README에 확장 설계로만 기술)
- 멀티모달 ingest (이미지/영상) — 텍스트 우선
- 임베딩 기반 벡터 검색 — index.md + 그래프 탐색으로 대체 (이것이 오히려 차별화 서사)

## 5. 성공 기준 (수상 관점)

- S1 Stage 1 통과: 데모가 실제로 동작 + Qwen API 사용 명확
- S2 망각 데모: 모순된 소스 2개 투입 → lint가 탐지·해소하는 장면을 영상에 포함
- S3 토큰 수치: 동일 질문에 대해 full-read 대비 N% 절감 표 제시
- S4 MCP 통합: 심사기준 명시 가점 요소 충족

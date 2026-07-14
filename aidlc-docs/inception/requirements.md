# Requirements — Librarian

> AI-DLC Inception 산출물 1/3. Track 1: MemoryAgent 제출용.
> 한 줄 정의: "기억을 검색하지 않고 유지보수하는 에이전트 — 위키처럼 쓰고, 사서처럼 잊는다."
> 공식 요구사항·일정·제출 필드는 `submission/hackathon-contract.json`이 정본이며,
> 이 문서는 Librarian의 derived product/evaluation 요구사항이다.

## 1. 문제 정의 (Problem Statement)

기존 RAG/메모리 시스템은 매 질문마다 지식을 재발견한다. 축적이 없고, 모순이 방치되며,
오래된 정보가 최신 정보를 오염시킨다. Track 1의 공식 요구인 (1) 효율적 저장·검색,
(2) 적시 망각, (3) 제한 컨텍스트 내 회상을 모두 만족하는 시스템이 필요하다.

## 2. 기능 요구사항 (MECE)

### FR-1. Ingest (기억 형성)
- FR-1.1 원문은 `source-id--sha256.md` 경로에 불변 저장한다.
- FR-1.2 LIGHT Qwen은 원문 근거 span을 가진 atomic claim을 strict JSON으로 추출한다.
- FR-1.3 동일 `scope::subject::predicate`의 중복은 provenance를 병합한다.
- FR-1.4 명시적 대체·효력 시점만 deterministic transition으로 적용하고, 애매한 충돌만 HEAVY에 보낸다.
- FR-1.5 HEAVY 출력은 evidence allow-list와 lifecycle validator를 통과해야만 상태를 바꾼다.
- FR-1.6 위키가 canonical이고 `index.md`, `graph.json`은 재생성 가능한 derived projection이다.
- FR-1.7 동일 memory root의 ingest/query/lint read-modify-write는 process-local `RLock`과
  OS file lock으로 직렬화하고, affected-key ingest journal, transition outbox,
  projection dirty marker로 interrupted write를 복구한다.

### FR-2. Query (기억 회상)
- FR-2.1 `graph.json` metadata를 먼저 점수화하고 실제 top-K 페이지만 읽는다.
- FR-2.2 active claim만 현재 사실로 사용하고 disputed는 충돌 표식과 함께 제한적으로 제공한다.
- FR-2.3 superseded/archived/future-effective claim은 answer context에서 제외한다.
- FR-2.4 응답은 `answer`, `facts`, `citations`, `confidence`, `abstained`를 반환한다.
- FR-2.5 정제 후 유효 citation/evidence가 없으면 HEAVY로 승격하고, 재실패하면 abstain한다.
- FR-2.6 corpus/candidate/loaded page, claim status, context/model token trace를 기록한다.

### FR-3. Forget (적시 망각) — 차별화 코어
- FR-3.1 망각 단위는 페이지가 아니라 claim이다.
- FR-3.2 `active → disputed/superseded`, `disputed → active/superseded`, `superseded → active/archived`만 허용한다.
- FR-3.3 모든 전이는 `memory/decisions.jsonl`에 근거와 함께 append-only 기록한다.
- FR-3.4 lint는 projection drift, invalid claim, audit gap, disputed group만 idempotent audit/repair한다.
- FR-3.5 모델 confidence, ingest 순서, 첫 숫자 불일치만으로 claim 또는 페이지를 archive하지 않는다.
- FR-3.6 rollback은 superseded claim을 active로 복원하며 원본과 다른 claim을 보존한다.

### FR-4. Interface
- FR-4.1 REST API (FastAPI): /ingest, /query, /lint, /stats
- FR-4.2 MCP 서버: memory_ingest, memory_query, memory_lint 툴 노출
- FR-4.3 최소 웹 UI 또는 CLI 데모 (영상 촬영용)

## 3. 비기능 요구사항

- NFR-1 Qwen 모델(DashScope OpenAI 호환 API) 사용 필수
- NFR-2 백엔드는 Alibaba Cloud에서 구동하고 code URL, Workbench screenshot,
  public test URL, exact-SHA/restart receipt를 서로 결속한다.
- NFR-3 `MAX_UNAPPROVED_SPEND_USD=0`. LIGHT는 qwen-flash, HEAVY 기본은
  qwen-plus-2025-07-28이며 모든 live call은 max calls/tokens/timeout/retry와
  `Free quota only` 재확인을 요구한다.
- NFR-4 토큰 절약 증명: naive full-read 대비 절감률 수치 산출
- NFR-5 MIT 라이선스와 공개 리포가 필요하되 visibility 변경은 별도 사용자 승인 사항
- NFR-6 fsync 후 원자적 파일 교체, multi-process writer 직렬화, process restart 후
  동일 claim state 복원
- NFR-7 gold와 runner를 분리하고 Qwen-as-a-judge를 금지한 deterministic evaluator
- NFR-8 production conformance는 transition ledger를 독립 replay하여 append-only,
  evidence binding, FSM, canonical-state 일치를 모두 검증한다.

## 4. 범위 제외 (Out of Scope)

- Autoresearch식 자율 자기개선 루프
- 멀티모달 ingest (이미지/영상) — 텍스트 우선
- 임베딩 기반 벡터 검색 — index.md + 그래프 탐색으로 대체 (이것이 오히려 차별화 서사)

## 5. 성공 기준

정량 threshold와 kill rule의 정본은 [`eval/policy.json`](../../eval/policy.json)이다.
Promotion은 동일 candidate에 대해 deterministic behavior, bounded live Qwen,
Alibaba restart persistence, 독립 private holdout을 모두 요구한다. Private holdout을
보기 전에 candidate tree와 dataset/policy hash를 고정하고, 이후 구현을 바꾸면 해당
holdout을 폐기한다. 구조·dev·live·deployed·private·submission PASS는 서로 대체할 수
없으며 synthetic/offline receipt는 product decision을 `HOLD`로 유지한다.

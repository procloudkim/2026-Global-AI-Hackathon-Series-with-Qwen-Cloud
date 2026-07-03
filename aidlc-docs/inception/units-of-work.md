# Units of Work — AI-DLC Inception 산출물 2/3

> AI-DLC 방식: 요구사항을 검증 가능한 작업 단위(Unit)로 분해. 각 Unit은 완료 기준(DoD)을 가짐.
> 마감: 2026-07-10 06:00 KST

## Unit 목록 (우선순위순)

### U1. 프로젝트 스캐폴드 + Qwen 연결 [P0] — D-7~6
- Python(uv) 프로젝트, FastAPI 뼈대, DashScope OpenAI 호환 클라이언트
- 모델 라우터: LIGHT(요약/분류) / HEAVY(판정/합성) 이원화
- DoD: /health 엔드포인트 + Qwen 호출 왕복 성공

### U2. 스토리지 레이어 [P0] — D-6
- memory/ 디렉터리: raw/, wiki/, wiki/index.md, wiki/log.md, archive/
- 페이지 파서: frontmatter(tags, updated, sources, links)
- DoD: 페이지 CRUD + 인덱스 자동 갱신 유닛테스트 통과

### U3. Ingest 파이프라인 [P0] — D-6~5
- 소스 투입 → LIGHT 요약 → 영향 페이지 식별 → HEAVY 교차 갱신 → index/log 갱신
- 모순 후보 플래깅
- DoD: 소스 3개 연속 투입 시 위키 일관성 유지

### U4. Query 파이프라인 [P0] — D-5
- index 우선 탐색 → 페이지 선별 로드 → 인용 포함 답변 → (옵션) 답변 재수록
- 토큰 계측 미들웨어 (요청당 in/out/합계 기록)
- DoD: 인용 링크가 실제 페이지로 연결, stats에 토큰 기록

### U5. Forget/Lint 엔진 [P0, 차별화] — D-4
- stale 탐지, 모순 쌍 판정(HEAVY), orphan 탐지, archive 이동 + 근거 로그
- DoD: 모순 시나리오(구가격→신가격) 데모 스크립트 통과

### U6. Alibaba Cloud 배포 [P0, 제출요건] — D-4
- ECS 또는 Serverless(Function Compute)에 FastAPI 배포
- 리포에 배포 코드/스크립트 파일 (증빙용 링크 대상)
- DoD: 공개 URL에서 /query 동작

### U7. MCP 서버 [P1, 가점] — D-3
- memory_ingest / memory_query / memory_lint 툴 노출 (stdio)
- DoD: Claude Code/Copilot 등 1개 클라이언트에서 실호출 데모

### U8. 토큰 절감 벤치마크 [P1, 증거] — D-3
- 동일 질문 5개: naive full-read vs Librarian 비교표 생성 스크립트
- DoD: README에 절감률 표 삽입

### U9. 데모 UI [P2] — D-3
- 최소 웹 페이지(위키 목록 + 질문창) 또는 잘 연출된 CLI
- DoD: 3분 영상 촬영 가능 상태

### U10. 제출 패키지 [P0] — D-2~1
- 아키텍처 다이어그램(mermaid→이미지), 영문 README, 3분 영상, 블로그 포스트(보너스), Devpost 드래프트
- DoD: D-1 드래프트 제출 완료

## 의존 관계

U1 → U2 → U3 → U4 → U5 → (U7, U8, U9 병렬) → U10
U6은 U4 완료 후 착수 가능 (U5와 병렬)

## 컷 라인

D-3 종료 시점에 U7/U8/U9 중 미완성 항목은 컷. U10은 절대 압축 불가.

---

## 개정 이력

### [2026-07-03] Deep-research-token.md 반영 (wiki/concepts/token-harness.md 참조)

- **U2 확장**: meter.py → **RunLedger** (runs.jsonl append-only: model, tier, prompt/completion tokens, latency_ms, task_type, success)
- **U3/U4 확장**: 프롬프트를 prompts.py로 분리 — 정적 prefix(정책/출력계약) 상수화 + PROMPT_VERSION 태깅, 동적 값은 suffix 주입. LIGHT 호출에 compact JSON 출력 계약 + max_tokens cap
- **U4 확장**: ModelRouter에 escalate-on-fail 규칙 (LIGHT 실패/저신뢰 시만 HEAVY)
- **U8 격상**: naive vs Librarian 단순 비교 → 3-실험 A/B 매트릭스 (L-E1 surgical context, L-E2 라우팅, L-E3 출력계약). KPI = cost_per_success (토큰 절감률과 분리 보고)

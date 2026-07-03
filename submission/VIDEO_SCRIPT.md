# Librarian — 3분 데모 영상 스크립트

> 목표: 3분 안에 (1) 문제, (2) 망각 엔진 차별화, (3) 토큰 효율 증거, (4) Qwen/MCP 사용을 보여준다.
> 준비: `docker compose up -d` 실행 상태, 브라우저에 `http://localhost:8080` (데모 UI), 터미널 1개.

---

## 0:00–0:25 — Hook & 문제 정의 (화면: 타이틀 슬라이드 → 데모 UI)

**내레이션:**
"AI agents don't have a memory problem — they have a *forgetting* problem. Most memory systems only append: they hoard every note until the context is full of stale, contradictory junk, and every extra page costs tokens. **Librarian** is a memory maintenance agent: it ingests, organizes, and — crucially — *forgets*, so agents answer better with fewer tokens."

## 0:25–0:55 — Ingest & 위키 구조 (화면: UI에서 ingest 실행 → 생성된 wiki/ 파일 트리)

**액션:** 데모 UI에서 텍스트 소스 하나 ingest → `memory/wiki/` 폴더와 `index.md` 열어 frontmatter/링크 표시.

**내레이션:**
"Raw sources go in immutable. A light Qwen model — qwen-flash — distills them into wiki pages with frontmatter, links, and a global index. Every write is a structured JSON contract, never freeform."

## 0:55–1:45 — ★ 망각 데모 (핵심 장면) (화면: 모순된 소스 2개 ingest → /lint 실행)

**액션:**
1. 소스 A ingest: "Our API rate limit is 100 requests/min."
2. 소스 B ingest: "As of v2, the API rate limit is 1000 requests/min."
3. `/lint` 실행 → conflict 탐지 결과 표시 → 구버전 페이지가 `archive/`로 이동하는 것을 파일 탐색기로 확인.

**내레이션:**
"Here's the differentiator. I ingest two sources that contradict each other. Librarian's lint engine detects the conflict, escalates just this one judgment to the heavy model — qwen-plus — and archives the stale page. Nothing is deleted; it's moved to archive, auditable and reversible. The wiki stays small, current, and cheap to query."

## 1:45–2:25 — 토큰 효율 증거 (화면: BENCHMARK.md 결과 표 → bench 재실행 터미널)

**내레이션:**
"Does it actually save money? We benchmarked 12 questions across three A/B experiments with a strict success criterion: answers must be valid JSON *with citations pointing to real pages*. Surgical retrieval cuts tokens-per-successful-answer by **47%** versus full-context reads, and **51%** versus freeform prompting. And we're honest: light-first routing costs 30% more raw tokens in one setup — but per *successful* answer it still wins by 17%, because cost-per-success is the KPI that matters."

## 2:25–2:50 — 아키텍처 & MCP (화면: architecture.png → MCP 툴 목록)

**내레이션:**
"Everything runs on Qwen via Alibaba Cloud's DashScope API, with a two-tier model router. Librarian also ships as an **MCP server** — memory_ingest, memory_query, memory_lint, memory_stats — so any MCP client like Claude Code or an IDE agent gets persistent, self-cleaning memory for free. It's one Docker command to run anywhere."

## 2:50–3:00 — 클로징 (화면: repo README + Devpost)

**내레이션:**
"Librarian: memory that forgets, so your agent remembers what matters — at half the cost. Code, benchmark, and Docker image are in the repo. Thank you."

---

## 촬영 체크리스트
- [ ] `docker compose up -d` + `/health` 200 확인
- [ ] 모순 소스 A/B 텍스트 파일 미리 준비
- [ ] BENCHMARK.md 표를 확대 표시 (숫자 3개: −47%, −51%, −17% 강조)
- [ ] 화면 녹화 1080p, 마이크 테스트
- [ ] 영어 자막 또는 영어 내레이션 (글로벌 심사)

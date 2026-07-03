# Token Harness — Deep Research 지식화

> 소스: Deep-research-token.md (2026-07-03, Tier0 공식문서 + Tier1 논문 기반)
> Librarian(Track 1)에 적용할 결론만 추출. 원문은 raw 소스로 보존.

## 핵심 명제 3개

1. **토큰 수 절감 ≠ 토큰 단가 절감** — 독립적인 두 레버. 먼저 토큰 수를 깎고, 남은 토큰을 싼 티어(캐시/배치)로 처리
2. **측정이 최우선** — 기법 도입 전에 RunLedger부터. 진짜 KPI는 `cost_per_success` (토큰 절감률이 아님)
3. **짧은 프롬프트 < 캐시되는 프롬프트** — 정적 prefix 앞배치(정책/출력계약/스키마), 동적 값(diff/질문)은 suffix

## Librarian에 즉시 적용 (D-7 스코프)

| 보고서 권고 | Librarian 반영 | Unit |
|---|---|---|
| RunLedger (runs.jsonl: model/tokens/latency/success) | meter.py를 계측 미들웨어가 아닌 **원장(ledger)**으로 설계 | U2/U4 |
| 정적 prefix 앞배치 + 프롬프트 버전 관리 | prompts.py: 시스템 프롬프트를 정적 상수로 분리, PROMPT_VERSION 태깅 | U3/U4 |
| 출력 계약 + hard cap | 모든 LIGHT 호출에 compact JSON 스키마 + max_tokens | U3/U5 |
| surgical context (diff/symbol/path only) | 이미 설계 원칙 (index→top-K 페이지만 로드) | U4 |
| small-first 라우팅, 실패 시만 escalation | ModelRouter에 escalate-on-fail 규칙 추가 | U4/U5 |
| A/B: 단일 변수, pass rate 유지 절감만 채택 | U8 벤치를 "naive vs Librarian" 단순 비교에서 **E-매트릭스 축소판**으로 격상 | U8 |

## U8 실험 설계 (보고서 E-매트릭스에서 채택)

고정 문제셋(질문 10~20개, 동일 위키 상태)에 대해:

| ID | 가설 | Baseline | 변경 | 채택 기준 |
|---|---|---|---|---|
| L-E1 | index→top-K 선별이 full-read보다 싸다 | 위키 전체 주입 | surgical context | cost/success 감소 + 답변 인용 정확도 유지 |
| L-E2 | LIGHT/HEAVY 라우팅이 HEAVY-only보다 싸다 | qwen-plus 단독 | small-first | cost/success 감소 + pass 유지 |
| L-E3 | 출력 계약(JSON+cap)이 출력 토큰을 줄인다 | 자유 서술 | strict contract | output -30% + 품질 유지 |

측정식: `token_reduction = 1 - candidate/baseline`, `cost_per_success = total_cost / successes`

## 하지 말 것 (보고서 Do-Not 중 Librarian 관련)

- 매 turn 공통 지침 전체 재서술 (→ 정적 prefix 1회)
- 위키 전체 본문 재첨부 (→ index + top-K)
- 자유 서술 출력 (→ 필드 수·항목 수 명시)
- 벤더 "up to X%" 수치를 목표로 삼기 (→ 자체 실측)
- multi-agent 선도입 (→ eval 없으면 낭비; Track 3이 아닌 이유이기도 함)

## Qwen(DashScope) 특이사항

- DashScope OpenAI 호환 모드는 usage에 prompt/completion tokens 노출 → RunLedger 즉시 가능
- Qwen context cache 지원 여부/조건은 문서 확인 필요 (open question) — 있으면 정적 prefix 설계가 그대로 단가 절감으로 연결

## 심사 관점 가치

이 접근 자체가 심사기준 직격: "performance optimization"(Innovation 30%) + "error handling·modularity"(Tech Depth 30%).
**"우리는 절감률을 주장하지 않고 측정했다"** — cost_per_success 표가 데모의 킬러 슬라이드.
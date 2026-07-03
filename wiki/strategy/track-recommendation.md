# 출전 트랙 추천 분석

> 질의일: 2026-07-03 (마감 D-7). 전제: 기반 개념 = LLM Wiki + Autoresearch + Graphify + CodeGraph + AI-DLC, 하네스 엔지니어링 지향.

## ✅ 최종 결정 (2026-07-03)

> **Track 1 — MemoryAgent 확정.** 하네스(LLM Wiki+Graphify+CodeGraph+Autoresearch+AI-DLC)를 프로덕트로 승격하는 self-hosting 전략. 목표는 수상.

## 결론 (요약)

> **1순위: Track 1 — MemoryAgent** (개념 스택과 트랙 요구사항이 거의 1:1 매핑)
> 2순위: Track 4 — Autopilot Agent (개발자 워크플로우 자동화로 재프레이밍할 경우)
> 비추천: Track 2(영상 생성 파이프라인 필요), Track 3(멀티에이전트+정량 베이스라인 비교 필요), Track 5(하드웨어 필요) — D-7 내 신규 역량 확보 부담이 큼

## 적합도 매트릭스

| 평가 축 | T1 Memory | T2 Showrunner | T3 Society | T4 Autopilot | T5 Edge |
|---|---|---|---|---|---|
| 개념 스택 정합성 | ★★★★★ | ★☆ | ★★☆ | ★★★☆ | ★☆ |
| D-7 실현 가능성 | ★★★★☆ | ★★☆ | ★★☆ | ★★★☆ | ★☆ |
| 심사기준 60%(기술 축) 공략 여지 | ★★★★★ | ★★★☆ | ★★★★☆ | ★★★★☆ | ★★★★☆ |
| 추가 리스크 | 낮음 | 영상 모델 학습곡선 | 정량 비교 실험 설계 필수 | 도메인 목업 필요 | 물리 디바이스·증빙 |

## Track 1 (MemoryAgent) 추천 근거 — 요구사항 1:1 매핑

트랙 1의 공식 심사 포커스 3가지가 보유 개념 스택으로 그대로 커버된다:

| 트랙 1 공식 요구 | 대응 개념 |
|---|---|
| ① 효율적 메모리 저장·검색 | **LLM Wiki** (구조화 위키 + index.md) + **Graphify** (지식 그래프 질의) |
| ② 오래된 정보의 적시 망각 | **LLM Wiki의 Lint 워크플로우** (stale claim·모순·고아 페이지 정리) — 대부분 참가자가 벡터DB TTL 수준에 그칠 영역에서 차별화 |
| ③ 제한 컨텍스트 내 핵심 기억 회상 | **CodeGraph 패턴** (surgical context: 그래프 1-질의로 정확한 컨텍스트만 주입, 토큰 절약) |
| (심화) 점점 더 정확한 의사결정 | **Autoresearch 패턴** (회상 정확도를 단일 지표로 메모리 정책 자율 실험·자기개선) |
| (프로세스) | **AI-DLC**로 개발 자체를 수행 → Presentation 15% + Blog Prize 재료 |

공식 아이디어 예시 중 *"a personal knowledge base that indexes everything you share and surfaces relevant context proactively"* 와 *"a research assistant that remembers every paper it has read"* 가 정확히 LLM Wiki + Graphify 조합이다.

## 심사기준 관점 득점 시나리오 (Track 1)

- **Innovation & AI Creativity 30%**: 메모리를 벡터 스토어가 아닌 "복리형 위키+그래프 이중 레이어"로 설계 = novel. 위키 질의를 **MCP 서버**로 노출하면 명시 가점 요소("MCP integrations") 충족
- **Technical Depth 30%**: ingest/query/lint 파이프라인의 모듈화, 망각 정책, 토큰 예산 관리 = "architectural depth" 어필
- **Problem Value 25%**: RAG의 무축적 문제는 공인된 페인포인트 (Karpathy gist 자체가 논거)
- **Presentation 15%**: 그래프 시각화(graph.html 류) → 데모 영상에서 "핵심 로직 시각화" 요건 직격

## 2순위: Track 4 (Autopilot Agent) — 조건부

같은 스택을 "개발자 업무 자동화"로 프레이밍하면 가능: 예) *리포·문서를 자율 ingest→위키 유지→온보딩/코드리뷰 컨텍스트 제공하는 팀 지식 오토파일럿*. 단, HITL 체크포인트·프로덕션 준비성 데모가 추가로 필요해 D-7에는 T1보다 부담이 큼. 
**메모/지식이 '수단'이면 T4, '주제'이면 T1** — 이 프로덕트는 지식 축적 자체가 주제이므로 T1이 자연스럽다.

## 비추천 사유

- **T2 AI Showrunner**: Wan/HappyHorse 영상 생성 파이프라인이 핵심 — 보유 스택과 접점 최소
- **T3 Agent Society**: "단일 에이전트 대비 측정 가능한 효율 향상" 정량 실험이 필수 — D-7 내 실험 설계+구현 병행은 고위험. (Autoresearch식 다중 에이전트로 확장하면 차기 도전 후보)
- **T5 EdgeAgent**: 물리 디바이스 필요 + 하드웨어 접근 제공 의무 가능성

## D-7 실행 체크포인트 (제안)

1. **D-7~6**: Qwen Cloud 가입·바우처·Discord, OpenAI 호환 API로 Qwen 첫 호출, 리포 생성(LICENSE 포함)
2. **D-6~4**: 코어 루프(ingest→wiki→query) + MCP 서버 노출, Alibaba Cloud 배포(증빙용 코드 파일 확보)
3. **D-4~2**: lint/망각 정책 + 그래프 시각화 + 토큰 절약 계측(before/after 수치)
4. **D-2~1**: 아키텍처 다이어그램, 3분 영상, 영어 README, 블로그 포스트(보너스), **드래프트 제출**
5. **D-1**: 최종 제출 (마감: 2026-07-10 06:00 KST)

## 관련 페이지
- [../hackathon/tracks.md](../hackathon/tracks.md) · [../hackathon/judging-and-submission.md](../hackathon/judging-and-submission.md)
- [../concepts/foundations.md](../concepts/foundations.md)

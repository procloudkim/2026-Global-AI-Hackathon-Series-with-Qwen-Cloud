# 심사기준 & 제출 요건 체크리스트

> 출처: Devpost rules 페이지 §4, §6

## 1. 심사 프로세스 (2단계)

- **Stage 1 (Pass/Fail)**: 테마 부합 + 필수 API/SDK(Qwen) 적용 여부의 기본 실행 가능성 판정
- **Stage 2 (가중 점수)**: 아래 기준으로 채점. 전문가 패널·피어 리뷰·**AI 자동 분석** 병용 가능

## 2. 심사기준 (Judging Criteria)

| 기준 | 가중치 | 세부 항목 |
|---|---|---|
| **Innovation & AI Creativity** | 30% | Qwen Cloud API의 정교한 활용(커스텀 skills, **MCP 통합**) · 알고리즘/엔지니어링 혁신(novel solution, 커스텀 컴포넌트, 성능 최적화) |
| **Technical Depth & Engineering** | 30% | 아키텍처 품질(모듈성·확장성·에러 핸들링) · 엔지니어링 우수성(클린 코드, non-trivial 로직) · 기술 스택 정교함(고급 패턴) |
| **Problem Value & Impact** | 25% | 실제 기술/비즈니스 페인포인트 해결 · 제품화/오픈소스 커뮤니티 확장 잠재력 |
| **Presentation & Documentation** | 15% | 핵심 로직의 효과적 시각화 · 아키텍처 문서 등 명확한 문서화 |

**전략적 시사점**:
- 기술 축(Innovation + Technical Depth)이 **60%** → 아키텍처 깊이와 엔지니어링이 승부처
- "custom skills, MCP integrations"가 명시적으로 언급됨 → **MCP 서버 구현은 직접적 가점 요소**
- AI 자동 분석 심사 가능성 → README·아키텍처 문서·코드 구조가 기계 가독적으로 명확해야 함

동점 시: 위 기준 순서대로 개별 점수 비교 → 그래도 동점이면 심사단 투표.

## 3. 제출 체크리스트 (Submission Requirements)

- [ ] **트랙 지정** (5개 중 1개)
- [ ] **코드 리포 URL** — 공개 + 오픈소스 라이선스 (About 섹션에서 라이선스 감지 가능해야 함) + 실행에 필요한 전체 소스·에셋·설치 안내 포함
- [ ] **알리바바 클라우드 배포 증빙** — Alibaba Cloud 서비스/API 사용을 보여주는 **리포 내 코드 파일 링크**
- [ ] **아키텍처 다이어그램** — Qwen Cloud ↔ 백엔드 ↔ DB ↔ 프론트엔드 연결 시각화
- [ ] **데모 영상** — 3분 미만 / 대상 디바이스에서 실제 동작 장면 / YouTube·Vimeo·Youku 공개 업로드 / 서드파티 상표·저작권 음악 금지
- [ ] **텍스트 설명** — 기능과 동작 설명 (영어)
- [ ] **테스트 접근** — 작동하는 데모 링크 또는 테스트 빌드 (비공개면 크리덴셜 제공, 심사 기간 끝까지 무료 개방)
- [ ] (선택) **블로그/소셜 포스트 URL** — Qwen Cloud 빌드 여정 공유 → Blog Post Prize($500+$500) 자격

## 4. 놓치기 쉬운 함정

1. 라이선스 파일만 넣으면 안 되고 **GitHub About에서 감지**되어야 함 (LICENSE 파일을 루트에 표준 텍스트로)
2. Alibaba Cloud 배포 증빙은 "코드 파일 링크" 형태 — SDK 호출 코드가 리포에 있어야 함
3. 기존 프로젝트 기반이면 **제출 기간 내 업데이트 내역 설명** 필수
4. 심사위원은 실행 없이 텍스트+영상만으로 평가 가능 → 데모 영상에서 핵심 로직 시각화 필수
5. 제출 마감 후 수정 불가 → 마감 최소 하루 전 드래프트 제출 권장

## 관련 페이지
- [overview.md](overview.md) · [tracks.md](tracks.md)
- [../strategy/track-recommendation.md](../strategy/track-recommendation.md)

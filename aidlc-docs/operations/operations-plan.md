# Operations Plan — AI-DLC 산출물 3/3

## 배포 대상
- Alibaba Cloud ECS (1 vCPU급이면 충분) 또는 Function Compute
- 리전: 싱가포르 권장 (dashscope-intl 엔드포인트와 근접)

## 배포 파이프라인 (수동, D-7 스코프)
1. deploy/setup.sh — ECS 초기화(uv, 서비스 등록)
2. deploy/deploy.sh — git pull + 서비스 재시작
3. 환경변수: DASHSCOPE_API_KEY (절대 커밋 금지 — .env + .gitignore)

## 제출 증빙 매핑
| 제출 요건 | 리포 내 위치 |
|---|---|
| Alibaba Cloud 사용 증빙 코드 | deploy/ + src/librarian/llm.py (dashscope-intl 호출) |
| 아키텍처 다이어그램 | aidlc-docs/construction/architecture.md (mermaid → PNG 추출) |
| 오픈소스 라이선스 | LICENSE (MIT, About 감지 확인) |

## 운영 체크
- /health, /stats 엔드포인트로 생존·토큰 사용량 확인
- 심사 기간(7/31)까지 인스턴스 중단 금지

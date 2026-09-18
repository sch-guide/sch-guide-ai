# SCHAT v1 Final Release Readiness Progress

- 날짜: 2026-09-18
- 기준 tag: `v0.9`
- 상태: **READY_EXCEPT_LIVE_RAGAS**
- Production retrieval/validator 변경: 0건
- 외부 호출/병원 데이터 외부 전송: 0회
- Git commit/tag/push: 0회

## 완료 항목

- Approved Positive Gold 21건, deferred 11건, approved abstention 4건 경계 재확인
- Deferred 11건 metadata-only 재검수 queue 생성, 자동 승인 0건
- TF027 7개 figure candidate와 10/10 사람 검수, image Gold 제외 결정 재확인
- TF027 한 화면에 stop flag와 10개 사람 확인 순서 표시
- Live RAGAS의 synthetic → 3~5건 → 21건 단계적 실행 계약 고정
- 외부 전송 필드, 제외 필드, PII preflight와 raw-free artifact 계약 고정
- ChromaDB/Qdrant를 production과 구분한 차기 비교 roadmap 기록
- 운영·안전·전체 회귀, Ruff, diff, 보안 감사 및 코드 리뷰 완료

## 변경 파일

- `tools/schat_v1_release_readiness.py`
- `tests/test_schat_v1_release_readiness.py`
- `tools/tf027_human_review_app.py`
- `tests/test_tf027_human_review.py`
- `docs/rag/67_SCHAT_V1_FINAL_RELEASE_READINESS.md`
- `docs/현재정본/00_현재상태.md`
- `docs/현재정본/06_테스트현황.md`
- `변경이력.md`
- `복구기준점.md`
- `artifacts/2026-09-18_schat-v1-final-release-readiness/`

## 테스트 결과

- Focused release readiness: 44 passed
- Integrated safety regression: 184 passed, 4 warnings
- Operational UAT: 36/36 PASS
- Full pytest: 648 passed, 4 skipped, 10 warnings
- Ruff: PASS
- `git diff --check`: PASS
- Artifact security audit: PASS
- Code review actionable defect: 0

## Pending과 중단 조건

- `BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL`: 외부 전송 정책과 provider 조건 승인 또는 외부 provider 미사용 결정
- Deferred Gold 11건은 사람 재검수 queue 유지

## 다음 단계

1. 외부 전송 승인 여부를 명시하고, 승인 시 별도 허가 아래 staged Live RAGAS를 실행한다.
2. Live RAGAS 결정 후 동일 회귀를 다시 실행해 `READY` 여부를 판정한다.

TF027은 사람의 제외 결정으로 닫혔으며, Live RAGAS 사람 결정에서 정확히 멈췄다.

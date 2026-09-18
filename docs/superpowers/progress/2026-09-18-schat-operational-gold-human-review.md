# SCHAT 운영 Positive Gold 사람 검수 도구 진행 기록

- 날짜: 2026-09-18
- 기준 안정 버전: `v0.9`
- 후보 버전: `v1.0-rc1`

## 완료 항목

- 운영 UAT와 provisional Gold에서 positive 32문항만 검수 대상으로 결합
- 검색 순위를 참고값으로만 보여주는 local candidate lookup
- text chunk와 structured table row 후보의 화면 전용 원문 표시
- Primary/Acceptable evidence, critical fact와 1·2차 승인 입력
- 원본 fixture를 보호하는 별도 reviewed fixture atomic save
- final-approved case만 Gold aggregate에 포함하는 fail-closed 계약
- Streamlit local review app과 비전공자용 사용 안내

## 변경 파일

- `tools/schat_gold_human_review.py`
- `tools/schat_gold_human_review_app.py`
- `tests/test_schat_gold_human_review.py`
- `docs/rag/65_SCHAT_OPERATIONAL_GOLD_HUMAN_REVIEW_GUIDE.md`
- `artifacts/2026-09-18_schat-operational-gold-human-review/`

## 테스트 결과

- TDD RED 확인: 구현 모듈 부재로 collection 실패
- Gold review 집중 테스트: **10 passed**
- 관련 Gold/UAT/table 회귀: **36 passed, 2 warnings**
- Full pytest: **587 passed, 4 skipped, 9 warnings**
- Ruff: PASS
- `git diff --check`: PASS
- Artifact security audit: 3 files, exact source/forbidden key/secret marker 0
- Code review: actionable correctness/safety/regression defect 0
- 외부 호출: 0회
- Production retrieval/generation 변경: 0건

## 다음 단계

1. 간호사가 로컬 화면에서 positive 32문항을 검수
2. 임상 중요 case의 2차 reviewer 확인
3. 승인된 reviewed fixture로 Gold/RAGAS/UAT aggregate 재평가

## Pending / 중단 조건

- 실제 사람 승인: pending
- 현재 승인: 0/32
- provider/vision 호출: 수행하지 않음
- Git commit/tag/push: 수행하지 않음

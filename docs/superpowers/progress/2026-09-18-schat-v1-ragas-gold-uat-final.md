# SCHAT v1 RAGAS·Gold·UAT 통합 평가 Progress

## 완료 항목

- v0.9 UAT 28/36 baseline freeze
- 단일 explicit document-context admission TDD
- out-of-scope/Q006 zero-call 회귀
- text/table 통합 운영 UAT 36/36
- 동일 수혈 90문항 retrieval 재평가 및 metric 불변 확인
- RAGAS ID Context Precision/Recall 재계산
- operational Gold label fixture와 approved/provisional 분리
- table 5/5 Hit@10 1.0 재검증
- TF027 pending 유지
- full regression, Ruff, diff check, code review, artifact audit
- 현재정본/변경이력/복구기준점/현황판 갱신

## 변경 파일

- Production: `mvp/query.py`, `mvp/evidence.py`, `mvp/app.py`
- Evaluation: `tools/schat_v1_final_validate.py`, `tools/schat_v1_ragas_gold_uat_evaluate.py`
- Tests/fixture: `tests/test_rag_contract.py`, `tests/test_schat_v1_final_validation.py`,
  `tests/test_schat_v1_ragas_gold_uat.py`, `tests/fixtures/schat_v1_operational_gold.json`
- Docs/artifacts: `docs/rag/64_SCHAT_V1_RAGAS_GOLD_UAT_FINAL_RESULT.md`, 현재정본,
  `artifacts/2026-09-18_schat-v1-ragas-gold-uat-final/`

## 테스트 결과

- Focused: 168 passed, 4 warnings
- Full: 577 passed, 4 skipped, 9 warnings
- Ruff: PASS
- `git diff --check`: PASS
- Artifact security: PASS, exact source/forbidden key/secret marker 0

## 다음 단계

1. 운영 positive Gold mapping과 critical facts 사람 검수
2. TF027 1차/2차 사람 검수
3. 외부 전송 정책 승인 뒤 제한 Provider Live와 LLM judge 평가
4. 별도 승인 후 `v1.0-rc1` recovery commit/tag/push

## Pending

- Provider Live / Faithfulness / Answer Relevancy
- TF027 production image Gold
- Positive operational Gold critical facts

## 중단 조건 여부

전체 중단 조건 없음. 외부 provider와 image 해석 track만 승인 대기다. 실제 외부 호출, commit,
tag, push는 수행하지 않았다.

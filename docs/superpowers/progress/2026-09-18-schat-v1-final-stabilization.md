# SCHAT v1 Final Stabilization Progress

- 날짜: 2026-09-18
- 기준 tag: `v0.9`
- 상태: **자동 수행 가능 범위 완료**
- Readiness: `READY_EXCEPT_IMAGE_AND_LIVE_RAGAS`

## 완료 항목

- 사람 승인 Positive Gold 21건과 deferred 11건을 fail-closed로 검증
- Approved abstention Gold 4건 유지
- BM25, production hybrid, E5, BM25+E5 RRF를 동일 Gold/Top-10으로 재평가
- Table subset 및 기존 approved table 5건 재평가
- Deferred 11건을 원인별 분류하고 사람 검수 queue 유지
- TF027 figure/checklist metadata queue 생성, 승인 0건 유지
- Live RAGAS readiness 점검, 외부 전송 미승인으로 호출 0회 유지
- Operational UAT 36/36 재검증
- 전체 pytest 637 passed, 4 skipped
- Ruff 통과

## 변경 파일

- `tools/schat_v1_final_stabilize.py`
- `tests/test_schat_v1_final_stabilization.py`
- `tests/test_schat_gold_draft.py`의 mutable review fixture 독립화
- `docs/rag/66_SCHAT_V1_FINAL_STABILIZATION_REPORT.md`
- `docs/현재정본/00_현재상태.md`
- `docs/현재정본/06_테스트현황.md`
- `변경이력.md`
- `artifacts/2026-09-18_schat-v1-final-stabilization/`

Production retrieval/generation/validator 파일은 이번 phase에서 변경하지 않았다.

## 테스트 결과

- Stabilization focused: 18 passed
- Integrated safety regression: 219 passed, 4 warnings
- Full pytest: 637 passed, 4 skipped, 10 warnings
- Ruff: PASS
- `git diff --check`: PASS
- Artifact security: 14 files, exact source/forbidden key/secret marker 0
- Code review: actionable defect 0
- Operational UAT: 36/36 PASS
- Provider/Vision/LLM judge calls: 0

## 다음 단계

1. Deferred 11건 사람 재검수
2. TF027 10개 checklist 사람 검수
3. 병원 데이터 외부 전송 조건 승인 뒤 제한 Live RAGAS
4. 모든 승인 후 v1.0 복구 기준점 저장 여부 결정

## Pending / 중단 조건

- TF027: 사람 검수 전 production image Gold 금지
- Live RAGAS: `BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL`
- Git commit/tag/push: 수행하지 않음

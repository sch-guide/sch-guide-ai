# TF027 이미지/도식 사람 검수 도구 진행 기록

- 작업일: 2026-09-18
- 기준 안정 버전: `v0.9`
- 작업 유형: local-only human review tooling

## 완료 항목

- 원본 PDF page와 후보 bbox를 메모리에서 나란히 보여주는 Streamlit 화면
- 10개 항목의 `미검수/확인됨/불확실/해당 없음` 입력
- 사람이 확인한 nodes, edges, branches, sequence, numbers, units, times 입력
- 원본 fixture 보호와 별도 reviewed fixture 원자적 저장
- 1차·2차 reviewer 및 승인 조건의 fail-closed 파생
- 승인 전 aggregate 및 production answerability 제외 테스트
- Vision/provider/network 호출이 없는 로컬 경로 검증

## 변경 파일

- `tools/tf027_human_review.py`
- `tools/tf027_human_review_app.py`
- `tests/test_tf027_human_review.py`
- `tests/test_schat_v1_final_validation.py`
- `docs/rag/61_TF027_IMAGE_HUMAN_REVIEW_CHECKLIST.md`
- `artifacts/2026-09-18_tf027-human-review/review.html`

## 사람 검수 상태

- 확인됨: 0/10
- 불확실: 0/10
- 미검수: 10/10
- 1차/2차 검수: 미완료
- `needs_human_review=true`
- `production_gold_approved=false`

## 테스트 결과

- TF027 집중 테스트: 10 passed
- TF027 및 v1 final validation 회귀: 28 passed, 1 warning
- Full pytest: 597 passed, 4 skipped, 9 warnings
- Ruff: PASS
- `git diff --check`: PASS
- Artifact security audit: 3 files, exact source 0, forbidden key 0, secret 0
- Code review: actionable defect 0

## 다음 단계

1. 간호사가 로컬 화면에서 원본을 보고 10개 항목을 직접 입력한다.
2. reviewer 1이 검수하고, 독립적인 reviewer 2가 다시 확인한다.
3. 안전 조건을 모두 만족한 reviewed fixture만 image Gold 평가 후보로 사용한다.

## Pending / 중단 조건

- 임상 workflow 의미는 사람이 확인하기 전까지 pending이다.
- 외부 vision/provider 호출은 수행하지 않았다.
- Production retrieval, generation, validator와 answerability는 변경하지 않았다.

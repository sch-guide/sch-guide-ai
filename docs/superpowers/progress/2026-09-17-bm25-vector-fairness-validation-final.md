# BM25–Vector Fairness Validation — Final Progress

- 완료일: 2026-09-17
- 상태: 완료
- 중단 조건: alternate embedding download 경계만 보류, 핵심 평가 진행에는 영향 없음
- Production retrieval 변경: 0건
- Provider/API 호출: 0회

## 완료 항목

- 동일 105 chunks / 90 questions / approved gold 공정 비교 계약 고정
- 기존 BM25/Vector preprocessing 차이 감사
- Minimal/current/canonical/expanded/temporal BM25 ablation
- Current/common/metadata-enriched MiniLM vector 비교
- Vector count/dimension/norm/model/cosine/determinism sanity 검증
- 기존 BM25/Chroma artifact 순위 재현 감사
- 전체 IR, ID RAGAS, question type/diagnostic axis, negative score, latency 집계
- Frozen A/B/C/D rule 적용: 판정 A
- Raw-free JSON/CSV/HTML artifacts 생성
- 집중/retrieval/full regression, Ruff, diff check, code review, artifact audit

## 변경 파일

- `tools/bm25_vector_fairness_evaluate.py`
- `tests/test_bm25_vector_fairness.py`
- `docs/superpowers/specs/2026-09-17-bm25-vector-fairness-validation-design.md`
- `docs/superpowers/plans/2026-09-17-bm25-vector-fairness-validation.md`
- `docs/rag/44_BM25_VECTOR_FAIRNESS_VALIDATION_RESULT.md`
- `artifacts/2026-09-17_bm25-vector-fairness-validation/`

이번 작업에서 `mvp/` production module, requirements/dependency, catalog와 fixture는 변경하지 않았다. Working tree의 다른 기존 변경은 이전 승인 작업 소유이며 되돌리지 않았다.

## 테스트 결과

- TDD RED: full evaluation 계약이 candidate-limit audit 부재로 실패함을 확인
- TDD GREEN: negative diagnostics 및 production candidate limit 40 반영 후 통과
- 집중/retrieval 회귀: `37 passed, 3 skipped, 1 warning`
- 전체 pytest: `514 passed, 4 skipped, 7 warnings`
- Ruff: 통과
- `git diff --check`: 통과

## 코드리뷰 결과

- Production code path mutation 없음
- BM25 기존 Top-10 exact reproduction 100%
- Current vector aggregate metric은 기존 Chroma baseline과 재현
- Exact cosine/HNSW 내부 순서 차이를 결과 문서에 명시
- Metadata representation 입력은 최대 124 tokens로 128-token 계약 내이며 truncation 없음
- Negative 11문항을 positive IR/RAGAS aggregate에서 제외
- Artifact에 질문, source text, API key, Authorization, raw provider response 없음
- 추가 조치가 필요한 correctness/safety/regression 결함 없음

## 다음 단계

현재 production retrieval은 유지한다. 추가 비교가 필요하면 별도 승인 및 local model 준비 후 stronger multilingual retrieval embedding 한 가지를 동일 fixture/metric으로 evaluation-only 평가한다.

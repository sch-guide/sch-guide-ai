# Integrated Enhancement Phase 02 — Production decision

- 상태: 완료
- 판정: **E — existing production hybrid 유지**
- Production code/settings/dependencies 변경: 0건
- 실제 provider/API 호출: 0회

## 판단 근거

Raw `BM25 + E5 RRF`는 가장 높은 retrieval core 0.8712를 기록했지만,
production의 existing reranker를 우회해야만 그 수치를 유지한다. Existing reranker를
적용한 경로는 core 0.7825, mean 265.16 ms, p95 372.32 ms로 BM25 current에
대한 정확도 이득이 +0.0131에 그쳤다. E5 model RSS 증분도 약 1.54 GB다.

따라서 production reranker를 제거하거나 안전 validator를 우회하지 않는 한
명확한 운영 개선으로 판정할 수 없다. Production은 기존
`BM25 + semantic/vector + RRF + reranker`를 그대로 유지한다.

## 비변경 계약

- Facet-slot / selection limit 16
- AnswerCoverage / `validate_answer()`
- citation/number/unit/time/condition/negation/action/source-order validators
- Parent atomicity / Q006 zero-call / query generalization
- Presentation sidecar / table evaluation UI
- Production embedding and requirements

## 다음 단계

- 완료된 generation UX/table/routing 트랙을 회귀 검증한다.
- Provider/image track은 기존 pending을 유지한다.

## 중단 조건 여부

- 없음. Production 변경을 하지 않는 것이 안전 계약에 부합한다.

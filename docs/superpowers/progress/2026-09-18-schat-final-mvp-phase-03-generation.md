# SCHAT Final MVP Phase 03 — Controlled Generation Boundary

- 완료일: 2026-09-18
- 실제 provider 호출: 0회
- Production generation 연결: 0건

## 완료 항목

- `ControlledGenerationDecision`을 추가해 controlled candidate의 공개 여부와 기존 extractive Answer fallback을 명시적으로 분리했다.
- Schema 오류, unknown/duplicate evidence, 숫자·단위, 조건, 부정, branch/phase mixing, coverage 오류는 재호출 없이 기존 Answer를 유지한다.
- 모든 invariant를 통과해도 semantic support가 별도 검증되지 않았으면 `semantic_support_pending`으로 기존 Answer를 유지한다.
- 결정 함수는 text 수정, 임상 의미 repair, 자동 dedup, 재정렬, provider retry를 수행하지 않는다.

## 안전 계약

| 상태 | 출력 | Retry |
|---|---|---:|
| Schema/invariant 실패 | 기존 verified extractive Answer | 0 |
| Semantic support pending | 기존 verified extractive Answer | 0 |
| 미래 semantic validator 통과 | controlled candidate 공개 가능 | 0 |

현재 semantic entailment와 unsupported synthesis의 의미 검증은 provider/validator 승인 전까지 pending이다. 따라서 controlled paraphrase는 production에 공개되지 않으며, 기존 exact SourceUnit + citation + presentation sidecar 경로가 유지된다.

## 변경 파일

- `mvp/controlled_generation.py`
- `tests/test_controlled_paraphrasing.py`
- 이 progress 문서

## 테스트 결과

- Controlled generation + presentation 집중: `21 passed`
- 실제 Groq/Gemini 호출: 0회

## 다음 단계

- Evidence-type routing, image pending, local web UAT, multi-document isolation과 성능 경계를 재검증한다.

## Pending / 중단 조건

- Live provider 품질 검증: 외부 전송 승인 대기
- Semantic entailment validator: 승인된 평가/validator가 없으므로 pending
- 중단 조건 발생: 없음. Pending track만 fail-closed 유지

# SCHAT Multimodal MVP Phase 02 — Controlled paraphrasing preparation

- 상태: 완료
- Production provider 연결: 없음
- 실제 generation API 호출: 0회
- Git commit/push: 0회

## 완료 항목

- Request-scoped SourceUnit ID enum 기반 closed response schema를 구현했다.
- Natural statement + `supporting_source_unit_ids`만 허용하는 provider-neutral contract를 구현했다.
- Server가 선택 ID에서 exact chunk ID와 quote를 파생하는 citation mapping을 구현했다.
- Unknown/duplicate ID, evidence coverage, number/unit/time, condition, negation, branch 및 phase 혼합을 fail closed한다.
- Prompt template은 corpus-wide answerability를 모델에 맡기지 않고 verified evidence 재서술 역할만 정의한다.
- `semantic_support_pending=true`를 명시하여 Mock validator가 semantic entailment를 완전히 증명한다고 주장하지 않는다.

## 변경 파일

- `mvp/controlled_generation.py`
- `tests/test_controlled_paraphrasing.py`

## 테스트 결과

- Red 확인: module 미구현 `ModuleNotFoundError`
- Green: **8 passed**

## 안전 계약

- Live `mvp.ai` provider path에 import/연결하지 않았다.
- Public Answer, reconstruction 및 `validate_answer()`를 변경하지 않았다.
- Mock은 synthetic evidence만 사용하며 병원 원문을 artifact에 저장하지 않는다.

## Pending track

- 실제 provider 품질, semantic entailment, 자연스러움 평가는 병원 데이터 외부 전송 및 provider 승인 전까지 pending이다.
- Image/vision track은 pending이다.

## 다음 단계

- Evaluation-only structured table record/citation/search/UI를 TDD로 구현한다.

## 중단 조건 여부

- 없음.

# SCHAT Multimodal MVP Phase 01 — Text presentation

- 상태: 완료
- 실제 generation API 호출: 0회
- Git commit/push: 0회

## 완료 항목

- `AnswerPresentation`에 clinical text가 아닌 `intent`, `answer_format` routing metadata를 추가했다.
- summary, preparation, cautions, fact/purpose 표시 스타일을 exact statement 기반으로 구현했다.
- branch/phase heading은 원래 statement 순서를 한 번만 순회하며 삽입한다.
- procedure의 기존 outer numbering 제거와 leading marker 1회 표시 계약을 유지했다.
- sidecar는 계속 private attribute이며 public Answer JSON/schema에 나타나지 않는다.
- Sidecar 생성 시 `plan.kind`를 전달하되 retrieval/selection/reconstruction/validator는 변경하지 않았다.

## 변경 파일

- `mvp/presentation.py`
- `mvp/answer_ui.py`
- `mvp/ai.py`
- `tests/test_answer_presentation.py`

## 테스트 결과

- Red 확인: `answer_display_rows` 미구현 ImportError
- Green: `tests/test_answer_presentation.py` 10 passed
- Presentation + SourceUnit 집중 회귀: **48 passed, 1 warning**
- Warning은 기존 FastEmbed pooling 안내이며 embedding은 변경하지 않았다.

## 안전 계약

- Statement text, evidence quote, chunk ID 변경 없음
- SourceUnit 선택 및 순서 변경 없음
- 의미 기반 dedup/요약/재작성 없음
- citation 및 `validate_answer()` 완화 없음

## Pending track

- 실제 natural paraphrasing 품질 평가는 provider 승인 전까지 pending이다.
- Image/vision track과 TF027은 pending이다.

## 다음 단계

- Provider에 연결하지 않는 controlled paraphrasing schema/prompt/validator를 synthetic Mock으로 구현한다.

## 중단 조건 여부

- 없음.

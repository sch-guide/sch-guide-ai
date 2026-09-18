# SCHAT Gold 자동 초안 + 사람 승인 설계

## 목표

운영 Positive Gold 32건 중 이미 최종 승인된 case는 그대로 보존하고, 나머지
provisional case에 대해 로컬 retrieval과 보수적인 규칙 기반 분석으로 검수 초안을
만든다. 초안은 사람이 검토하기 전까지 Gold가 아니며 RAGAS/Gold aggregate에 포함되지
않는다.

## 안전 경계

- `UAT-S01`, `UAT-S02`를 포함한 기존 `final_gold_approved=true` case는 초안 생성과
  자동 갱신에서 제외한다.
- retrieval 순위는 화면 참고 및 동일 점수 tie-break에만 사용한다. Primary 후보 여부는
  질문·문서·evidence type·section·근거 문장 직접성의 복합 신호로 결정한다.
- 숫자, 단위, 시간, 조건, 금기, 부정, 단계는 선택된 exact evidence에 실제 문자열이
  있을 때만 추출한다. 재작성하거나 임상정보를 보완하지 않는다.
- 초안 생성은 `draft_only=true`, `final_gold_approved=false`를 고정한다.
- 초안은 Git에서 제외되는 `data/review/schat_v1_operational_gold_drafts.json`에 원자적으로
  저장한다. 기존 provisional/reviewed fixture는 초안 생성 과정에서 수정하지 않는다.
- provider, 네트워크, production retrieval/generation/validator는 사용하거나 변경하지
  않는다.

## 구성요소

### `tools/schat_gold_draft.py`

순수한 초안 계약과 규칙 기반 생성기를 소유한다.

- `build_case_draft(case, candidates)`: 단일 case 초안 생성
- `build_draft_fixture(inputs, review, candidate_loader)`: 최종 승인 case를 제외하고 batch 생성
- `validate_draft_fixture(payload)`: 자동 승인, 외부 text 대량 저장, 승인 case 포함을 차단
- `save_draft_fixture(path, payload, protected_paths)`: local-only 원자적 저장
- `draft_progress(payload, review)`: 준비/수정/사람 확인 필요/승인 상태 집계

초안은 case ID, 제안 scope/document, Primary/Acceptable ID, exact 핵심 문장과 구조적
invariant, table/image 제안, confidence, 사람이 확인해야 할 이유만 가진다. 전체 후보
본문, 전체 질문, retrieval score/rank는 저장하지 않는다.

### 기존 local retrieval

`tools/schat_gold_human_review.py`의 local BM25 + MiniLM + RRF + reranker 후보와 structured
table 후보를 그대로 사용한다. Draft generator는 후보를 소비할 뿐 retrieval 동작을
변경하지 않는다.

### Streamlit 검수 화면

- 미승인 case 30건을 대상으로 전체 초안 생성 버튼을 제공한다.
- 자동 초안을 별도 영역에 표시하고 confidence와 사람 확인 필요 사유를 보여준다.
- `초안 그대로 승인`, `수정 후 승인`, `판단 보류`를 중심 동작으로 제공한다.
- 초안/수정 승인 시 reviewer와 1차 승인 시각을 기록한다. 최종 Gold 체크가 없으면
  `reviewing` 상태로 저장되고 aggregate에는 들어가지 않는다.
- 사람이 최종 Gold 체크를 직접 선택한 경우에만 기존 fail-closed validator를 거쳐
  `approved`와 `final_gold_approved=true`가 된다.
- 저장 후 다음 unreviewed case로 이동하며 이전/다음 미검수 탐색을 제공한다.
- image/TF027은 항상 사람 확인 및 2차 검수 대상으로 남긴다.

## 초안 판정

- `ready_for_human_approval`: 직접 근거와 최소 핵심 사실이 있고 중요한 불확실성이 없음
- `edit_recommended`: 근거 후보는 있으나 multi-evidence, 표·혼합 또는 핵심 사실 범위를
  사람이 다듬어야 함
- `manual_review_required`: 직접 근거가 부족하거나 image/불확실성이 남음

세 상태 모두 사람의 검토 대상이며 자동 Gold 승인을 뜻하지 않는다.

## 검증

- 기존 승인 2건 불변 및 batch 제외
- rank 1만으로 Primary가 되지 않는 synthetic 역순 후보
- evidence에 없는 숫자/단위/시간/조건 미생성
- out-of-scope에 clinical Gold 미생성
- 사람 승인 전 aggregate 제외
- Streamlit Mock에서 세 버튼, 진행률, 자동 다음 이동, 최종 승인 분리
- provider/HTTP transport 0회
- focused pytest, Ruff, `git diff --check`, 코드 리뷰

# SCHAT v1 RAGAS·Gold·UAT 통합 평가 설계

## 목적

고정된 운영 UAT 36문항과 기존 retrieval fixture를 바꾸지 않고, 명시적으로 선택한 단일 병원 지침 문맥을 질문 의미와 실제 회수 근거에 결합한다. 검색 결과만으로 Gold를 바꾸거나, 선택 문서만으로 모든 질문을 병원 질문으로 승격하지 않는다.

## Domain/topic admission

- `QueryPlan`은 질문에서 추출한 domain과 별도로 명시적 `context_document_ids` 및 해당 문서의 canonical topic metadata를 보관한다.
- 명시적 단일 문서 scope가 있을 때만 그 topic을 검색 표현에 추가할 수 있다. 원 질문, BM25/vector 알고리즘, 임계값은 변경하지 않는다.
- `out_of_scope` 또는 clarification 질문은 context admission 대상이 아니다.
- `unknown` 질문은 실제 검색 결과가 명시 scope 안에만 있고 기존 substantive/topic/aspect 검사를 통과할 때만 hospital evidence로 admission한다.
- 직접적인 질문 topic이 근거 본문에 있으면 원 topic을 우선한다. 직접 검사가 `no_topic_evidence`인 경우에만, 임상 intent가 구조화되어 있고 단일 scope가 확인된 요청에 문서 topic fallback을 적용한다.
- evidence가 부족하거나 parent가 불완전하면 기존 reason으로 fail closed한다. parent atomicity와 모든 validator는 그대로 둔다.

## Web scope

- 기존 기본값은 전체 등록 지침 검색이다.
- 사용자가 지침 하나를 명시적으로 선택한 경우에만 `context_document_ids`를 전달한다.
- scope 변경 시 대화 문맥을 초기화한다. 선택하지 않은 문서의 근거는 검색·citation에 들어갈 수 없다.

## 평가

- 수정 전 결과는 별도 baseline artifact로 고정한다.
- Retrieval/RAGAS는 기존 `transfusion-retrieval-v3` 90문항, approved positive 78문항과 기존 metric 정의를 유지한다.
- UAT는 `schat-v1-operational-uat-v1` 36문항을 그대로 사용한다.
- Gold/Label은 기존 사람이 승인한 fixture에서 명확히 재사용 가능한 ID만 연결한다. 새로운 임상 Gold를 retrieval 결과로 자동 확정하지 않는다. 불명확한 case는 `needs_human_review=true`로 aggregate에서 제외한다.
- Table, image, provider는 text evidence와 별도 상태로 기록한다. TF027, provider Live, LLM-judge RAGAS는 승인 전 pending이다.

## 안전 계약

- Q006 및 명백한 외부 질문은 선택 문서가 있어도 provider zero-call이다.
- production retrieval 구조, 임계값, Facet-slot, selection limit 16, Prompt/AnswerCoverage, parent atomicity 및 validator는 변경하지 않는다.
- artifact에는 질문 원문, 병원 source text, full prompt, raw provider response, API key/Authorization을 저장하지 않는다.
- commit/tag/push는 수행하지 않고 `v0.9` tag를 그대로 보존한다.

## 완료 판정

- 고정 UAT, Gold, retrieval/RAGAS, table, safety를 개별 metric으로 보존한다.
- 미검수 Gold 또는 승인 대기 track은 점수에서 제외하고 pending으로 표시한다.
- core UAT/Gold/safety 실패가 남으면 `v0.9 유지`, core가 통과하고 provider/image 승인만 남으면 `v1.0-rc1`, 승인된 전체 범위가 통과할 때만 `v1.0`을 제안한다.

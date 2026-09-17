# SCHAT Multimodal MVP Text/Table 안전 확장 설계

## 1. 목표와 경계

이번 확장은 기존 retrieval, SourceUnit 선택, reconstruction, AnswerCoverage 및 `validate_answer()`가 승인한 근거를 바꾸지 않고 다음 기능을 준비한다.

- 질문 intent에 맞는 text answer presentation
- 향후 자연어 재서술을 위한 controlled paraphrasing 계약과 fail-closed validator
- PDF 표의 header/row/cell 구조와 table citation
- text/table/image/mixed evidence-type routing metadata
- 로컬 evaluation UI와 raw-free 검수 artifact

실제 Groq/Gemini generation, image interpretation, production retriever 교체는 범위 밖이다.

## 2. 불변식

- `Statement.text`, `Evidence.quote`, `chunk_id`는 기존 exact SourceUnit reconstruction 값을 유지한다.
- presentation은 `validate_answer()`와 citation assessment 통과 후 private sidecar로만 붙인다.
- statement 및 SourceUnit 순서를 변경하거나 의미 기반 dedup을 수행하지 않는다.
- 표 값은 PDF parser가 추출한 exact cell만 사용하며 생성·추론하지 않는다.
- table artifact에는 clinical cell/header text를 저장하지 않고 ID, bbox, hash, source link만 저장한다.
- controlled paraphrasing 결과는 현재 public `Answer`나 provider path에 연결하지 않는다.
- BM25/RRF/reranker/Facet-slot/evidence gate/validator는 변경하지 않는다.

## 3. Text presentation

`AnswerPresentation`에 clinical text가 아닌 `intent`와 `format` routing metadata를 추가한다. `StatementPresentation`의 source-unit identity metadata는 유지한다.

UI는 원래 statement index를 한 번만 순회한다.

- fact/purpose: outer numbering 없는 compact paragraph rows
- procedure: 기존 branch → phase headings와 source marker 1회 표시
- preparation/materials: checklist 모양의 정적 bullet 표시
- cautions/release: warning-list 모양의 bullet 표시
- summary/synthesis: `핵심 요약` heading과 source-preserving bullets
- comparison: 기존 exact statement Markdown table
- branch/temporal metadata: 순서를 재정렬하지 않고 값이 바뀌는 지점에 heading만 삽입

## 4. Controlled paraphrasing contract

Provider-neutral 입력은 검증된 SourceUnit identity, exact text, branch, phase이다. 출력은 다음 두 필드만 가진 statement 목록이다.

- `text`: 자연어 statement 후보
- `supporting_source_unit_ids`: 검증된 allowlist의 ID

서버 validator는 다음을 fail closed한다.

- unknown/empty/duplicate evidence ID
- statement/selection 한도 초과
- cited evidence coverage 부족
- evidence에 없는 number, percentage, unit, time
- negation 또는 condition marker 추가·누락
- 한 statement 안의 adult/pediatric branch 혼합
- 허용되지 않은 phase 혼합

이 validator는 semantic entailment의 완전한 대체물이 아니다. 실제 provider 연결은 별도 승인과 품질 평가가 있을 때만 수행한다.

## 5. Structured table evidence

Evaluation-only parser는 PDF table마다 다음 구조를 메모리에서 만든다.

- stable `table_id`, page, table index, bbox, fingerprint
- exact header tuple
- row index와 exact cell tuple
- 가능한 cell bbox
- 동일 page의 production chunk ID links

`TableCitation`은 document, page, table ID, row index를 추적한다. Markdown renderer는 exact parsed cell만 표시하고 citation을 각 row에 붙인다.

Persisted safe manifest에는 header/cell text 대신 SHA-256만 저장한다. Local Streamlit evaluation UI는 실행 중에만 exact preview를 보여 준다.

## 6. Evidence-type routing

QueryPlan과 독립적으로 계산 가능한 순수 router를 둔다.

- `text`: 일반 fact, purpose, preparation, cautions, procedure
- `table`: 비교, 제품별 수치 lookup, 속도·시간·보관·간격 질의
- `mixed`: text context와 table lookup이 모두 필요한 질의
- `image`: 도식·화면·순서 이미지 질의이며 현재 `pending_image_review`

Routing은 후보 metadata일 뿐 기존 retrieval이나 answerability를 우회하지 않는다.

## 7. 평가 및 production 결정

- Text UX는 exact identity/citation invariant를 만족하면 production UI에 최소 반영한다.
- Controlled paraphrasing은 Mock 전용으로 유지한다.
- Structured table은 evaluation-only로 유지한다. 기존 측정상 table-aware retrieval이 명확한 개선이 아니므로 production retrieval에 반영하지 않는다.
- Evidence routing metadata는 retrieval에 사용하지 않고 trace/evaluation에만 사용한다.

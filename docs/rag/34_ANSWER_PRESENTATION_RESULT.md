# Procedure Answer Presentation Sidecar 구현 및 Mock/UI 검증 결과

- 구현·검증일: 2026-09-15
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 승인안: Option B — 검증된 Answer와 분리된 presentation sidecar
- 검증 방식: 기존 Q002 Facet-slot fixture, `httpx.MockTransport`, UI row/snapshot 테스트
- 실제 Groq 호출: 0회
- 상태: 구현과 오프라인 검증 완료, 실제 웹앱 반영 확인 대기

## 1. 결론

검증된 `Answer`와 citation 계약을 변경하지 않고 procedure 답변 전용 presentation sidecar를 추가했다. Sidecar는 reconstruction에 사용된 `SourceUnit`에서 `statement_index`, `source_unit_id`, `branch`, `phase`, `source_order`, `leading_marker`만 생성하며 임상 문장, quote 또는 재작성 text를 포함하지 않는다.

Q002 11개 SourceUnit Mock에서 statement 수와 ID 선택을 그대로 유지했다. 서버가 검증한 원문 `Statement.text`, `Evidence.quote`, `chunk_id`는 모두 동일했고 citation coverage 100%, source-order 역전 0건이었다. UI는 원래 statement 순서를 바꾸지 않은 채 `공통 → 성인 → 소아` branch heading과 성인/소아 내부의 `시행 전 → 시행 중 → 시행 후` phase heading을 삽입한다.

Grouped procedure 화면에서는 기존 `enumerate()` 바깥 번호를 사용하지 않는다. 원문의 `1)`, `2.`, `①`, `④` 등 leading marker는 한 번만 표시하고, `15분`, `10분`, `10 mg`, `95%`는 marker로 해석하지 않는다. 일반 답변에는 기존 renderer가 그대로 적용된다.

## 2. 구현 구조

### Presentation sidecar

`mvp/presentation.py`에 표시 전용 immutable 타입을 추가했다.

| 필드 | 역할 |
|---|---|
| `statement_index` | 검증된 Answer statement의 원래 위치 |
| `source_unit_id` | reconstruction에 사용된 request-local SourceUnit ID |
| `branch` | 서버 SourceUnit metadata의 common/adult/pediatric 구분 |
| `phase` | 서버 SourceUnit metadata의 before/during/after 구분 |
| `source_order` | 검증된 원래 source 순서 |
| `leading_marker` | 화면에서 한 번만 표시할 구조적 원문 prefix |

Sidecar 생성 전 다음 identity를 다시 확인한다.

- statement 수와 SourceUnit 수가 같음
- SourceUnit 순서가 역전되지 않음
- `Statement.text == SourceUnit.exact_text`
- statement당 evidence가 정확히 1개임
- `Evidence.chunk_id == SourceUnit.chunk_id`
- `Evidence.quote == SourceUnit.exact_text`

Identity가 맞지 않으면 presentation을 붙이지 않으며 기존 검증 Answer renderer로 안전하게 fallback한다. Answer 자체나 validator 성공 결과를 presentation 오류로 변경하지 않는다.

### 생성 경계

Sidecar는 provider response parsing, selection validation, reconstruction, `validate_answer()`, citation 기반 evidence assessment가 모두 통과한 뒤 생성된다. `Answer`에는 Pydantic private attribute로만 연결되어 `model_dump()`와 `Answer.model_json_schema()`에는 나타나지 않는다.

따라서 다음 계약은 바뀌지 않았다.

- Provider response schema와 Facet-slot selection
- Public Answer JSON 구조
- Statement text, evidence quote와 chunk ID
- AnswerCoverage 및 `validate_answer()`
- Exact citation, source sentence, number, unit, condition/negation, unsupported action와 source-order validator

## 3. UI 표시 계약

Procedure 답변은 sidecar를 원래 `statement_index` 순서대로 한 번만 순회한다. Branch 또는 phase가 바뀌는 지점에 heading을 삽입할 뿐 statement를 정렬하거나 합치지 않는다.

예상 구조:

```text
공통
• 검증된 원문 statement [1]

성인
시행 전
1) 검증된 원문 statement [2]
시행 중
• 15분 간격을 포함한 검증된 원문 statement [2]
시행 후
④ 검증된 원문 statement [2]

소아
시행 전
① 검증된 원문 statement [3]
시행 중
• 10분 간격을 포함한 검증된 원문 statement [3]
시행 후
• 검증된 원문 statement [3]
```

위 예시는 배치 구조만 설명하며 sidecar가 만드는 임상 요약문이 아니다. 실제 review는 검증된 fixture Answer의 원문을 그대로 표시한다.

`format=steps`이면서 완전한 sidecar가 있을 때만 grouped renderer를 사용한다. Q001/Q003/Q004/Q005 같은 일반 Answer, comparison, paragraph, bullet, summary 또는 sidecar가 없는 과거 turn은 기존 UI 경로를 유지한다.

## 4. Q002 Mock 결과

| 항목 | 결과 |
|---|---:|
| 실제 Groq 호출 | 0회 |
| MockTransport 호출 | 1회 |
| 기존 selected SourceUnits | 11개, 동일 |
| Verified statements | 11개, 동일 |
| Sidecar records | 11개 |
| Answer JSON schema 변경 | 없음 |
| Sidecar clinical text | 없음 |
| Statement text/quote/chunk identity | 100% |
| Citation assessment | `supported` |
| Citation coverage | 100% |
| Source-order reversal | 0건 |
| Branch 기여 | common 3, adult 5, pediatric 3 |
| Phase metadata | unspecified 3, before 3, during 2, after 3 |
| Leading marker 표시 대상 | 9개 |
| UI outer numbering 추가 | 없음 |
| Response parse stage | `complete` |
| Validation reason | 없음 |

성인과 소아의 15분/10분 임상 값은 원문 statement에 그대로 남아 각각의 branch 아래 표시된다. 두 문장을 유사하다는 이유로 합치거나 제거하지 않는다.

## 5. Marker 및 순서 검증

구조적 prefix로 허용한 예시는 숫자 목록(`1)`, `2.`), 원문 원형 번호(`①`~`⑳`), 한글 목록과 bullet marker다. Marker는 statement 저장값에서 삭제하지 않고 renderer가 화면에서 prefix와 body를 분리해 한 번만 출력한다.

다음 임상 숫자는 leading marker로 인식하지 않는 테스트를 고정했다.

- `15분`
- `10분`
- `10 mg`
- `95%`

Display row의 statement index는 `0..10` 원래 순서를 그대로 유지한다. Branch/phase별 재정렬, 의미 기반 dedup 또는 유사 문장 병합은 없다.

## 6. 회귀와 정적 검사

- Presentation/SourceUnit 집중 테스트: `43 passed, 1 warning`
- Q001~Q006, Q002 Facet-slot 및 UI 관련 회귀: `125 passed, 3 warnings`
- 전체 pytest: `352 passed, 1 skipped, 4 warnings`
- Ruff: 통과
- `git diff --check`: exit code 0, 통과
- Code review: 추가 조치가 필요한 correctness, safety 또는 regression 결함 없음

Warning은 기존 FastEmbed multilingual MiniLM pooling 기본값 안내이며 embedding 설정을 변경하지 않았다. 기존 테스트 임시 디렉터리 일부의 접근 경고는 Git 상태 조회에만 나타났고 diff check 결과는 성공이었다.

## 7. 변경 및 비변경 범위

변경:

- `mvp/presentation.py`: metadata-only sidecar, marker 검사와 원순서 display rows
- `mvp/ai.py`: 모든 Answer 검증 후 sidecar 연결
- `mvp/answer_ui.py`: procedure grouped renderer와 outer numbering 제거
- `mvp/app.py`: 검증된 sidecar를 turn에 보관하고 renderer에 전달
- `tests/test_answer_presentation.py`: sidecar, marker, grouping 및 일반 UI 회귀
- `tests/test_source_unit_selection.py`: Q002 11-unit exact identity 회귀
- `tools/rag_answer_presentation_evaluate.py`: raw-free Mock/UI 검수 산출물 생성

비변경:

- BM25, embedding, RRF, reranker와 selected evidence
- Facet-slot selection과 selected SourceUnit 결과
- SourceUnit segmentation/eligibility
- Provider schema, prompt, output/request budget와 selection limit
- Reconstruction 값과 모든 안전 validator
- Retry, fallback과 web search

## 8. 산출물 및 작업 중지

최종 검수 산출물:

`artifacts/2026-09-15_rag-answer-presentation-mock-02/`

- `mock_report.json`
- `test_results.json`
- `review.html`

같은 작업 중 먼저 생성한 `artifacts/2026-09-15_rag-answer-presentation-mock/`은 덮어쓰지 않았으며, identity 보고 계산을 바로잡아 새로 만든 `-02`가 최종 검수본이다.

이번 단계에서는 실제 Groq를 호출하지 않았다. 검수 HTML은 실제 provider 재호출 없이 Q002의 기존 검증 fixture를 사용한 예상 화면이다. 사용자 승인 전 추가 Groq 호출이나 retrieval/selection/validator 변경은 수행하지 않는다.

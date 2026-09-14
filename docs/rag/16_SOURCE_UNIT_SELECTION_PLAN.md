# Groq Source Unit ID Selection 구조 설계

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 결과: `docs/rag/16_UNSUPPORTED_SENTENCE_LIVE_RESULT.md`
- 분석 대상: Q002 selected evidence 12 chunks
- 실제 Groq 호출: 0회
- 코드 수정: 0회
- 상태: 분석·설계 완료, 구현 승인 대기
- 최종 권고: **D. 서버가 source unit을 만들고 LLM은 임시 ID만 선택**

## 1. 결정 요약

현재 방식은 Groq가 `statement.text`와 `evidence.quote`에 원문을 다시 생성하게 한다. System prompt와 strict schema description이 exact extractive 출력을 요구하지만 두 차례 live 평가에서 모두 `AI_EVIDENCE / unsupported sentence`가 발생했다. Validator를 완화하지 않고 같은 생성 작업을 반복하는 것보다, 모델의 책임을 “원문 복사”에서 “서버가 제공한 source unit ID 선택”으로 줄이는 편이 안전하다.

권고 흐름은 다음과 같다.

1. 서버가 승인된 selected evidence 12 chunks를 현재 `source_sentences()`로 문장·표 행 후보로 분리한다.
2. heading/열 머리글처럼 답변 근거가 될 수 없는 후보를 기존 구조 신호로 제외하고, 선택 가능한 unit에 요청 단위 임시 ID를 부여한다.
3. prompt에는 group/chunk metadata를 반복하지 않는 compact source-unit catalog를 한 번만 보낸다.
4. Groq는 exact text나 quote를 반환하지 않고 최대 10개의 `source_unit_id`만 원문 순서대로 선택한다.
5. 서버는 요청 시점의 immutable ID map에서 실제 `exact_text`, `chunk_id`, branch와 source order를 복원한다.
6. 서버가 기존 public `Answer → Statement → Evidence` 구조를 직접 구성한다.
7. 기존 citation, number, unit, action, source-order 검증을 방어 계층으로 다시 실행한다.
8. 존재하지 않는 ID, 중복, 비선택 unit, branch coverage 손실 또는 순서 역전은 Groq 재호출 없이 차단한다.

이 구조에서는 모델이 원문의 punctuation, 어미, 공백 또는 quote를 재생성하지 않으므로 현재 `unsupported sentence` 실패의 주된 입력 경로가 제거된다.

## 2. 범위와 고정 불변식

이 문서는 설계 산출물이다. 다음은 수정하거나 실행하지 않았다.

- 실제 Groq 호출
- system prompt
- Answer schema와 validator
- `source_sentences()`, `sentence_evidence()`, `validate_answer()`
- BM25, embedding, RRF와 reranker
- evidence selection 및 selected evidence 12 chunks
- `OUTPUT_LIMIT=2048`
- `GROQ_REQUEST_TOKEN_BUDGET=5120`
- 기존 artifacts

향후 D안 구현에는 provider 내부 응답 schema와 evidence serialization 변경이 필요하다. 현재 system prompt는 기존 `statement.text/evidence.quote` JSON을 직접 요구하므로 D안을 구현하면서 영구히 그대로 둘 수는 없다. 이번 계획 단계에서는 수정하지 않으며, 구현 승인 시 source-unit 선택 계약으로 바꾸는 범위를 명시적으로 함께 승인받아야 한다.

## 3. 현재 실패와 책임 경계

Live 결과에서 확정된 사실은 다음과 같다.

- HTTP 200 및 `finish_reason=stop`
- completion 924/2048로 truncation 없음
- strict JSON parsing 성공
- Q002 selected evidence 12 chunks와 required gold 10/10 유지
- grounding validator에서 `unsupported sentence`

Raw content를 저장하지 않았으므로 모델이 paraphrase, fragment, punctuation 변경, label 불일치 또는 다른 문자열 차이를 만들었는지는 알 수 없다. D안은 특정 실패 문장을 추정하지 않고, 원문 문자열 생성 자체를 모델 책임에서 제거한다.

## 4. `source_sentences()` 사용 가능성

### 현재 Q002 측정

현재 selected 12 chunks를 변경하지 않고 `source_sentences()`에 통과시킨 로컬 계산 결과는 다음과 같다.

| 항목 | 결과 |
|---|---:|
| selected chunks | 12 |
| 원시 source units | 43 |
| chunk당 최소 units | 1 |
| chunk당 최대 units | 6 |

`source_sentences()`는 다음 현재 계약을 이미 가진다.

- NFC 정규화와 whitespace 축소
- 명확한 문장부호 뒤 분리
- PDF soft wrap으로 판단되는 일부 줄바꿈 연결
- 목록, 표 행, 제목 경계 유지

따라서 **source unit 후보 생성의 기준 함수로 사용할 수 있다.** 기존 validator도 같은 함수를 사용하므로 catalog 경계와 사후 exact validation 경계가 일치한다.

### 그대로 사용할 때의 제한

`source_sentences()`는 모든 반환값이 답변 근거인지 분류하지 않는다. Q002의 원시 43개에는 다음이 섞일 수 있다.

- 완전한 임상 문장
- 번호가 붙은 절차 문장
- 표 행
- section heading
- `성인 | 소아` 같은 열 머리글
- 짧은 caption 또는 형식 행

따라서 반환값 전체에 ID를 붙여 모델이 자유롭게 고르게 해서는 안 된다. catalog builder가 기존 `has_substantive_body()`, heading/header 구조 신호, procedure action 여부와 evidence group 정보를 사용해 `selectable`을 결정해야 한다. 병원별 단계명을 하드코딩하거나 Q002 gold ID를 운영 코드에 넣지는 않는다.

보수적인 1차 정책은 다음과 같다.

1. selected Chunk 자체가 title/header-only이면 unit을 만들지 않는다.
2. Chunk 안의 명백한 heading, 표 열 머리글과 caption-only 행은 context로만 보존하고 selectable catalog에서는 제외한다.
3. 완전한 문장 또는 독립적으로 의미가 있는 표 행만 selectable로 둔다.
4. 애매한 짧은 unit은 자동 보강하지 않고 제외 사유를 trace한다.
5. Q002 gold fixture는 selectable 정책의 평가에만 사용하고 운영 판정에는 사용하지 않는다.

이 분류가 현재 코드만으로 충분하지 않다는 증거가 나오면 `source_sentences()`를 변경하지 않고 별도 순수 eligibility 함수의 설계를 다시 승인받는다.

## 5. Source unit 데이터 구조

요청 내부의 canonical 구조는 다음 정도로 제한한다.

```python
# 미구현 설계 예시
@dataclass(frozen=True)
class SourceUnit:
    source_unit_id: str       # 요청 단위 임시 ID, 예: su001
    chunk_id: str             # 서버측 selected Chunk ID
    source_order: tuple[int, int]  # chunk.index, chunk 내부 unit index
    branch: str               # common / adult / pediatric / scoped
    exact_text: str           # source_sentences()가 반환한 canonical 원문
    group_key: str            # 기존 EvidenceGroup 연결
    required: bool            # group-level required/optional 정책에서 파생
    selectable: bool
```

### ID와 lifecycle

- ID는 `su001`, `su002`처럼 짧고 request-local한 opaque 값으로 만든다.
- 최종 selected evidence와 source order가 고정된 뒤 결정적으로 부여한다.
- ID에 chunk ID, 문서명, branch 또는 임상 의미를 인코딩하지 않는다.
- 같은 요청 안에서만 유효한 immutable `dict[source_unit_id, SourceUnit]`을 만든다.
- DB, Streamlit global cache 또는 대화 history에 저장하지 않는다.
- Groq 응답을 처리한 직후 폐기한다.
- prompt의 ID map과 응답 검증에 사용하는 map은 동일 객체여야 한다.

### Source order

한 문서 안에서는 `(chunk.index, unit_index)`를 기본 순서로 사용한다. 여러 branch는 기존 evidence group의 문서/분기 순서를 유지한다. 모델에게 긴 order tuple을 반복 전송하지 않고 catalog 배열 순서와 짧은 ID 순서로 표현하며, 실제 tuple은 서버 map과 trace에 유지한다.

## 6. Compact prompt catalog

권고 prompt 입력은 group/chunk metadata를 현재처럼 상위에서 한 번만 표현하고 source text 자리에 unit 배열을 둔다.

```json
{
  "schema_version": 3,
  "groups": [
    {
      "group_id": 1,
      "branch": "common",
      "required": true,
      "sources": [
        {
          "chunk_id": "...chunk-00013",
          "order": 12,
          "units": [
            {"id": "su001", "text": "완전한 원문 문장"},
            {"id": "su002", "text": "다음 완전한 원문 문장"}
          ]
        }
      ]
    }
  ]
}
```

`exact_text`는 모델이 선택하려면 입력 prompt에 반드시 한 번은 있어야 한다. 다만 응답에서 다시 출력하지 않는다. `chunk_id`, branch와 order는 group/source level에서 공유하고 unit마다 반복하지 않는다.

Prompt schema는 compact evidence schema v2와 의미가 달라지므로 구현 시 v3으로 올려야 한다. Selected Chunk의 원문 범위는 동일하며 문서 전체나 다른 Chunk는 추가하지 않는다.

## 7. Provider 응답 schema

### 권고 최소 schema

```python
# 미구현 provider-internal schema
class SourceUnitSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answerable: bool
    selected_source_unit_ids: list[str] = Field(max_length=10)
    format: Literal["paragraph", "steps", "bullets", "summary", "comparison"]
    conflict: bool
```

불변식:

- `answerable=true`이면 ID가 1~10개여야 한다.
- `answerable=false`이면 ID 목록은 비어 있어야 한다.
- ID는 제공된 catalog에서만 선택한다.
- 반환 순서는 source order여야 한다.
- label, statement text, quote, chunk ID, branch와 order를 모델이 생성하지 않는다.
- strict JSON schema와 `additionalProperties=false`를 유지한다.

`format`은 현재 public Answer 호환을 위해 유지할 수 있지만 서버의 `QueryPlan.format`으로 결정하는 편이 더 안전하다. 최소 1차 구현에서는 provider가 선택할 필요가 없는 `format`과 `conflict`도 제거하고 서버에서 결정하는 대안을 우선 검토한다. 문서 충돌이 실제로 존재하면 현재 `explicit_conflicts()`의 서버 결과로 conflict를 설정한다.

### 여러 evidence 관계

모델이 서로 다른 source units를 하나의 자연어 문장으로 합치게 하지 않는다. 각 선택 ID는 하나의 exact source unit으로 복원한다. 동일 exact sentence가 여러 Chunk에 존재해 복수 citation이 필요한 경우 서버가 normalized text fingerprint로 동등한 units를 연결하고 기존 `Statement.evidence`에 최대 4개까지 붙인다. 서로 다른 문장을 한 statement로 합쳐 의료 행동을 새로 만들지는 않는다.

여기서 source unit은 문장/표 행 경계이고 임상적 procedural unit과 동일하지 않다. 하나의 source sentence가 여러 gold stage를 지지할 수 있고, 하나의 gold stage가 여러 source units를 필요로 할 수도 있다. 운영 코드에서 `1 procedural unit = 1 output statement`로 가정하지 않는다.

## 8. 서버측 Answer 복원

응답 검증 후 서버는 ID map만 신뢰해 public Answer를 만든다.

```python
# 미구현 의사코드
selection = SourceUnitSelection.model_validate_json(content)
units = validate_selection(selection, source_unit_map, coverage)

statements = [
    Statement(
        text=unit.exact_text,
        label="",
        evidence=[Evidence(chunk_id=unit.chunk_id, quote=unit.exact_text)],
    )
    for unit in units
]
answer = Answer(
    answerable=bool(statements),
    statements=statements,
    format=plan.format,
    conflict=server_conflict,
)
answer = validate_reconstructed_answer(answer, selected_hits, plan)
```

중요한 소유권은 다음과 같다.

- `exact_text`: 서버 `SourceUnit`에서 가져온다.
- `chunk_id`: 같은 서버 unit에서 가져온다.
- `quote`: 서버가 `exact_text`로 직접 채운다.
- `label`: 1차 구현에서는 빈 문자열로 고정한다.
- format/conflict: QueryPlan과 서버 conflict 검사에서 결정한다.

Groq 응답에 text, quote, chunk ID 또는 metadata가 포함되더라도 strict schema가 거부한다.

## 9. 잘못된 선택 차단

| 오류 | 검증 위치 | 동작 |
|---|---|---|
| 존재하지 않는 ID | selection validation | 전체 응답 차단, 재시도 없음 |
| 중복 ID | selection validation | 전체 차단; 자동 dedup하지 않음 |
| non-selectable unit | selection validation | 전체 차단 |
| 10개 초과 | strict schema/Pydantic | 전체 차단 |
| answerable/ID 목록 불일치 | selection validation | 전체 차단 |
| required group 누락 | post-selection coverage | 전체 차단 |
| required procedural unit 누락 | post-selection coverage | 전체 차단 |
| 필요한 branch 누락 | post-selection coverage | 전체 차단 |
| 서로 다른 branch를 잘못 혼합 | branch validation | 전체 차단 |
| source order 역전 | monotonic order validation | 전체 차단; 자동 재정렬하지 않음 |
| 동일 exact text 반복 선택 | fingerprint/duplicate validation | 전체 차단 또는 prompt 전 canonical dedup |
| selected evidence 밖 Chunk 참조 | ID map 경계 | 구조상 불가능; 발견 시 전체 차단 |

자동 정렬이나 dedup으로 모델 오류를 숨기지 않고 fail closed한다. Trace에는 원문 없이 unknown ID count, duplicate count, branch mismatch 여부, first order violation position과 coverage reason만 남긴다.

## 10. 기존 validator의 유지와 구조상 변화

### 그대로 유지할 검증

- selected Chunk 범위와 권한
- exact citation: quote가 실제 Chunk text에 존재하는지
- `source_sentences()` 기반 완전 문장/표 행 검증
- 숫자와 단위 보존
- unsupported action 검사
- procedure source order
- required group/branch/procedural coverage
- answerable/statements 일관성
- privacy 및 markup 차단
- 최대 10 statements

서버가 원문으로 Answer를 구성하므로 대부분 항상 통과해야 하지만 defense-in-depth와 회귀 감지를 위해 제거하지 않는다.

### 구조상 모델 출력에 대해 불필요해지는 검증

- 모델이 생성한 `statement.text`의 paraphrase 여부
- 모델이 생성한 quote의 Chunk 포함 여부
- 모델이 생성한 chunk ID의 존재 여부
- model label grounding

이 값들은 더 이상 모델 출력에 존재하지 않는다. 다만 복원된 public Answer에는 기존 validator를 다시 실행하므로 코드 자체를 삭제하거나 느슨하게 만들지 않는다. Provider response parsing과 public Answer validation을 별도 함수로 분리한다.

### Failure code

Provider envelope/schema 오류는 기존 `AI_RESPONSE`, `finish_reason != stop`은 `AI_INCOMPLETE`를 유지한다. 선택 오류에는 raw ID를 기록하지 않는 고정 detail을 둔다.

- `selection_unknown_id`
- `selection_duplicate_id`
- `selection_non_selectable`
- `selection_inconsistent`
- `selection_missing_required`
- `selection_branch`
- `selection_source_order`

사용자 결과는 기존 안전한 근거 부족 문구로 차단하며 자동 retry하지 않는다.

## 11. Current Answer API/UI 호환

`SourceUnitSelection`은 Groq provider 내부 DTO일 뿐 public API가 아니다. 서버가 기존 `Answer` 객체로 복원하므로 다음 호출자는 변경할 필요가 없다.

- `answer_text()`
- Streamlit 답변 렌더링
- statement별 citation UI
- 문서명/page/section/chunk 표시
- exact quote 상세 보기
- conversation cache의 최종 Answer 형태

UI는 source unit ID를 사용자에게 표시하지 않는다. 필요 시 관리자 trace에서만 ID, chunk ID, order와 선택/제외 사유를 확인한다. Citation metadata는 prompt에서 되받지 않고 서버의 실제 Chunk에서 가져오는 기존 계약을 유지한다.

## 12. Token 및 budget 영향

Q002의 현재 로컬 tiktoken 추정값은 다음과 같다.

| 구조 | evidence serialization | 현재 대비 | 예상 reservation | 5120 대비 headroom | 판단 |
|---|---:|---:|---:|---:|---|
| 현재 chunk text v2 | 1498 | 기준 | 4410 | 710 | 현재 |
| compact grouped unit catalog | 1625 | +127 | 4537 | 583 | 통과 예상 |
| unit마다 모든 metadata 반복 | 2742 | +1244 | 5654 | -534 | cap 초과, 기각 |

Compact 예상 reservation 4537의 최소 headroom 기준은 `max(256, ceil(4537×8%)) = 363`이다. 예상 headroom 583은 이를 충족한다.

Provider response schema 자체의 로컬 직렬화 비교는 다음과 같다.

- 현재 Answer strict schema: 약 375 tokens
- 최소 ID-selection schema: 약 97 tokens
- 차이: 약 -278 tokens

현재 SCHAT admission estimator는 messages와 completion reserve를 기준으로 하며 response schema token을 reservation에 직접 포함하지 않는다. 따라서 schema 감소분을 5120 admission 여유로 선반영하지 않는다. 실제 Groq prompt token은 provider 내부 accounting에 따라 달라질 수 있으므로 구현 후 Mock에서 reservation을 확인하고, 별도 승인된 단일 live에서만 실제 usage를 측정한다.

Completion은 원문 문장과 quote를 반복 출력하지 않고 짧은 ID만 반환하므로 2048 안에서 훨씬 작아질 가능성이 높다. 하지만 이 역시 live 측정 전에는 예상으로만 기록한다.

## 13. ID-only와 ID + exact text 응답 비교

### 먼저 구분할 점

모델이 relevance를 판단하려면 **입력 prompt에는 ID와 exact text의 매핑이 필요하다.** Prompt에 ID만 보내고 text를 전혀 제공하지 않으면 모델은 무엇을 선택할지 알 수 없으므로 설계 대안이 될 수 없다.

비교 대상은 provider **응답**에서 exact text를 다시 보내게 할지 여부다.

| 기준 | 응답에 ID만 | 응답에 ID + exact text |
|---|---|---|
| Grounding | 서버 map으로 결정적 복원 | 모델 복사 오류가 다시 발생 가능 |
| `unsupported sentence` 경로 | 생성 text가 없어 제거 | 유지 |
| Completion token | 가장 작음 | 원문과 quote 반복으로 큼 |
| 존재하지 않는 내용 | ID allowlist로 차단 | text와 ID 불일치 검증 추가 필요 |
| Citation | 서버 Chunk에서 구성 | 모델 text/quote를 다시 검증해야 함 |
| 설명 가능성 | trace의 ID→Chunk map으로 충분 | 원문 중복 저장 위험 |
| 권고 | **채택** | 기각 |

Prompt에서는 `id + exact text`를 한 번 제공하고, 응답은 ID만 받는 조합이 권고안이다.

## 14. 대안 비교

| 기준 | A. Prompt 예시 강화 | B. Schema description | C. Display/extractive 분리 | D. Source-unit ID selection |
|---|---|---|---|---|
| Live 근거 | 미검증 | 두 번째 live에서도 실패 | 미검증 | 미검증 |
| 원문 복사 책임 | LLM | LLM | extractive는 LLM, display도 LLM | **서버** |
| Grounding 안전성 | 중간 | 중간 | 설계에 따라 중간, display 환각 위험 | 가장 높음 |
| Validator 완화 필요 | 없음 | 없음 | natural display용 새 검증 필요 | 없음 |
| Token | 예시만큼 증가 | schema description 증가 | 출력·schema 증가 | input +127 예상, output 크게 감소 예상 |
| Current API 호환 | 높음 | 높음 | 낮음~중간 | 높음, 서버 adapter 사용 |
| 구현 복잡도 | 낮음 | 완료 | 높음 | 중간 |
| 실패 시 설명 가능성 | 낮음 | 낮음 | 복잡 | unknown/duplicate/order 등 결정적 reason |
| 최종 판단 | 예비 실험으로도 우선순위 낮음 | 단독 해결 실패 | 기각 | **권고** |

### A. 현재 구조 + prompt 예시 강화

복사 성공률을 높일 수 있지만 모델이 여전히 긴 한국어 원문과 quote를 재생성한다. 예시가 prompt tokens를 늘리고 특정 형식에 과적합할 수 있다. 두 번의 live 실패 뒤에도 같은 책임 구조를 유지하므로 우선 권고하지 않는다.

### B. 현재 구조 + schema description

Mock에서는 의미 계약 전달이 확인됐지만 실제 단일 live에서 다시 `unsupported sentence`가 발생했다. 안전 경계로는 유지할 수 있으나 단독 해결책으로는 부족하다는 실증이 있다.

### C. Display text / extractive text 분리

검증용 원문과 사용자용 자연어를 나누면 가독성은 좋아질 수 있다. 그러나 display text가 새로운 의료 조건이나 행동을 만들지 않았는지 별도 검증해야 하고 현재 Answer/UI 계약도 넓어진다. 이번 문제는 안전한 extractive 답변조차 완성되지 않는 것이므로 우선순위가 아니다.

### D. Source-unit ID selection

모델은 relevance와 선택만 담당하고 원문·citation은 서버가 소유한다. Exact string generation 오류, 허위 chunk ID와 quote 변형을 구조적으로 제거하고 기존 validator를 유지할 수 있다. 현재 문제와 병원 지침 grounding 안전성에 가장 직접적이므로 최종 권고한다.

## 15. 모듈 소유권과 최소 변경 범위

구현 승인 후 예상 범위는 다음과 같다.

| 파일 | 책임 |
|---|---|
| `mvp/evidence.py` | selected hits에서 request-scoped SourceUnit catalog 생성, eligibility, group/branch/coverage 연결 |
| `mvp/ai.py` | provider 내부 selection schema, compact v3 serialization, ID validation, 기존 Answer 복원과 validator 재실행 |
| `mvp/search_trace.py` | 원문 없는 unit count, selected ID count, failure detail, order/branch/coverage 기록 |
| 관련 테스트 | source split, eligibility, unknown/duplicate/order/branch, reconstruction와 기존 validator 회귀 |
| 평가 도구 | Q002 Mock selection과 Q006 zero-call, raw-free review.html |

`mvp/retrieval.py`, embedding, RRF, reranker, context의 selected evidence 12 chunks와 BM25 trace는 수정하지 않는다.

구현 시 버전은 다음처럼 분리한다.

- `PROMPT_EVIDENCE_SCHEMA_VERSION`: 2 → 3
- `AI_VERSION`: 12 → 13
- `SEARCH_VERSION`, `CHUNK_VERSION`: 유지

## 16. Mock 테스트 계획

### Source unit 생성

- Q002 selected chunks가 정확히 12개인지 확인한다.
- `source_sentences()` 출력과 SourceUnit exact_text가 일치한다.
- ID가 유일하고 source order가 결정적이다.
- unit이 원래 chunk, group과 branch에 정확히 연결된다.
- heading/header-only unit은 selectable이 아니다.
- 운영 코드에 Q002 chunk ID나 gold 단계명이 하드코딩되지 않는다.

### Selection schema와 차단

- 1~10개의 유효 ID를 source order로 반환하면 통과한다.
- unknown ID, duplicate ID, non-selectable ID를 각각 고정 reason으로 차단한다.
- answerable/빈 목록 불일치를 차단한다.
- required group/unit/branch 누락을 차단한다.
- common/adult/pediatric 잘못된 혼합과 순서 역전을 차단한다.
- text, quote, chunk ID를 응답에 추가하면 strict schema가 거부한다.

### Answer 복원과 기존 검증

- 복원된 `statement.text`와 `evidence.quote`가 SourceUnit exact_text와 동일하다.
- 복원된 chunk ID가 서버 map의 값과 동일하다.
- citation coverage 100%, exact citation 100%다.
- number/unit/action/condition/negation이 원문 그대로 유지된다.
- procedure source order 역전이 0건이다.
- 기존 `validate_answer()` 방어 검증을 통과한다.
- current Answer API와 UI fixture가 변경 없이 동작한다.

### Q002/Q006와 budget

- Q002 pre/post required gold recall 10/10을 유지한다.
- selected evidence 12 chunks, parent partial inclusion 0, required branches를 유지한다.
- 선택 ID 10개 이하로 required gold를 모두 지지하는지 평가 fixture로 확인한다.
- compact catalog reservation ≤5120, headroom ≥`max(256, reservation×8%)`를 충족한다.
- Q006은 source catalog와 transport 호출 모두 0회다.
- 실제 Groq 호출 없이 MockTransport만 사용한다.

## 17. 단계별 구현 순서

사용자 승인 후에도 실제 Groq를 호출하지 않고 다음까지만 1차 구현한다.

1. 현재 Q002 selected 12 chunks와 원시 43 units를 test fixture에서 고정한다.
2. request-scoped SourceUnit 타입과 catalog builder를 구현한다.
3. selectable/non-selectable 경계를 단위 테스트로 고정한다.
4. compact source-unit catalog v3와 selection-only strict schema를 구현한다.
5. unknown, duplicate, branch, coverage와 source-order validation을 구현한다.
6. 선택 ID를 기존 public Answer로 복원하고 기존 validator를 재실행한다.
7. Q002 MockTransport 성공/실패 matrix와 Q006 0회를 검증한다.
8. reservation, unit별 token과 required coverage를 새 artifacts/review.html로 생성한다.
9. 전체 테스트와 Ruff를 실행하고 Mock 결과 문서를 작성한다.
10. 작업을 중지하고 사용자 승인을 기다린다.

Mock 결과 승인 전에는 실제 Groq를 호출하지 않는다. 이후 실제 단일 호출도 별도 명시 승인을 받아야 한다.

## 18. 합격 기준

1. Q002 selected evidence 12 chunks와 pre/post required gold 10/10 유지
2. SourceUnit ID uniqueness 100%
3. selectable unit의 exact_text/chunk mapping 정확도 100%
4. unknown/duplicate/non-selectable ID 안전 차단 100%
5. required group/unit/branch 유지
6. parent partial inclusion 0건
7. source order 역전 0건
8. 서버 복원 statement의 exact source sentence/row 일치 100%
9. exact quote/chunk citation coverage 100%
10. 근거 없는 숫자·단위·조건·부정·행동 0건
11. compact projected/actual reservation ≤5120 및 최소 headroom 충족
12. Q006 catalog 생성 0건, MockTransport 0회
13. Answer API/UI 회귀 없음
14. 전체 테스트와 Ruff 통과

## 19. Rollback

D안은 chunk, embedding, DB와 retrieval 결과를 바꾸지 않는다.

1. provider response schema를 기존 Answer strict schema로 되돌린다.
2. compact catalog v3를 기존 evidence schema v2로 되돌린다.
3. SourceUnit catalog/selection/reconstruction 경로를 제거한다.
4. `AI_VERSION`과 prompt schema version을 이전 값으로 되돌린다.
5. D안 전용 tests와 Mock artifacts를 분리해 보존한다.

BM25, semantic index, RRF, reranker, selected Chunk, 기존 artifacts와 원본 문서는 rollback 대상이 아니다.

## 20. 위험과 열린 사항

- `source_sentences()`는 segmentation 함수이지 semantic eligibility 분류기가 아니다. heading/표 머리글을 selectable로 잘못 노출하지 않는 테스트가 필수다.
- 원시 43 units 중 selectable 수는 정책 확정 후 달라진다. 43개를 모두 답변 후보라고 주장하지 않는다.
- ID-only selection은 exact 복사 문제를 제거하지만 관련 unit 누락 문제를 자동 해결하지 않는다. required coverage gate를 반드시 post-selection에 적용한다.
- 최대 10 IDs 안에서 Q002 required evidence를 표현할 수 있는지 Mock fixture로 먼저 확인해야 한다. gold 단계 수와 output statement 수를 일대일로 강제하지 않는다.
- 서로 다른 원문 문장을 하나의 자연어 단계로 합치는 기능은 제공하지 않는다. 가독성 개선이 필요하면 grounded display layer를 별도 설계한다.
- System prompt의 기존 Answer JSON 요구와 새 selection schema는 동시에 유지할 수 없다. 구현 승인 시 provider contract 변경을 versioned atomic change로 수행해야 한다.

## 21. 승인 대기

최종 권고는 D안이다. 서버가 selected Chunk를 완전한 source units로 분리하고 request-local ID와 exact 원문 map을 소유한다. Groq는 ID만 선택하며 서버가 기존 Answer, quote와 citation을 실제 Chunk에서 복원한 뒤 현재 validator를 그대로 실행한다.

이번 작업에서는 실제 Groq 호출과 코드 수정을 수행하지 않았고 기존 artifacts를 변경하지 않았다. 구현과 Mock 검증은 사용자의 별도 승인 후에만 진행한다.

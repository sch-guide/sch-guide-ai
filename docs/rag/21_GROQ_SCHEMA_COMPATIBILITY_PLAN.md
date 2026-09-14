# Groq Structured Output Schema 호환성 분석 및 최소화 결과

- 작성·검증일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 Live 결과: `docs/rag/21_BRANCH_SELECTION_LIVE_RESULT.md`
- 대상 모델: `openai/gpt-oss-20b`
- 실제 Groq 호출: 0회
- 상태: provider-compatible 최소 schema 구현 및 Mock 검증 완료, Live 재평가 승인 대기

## 1. 결론

Q002 Live의 HTTP 400은 Groq가 strict Structured Output request를 수락하지 않은 schema 경계 실패다. 오류 body를 읽거나 저장하지 않았으므로 어떤 keyword가 직접 원인이었는지는 확정할 수 없다.

변경 전 Q002 schema는 root `anyOf` 1개, `minItems` 5개, `maxItems` 10개와 `enum` 12개를 사용했다. Groq 공식 문서는 strict subset에서 object, array, enum과 `anyOf`를 명시적으로 지원하고, 모든 property의 required 지정과 모든 object의 `additionalProperties:false`를 요구한다. 반면 `minItems`와 `maxItems`는 지원 항목, 제한 또는 공식 예시에서 확인되지 않는다. 따라서 `anyOf`를 실패 원인으로 단정하지 않고, provider가 맡을 책임을 **허용된 shape와 per-slot ID enum**으로 줄이는 최소 schema를 적용했다. [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs)

새 schema는 root object 하나만 사용한다.

- `answerable`은 required boolean이다.
- `group_selections`는 required closed object다.
- 현재 request의 `g1`~`g5`는 모두 required property다.
- 각 group 값은 array이고, item은 그 group의 selectable request-local source-unit ID enum이다.
- `anyOf`, `minItems`, `maxItems` 및 기타 복합 제약은 provider schema에서 사용하지 않는다.

Required group non-empty, adult/pediatric branch coverage, answerable/selection 일관성, duplicate, 총 16개 제한과 source order는 기존 서버 validator가 계속 fail closed한다. 이는 validator 완화가 아니라 provider에서 공식 지원이 불명확한 표현만 제거하고 이미 존재하던 서버 검증을 최종 신뢰 경계로 유지하는 변경이다. 누락 ID 자동 보충, dedup 또는 재정렬은 없다.

Q002 Mock은 14개 ID 선택, 서버 reconstruction, 기존 `validate_answer()`를 통과했다. Citation coverage는 100%, source order 역전은 0건이다. Q006 transport는 0회였다. 전체 테스트는 `289 passed, 1 skipped`, Ruff는 통과했다.

## 2. 확정 사실과 추정 범위

### 확정 사실

- 이전 Live request는 HTTP 400에서 종료됐다.
- Response parsing, group selection validation과 reconstruction에는 도달하지 않았다.
- 당시 실제 Q002 response schema에는 `anyOf`, `minItems`, `maxItems`, request-local enum이 함께 있었다.
- Groq 공식 문서는 `anyOf`를 지원 composition으로 명시한다.
- 공식 문서는 `minItems`와 `maxItems`를 명시적 지원 keyword로 열거하거나 예시로 사용하지 않는다.
- `openai/gpt-oss-20b`는 strict mode 지원 모델로 문서화돼 있다.

### 확정할 수 없는 사실

- HTTP 400의 직접 원인이 `minItems`, `maxItems`, root `anyOf`, 동적 properties, enum 크기 또는 다른 provider-side validation인지 여부
- `minItems`와 `maxItems`가 항상 거부된다는 일반 명제
- 이번 최소 schema가 실제 Groq endpoint에서 수락된다는 사실

마지막 항목은 actual call 0회 조건 때문에 Mock 단계에서는 주장하지 않는다.

## 3. 변경 전 실제 Q002 schema 구조 감사

병원 원문과 prompt는 출력하지 않고 schema의 shape와 keyword만 read-only로 집계했다.

| 항목 | 변경 전 값 |
|---|---:|
| Root key | `anyOf` |
| Group slots | 5 |
| Common required slots | 3 |
| Adult required slots | 1 |
| Pediatric required slots | 1 |
| g1/g2/g3 enum 크기 | 각 1 |
| g4 enum 크기 | 6 |
| g5 enum 크기 | 12 |

Keyword 감사:

| Keyword | 변경 전 | 최소 schema | 판단 |
|---|---:|---:|---|
| `anyOf` | 1 | 0 | 공식 지원이지만 단일 object로 단순화 가능해 제거 |
| `minItems` | 5 | 0 | 공식 strict 지원이 명확하지 않아 provider에서 제거 |
| `maxItems` | 10 | 0 | 공식 strict 지원이 명확하지 않아 provider에서 제거 |
| `enum` | 12 | 5 | 각 group의 허용 ID 제약만 유지 |
| `const` | 0 | 0 | 미사용 |
| `oneOf` | 0 | 0 | 미사용 |
| `allOf` | 0 | 0 | 미사용 |
| `if` / `then` / `else` | 0 | 0 | 미사용 |
| `pattern` | 0 | 0 | 미사용 |
| `uniqueItems` | 0 | 0 | 미사용, duplicate는 서버 차단 |
| `dependentSchemas` | 0 | 0 | 미사용 |
| `propertyNames` | 0 | 0 | 미사용 |
| `minProperties` / `maxProperties` | 0 | 0 | 미사용 |

변경 전 `enum` 12개는 true/false variant의 answerable enum 2개와 두 variant에 반복된 group item enum 10개다. 변경 후에는 다섯 group item enum만 남는다.

## 4. Provider-compatible 최소 schema

다음은 병원 원문과 실제 ID 값을 제외한 안전한 구조 예시다. Runtime에서는 `su_gN_*` 자리에 해당 request group의 실제 짧은 `suNNN` allowlist가 들어간다.

```json
{
  "type": "object",
  "properties": {
    "answerable": {"type": "boolean"},
    "group_selections": {
      "type": "object",
      "properties": {
        "g1": {
          "type": "array",
          "items": {"type": "string", "enum": ["su_g1_allowed"]}
        },
        "g2": {
          "type": "array",
          "items": {"type": "string", "enum": ["su_g2_allowed"]}
        },
        "g3": {
          "type": "array",
          "items": {"type": "string", "enum": ["su_g3_allowed"]}
        },
        "g4": {
          "type": "array",
          "items": {"type": "string", "enum": ["su_g4_allowed_1", "su_g4_allowed_2"]}
        },
        "g5": {
          "type": "array",
          "items": {"type": "string", "enum": ["su_g5_allowed_1", "su_g5_allowed_2"]}
        }
      },
      "required": ["g1", "g2", "g3", "g4", "g5"],
      "additionalProperties": false
    }
  },
  "required": ["answerable", "group_selections"],
  "additionalProperties": false
}
```

Q002의 모든 slot에는 selectable ID가 있어 다섯 arrays 모두 enum을 가진다. 일반 request에서 optional slot의 selectable allowlist가 비어 있으면 빈 enum은 유효한 JSON Schema가 아니므로 item type만 남긴다. 그 slot의 non-empty 값은 기존 서버의 unknown/wrong-group/non-selectable 검사에서 fail closed된다. Required slot에 selectable unit이 없으면 schema 생성 전에 기존 `after_budget:required_group_has_no_selectable_unit` 경계에서 LLM 호출 없이 중단한다.

## 5. Answerable true/false 단순화

변경 전에는 root `anyOf`로 다음 의미를 provider schema에 표현했다.

- `answerable=true`: required group array의 `minItems=1`
- `answerable=false`: 모든 group array의 `maxItems=0`

변경 후에는 `answerable:boolean`과 모든 group arrays를 항상 required로 둔다. Provider는 JSON shape와 group별 ID allowlist만 강제한다.

서버 `validate_source_unit_selection()`은 기존 로직으로 다음을 계속 차단한다.

| 상태 | 서버 결과 |
|---|---|
| `answerable=true`, 선택 ID 없음 | `selection_inconsistent` |
| `answerable=false`, 하나 이상의 ID 있음 | `selection_inconsistent` |
| Required common group empty | `selection_missing_group` |
| Required adult/pediatric group empty | `selection_branch` |
| Unknown ID | `selection_unknown_id` |
| 다른 group의 ID | `selection_wrong_group` |
| Non-selectable ID | `selection_non_selectable` |
| Duplicate ID | `selection_duplicate_id` |
| 총 16개 초과 | `selection_limit` |
| Group 내부 또는 전체 order 역전 | `selection_source_order` |

Schema가 빈 required group이나 false+IDs의 **모양**을 허용하더라도 그 상태가 Answer reconstruction이나 사용자 출력으로 진행되는 것은 아니다. 같은 request 안에서 서버 검증이 반드시 실행되며, 오류 시 `AI_EVIDENCE`로 끝난다. Branch 누락을 허용하거나 자동 보충하는 변경이 아니다.

## 6. 변경 범위

### 변경

- `mvp/ai.py`
  - response schema를 단일 closed object + per-slot enum으로 단순화
  - `RESPONSE_SELECTION_SCHEMA_VERSION=3`
  - `AI_VERSION=15`로 generation cache 계약 분리
- `tests/test_source_unit_selection.py`
  - provider shape와 keyword 부재 검사
  - schema가 허용하는 의미 오류를 기존 서버 validator가 차단하는 회귀 유지
- `tests/test_groq_structured_output.py`
  - 최소 schema와 새 version 계약 검사
- `tests/test_unsupported_sentence_schema.py`
  - 새 AI version 회귀 반영
- `tools/rag_source_unit_evaluate.py`
  - raw-free schema keyword audit와 provider/server 책임 분리 결과 추가

### 비변경

- Selected evidence 12 chunks
- Retrieval, BM25, embedding, RRF와 reranker
- Segmentation과 SourceUnit eligibility/catalog v3
- PromptCoverage와 AnswerCoverage 의미
- Server reconstruction
- `source_sentences()`와 `validate_answer()`
- Exact citation, number, unit, action, condition/negation, source-order validator
- `OUTPUT_LIMIT=2048`
- `GROQ_REQUEST_TOKEN_BUDGET=5120`
- `PROMPT_EVIDENCE_SCHEMA_VERSION=3`
- `SEARCH_VERSION=12`, `CHUNK_VERSION=4`
- System prompt, retry, fallback과 web search

## 7. MockTransport 검증

### 정상 Q002

| 항목 | 결과 |
|---|---:|
| Selected evidence | 12 chunks |
| Required groups | 5/5 |
| Required branches | adult, pediatric |
| Selected source units | 14/16 |
| Pre-budget required gold | 10/10 |
| Post-budget required gold | 10/10 |
| Source-unit gold | 10/10 |
| Parent partial inclusion | 0건 |
| Server reconstruction | 14 statements |
| Reconstructed text exact | 100% |
| Reconstructed quote exact | 100% |
| Exact citation validation | `supported` |
| Citation coverage | 100% |
| Source order 역전 | 0건 |
| Response parse stage | `complete` |
| Failure code / validation reason | 없음 |
| Q002 MockTransport calls | 1회 |

### Fail-closed 사례

| Mock 사례 | 차단 경계 |
|---|---|
| Required adult group empty | 서버 `selection_branch` |
| Required pediatric group empty | 서버 `selection_branch` |
| Required common group empty | 서버 `selection_missing_group` |
| Wrong-slot ID | JSON enum 또는 서버 `selection_wrong_group` |
| Unknown ID | JSON enum 또는 서버 `selection_unknown_id` |
| Duplicate ID | 서버 `selection_duplicate_id` |
| Non-selectable ID | JSON enum 또는 서버 `selection_non_selectable` |
| 총 16개 초과 | 서버 `selection_limit` |
| `answerable=false` + IDs | 서버 `selection_inconsistent` |
| Group 내부 order 역전 | 서버 `selection_source_order` |
| 전체 order 역전 | 서버 `selection_source_order` |

Q006은 SourceUnit catalog 0건, transport 0회와 기존 고정 근거 부족 응답을 유지했다.

## 8. Budget과 schema 크기

| 항목 | 결과 |
|---|---:|
| Request admission reservation | 4528 |
| Admission cap | 5120 |
| Headroom | 592 |
| 최소 요구 headroom | `max(256, ceil(4528 × 8%)) = 363` |
| 변경 전 response schema estimate | 503 tokens |
| 최소 response schema estimate | 212 tokens |
| Schema estimate 감소 | 291 tokens, 약 57.9% |

기존 reservation은 messages와 output reserve 계약을 사용하므로 response schema estimate와 별도 관측한다. Provider schema 단순화는 request admission reservation을 늘리지 않았고 headroom 기준도 유지했다.

## 9. 테스트와 코드 검수

- 집중 Mock/schema/response 테스트: `48 passed`
- 전체 테스트: `289 passed, 1 skipped`
- Ruff 전체 검사: 통과
- `git diff --check`: 공백 오류 없음
- 기존 FastEmbed multilingual MiniLM pooling 기본값 안내 warning 2건: 이번 변경과 무관, embedding 미변경

AGENTS.md의 code-review 절차로 이번 provider schema 변경, server validator 경로, version/cache, artifacts 보안과 테스트 범위를 검수했다.

- 추가 조치가 필요한 correctness, safety 또는 regression 결함 없음
- `validate_source_unit_selection()`의 fail-closed 검사 삭제·완화 없음
- `validate_answer()`와 reconstruction 변경 없음
- Raw 병원 원문, 전체 prompt, API key, Authorization 및 raw response 저장 경로 추가 없음
- 실제 provider 호환성은 Mock만으로 입증할 수 없다는 잔여 경계가 있음

## 10. 산출물과 작업 중지

새 산출물 디렉터리:

`artifacts/2026-09-14_rag-groq-schema-compatibility-mock/`

- `mock_report.json`
- `test_results.json`
- `review.html`

기존 artifacts는 덮어쓰지 않았다. 이번 단계의 실제 Groq 호출은 0회다. 최소 schema의 실제 endpoint 수락 여부를 확인하는 Live 호출, prompt 변경 또는 다른 schema 대안으로의 전환은 수행하지 않고 사용자 승인을 기다린다.

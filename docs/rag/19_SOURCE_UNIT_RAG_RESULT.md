# Q002 Source Unit RAG 구현 및 단일 Live 평가 결과

- 구현·평가일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 분석: `docs/rag/18_SOURCE_SEGMENTATION_ANALYSIS.md`
- 구현안: Source Unit ID Selection
- 실제 Groq 호출: Q002 1회, Q006 0회
- 상태: 구현과 Mock 검증 완료, Live는 `selection_branch`로 안전 차단되어 최종 승인 대기

## 1. 결론

Source Unit RAG의 승인 범위를 구현했다. Q002의 PDF wrapped bullet 3쌍을 일반화된 구조 규칙으로 복원한 결과 raw source unit은 43개에서 40개가 됐다. 이 가운데 21개가 선택 가능한 완전 문장 또는 독립 행이며, heading·caption·단계 표지와 orphan continuation은 catalog에 보존하되 선택할 수 없게 했다.

PromptCoverage와 AnswerCoverage를 분리했다. 기존 입력 완전성 지표인 required procedural units 23개는 그대로 유지하고, 출력은 required group 대표, adult/pediatric branch 대표, 질문에 명시된 action과 source order를 별도로 검증한다. Q002 평가 fixture의 required gold 10개 stage는 fingerprint가 일치하는 14개 source unit으로 표현됐다.

MockTransport에서는 14개 ID 선택, 서버 reconstruction, 기존 `validate_answer()` 전체 재검증까지 통과했다. Exact text와 quote는 100% 일치했고 citation coverage 100%, source order 역전 0건이었다. Request reservation은 4497, admission cap은 5120, headroom은 623으로 최소 요구 360을 충족했다.

사전 조건이 모두 충족되어 승인된 실제 Groq 호출을 정확히 1회 수행했다. HTTP 200, `openai/gpt-oss-20b`, `finish_reason=stop`으로 응답했지만 selection validation에서 `AI_EVIDENCE / selection_branch`로 안전 차단됐다. 모델이 선택한 ID 집합이 required adult/pediatric branch 중 하나 이상을 충족하지 못했다는 뜻이다. Raw response를 저장하지 않았으므로 누락된 구체 ID나 branch를 추정하지 않는다. 자동 보정, 재정렬, retry, fallback 또는 두 번째 호출은 수행하지 않았다.

따라서 Source Unit 구조는 기존의 `unsupported sentence`를 구조적으로 제거할 수 있는 형태로 구현됐고 Mock에서는 완결됐지만, 이번 단일 live 결과는 최종 답변 합격 기준을 충족하지 못했다.

## 2. 구현 내용

### Segmentation

`mvp/evidence.py::source_sentences()`에 다음 일반 규칙만 적용했다.

- PDF bullet marker 집합에 추출 문자 `Ÿ`를 포함한다.
- bullet이 종결되지 않았고 다음 줄이 새 구조 경계가 아니면 wrapped continuation으로 결합한다.
- 새 bullet, 번호, heading, 표 경계는 continuation보다 우선한다.
- orphan fragment는 인접 chunk와 자동 결합하지 않는다.
- Q002 chunk ID나 진정간호 특정 문구는 production 코드에 넣지 않았다.

PromptCoverage의 기존 23-unit 계약은 이전 segmentation 기준으로 유지하고, Answer source-unit catalog에만 개선된 완전 단위를 사용한다.

### SourceUnit catalog와 eligibility

요청별 catalog는 다음 값을 가진다.

- `source_unit_id`: `su001` 형식의 짧은 요청 로컬 ID
- `chunk_id`
- `source_order`: chunk index와 unit position
- `branch`
- `exact_text`
- `group_key`
- `required`
- `selectable`

완전한 문장·독립 행만 selectable이다. 문서/section heading, caption, 단계 표지, 비종결 fragment와 이전 chunk의 overlap orphan은 non-selectable로 유지한다. 삭제하지 않으므로 prompt 문맥과 구조 해석에는 사용할 수 있다.

### Compact catalog v3

`PROMPT_EVIDENCE_SCHEMA_VERSION`을 3으로 올렸다. Group 수준에는 `required`, `branch`와 공통 document/page/section/location을 한 번만 기록하고, source 수준에는 chunk와 순서 및 필요한 metadata override를 둔다. 각 unit에는 ID, selectable 여부와 exact text만 전송한다.

Selected evidence 12 chunks 외 원문은 추가하지 않았다. LLM은 원문이나 citation을 다시 생성하지 않고 다음 두 필드만 반환할 수 있다.

```json
{
  "answerable": true,
  "selected_source_unit_ids": ["su002", "su003"]
}
```

Strict schema는 추가 속성을 금지하며 selection 최대치는 16이다.

### Validation과 reconstruction

다음 오류는 모두 fail closed로 `AI_EVIDENCE` 처리한다.

- unknown ID
- duplicate ID
- non-selectable ID
- 16개 초과
- answerable과 ID 목록 불일치
- required group 또는 branch 누락
- 질문에 명시된 action 누락
- source order 역전

자동 정렬, dedup, ID 보정과 coverage 보충은 하지 않는다. 정상 선택은 서버 catalog에서 다음처럼 복원한다.

- `Statement.text = SourceUnit.exact_text`
- `Evidence.chunk_id = SourceUnit.chunk_id`
- `Evidence.quote = SourceUnit.exact_text`
- `label = ""`
- `format`과 `conflict`는 서버의 QueryPlan 및 conflict 검사 결과 사용

복원 후 기존 `validate_answer()`를 그대로 다시 실행한다. Exact citation, 완전 문장, number, unit, action, condition/negation, source order, branch/coverage와 answerable/statements 계약을 삭제하거나 완화하지 않았다.

### 버전과 한도

| 항목 | 결과 |
|---|---:|
| `PROMPT_EVIDENCE_SCHEMA_VERSION` | 3 |
| `AI_VERSION` | 13 |
| Source Unit selection 최대 | 16 |
| Public Answer statements 최대 | 16 |
| `OUTPUT_LIMIT` | 2048, 유지 |
| `GROQ_REQUEST_TOKEN_BUDGET` | 5120, 유지 |
| `SEARCH_VERSION` | 12, 유지 |
| `CHUNK_VERSION` | 4, 유지 |

Selection/statement 상한을 10에서 16으로 바꾼 이유는 사람이 검수한 Q002 required gold의 의미적으로 완전한 최소 표현이 14 units이기 때문이다. Unit을 억지로 병합하거나 gold를 줄이지 않고 2개 여유를 둔 값이다.

## 3. Q002 gold fixture

`tests/fixtures/q002_gold_stages.json`은 평가 전용 schema version 2로 갱신했다.

- Required gold stage: 10개
- 개선 segmentation에서 필요한 source units: 14개
- Mapping: `chunk_id + source_unit_position`
- Drift 검증: `exact_text` SHA-256 fingerprint
- Production 코드의 fixture 접근: 없음

Mock 실행에서 모든 fingerprint가 일치했고 required source-unit gold recall은 10/10이었다.

## 4. MockTransport 결과

| 검증 항목 | 결과 |
|---|---:|
| selected evidence | 12 chunks |
| segmentation raw units | 40 |
| selectable units | 21 |
| PromptCoverage required input units | 23 |
| selected source units | 14 |
| pre-budget required gold recall | 10/10 |
| post-budget required gold recall | 10/10 |
| source-unit gold stage recall | 10/10 |
| required groups | 5, 모두 유지 |
| required branches | adult, pediatric 유지 |
| parent partial inclusion | 0건 |
| source order 역전 | 0건 |
| reconstructed text exact match | 100% |
| reconstructed quote exact match | 100% |
| citation coverage | 100% |
| 기존 citation validation | `supported` |
| response parse stage | `complete` |
| failure code / reason | 없음 |
| Q002 MockTransport | 1회 |
| Q006 catalog / transport | 0건 / 0회 |

Negative Mock에서는 unknown, duplicate, non-selectable, limit 초과, answerable 불일치, group/branch 누락, source order 역전과 strict schema 외 필드를 모두 안전 차단했다. 기존 잘못된 citation, number, unit, unsupported action과 procedure order 검증도 유지됐다.

## 5. Budget 결과

| 항목 | 결과 |
|---|---:|
| 실제 계산 reservation | 4497 |
| admission cap | 5120 |
| headroom | 623 |
| 최소 요구 headroom | `max(256, ceil(4497 × 8%)) = 360` |
| 판정 | 통과 |

`Quota.reserve()`에는 cap 5120이 아니라 실제 계산 reservation 4497이 전달됐다.

## 6. 전체 테스트와 정적 검사

- Source Unit 집중 테스트: 15 passed
- 최종 전체 테스트: 282 passed, 1 skipped
- Ruff 전체 검사: 통과
- `git diff --check`: 공백 오류 없음
- 경고: 기존 FastEmbed MiniLM pooling 기본값 변경 안내 2건. 이번 변경과 무관하며 embedding 설정은 수정하지 않았다.

코드 검수에서 Q002 ID나 진정간호 문구의 production 하드코딩, validator 완화, retrieval 변경, retry/fallback/web search 추가는 발견되지 않았다. Compact catalog에 required group 표시가 빠지는 문제는 live 전에 발견해 보완하고 테스트로 고정했다.

## 7. 실제 Groq 단일 평가

### 호출 및 envelope

| 항목 | 결과 |
|---|---|
| Q006 실제 호출 | 0회 |
| Q002 실제 호출 | 정확히 1회 |
| HTTP status | 200 |
| 반환 모델 | `openai/gpt-oss-20b` |
| finish reason | `stop` |
| prompt tokens | 2326 |
| completion tokens | 104 |
| total tokens | 2430 |
| latency | 938.12 ms |
| response parse stage | `validation` |
| failure code | `AI_EVIDENCE` |
| validation reason | `selection_branch` |

Output truncation이나 strict envelope parsing 실패는 없었다. 모델 응답은 selection-only schema를 통과한 뒤 AnswerCoverage의 required branch 검사에서 차단됐다.

### Evidence 불변성

- Selected evidence: 기존 12 chunks
- Pre-budget required gold recall: 10/10
- Post-budget required gold recall: 10/10
- 문서 전체 또는 다른 chunk 전송: 없음

### 최종 Answer 판정

| 기준 | 결과 | 판정 |
|---|---|---|
| `response_parse_stage=complete` | `validation` | 실패 |
| failure code 없음 | `AI_EVIDENCE` | 실패 |
| answerable | false | 실패 |
| verified statements 1~16 | 0 | 실패 |
| exact citation / coverage | 복원 전 차단 | 미평가 |
| source order | 최종 Answer 없음 | 실질 미평가 |
| 근거 없는 숫자·단위·조건·부정·행동 | 최종 Answer 없음 | 미평가 |

`selection_branch`는 required adult/pediatric branch 중 하나 이상이 모델 선택에 없었다는 고정 실패 사유다. 구체 선택 ID와 raw content를 저장하지 않았으므로 더 좁혀 추정하지 않는다.

## 8. 보안 및 실행 제약

- API key 및 Authorization header 미기록
- 전체 prompt 미저장
- raw response/message content/refusal 원문 미저장
- selected 12 chunks 외 원문 미전송
- retry 없음
- fallback 없음
- web search 없음
- Q002 이후 추가 실제 호출 없음
- BM25, embedding, RRF, reranker와 evidence selection 미변경

## 9. 산출물

최종 Mock:

`artifacts/2026-09-14_rag-source-unit-selection-mock-02/`

- `mock_report.json`
- `review.html`

실제 단일 평가:

`artifacts/2026-09-14_rag-source-unit-selection-live/`

- `live_report.json`
- `review.html`

기존 artifacts는 덮어쓰지 않았다.

## 10. 작업 중지 및 승인 대기

승인된 구현, Mock, 전체 회귀와 실제 Q002 단일 호출까지 완료했다. Live 실패를 임의로 보정하지 않았고 추가 호출도 하지 않았다.

다음 단계에서는 `selection_branch`가 생긴 모델 선택 정책을 별도로 분석해야 한다. Branch를 서버가 자동 보충하거나 validator를 완화해서는 안 된다. Prompt/catalog의 branch 선택 명확성, 계층적 group→unit selection 또는 selection schema에서 branch별 ID 묶음을 요구하는 대안을 별도 설계·승인한 뒤 검토한다.

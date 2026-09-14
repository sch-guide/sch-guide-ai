# Source Unit ID Selection 구현 전 가능성 검증 결과

- 검증일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/16_SOURCE_UNIT_SELECTION_PLAN.md` D안
- 검증 방식: 현재 Q002 fixture와 `source_sentences()` 읽기 전용 분석
- 실제 Groq 호출: 0회
- production 코드 수정: 0개
- 상태: **구현 중단 — 승인 조건 간 충돌**

## 1. 결론

승인 조건에 따라 production 구현 전에 “Q002 required evidence를 최대 10개의 source unit ID로 표현할 수 있는가”를 먼저 검증했다.

현재 Q002 selected evidence 12 chunks는 `source_sentences()` 기준 원시 source unit 43개다. 현재 운영 `ProcedureCoverage`는 required groups 5개 안에서 `required_procedural_unit_keys` 23개를 생성한다. 승인된 selection validator는 required unit 누락을 fail closed해야 하고 응답은 최대 10개 ID만 허용한다.

따라서 **23개의 현재 required procedural units를 모두 유지하면서 최대 10개 source unit ID로 응답하는 것은 불가능하다.** 정상 selection도 항상 required coverage 누락으로 차단된다.

사용자가 명시한 중단 조건에 따라 ID 제한을 늘리거나 unit을 병합하거나 required unit을 임의로 optional로 바꾸지 않았다. SourceUnit 타입, catalog v3, provider schema와 generation 코드는 구현하지 않았다.

## 2. 확인된 수치

| 항목 | 결과 |
|---|---:|
| Q002 selected chunks | 12 |
| 원시 source units | 43 |
| required groups | 5 |
| 현재 required procedural units | 23 |
| optional procedural units | 0 |
| required branches | adult, pediatric |
| Q002 required gold stages | 10 |
| Gold 10/10에 필요한 최소 distinct chunks | 10 |
| 승인된 최대 selection | 10 source unit IDs |

### Required group별 분포

| Branch | Chunks | Raw source units | Required procedural units |
|---|---:|---:|---:|
| common | 1 | 2 | 1 |
| common | 1 | 2 | 2 |
| common | 1 | 1 | 1 |
| adult | 4 | 16 | 7 |
| pediatric | 5 | 22 | 12 |
| 합계 | 12 | 43 | 23 |

현재 required unit은 `mvp/evidence.py`의 `_procedure_unit_keys()`가 required group 안에서 `PROCEDURE_ACTION`과 일치하는 모든 source sentence에 부여한다.

## 3. Gold 10/10과 source-unit coverage의 차이

Q002 gold fixture의 required stage는 10개이며 각 stage는 `allowed_chunk_ids`에 연결돼 있다. Selected 12 chunks 안에서 brute-force로 계산하면 required gold 10/10을 충족하는 데 최소 10개의 서로 다른 Chunk가 필요하다.

그러나 현재 fixture는 exact source-unit ID나 Chunk 내부 sentence position을 gold stage에 연결하지 않는다. 따라서 각 Chunk에서 임의로 source unit 하나를 고른 10개 ID가 실제 10개 gold stage 내용을 모두 지지한다고 증명할 수 없다.

다음을 구분해야 한다.

- Chunk-level gold recall: 10개 Chunk 선택으로 10/10 표현 가능
- Source-unit-level gold recall: 현재 mapping 부재로 확정 불가
- 운영 structural required-unit coverage: 현재 23개라 최대 10 ID로 불가능

Chunk-level 수치만 이용해 required source units를 10개로 축소하면 운영 coverage 정책을 임의 변경하는 것이므로 수행하지 않았다.

## 4. 충돌하는 승인 조건

동시에 만족할 수 없는 조건은 다음과 같다.

1. 응답 ID 최대 10개
2. required procedural unit 누락은 fail closed
3. 현재 `ProcedureCoverage.required_procedural_unit_keys` 유지
4. unit 병합 금지
5. 제한 임의 증가 금지

현재 required units가 23개이므로 1번과 2~5번을 동시에 충족할 수 없다.

## 5. 수행하지 않은 작업

- SourceUnit production 타입 추가
- catalog builder 및 eligibility 구현
- compact source-unit schema v3 구현
- Groq provider response schema 변경
- server reconstruction 구현
- `AI_VERSION` 또는 prompt schema version 변경
- system prompt 변경
- validator, `source_sentences()` 또는 retrieval 변경
- 전체 테스트와 Ruff 실행

코드 변경이 없으므로 구현 후 테스트 단계로 진행하지 않았다. 기존 정상 테스트 상태와 승인된 RAG/BM25 구현은 그대로 유지된다.

## 6. 다음 승인이 필요한 설계 결정

진행하려면 다음 중 하나를 별도 설계·승인해야 한다.

### 대안 1: source-unit별 Q002 gold mapping 추가

사람이 Q002의 각 required stage를 정확한 `(chunk_id, sentence_position)`에 연결한다. 이를 평가 fixture에서만 사용해 최대 10 units로 gold 10/10이 실제 가능한지 다시 검증한다. 운영 코드에는 Q002 ID나 단계명을 넣지 않는다.

이 대안만으로 운영의 23개 required-unit 정책은 해결되지 않으므로, selection 후 운영 충분성을 어떤 구조 신호로 판정할지도 함께 설계해야 한다.

### 대안 2: “prompt required evidence”와 “answer required units” 분리

현재 required group/unit은 LLM에 빠짐없이 전달해야 하는 prompt coverage다. 이를 최종 답변이 모든 unit을 선택해야 한다는 의미와 분리한다. 최종 selection은 required groups/branches를 대표하고 질문에 필요한 action units를 선택하되, 일반 질문에서 임상적 완전성을 추론하지 못하면 abstain한다.

이는 coverage 의미를 바꾸는 중요한 정책 변경이므로 현재 승인 범위에서 임의 적용하지 않는다.

### 대안 3: 응답 한도 또는 선택 단위 변경

- 최대 ID 수를 23 이상으로 늘림
- source unit이 아니라 원자 EvidenceGroup ID를 선택
- 계층형 group → unit selection 사용

사용자가 금지한 제한 증가 또는 unit 병합과 관련될 수 있으므로 별도 설계가 필요하다.

## 7. 권고

우선 **대안 1과 2를 함께 설계**할 것을 권고한다.

1. 평가 fixture에 source-unit-level gold mapping을 추가해 Q002 최대 10개 가능성을 사람 검수로 확정한다.
2. 현재 23개 `required_procedural_unit_keys`는 prompt 입력 완전성용인지 답변 출력 완전성용인지 의미를 분리한다.
3. 운영 답변 충분성은 gold를 하드코딩하지 않고 group/branch 및 질문별 구조 신호로 보수적으로 판단한다.
4. 이 정책이 승인된 뒤 SourceUnit D안 구현을 다시 시작한다.

## 8. 산출물 및 작업 중지

읽기 전용 feasibility 결과는 새 디렉터리에 저장했다.

`artifacts/2026-09-13_rag-source-unit-selection-feasibility/`

- `feasibility_report.json`
- `review.html`

실제 Groq 호출은 0회이며 기존 artifacts를 덮어쓰지 않았다. 승인 조건에 따라 이 지점에서 작업을 중지하고 다음 설계 결정을 기다린다.

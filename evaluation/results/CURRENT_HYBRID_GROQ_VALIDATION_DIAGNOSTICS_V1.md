# Current Hybrid + Groq Validation Diagnostics v1

분석일: 2026-09-18. 저장된 `bm25_answer_diagnostics_groq_v1.json`과 현재 검증·진단 코드만 읽었다. 평가, 검색, API 호출, 검증 재실행을 하지 않았으며 기존 JSON·코드·설정을 수정하지 않았다.

## 1. 문항별 결과

`validation_reason`은 post-LLM 검증기의 필드다. 호출 전 차단 문항에는 이 필드 자체가 없으므로 다른 reason을 대신 채우지 않았다.

| ID | 최종 status | validation_reason | API 호출 | validation_failure |
| --- | --- | --- | --- | --- |
| TRF-001 | abstained | 필드 없음 | false | 없음 |
| TRF-002 | abstained | 필드 없음 | false | 없음 |
| TRF-003 | abstained | 필드 없음 | false | 없음 |
| TRF-004 | abstained | 필드 없음 | false | 없음 |
| TRF-005 | abstained | 필드 없음 | false | 없음 |
| TRF-006 | abstained | unsupported sentence | true | 있음 |
| TRF-007 | abstained | 필드 없음 | false | 없음 |
| TRF-008 | abstained | unsupported sentence | true | 있음 |
| TRF-009 | abstained | 필드 없음 | false | 없음 |
| TRF-010 | abstained | 필드 없음 | false | 없음 |

TRF-001~005, 007, 009~010은 모두 다음 값이 유지된다.

```text
stage=before_llm
pre_llm_assessment=incomplete_semantic_block
block_reason=pre_llm:incomplete_semantic_block
llm_called=false
answerable=false
```

8건은 모델이 거절한 것이 아니라 모델 호출 이전의 근거 검사에서 종료됐다. 나머지 2건은 응답 후 검증에서 거절됐다. 실제 저장 reason은 밑줄이 없는 `unsupported sentence`다.

## 2. TRF-006 상세

### 저장된 값

| 항목 | 실제 값 |
| --- | --- |
| Groq 응답 생성 | 예: llm_called=true이며 생성된 statement가 검증됨 |
| stage | citation_validation |
| post_llm_validation_stage | sentence_matching |
| pre_llm_assessment / budget_assessment | supported / supported |
| validation_reason | unsupported sentence |
| block_reason | invalid_citation_or_statement |
| statement_index | 1 |
| sentence_index | 1 |
| label | 활력징후 및 부작용 확인 시점 |
| failure_type | sentence_not_in_source |
| text_redacted | false |

`statement_text`와 `sentence_text`는 동일하다.

> 수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰

evidence 진단은 다음과 같다.

```json
{
  "evidence_index": 1,
  "chunk_id": null,
  "document_id": null,
  "chunk_found": true,
  "quote_in_chunk": true,
  "sentence_in_chunk": false,
  "sentence_in_quote": true
}
```

순번은 1부터 시작하며 statement_index는 답변 항목 순번, sentence_index는 그 항목 안에서 나눈 문장 순번이다.

### 근거 본문 대조

저장된 prompt 선택 목록에는 p.5 청크 `8beba164-15a2-4318-b105-390b2037eceb`가 있고 다음 문구를 포함한다.

> (수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰)

답변과 이 원문 문구의 차이는 바깥 괄호다. `source_sentences()`는 바깥 괄호를 제거하지 않으며 `sentence_evidence()`는 답변 문장이 원문 문장 목록의 원소와 일치하기를 요구한다. 원문의 부분 문자열인 것만으로 통과하지 않는다.

`sentence_in_quote=true`, `quote_in_chunk=true`는 답변 텍스트가 실제 quote 안에 있고 quote도 원문에 있음을 보여 준다. 따라서 `sentence_not_in_source`라는 이름을 “실제 근거에 없는 내용”으로 해석하면 안 된다. 여기서는 **원문 문장 단위 완전일치 실패**라는 뜻이다.

판정: **B — 근거는 존재하지만 문장 단위 매칭에 실패.** 저장 본문 대조상 괄호 제거가 직접적인 불일치다. A(근거 없는 내용 생성)나 D(여러 문장 병합)는 확인되지 않는다.

label도 기록되어 있지만 이번 실패 유형은 `label_not_in_quote`가 아니다. 문장 검사에서 먼저 실패했기 때문에 label의 최종 적합성은 별도 확인 대상이다. 괄호 문제만 해결하면 전체 답변이 반드시 통과한다고 단정할 수 없다.

주의: 위 p.5 ID는 선택된 context의 본문을 대조해 찾은 해당 문구의 위치다. 실패 evidence의 ID 자체는 null이므로 trace에 해당 ID가 기록됐다고 주장하지 않는다.

## 3. TRF-008 상세

### 저장된 값

| 항목 | 실제 값 |
| --- | --- |
| Groq 응답 생성 | 예: llm_called=true이며 생성된 statement가 검증됨 |
| stage | citation_validation |
| post_llm_validation_stage | sentence_matching |
| pre_llm_assessment / budget_assessment | supported / supported |
| validation_reason | unsupported sentence |
| block_reason | invalid_citation_or_statement |
| statement_index | 1 |
| sentence_index | 1 |
| label | 빈 문자열 |
| failure_type | sentence_not_in_source |
| text_redacted | false |

`statement_text`와 `sentence_text`는 동일하다.

> 적혈구, 혈소판 혈액제제에 방사선 조사를 시행한다.

```json
{
  "evidence_index": 1,
  "chunk_id": null,
  "document_id": null,
  "chunk_found": true,
  "quote_in_chunk": true,
  "sentence_in_chunk": false,
  "sentence_in_quote": false
}
```

### 근거 본문 대조

선택된 p.4 청크 `f80c5748-1377-4b00-9353-7c5cb830a35e`에는 다음 구조가 있다.

```text
(3) 대상혈액
적혈구, 혈소판 혈액제제
(4) 원칙
```

같이 선택된 p.4의 다른 청크에는 상위 항목 “2) 방사선조사 혈액제제”가 있다. 모델 문장은 이 항목형 정보를 “...에 방사선 조사를 시행한다.”라는 완성형 서술문으로 표현했다. 해당 완성형 문장은 저장된 선택 context에 그대로 존재하지 않는다.

이것은 추출형 답변을 요구하는 현행 Prompt/validator의 계약과 다르다. `sentence_in_quote=false`이므로 모델 문장 전체가 자신이 제시한 quote에도 포함되지 않는다. 다만 실제 quote 텍스트와 연결 ID가 보존되지 않아 어떤 항목을 정확히 quote했는지는 복원할 수 없다.

판정: **B — 관련 근거는 있으나 생성 문장이 원문 그대로가 아니어서 매칭 실패.** 모델이 추출형 계약 대신 서술문을 생성한 문제가 함께 확인된다. **A(임상적으로 근거 없는 사실 생성)는 입증되지 않는다.** C의 label 문제는 label이 빈 문자열이므로 아니다. quote에 문장이 없다는 연결 불일치는 관측됐지만 `citation_mismatch`가 기록된 것은 아니며, quote 자체는 실제 청크에 존재한다. D의 과도한 문장 분할·병합도 이번 trace로 입증되지 않는다.

이전 Gemini API 오류와 달리 이번에는 API 응답 이후의 로컬 문장 검증 실패다.

## 4. 원인 분류 요약

| 분류 | TRF-006 | TRF-008 |
| --- | --- | --- |
| A. 실제 근거 없는 내용 생성 | 근거 없음: 문구가 quote 안에 존재 | 입증 안 됨: 대상혈액 근거는 존재 |
| B. 근거가 있으나 문장 매칭 실패 | 확인: 괄호 포함 원문과 완전일치 실패 | 확인: 항목형 원문을 서술문으로 바꿈 |
| C. citation/label 연결 문제 | 직접 원인으로 기록되지 않음; label 후속 판정 미확인 | sentence_in_quote=false는 확인; label은 비어 있음. 별도 citation 오류는 아님 |
| D. 과도한 문장 분할/병합 | 입증 안 됨 | 입증 안 됨 |
| E. 기타 | 진단 ID가 과도한 필터로 null | 동일한 진단 ID 손실 |

분류 B는 validator에 반드시 버그가 있다는 뜻이 아니다. 현재 완전일치 정책이 의도대로 동작한 결과와 모델의 추출형 출력 계약 위반을 구분해야 한다. 문맥상 타당해 보인다는 이유만으로 현행 검증을 우회할 근거는 없다.

## 5. 진단 trace 검수

### 정상 기록된 부분

- 호출된 두 문항 모두 post_llm_validation_stage, validation_reason, statement/sentence 순번, 원문, label, failure_type, 비교 결과 boolean이 기록됐다.
- 두 문항 모두 `diagnostic_unavailable`이 아니다.
- 사전 차단 8문항에 validation_failure가 없는 것은 정상이다. post-LLM 검증 자체를 수행하지 않았다.
- 사용자용 결과는 여전히 고정 거절 답변이고 내부 실패 문장은 별도 generation_trace에 남았다.

### 결함: evidence 식별정보가 보존되지 않음

두 문항의 chunk_id와 document_id가 모두 null이다. `chunk_found=true`이므로 lookup 실패가 아니다.

현재 `mvp/validation_trace.py`의 `safe_text()`는 `[A-Za-z0-9_\-]{20,}`을 비밀 토큰 후보로 보고 원문을 제거한다. 정상 UUID도 36자의 영숫자·하이픈 조합이므로 이 패턴에 걸린다. `record_failure()`가 내부 chunk/document ID에도 같은 자유 텍스트 필터를 적용하여 ID까지 제거한 것이 원인이다.

따라서 진단 보강은 **부분적으로 정상 작동**했다. 실패 문구 확인 목적은 달성했지만 정확한 citation 연결 추적은 불완전하다. `text_redacted=false`는 statement_text에 대한 값이며 evidence ID까지 비공개 처리되지 않았다는 뜻이 아니다.

### 민감정보 점검

현재 mvp/.env와 프로세스 환경의 KEY/TOKEN/SECRET/PASSWORD 관련 값 중 8자 이상인 값을 출력하지 않고 전체 JSON과 대조했으며 일치가 검출되지 않았다. Authorization 헤더 및 Bearer 인증값 패턴도 검출되지 않았다. 이번에 새로 저장된 두 실패 문장과 label에서는 환자 식별정보가 관찰되지 않았다.

이는 검사한 값·패턴 및 실제 진단 내용에 대한 결과다. 임의의 모든 개인정보를 자동으로 탐지했다는 보장은 아니다. API 키 실제 값이나 환경변수 전체를 이 보고서에 기록하지 않았다.

## 6. pooling warning

직접 원인으로 기록된 근거가 없다. 두 post-LLM 실패는 `sentence_matching`, 나머지 8건은 `pre_llm:incomplete_semantic_block`이다. **pooling warning의 직접 영향 확인 안 됨.** 경고를 이번 실패 원인으로 분류하지 않는다.

## 원본 보존

입력 JSON SHA-256: `dc014bd48bffe1bf1aefc2998d890fff76b006cb50ca19d55542b0b989f1e1b1`.

이번 작업은 이 보고서만 추가했다. 기존 결과·분석 보고서·검증 기준은 보존했다.

## TRF-006 실패 원인

근거의 괄호 안 문구에서 바깥 괄호를 제거한 답변이 원문 문장 단위 완전일치 검사에 실패했다. 내용은 quote에 존재하며, 실제 기록은 `sentence_not_in_source`, `sentence_in_quote=true`다. 없는 근거를 만들어냈다고 볼 수 없다.

## TRF-008 실패 원인

“대상혈액 / 적혈구, 혈소판 혈액제제”라는 항목형 근거를 “적혈구, 혈소판 혈액제제에 방사선 조사를 시행한다.”로 서술했다. 생성된 문장 전체는 원문 문장이나 quote와 일치하지 않았다. 현행 추출형 출력 계약 위반이며 임상적 허위라는 판정은 아니다.

## LLM 생성 문제인가, validator 문제인가?

**모델 출력 형태와 원문 완전일치 검증 계약 사이의 불일치다.** TRF-006은 괄호 처리, TRF-008은 항목의 서술문 전환이 관찰됐다. 현재 validator는 그 엄격한 규칙대로 거절했다. validator의 임상적 오류나 provider 성능 문제로 단정할 근거는 없다. 별도로 진단 코드에는 정상 UUID를 가리는 결함이 확인됐다.

## 다음에 수정해야 할 한 가지

**진단 trace의 정상 내부 UUID 보존부터 수정하는 것을 제안한다.** 실제 sources에서 확인된 chunk/document ID에 한해 UUID 형식을 검증해 기록하고, 모델이 임의로 낸 식별자와 자유 텍스트에는 기존 비공개 처리를 유지하는 방식이다. 답변 허용 기준을 바꾸지 않고도 정확한 인용 연결을 복원할 수 있다. 그다음 괄호·항목형 원문의 처리 계약을 실패 사례 기반으로 검토해야 한다. 이번에는 어느 것도 수정하지 않았다.

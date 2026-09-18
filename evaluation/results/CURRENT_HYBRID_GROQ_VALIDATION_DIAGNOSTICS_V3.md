# Current Hybrid + Groq Validation Diagnostics v3

분석일: 2026-09-18. 입력은 `bm25_answer_diagnostics_groq_v3.json` 하나뿐이다. 코드·과거 결과를 추가로 읽거나 실행하지 않았다. 검색·API·validator·Baseline 재실행 및 기존 파일 수정은 수행하지 않았다.

## 1. 문항별 실패 경로

모든 최종 status는 `abstained`다. “필드 없음”은 null이나 임의 추정값으로 대체하지 않은 실제 기록 상태다.

| ID | 중단 단계 | final_validation_stage | final_validation_reason | Groq 호출 여부 |
| --- | --- | --- | --- | --- |
| TRF-001 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |
| TRF-002 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |
| TRF-003 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |
| TRF-004 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |
| TRF-005 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |
| TRF-006 | post-LLM / citation_validation | label_matching | label_not_in_quote | true |
| TRF-007 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |
| TRF-008 | post-LLM / citation_validation | sentence_matching | unsupported sentence | true |
| TRF-009 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |
| TRF-010 | pre-LLM / before_llm | 필드 없음 | 필드 없음 | false |

### A. pre-LLM 차단: 8건

TRF-001, TRF-002, TRF-003, TRF-004, TRF-005, TRF-007, TRF-009, TRF-010.

전부 `pre_llm_assessment=incomplete_semantic_block`, `block_reason=pre_llm:incomplete_semantic_block`, `answerable=false`다. Groq가 답변을 생성하지 않았으며 post-LLM 최종 판정 필드가 없는 것이 자연스럽다.

### B. post-LLM 차단: 2건

TRF-006, TRF-008. 두 문항 모두 사전·예산 근거 검사는 `supported`이고 `llm_called=true`다. 응답을 받은 뒤 `citation_validation`에서 `block_reason=invalid_citation_or_statement`로 끝났다. 최종 거절 문구는 모델이 스스로 답변을 거절했다는 뜻이 아니다.

## 2. TRF-006 상세

아래 값은 JSON에 실제 저장되어 있다.

```json
{
  "statement_index": 1,
  "exact_match_passed": false,
  "normalization_attempted": true,
  "normalization_passed": true,
  "label_validation_attempted": true,
  "label_validation_passed": false,
  "final_validation_stage": "label_matching",
  "final_validation_reason": "label_not_in_quote",
  "matched_source_text": [
    "(수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰)"
  ]
}
```

검증된 첫 statement 원문:

> 수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰

실패한 label:

> 활력징후 및 부작용 확인 시점

본문 완전일치는 실패했지만 normalization을 시도해 통과했다. 이제 이는 추론이 아니라 `normalization_passed=true`와 `matched_source_text`로 직접 확인된다. 다음 label 검사에서 label이 quote에 포함되지 않아 거절됐다.

`validation_failure.failure_type=label_not_in_quote`, `post_llm_validation_stage=label_matching`도 같은 결론을 가리킨다. sentence_index와 sentence_text가 null인 것은 문장 자체를 실패 대상으로 지정하지 않았기 때문이다. statement_index=1은 유지된다.

기존 상위 `validation_reason=unsupported sentence`는 여전히 남아 있지만 상세 최종 reason은 `label_not_in_quote`다. 상위 값만 읽어 본문 normalization이 실패했다고 해석하면 안 된다. 본문과 label을 모두 포함한 첫 statement 전체는 통과하지 못했다. 이후 statement의 성공 여부는 기록되지 않았다.

## 3. TRF-008 상세

```json
{
  "statement_index": 1,
  "exact_match_passed": false,
  "normalization_attempted": true,
  "normalization_passed": false,
  "matched_source_text": [],
  "label_validation_attempted": false,
  "label_validation_passed": null,
  "final_validation_stage": "sentence_matching",
  "final_validation_reason": "unsupported sentence"
}
```

처음 실패한 statement와 sentence의 순번은 모두 1이다. 두 원문 필드도 동일하다.

> 방사선 조사는 적혈구제제와 혈소판 혈액제제에 시행된다.

label은 빈 문자열이다. `failure_type=sentence_not_in_source`, `sentence_in_chunk=false`, `sentence_in_quote=false`다. 반면 `chunk_found=true`, `quote_in_chunk=true`이므로 quote 자체가 존재하지 않거나 청크 lookup에 실패한 경우는 아니다.

normalization은 실제 시도됐으나 매칭 결과를 얻지 못했다. 따라서 label 검사에는 도달하지 않았고, null을 label 검증 실패로 해석해서는 안 된다.

### 사실의 근거가 존재하는가?

같은 v3 JSON의 선택된 context에 다음 근거가 있다.

- p.4, `3a433228-72ff-4366-b91b-04b2232bc166`: “2) 방사선조사 혈액제제”라는 상위 항목.
- p.4, `f80c5748-1377-4b00-9353-7c5cb830a35e`:

```text
(3) 대상혈액
적혈구, 혈소판 혈액제제
```

따라서 방사선 조사 대상혈액에 관한 관련 사실은 선택 context에 존재한다. 다만 모델은 “적혈구”를 “적혈구제제”로 표현하고 “...에 시행된다”라는 서술문으로 바꿨다. 모델 문장 전체가 원문/quote와 일치한다는 기록은 없으며 normalization도 실패했다.

이 사실만으로 임상적으로 거짓인 내용을 생성했다고 단정할 수 없다. 반대로 이 JSON에는 normalization 내부의 실패 규칙·section metadata·실제 quote 전체가 없으므로 정확히 어떤 세부 규칙이 거절했는지도 특정할 수 없다. 확정되는 것은 **첫 statement가 원문 완전일치와 normalization 양쪽 모두에서 매칭되지 않았다는 것**이다.

근거 ID는 retrieved_contexts에서 확인한 위치다. validation_failure 안의 chunk_id/document_id는 null이므로 실제 실패 citation의 ID를 확정적으로 복원한 것은 아니다.

## 4. trace 일관성 검증

v3 JSON 내에서 다음을 확인했다.

1. TRF-006의 최상위 결과와 statement_validation[0]이 동일하다: exact 실패 → normalization 성공 → label 실패.
2. TRF-006의 final_validation_stage, post_llm_validation_stage, validation_failure.failure_type이 모두 label 실패를 가리킨다. normalization 성공을 다시 sentence_not_in_source로 기록한 모순은 이 문항에 없다.
3. TRF-008은 최상위 결과와 statement_validation[0]이 동일하다: exact 실패 → normalization 실패 → label 미시도.
4. TRF-008의 final_validation_stage 및 failure_type도 문장 매칭 실패로 일관된다.
5. 사전 차단 문항에는 post-LLM 판정 기록이 없다.

**이번 두 사례에서 trace 내부의 판정 경로는 일관되며, TRF-006의 본문 성공과 label 실패가 구분되어 기록됐다.** 다만 JSON만으로 코드 내부에 독립 재판정이 전혀 남아 있지 않다는 구현 수준의 보증은 할 수 없다. 요청에 따라 코드를 읽거나 재실행하지 않았으므로, 확인 범위는 저장된 실행 기록의 일관성이다.

또한 비교 evidence의 chunk_id/document_id는 여전히 null이며 이 진단 한계는 남아 있다. label 실패의 실제 quote 전체도 저장되지 않았다. 현재 기록으로 확인 가능한 단계와 확인 불가능한 세부 정보를 구분해야 한다.

## 보존 확인

입력 SHA-256: `4e6e63bc0e56b4198b8fc48d101afc1518a07fb4e3a0cbda2d24f2ecb1a7a979`.

이번 산출물은 이 보고서 한 개다. 결과 JSON·코드·기존 보고서를 변경하지 않았다.

## pre-LLM 차단 문항

TRF-001~005, TRF-007, TRF-009~010: 8건 모두 pre_llm:incomplete_semantic_block. Groq 호출 없음.

## post-LLM 차단 문항

TRF-006과 TRF-008: 2건. Groq 호출 및 응답 이후 로컬 검증에서 차단.

## TRF-006의 정확한 실패 원인

본문 normalization은 성공했다. 그 다음 `활력징후 및 부작용 확인 시점`이라는 label이 quote에 없어 `label_matching / label_not_in_quote`로 첫 statement가 거절됐다.

## TRF-008의 정확한 실패 원인

첫 문장 `방사선 조사는 적혈구제제와 혈소판 혈액제제에 시행된다.`가 exact matching과 normalization 모두에서 실패했다. 관련 대상혈액 사실은 context에 존재하지만 현재 기록상 매칭되지 않았다. label 실패가 아니다.

## 현재 가장 큰 병목

전체 문항 수 기준으로는 8/10의 pre-LLM semantic block 완전성 차단이다. post-LLM에서는 label 검증과 항목형 근거의 서술문 매칭이 서로 다른 병목으로 남아 있다.

## 다음에 실제로 수정해야 할 1순위

현재 진행 중인 post-LLM 문제에서는 **TRF-006의 부가 label 처리**를 우선 대상으로 제안한다. 본문과 인용이 검증된 경우, 답변 사실이 아닌 표시용 label 때문에 전체 답변을 버릴지, 지원되지 않는 부가 label만 제거할지를 명시적인 정책과 회귀 테스트로 결정해야 한다. label의 조건·숫자·부정 등 의미 있는 내용을 무조건 삭제하거나 모든 label을 허용하는 방식은 피해야 한다. 이는 이제 명확한 trace로 확인되는 가장 좁은 수정 후보이며, 전체 성능의 가장 큰 병목인 pre-LLM 8건과는 별도 과제다. 이번에는 수정하지 않았다.

# Current Hybrid + Groq Validation Diagnostics v2 변화 분석

분석일: 2026-09-18. 진단 v1/v2 JSON 및 현재 검증·normalization·trace 코드를 읽었다. 검색, API, Baseline, validator를 재실행하지 않았다. 기존 코드·JSON·보고서를 수정하지 않았다.

## 1. 문항별 v2 결과

| ID | 최종 status | validation_reason | 중단 단계 | Groq 호출 |
| --- | --- | --- | --- | --- |
| TRF-001 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |
| TRF-002 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |
| TRF-003 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |
| TRF-004 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |
| TRF-005 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |
| TRF-006 | abstained | unsupported sentence | post-LLM: citation_validation | 예 |
| TRF-007 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |
| TRF-008 | abstained | unsupported sentence | post-LLM: citation_validation | 예 |
| TRF-009 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |
| TRF-010 | abstained | 필드 없음 | pre-LLM: incomplete_semantic_block | 아니오 |

TRF-001~005, 007, 009~010의 실제 block_reason은 모두 `pre_llm:incomplete_semantic_block`이다. validation_reason이 없는 이유는 post-LLM 검증을 하지 않았기 때문이다. v1과 동일하다.

## 2. normalization 코드가 실행에 포함됐는가?

v2 JSON의 서비스 소스 해시에서 `mvp/ai.py`, `mvp/answer_normalization.py`, `mvp/validation_trace.py`는 현재 코드와 일치한다. v1에는 normalization 모듈이 없고 ai.py 해시도 현재와 다르다. 따라서 수정 전 코드를 잘못 실행한 결과라고 볼 근거는 없다.

v2의 `validate_answer()`는 기존 `sentence_evidence()`가 실패하면 `normalized_evidence()`를 호출한 다음 문장 매칭 결과와 label 포함 여부를 검사한다. 두 문항의 저장된 원문 불일치와 이 코드 경로를 종합하면 normalization 재검사 경로에 도달했다고 판단할 수 있다. 다만 `normalization_applied`, 통과 규칙, 통과 문장 목록 자체를 저장하는 필드는 없다.

## 3. TRF-006 직접 비교

| 항목 | v1 | v2 |
| --- | --- | --- |
| status | abstained | abstained |
| validation_reason | unsupported sentence | unsupported sentence |
| post_llm_validation_stage | sentence_matching | sentence_matching |
| statement_index / sentence_index | 1 / 1 | 1 / 1 |
| failure_type | sentence_not_in_source | sentence_not_in_source |
| label | 활력징후 및 부작용 확인 시점 | 동일 |
| sentence_in_chunk / sentence_in_quote | false / true | false / true |
| quote_in_chunk / chunk_found | true / true | true / true |

두 실행의 실패 statement_text는 동일하다.

> 수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰

선택된 p.5 청크 `8beba164-15a2-4318-b105-390b2037eceb`에 있는 대응 원문은 다음과 같다.

> (수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰)

### 코드와 저장 본문으로 확인되는 변화

v1은 바깥 괄호 차이 때문에 문장 완전일치에서 실패한다. v2의 `surface()`는 이 바깥 괄호를 제거한다. 저장된 `sentence_in_quote=true`, `quote_in_chunk=true`와 대응 원문을 대조하면 같은 문구가 quote에도 포함되어 있으므로 첫 statement의 본문은 normalization 규칙에 맞는다.

반면 label `활력징후 및 부작용 확인 시점`은 해당 원문에 그대로 존재하지 않는다. v2 코드의 label 검사는 변경되지 않았으며, 문장 매칭에 성공하더라도 label이 quote에 없으면 같은 `unsupported sentence`를 발생시킨다.

**판정: 해당 문제 해결, 다음 검증에서 차단 — 코드와 저장 본문을 통한 추론이다.** 첫 statement의 괄호 차이 문제는 해소되는 경로이며, 다음 label 검사에서 차단된 것으로 판단된다. 이는 최종 답변 성공이나 다음 statement까지 통과했다는 뜻이 아니다. 통과 이벤트가 직접 저장되지 않았다는 한계는 유지한다.

### 왜 trace는 이전과 똑같은가?

`mvp/validation_trace.py:record_failure()`는 실패 원인을 재분류할 때 `source_sentences()`에 대한 원문 완전일치만 검사한다. 새 `normalized_evidence()` 결과는 받지도 재사용하지도 않는다. 따라서 실제 validator에서 정규화 본문이 통과하고 label이 실패해도 진단기는 괄호 차이를 다시 발견해 `sentence_not_in_source`라고 기록한다.

이 실행의 `failure_type`은 실제 저장 값 그대로 보고해야 하지만, normalization 이후 최초 실패 지점을 정확히 표현하는 값이라고 신뢰해서는 안 된다. 앞선 statement가 정상 통과했다는 기록은 없으며, 이번 판단은 첫 statement 내부의 본문 검사와 label 검사를 구분한 것이다.

## 4. TRF-008 직접 비교

| 항목 | v1 | v2 |
| --- | --- | --- |
| status | abstained | abstained |
| validation_reason | unsupported sentence | unsupported sentence |
| post_llm_validation_stage | sentence_matching | sentence_matching |
| statement_index / sentence_index | 1 / 1 | 1 / 1 |
| failure_type | sentence_not_in_source | sentence_not_in_source |
| label | 빈 문자열 | 빈 문자열 |
| sentence_in_chunk / sentence_in_quote | false / false | false / false |

v1 문장:

> 적혈구, 혈소판 혈액제제에 방사선 조사를 시행한다.

v2 문장:

> 적혈구와 혈소판 혈액제제는 방사선 조사를 시행한다.

v2는 모델 출력 자체도 v1과 달라졌다. 현재 target_forms()가 허용하는 서술형은 다음 두 형태뿐이다.

1. `방사선 조사는 {대상1}와 {대상2}에 적용한다`
2. `{대상1}, {대상2}에 방사선 조사를 시행한다`

실제 v2 문장은 `{대상1}와 {대상2}는 방사선 조사를 시행한다`이므로 둘 다 해당하지 않는다. 일반 조사 교체는 구현되지 않았다. 또한 변환 규칙은 명시적인 section 주제와 `대상혈액: 값` 형식을 요구하지만 저장된 관련 p.4 본문은 `(3) 대상혈액`과 값이 별도 줄인 구조다. 그 구조를 임의로 합치는 규칙은 없다.

**판정: 효과 없음 — 이번 TRF-008의 관측된 출력에 한정.** normalization 경로는 추가됐지만 현재 생성 문장과 등록 본문 형식이 좁은 허용 규칙 밖에 있어 첫 statement에서 계속 차단된다. 이후 문장이나 label에서 새롭게 실패한 것은 아니다. normalization 전체가 어떤 입력에도 효과 없다는 뜻은 아니다.

## 5. 진단의 관측 한계

- 두 문항 모두 실패 evidence의 chunk_id/document_id가 null이다. 기존 UUID 필터 문제는 남아 있다. chunk_found=true이므로 근거 lookup 실패를 뜻하지 않는다.
- 실제 quote 원문 전체와 모든 생성 statement는 저장되지 않았다.
- normalization 성공 여부 및 통과 문장을 명시적으로 남기는 필드가 없다.
- trace가 기존 matcher로 원인을 재판정하므로 normalization 후 label 실패를 문장 실패로 오분류할 수 있다.
- 10문항의 검색 청크 ID·순서는 v1/v2에서 동일하다. 최종 abstention 수만으로 normalization의 내부 효과를 평가할 수 없다.

## 6. 숫자·조건·금기·임상 내용의 잘못된 통과 흔적

저장된 실패 문장에서는 normalization으로 숫자·조건·금기가 바뀐 채 최종 답변으로 제공된 흔적이 없다. 두 post-LLM 문항 모두 첫 statement에서 거절됐고, 전체 10문항 모두 최종 answerable=false다. TRF-006의 시간 표현은 v1과 동일하다. TRF-008은 표현형이 달라졌지만 허용되지 않았다.

따라서 **이번 저장 결과에서 잘못 통과한 흔적은 확인되지 않는다.** 그러나 성공/부분 통과 이벤트와 전체 모델 원문이 없으므로 이 결과만으로 모든 normalization 입력의 안전성을 증명할 수는 없다. 이번에는 테스트나 validator도 다시 실행하지 않았다.

## 보존 확인

- 진단 v1 SHA-256: `dc014bd48bffe1bf1aefc2998d890fff76b006cb50ca19d55542b0b989f1e1b1`
- 진단 v2 SHA-256: `2e80a5470642bf02fe53843b28deef32eedcc587dcb2b4220a0406211a557c75`

## TRF-006 v1 → v2 변화

최종 status와 저장된 failure_type은 동일하다. 하지만 코드·본문 대조상 괄호 정규화로 첫 statement 본문은 매칭되고, 근거에 없는 label이 다음 차단 지점이다. “해당 문제 해결, 다음 검증에서 차단”으로 판단하되 직접적인 성공 trace가 아니라 코드 기반 추론임을 명시한다.

## TRF-008 v1 → v2 변화

첫 문장이 다른 서술형으로 생성됐고, 새 허용 템플릿에도 해당하지 않는다. 첫 문장 매칭에서 그대로 실패했으므로 이번 출력에는 “효과 없음”이다.

## normalization 수정 효과

전체적으로 일부 효과가 있으나 최종 답변 성공으로 이어지지 않았다. TRF-006의 괄호 문제와 TRF-008의 제한 밖 표현을 구분해야 한다. trace 자체가 normalization 결과를 반영하지 않는 문제도 드러났다.

## 현재 가장 큰 병목

문항 수 기준으로는 여전히 8/10의 pre-LLM incomplete_semantic_block이다. post-LLM에서는 label의 근거 일치와 좁은 서술형 템플릿이 남아 있다. 이번 변경은 pre-LLM 경로를 대상으로 하지 않았다.

## 다음에 수정해야 할 한 가지

**진단 trace가 실제 validator의 판정 결과를 그대로 받도록 연결하는 것**을 우선 제안한다. normalization 적용·성공 여부와 실제 실패 단계(본문/label)를 validator에서 넘기고, 별도의 구형 matcher로 실패 원인을 다시 추정하지 않도록 해야 한다. 허용 범위를 추가 확대하기 전에 실제 병목을 정확히 기록할 필요가 있다. 이번에는 수정하지 않았다.

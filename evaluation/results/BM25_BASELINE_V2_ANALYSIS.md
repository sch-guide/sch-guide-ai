# BM25 Answer Baseline v2 원인 분석

분석일: 2026-09-18. 저장된 v1/v2 JSON과 현재 소스만 읽어 분석했다. 검색, 임베딩, Gemini 호출, 평가 재실행은 수행하지 않았다. JSON·서비스 코드·설정은 변경하지 않았다.

## 근거와 분석 한계

- 입력: `bm25_answer_baseline_v1.json`, `bm25_answer_baseline_v2.json` 및 현재 `mvp/ai.py`, `mvp/evidence.py`, `mvp/gemini_provider.py`, `mvp/library.py`, `evaluation/run_bm25_baseline.py`.
- v2 실행 시각: `2026-09-18T00:51:11.578954+00:00`.
- v2 브랜치/커밋: `lagom-bm25-context-fix` / `bc05402836469c78146f07170f0e74327bbd6bdb`.
- v2에 기록된 모든 서비스 소스 SHA-256이 현재 파일과 일치한다. 현재 코드로 실행 경로를 해석할 수 있다.
- v1/v2 간 서비스 소스 변경은 `evidence.py`, `settings.py`, `test_gemini_connection.py`이다. `ai.py`, 검색·context·파싱 모듈의 해시는 동일하다.
- 최종 서비스 답변을 저장하는 결과 파일이다. 검증 전 Gemini 원본 답변, SDK 오류 본문, HTTP 오류 코드, 전체 traceback은 저장되어 있지 않다. 따라서 특정 실패 문장이나 API 오류 세부 원인을 복원할 수 없다.
- 이름은 BM25 Baseline이지만 실제 검색은 기록대로 BM25 + FAISS + RRF + 규칙 재정렬 + context expansion이다.

## 1. 문항별 v1 → v2 비교

v1은 전 문항 `abstained`, `pre_llm:incomplete_semantic_block`, `llm_called=false`였다.

| ID | v1 상태 | v2 상태 | v2 차단/오류 이유 | Gemini 호출 여부 |
| --- | --- | --- | --- | --- |
| TRF-001 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-002 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-003 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-004 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-005 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-006 | abstained | abstained | invalid_citation_or_statement / unsupported sentence | 예, 응답 후 검증 도달 |
| TRF-007 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-008 | abstained | error | GuideError / AI_SERVER | 예, API 오류 경로 |
| TRF-009 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-010 | abstained | abstained | pre_llm:incomplete_semantic_block | 아니오 |

호출 여부는 `llm_called`와 후속 코드 경로를 함께 확인했다. TRF-008의 `block_reason`은 실제로 `null`이며 `AI_SERVER`는 `error.message`의 오류 식별자다.

### generation_trace 전체 흐름

아래 A는 저장된 trace 필드 전체가 동일한 흐름이다.

**A — 사전 근거 차단:** `llm_called=false` → `stage=before_llm` → `pre_llm_assessment=incomplete_semantic_block` → `block_reason=pre_llm:incomplete_semantic_block` → `answerable=false`. 예산 검사·LLM·인용 검증에는 도달하지 않았다.

| ID | 저장 기록으로 확인한 흐름 |
| --- | --- |
| TRF-001 | A. 질문 계획은 procedure |
| TRF-002 | A. 질문 계획은 fact |
| TRF-003 | A. 질문 계획은 procedure |
| TRF-004 | A. 질문 계획은 fact |
| TRF-005 | A. 질문 계획은 fact |
| TRF-006 | pre_llm_assessment=supported → prompt_chunk_ids 5개 → budget_assessment=supported → llm_called=true → stage=citation_validation → validation_reason=unsupported sentence → block_reason=invalid_citation_or_statement → answerable=false |
| TRF-007 | A. 질문 계획은 procedure |
| TRF-008 | pre_llm_assessment=supported → prompt_chunk_ids 9개 → budget_assessment=supported → llm_called=true → stage=llm_request → GuideError(AI_SERVER). block_reason=null; answerable 필드는 없음 |
| TRF-009 | A. 질문 계획은 fact |
| TRF-010 | A. 질문 계획은 fact |

trace는 단계별 이벤트 배열이 아니라 실행 중 갱신되는 사전이다. 위 화살표는 저장된 필드와 `generate()`의 실행 순서를 조합한 것이며 저장되지 않은 reason을 보충한 것이 아니다. 모든 문항의 검색 trace는 `reason=retrieved`다.

## 2. 9개 abstained 문항의 실제 reason별 그룹

| 그룹 | 실제 reason | 문항 | 건수 |
| --- | --- | --- | ---: |
| Pre-LLM | pre_llm:incomplete_semantic_block | 001, 002, 003, 004, 005, 007, 009, 010 | 8 |
| Post-LLM 문장·인용 검증 | invalid_citation_or_statement; validation_reason=unsupported sentence | 006 | 1 |

`insufficient_evidence`, `missing_required_fact`는 이번 저장 trace의 차단 reason이 아니다. TRF-008은 abstained가 아니라 별도의 error 1건이다. 성공 답변은 0건이지만, Gemini 호출은 0건이 아니라 2건이다.

## 3. TRF-006 상세

질문: “수혈 시작하면 활력징후와 부작용은 언제 확인해야 해?”

### 핵심 근거와 선택 결과

프롬프트에 실제 선택된 청크는 다음 5개다. ID는 `generation_trace.prompt_chunk_ids`와 `generation_selected_contexts`를 대조했다.

| 순서 | 페이지 | chunk ID | 내용 요약 |
| ---: | ---: | --- | --- |
| 1 | 5 | 8beba164-15a2-4318-b105-390b2037eceb | 관찰 시점, 간호기록, RBC 기록 시한 |
| 2 | 9 | 9d473319-428a-474a-ba5f-d1381cbc8f1a | 수혈 전 검사의 목적·확인 |
| 3 | 9 | 44dbc444-4c57-4835-a90f-0ba47eae91ab | 수혈 전 혈액검사 결과 확인 |
| 4 | 5 | e15105be-9e53-4a6e-87f8-9e81501030f7 | 수혈 시 주의사항과 상태 확인 |
| 5 | 9 | dd0ce7bc-5e11-412a-ba6f-d0c579b1df67 | 채혈 확인·검사 시점 |

첫 청크에는 다음 원문이 실제 포함되어 있다.

> (수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰)

### 어느 문제가 해결됐고 어디서 멈췄나

- 이 실행에서는 기존 `incomplete_semantic_block` 차단을 통과했다. `pre_llm_assessment=supported`, `budget_assessment=supported`가 직접 증거다.
- `complete_subset()`이 불완전 부모를 제외한 독립적인 완전 근거 집합을 허용하는 경로가 효과를 냈다. 전체 시스템의 모든 불완전 부모 문제가 해결됐다는 뜻은 아니다.
- Gemini 호출 및 응답 수신 이후 `stage=citation_validation`까지 진행했다.
- 최종 status는 `abstained`, `block_reason=invalid_citation_or_statement`, `validation_reason=unsupported sentence`, `answerable=false`, `error=null`이다.
- Gemini가 스스로 답변을 거절한 `llm_abstained`가 아니다. 로컬 검증기가 응답을 거부해 고정된 근거 부족 문구로 바꾼 것이다.

### 정확한 검증 조건

`mvp/ai.py:121` 부근의 `validate_answer()`는 `sentence_evidence()` 결과가 비어 있거나, 비어 있지 않은 statement label이 어떤 인용문에도 포함되지 않으면 `ValueError('unsupported sentence')`를 발생시킨다. 이후 `generate()`의 391–396행 부근에서 이를 `invalid_citation_or_statement` abstention으로 변환한다.

`mvp/evidence.py:202`의 `sentence_evidence()`는 답변을 나눈 각 문장이 원문 청크의 문장 목록과 일치하고 해당 quote 안에도 포함되기를 요구한다. 임상 의미만 같은 의역을 허용하는 검사가 아니다. 한 문장이라도 대응하지 않으면 빈 결과를 반환한다.

**확정 가능한 원인:** 문장 단위 원문 일치/인용 범위 또는 label 포함 조건에서 거부됐다. **확정 불가능한 세부:** 실제 어떤 문장이 달랐는지, 의역·목록 기호·괄호·줄바꿈·label 중 무엇이 원인이었는지. 원본 모델 응답이 저장되지 않아 임상적으로 잘못된 답변이었다고 단정할 수 없다. 조건 완화나 안전 검증 우회가 필요하다는 결론도 아직 내릴 수 없다.

## 4. TRF-008 상세

질문은 방사선 조사 혈액의 목적, 조사 RBC 사용 기한, FFP/CRYO 적용 여부를 함께 묻는다.

- 저장된 exception 종류: `GuideError`.
- 저장 메시지: `AI 서버가 요청을 처리하지 못했습니다. 연결 설정과 모델 지원 형식을 확인하세요. (AI_SERVER)`.
- `retry_after_seconds=null`.
- 사전 및 예산 근거 검사는 모두 `supported`; p.4와 p.9의 청크 9개가 prompt에 선택됐다.
- `stage=llm_request`, `llm_called=true`; 인용·답변 검증 단계까지는 도달하지 않았다.
- 예외 때문에 `run_case()`의 기본 `status=error`, `generated_answer=null`이 유지됐다. `generation_selected_contexts`가 없는 것은 정상 반환 전에 예외가 발생했기 때문이며, prompt 선택 자체가 없었다는 뜻이 아니다.

### 코드상 발생 경로

1. `mvp/ai.py:344` 부근 `generate()` → Gemini `completion_response()`.
2. `mvp/gemini_provider.py:31`에서 공식 SDK `client.models.generate_content()` 호출.
3. 같은 파일 41–44행의 `except errors.APIError`가 API 오류를 빈 본문의 `httpx.Response`로 바꾼다. 유효한 400–599 code는 유지하고, 그 외 code는 502로 정규화한다.
4. `mvp/ai.py:373` 부근의 `response.status_code >= 400` 분기가 `GuideError(...AI_SERVER)`를 발생시킨다. 401/403은 앞서 AI_AUTH, 429는 AI_RATE로 따로 처리하므로 이번 저장 오류는 그 분기가 아니다.
5. `evaluation/run_bm25_baseline.py:200` 부근 `run_case()`가 예외 종류와 안전한 고정 메시지만 저장한다.

따라서 **Gemini provider의 API 오류 경로**인 것은 확인된다. 로컬 semantic block, 인용 검증, Label Set 데이터 파싱에서 난 오류가 아니다. 그러나 원래 HTTP status와 SDK 오류 세부 정보가 소실되어 **모델 미지원, 요청 형식 문제, 특정 입력에 대한 제한, 일시적 서버 오류 중 무엇인지 확인할 수 없다**. 실제 모델 이름이 잘못되었다고 단정할 근거도 없다.

### 왜 다른 문항에는 없었나

8개 문항은 호출 전에 차단되어 같은 API 오류를 겪을 기회가 없었다. 호출된 나머지 TRF-006은 응답을 받고 로컬 검증까지 진행했다. 그러므로 “TRF-008만 API 오류로 기록됐다”는 사실은 설명할 수 있지만, TRF-006과 달리 이 요청이 실패한 구체적인 이유는 저장 정보만으로 알 수 없다. 질문 길이·복합성이나 특정 혈액제제 이름을 원인으로 단정하지 않는다.

## 5. evidence 수정의 효과와 남은 범위

| 파이프라인 지표 | v1 | v2 |
| --- | ---: | ---: |
| incomplete_semantic_block 사전 차단 | 10/10 | 8/10 |
| 사전·예산 근거 검사 통과 및 LLM 호출 | 0/10 | 2/10 |
| 응답 후 인용 검증 도달 | 0/10 | 1/10 |
| 최종 answered | 0/10 | 0/10 |

**효과는 확인된다.** 목표 사례 TRF-006을 포함한 2건이 더 뒤 단계까지 진행했다. 최종 답변 제공 문제는 아직 해결되지 않았다.

남은 8건은 `assess_evidence()`의 불완전 부모 검사에서 동일하게 차단됐다. `complete_subset()`은 `fact` 질문에만 적용된다. TRF-001/003/007의 `procedure` 계획에는 적용되지 않는 것이 현재 코드의 명시적인 제한이다. 나머지 fact 문항은 선두 근거의 완전성, 완전 집합 재검증, 시간 근거, 질문 단어 보존, 제외 부모의 엄격한 부분집합 조건 등을 모두 만족해야 한다. 저장 trace는 이 helper 내부의 거절 지점을 세분화하지 않으므로 각 fact 문항이 어느 하위 조건에서 실패했는지는 이번 기록만으로 특정하지 않는다.

TRF-004의 저장된 최종 차단은 여전히 incomplete_semantic_block이다. 이전에 확인한 p.2 표 추출 손실과 후보 재정렬 문제를 이번 evidence 수정이 해결했다고 볼 수 없다. 검색·PDF 파싱 코드는 v1과 동일하며 이번에도 수정하지 않았다.

## 6. pooling 경고의 영향

현재 실제 임베딩 로더는 SentenceTransformer 클래스가 아니라 `mvp/library.py:197`의 `Embedder` → FastEmbed `TextEmbedding`이다. 설치된 `fastembed/text/text_embedding.py:98` 부근은 이 모델 이름에 대해 “now uses mean pooling instead of CLS embedding” 경고를 조건부 출력한다. 이는 모델 이름에 따라 초기화 때 나오는 버전 동작 안내이며 해당 질문의 처리 실패를 뜻하지 않는다.

저장 결과를 비교하면:

- corpus 해시가 동일하다.
- 10문항 모두 최종 context의 청크 ID, 순서, 페이지, 본문, similarity, fusion_score, rerank_score가 v1/v2에서 동일하다.
- context 레코드에서 달라진 필드는 9문항의 `bm25_score`뿐이고 TRF-009는 레코드 전체가 같다. 이 값의 차이를 pooling 때문이라고 해석하지 않는다. BM25는 임베딩 pooling을 사용하지 않는다.
- 10문항 모두 검색이 완료됐고, 임베딩 초기화/벡터 생성 오류는 기록되지 않았다.

**이번 abstain/error의 직접 원인 및 v1→v2 결과 변화에는 pooling 경고의 영향 없음.** 실패 지점은 위에서 확인한 근거 완전성, 문장 검증, Gemini API 오류다. 다만 과거 다른 FastEmbed 버전과의 검색 품질까지 완전히 같다는 뜻은 아니다. 터미널 경고 원문이 JSON에 저장되지 않았으므로 여기서 식별한 경고는 현재 설치 코드에서 확인한 위 문구를 기준으로 한다.

## 보존 확인

분석 입력의 SHA-256:

- v1: `4012a37cdb8ed3782d69494e199ca7a15df53a6708ff640369cc4752702e6aa1`
- v2: `06b234775e8129a912db2a8d455270a25a70df92db2bb3103ec2cd264e59dcfe`

이번 산출물은 이 보고서 한 개다. 원본 JSON의 답변·trace·평가값과 기존 보고서는 보존한다.

## v1 → v2에서 실제로 개선된 것

사전 불완전 부모 차단이 10건에서 8건으로 줄었다. TRF-006은 핵심 근거를 유지한 채 Gemini 응답 검증까지, TRF-008은 Gemini API 요청까지 진행했다. 좁은 사실 질문의 완전 근거 선택 경로가 실제 실행에서 작동했다.

## 현재 새 병목

TRF-006은 원문 문장·인용 검증의 `unsupported sentence`, TRF-008은 `AI_SERVER`다. 동시에 기존 incomplete_semantic_block 8건이 남아 있다. 검증 전 응답과 안전한 API 오류 식별정보가 없어 후속 실패의 세부 진단이 제한된다.

## TRF-008 오류 원인

Gemini APIError를 정규화한 응답이 서비스의 AI_SERVER 예외로 변환됐다. 정확한 HTTP code 및 provider 사유는 저장되지 않아 세부 원인은 확인 필요다. semantic block이나 로컬 답변 검증 오류가 아니다.

## 다음 수정이 필요한가?

예. 이번에는 수정하지 않았다.

## 다음에 가장 먼저 손대야 할 한 가지

**후속 실패를 구분할 수 있는 안전한 진단 trace부터 보강하는 것을 제안한다.** 문장 검증에는 실패 statement의 순번과 문장/label 실패 구분, API 실패에는 HTTP status와 정제된 provider 오류 식별자만 남기도록 하는 작은 변경이다. API 키·요청 헤더·병원 원문·무가공 SDK 오류 본문은 기록하지 않는다. 현재 기록만으로 원인 문장을 추측해 검증 기준을 낮추거나 모델을 바꾸지 않는다. 진단 보강 후 별도 승인된 검증으로 실제 실패 조건을 확보하고 그 조건에 맞는 수정을 결정한다.

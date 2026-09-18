# BM25 Answer Baseline v2 — Groq 원인 분석

분석일: 2026-09-18. 저장된 Groq/Gemini v2 JSON 및 현재 소스만 읽었다. 평가·검색·임베딩·API 호출을 다시 실행하지 않았으며 기존 JSON·서비스 코드·설정을 변경하지 않았다.

## 분석 근거

- Groq 실행: `2026-09-18T01:17:58.268848+00:00`, 모델 `openai/gpt-oss-20b`.
- Gemini 실행: `2026-09-18T00:51:11.578954+00:00`, 모델 `gemini-3.1-flash-lite`.
- 두 결과의 서비스 소스 해시는 서로 같고 현재 파일과도 모두 일치한다.
- Label Set·corpus·Prompt 해시, Top-k, chunk size/overlap, temperature, min_similarity도 서로 같다.
- 저장된 trace와 `mvp/ai.py`의 `generate()`, `validate_answer()`, `prompt_messages()`, `mvp/evidence.py`의 `assess_evidence()`, `complete_subset()`, `sentence_evidence()`를 대조했다.
- 검증 전 모델 원문 및 실패 statement는 저장되어 있지 않다. 세부 문구 차이와 임상적 정오를 추측하지 않는다.

## 1. Groq 문항별 최종 상태와 호출 여부

| ID | 최종 상태 | 차단/검증 이유 | Groq 호출 여부 |
| --- | --- | --- | --- |
| TRF-001 | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-002 | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-003 | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-004 | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-005 | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-006 | abstained | invalid_citation_or_statement / unsupported sentence | 예, 응답 후 검증 도달 |
| TRF-007 | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-008 | abstained | invalid_citation_or_statement / unsupported sentence | 예, 응답 후 검증 도달 |
| TRF-009 | abstained | pre_llm:incomplete_semantic_block | 아니오 |
| TRF-010 | abstained | pre_llm:incomplete_semantic_block | 아니오 |

10문항 모두 `error=null`이다. API/model 오류로 저장된 문항은 없다. 성공 답변은 0/10, API 호출은 2/10이다.

## 2. 실제 reason별 분류와 흐름

### Pre-LLM 근거 완전성 차단 — 8건

대상: TRF-001, 002, 003, 004, 005, 007, 009, 010.

모든 해당 문항의 trace는 다음과 같다.

```text
llm_called=false
stage=before_llm
pre_llm_assessment=incomplete_semantic_block
block_reason=pre_llm:incomplete_semantic_block
answerable=false
```

`assess_evidence()`는 관련 근거 중 불완전 부모가 있으면 `complete_subset()`으로 안전한 완전 근거 집합을 만들 수 있는지 확인한다. 이 경로가 허용되지 않으면 위 reason으로 종료한다. Groq 설정이나 응답을 보기 전에 발생하는 차단이다.

현재 helper는 좁은 `fact` 질문만 허용하며 선두 근거 완전성, 완전 집합의 기존 검사 통과, 질문 단어 보존 등을 요구한다. 저장된 trace는 helper 내부의 개별 실패 조건까지 남기지 않으므로 8건 각각을 특정 하위 조건으로 단정할 수 없다.

### Post-LLM 문장·인용 검증 차단 — 2건

대상: TRF-006, TRF-008.

```text
pre_llm_assessment=supported
budget_assessment=supported
llm_called=true
stage=citation_validation
validation_reason=unsupported sentence
block_reason=invalid_citation_or_statement
answerable=false
```

TRF-006의 prompt_chunk_ids는 5개, TRF-008은 9개다. 사전 검사와 예산 검사를 통과하고 API 응답까지 받았지만 로컬 검증에서 거부됐다. `llm_abstained`가 아니므로 모델이 스스로 “근거 없음”을 반환한 것으로 해석하면 안 된다.

실제 저장 값은 밑줄을 쓴 `unsupported_sentence`가 아니라 **공백이 있는 `unsupported sentence`**이다. `insufficient evidence` 등 다른 예시 reason은 이번 trace에 없으므로 분류에 추가하지 않는다.

### unsupported sentence의 정확한 의미와 한계

`mvp/ai.py:120` 부근에서 다음 중 하나면 이 reason이 발생한다.

1. `sentence_evidence(statement.text, statement.evidence, sources)`가 빈 결과를 반환한다.
2. 비어 있지 않은 statement label이 어느 quote에도 포함되지 않는다.

`mvp/evidence.py:202`의 `sentence_evidence()`는 답변의 각 문장이 인용 대상 원문 청크의 문장 목록에 일치하고 해당 quote에도 포함되어야 한다. 의미상 같은 요약만으로 통과하는 검사가 아니다. 한 문장이라도 대응하지 않으면 빈 결과가 된다.

이후 `generate()`가 `GuideError(AI_EVIDENCE)`를 받아 `invalid_citation_or_statement`로 고정된 거절 답변을 반환한다. 이는 데이터 파싱 예외나 API 연결 실패와 구분된다.

원본 응답이 없어 이번 실패가 의역, 부분 문장, 기호·문장 경계, label 불일치 중 무엇 때문인지는 확인 불가다. 실제 임상적 오류였는지, 정확한 답변을 형식 때문에 거부했는지도 현재 기록만으로 판단할 수 없다.

## 3. Gemini v2와 비교

| ID | Gemini v2 | Groq v2 | 차이가 발생한 단계 |
| --- | --- | --- | --- |
| TRF-001 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |
| TRF-002 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |
| TRF-003 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |
| TRF-004 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |
| TRF-005 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |
| TRF-006 | abstained / unsupported sentence | abstained / unsupported sentence | 없음: 모두 응답 후 문장·인용 검증 |
| TRF-007 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |
| TRF-008 | error / AI_SERVER | abstained / unsupported sentence | Gemini는 API 오류, Groq는 응답 후 검증까지 진행 |
| TRF-009 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |
| TRF-010 | abstained / incomplete_semantic_block | abstained / incomplete_semantic_block | 없음: 모두 Pre-LLM |

### TRF-006

두 provider 모두 사전·예산 검사에서 `supported`, API 호출 후 `citation_validation`에서 `unsupported sentence`다. Groq 전환으로 이 병목은 해소되지 않았다. 양쪽 모두 p.5의 활력징후·부작용 관찰 시점 근거를 포함한 동일한 5개 청크를 전달받았다. 같은 reason이라는 사실만으로 모델 응답 문장까지 같았다고 판단하지 않는다.

### TRF-008

Gemini는 `stage=llm_request`, `GuideError(AI_SERVER)`, `block_reason=null`로 끝났다. Groq는 같은 9개 청크로 응답을 받아 `stage=citation_validation`까지 진행했으나 `unsupported sentence`로 거부됐다.

Groq 실행에서는 Gemini에서 관찰한 API 오류가 재현되지 않았다는 점은 확인된다. 그러나 단일 실행이고 Gemini의 HTTP 오류 세부 정보가 없으므로 Groq가 항상 더 안정적이거나 Gemini 모델이 이 질문을 지원하지 않는다고 일반화할 수 없다.

## 4. Provider별 실제 prompt context 비교

| 문항 | Gemini 선택 청크 | Groq 선택 청크 | 순서·본문 비교 |
| --- | ---: | ---: | --- |
| TRF-006 | 5 | 5 | 동일 |
| TRF-008 | 9 | 9 | 동일 |
| 나머지 8문항 | 프롬프트 생성 전 차단 | 프롬프트 생성 전 차단 | 실제 LLM 입력 없음 |

두 문항은 `generation_trace.prompt_chunk_ids`의 ID와 순서가 정확히 일치한다. 해당 ID를 `retrieved_contexts`의 content와 대조해 본문도 동일함을 확인했다. 10문항 전체의 최종 검색 청크 ID·순서와 벡터 similarity도 동일하다.

따라서 **이번 실행에서 Groq 3,500토큰 예산 때문에 prompt context가 실제로 달라진 증거는 없고, 호출된 두 문항의 선택 context는 동일하다.** `mvp/ai.py:248`의 prompt 구성 코드는 양쪽이 공유한다. 다만 완성된 전송 payload 자체를 저장한 기록은 아니므로 SDK/HTTP wire payload의 바이트 단위 동일성을 주장하지 않는다.

완전한 provider/model-only comparison은 아니다. 결과에도 `provider_model_only_comparison=false`가 명시되어 있다.

- Groq만 request token budget=3500을 적용한다. Gemini의 null은 별도 토큰 예산 없음이라는 뜻이며 공통 14,000바이트 prompt 제한까지 없다는 뜻은 아니다.
- Groq는 `reasoning_effort=low`를 사용한다.
- 전송 방식과 출력 제한 파라미터 명칭, provider별 사용량 제한·토큰 추정 방식이 다르다.
- 이번 두 호출에서는 context 차이가 관측되지 않았지만 위 실행 조건 차이 자체는 남아 있다.

정확한 표현은 **“동일한 서비스 검색·근거 검증 경로에서 기존 Gemini/Groq provider 구성 비교”**다. 이 결과만으로 LLM 모델의 순수 성능 차이를 분리해 추정할 수 없다.

## 5. 순수 BM25 vs ChromaDB라는 명칭의 적절성

저장 메타데이터는 다음과 같다.

```text
retrieval_implementation = BM25 + FAISS dense + RRF + rule rerank + context expansion
is_pure_bm25 = false
```

순수 BM25라고 부르는 것은 부정확하다. 현재 검색은 BM25 어휘 검색과 FAISS 밀집 벡터 검색 후보를 RRF로 결합한 뒤 규칙 재정렬과 context 확장을 수행하는 하이브리드 경로다.

또한 이번 두 결과는 검색 방식 비교가 아니라 LLM provider 구성 비교이며, ChromaDB 실행 결과도 아니다. ChromaDB는 벡터 저장·검색 시스템이고 BM25는 검색 점수 알고리즘이므로 명칭만으로 같은 축의 대조군이 되지도 않는다.

권장 명칭: **“SCH Guide AI Hybrid Retrieval + LLM Answer Baseline v2 — Gemini/Groq 비교”**. 기존 파일명 BM25는 기록 식별자로 유지하되 보고서에 하이브리드 구현임을 병기하는 것이 정확하다. 향후 ChromaDB를 비교하려면 벡터 저장소만 비교하는지, 검색 파이프라인을 바꾸는지 먼저 정의해야 한다. 이번에는 구현·변경하지 않았다.

## 6. pooling 경고

현재 `mvp/library.py`의 임베딩 로더는 FastEmbed `TextEmbedding`이다. 이전 분석에서 확인한 설치 코드의 경고는 해당 sentence-transformers 모델이 CLS 대신 mean pooling을 사용하는 버전 동작 안내다. 경고 원문은 이번 JSON에 포함되지 않았다.

이번 Groq 결과에서 pooling 경고가 abstain을 직접 유발했다는 근거는 없다. 모든 문항은 검색 이후 evidence 단계에 도달했고, 두 provider 실행의 최종 청크 ID·순서와 similarity가 같다. 실제 실패는 8건의 근거 완전성 차단과 2건의 응답 후 문장 검증으로 기록되어 있다. 임베딩 예외나 경고로 인한 중단은 기록되지 않았다.

따라서 **이번 Groq 차단의 직접 원인으로 확인되지 않으며, provider 간 결과 차이를 설명하는 근거도 없다.** 다른 임베딩 버전과의 검색 성능까지 영향이 전혀 없다는 일반적 결론으로 확대하지 않는다.

## 원본 보존

분석 전후 확인용 SHA-256:

- Groq v2: `a14bdb952c4df082887b6490ae8998a738e5d4762e3d8ddd4bf31f2609851303`
- Gemini v2: `06b234775e8129a912db2a8d455270a25a70df92db2bb3103ec2cd264e59dcfe`

이번 작업에서는 이 보고서만 추가했다.

## Groq 10문항 abstain 핵심 원인

8건은 LLM에 도달하기 전 `pre_llm:incomplete_semantic_block`, 2건은 응답 후 `invalid_citation_or_statement` / `unsupported sentence`다. Groq가 10개 질문에 스스로 거절 답변을 생성한 것이 아니다.

## Gemini vs Groq 차이

9문항은 같은 단계·reason으로 끝났다. TRF-008만 Gemini의 API 오류에서 Groq의 응답 후 검증 차단으로 바뀌었다. 호출된 문항의 실제 context는 같으며 Groq도 최종 답변은 제공하지 못했다.

## LLM provider가 현재 실패의 주원인인가?

**아니오 — 전체 실패의 주원인이라는 근거는 없다.** 8/10은 provider 실행 이전에 막히고, 두 provider 모두 TRF-006의 동일 검증 조건에서 막힌다. TRF-008의 API 단계 차이는 있지만 전체 실패를 설명하지 못한다. 모델 출력이 두 post-LLM 거절에 어떻게 기여했는지는 원본 응답이 없어 확인 필요다.

## 현재 Baseline을 BM25라고 부르는 것이 정확한가?

기존 산출물 식별 이름으로는 유지할 수 있지만 **순수 BM25 실험이라는 기술적 설명은 부정확하다.** 실제 하이브리드 검색 경로와 provider 구성 비교임을 명시해야 한다.

## 다음에 가장 먼저 고쳐야 할 한 가지

**문장 검증 실패 trace의 식별력을 보강하는 것**을 우선 제안한다. TRF-006/008의 `unsupported sentence`를 실패 statement 순번과 문장 대응 실패/label 불일치로 구분해 남기면 다음 수정의 근거를 확보할 수 있다. 키·원문·무가공 API 응답을 로그에 남기지 않는 범위에서 설계해야 한다. 지금 근거 없이 validator를 완화하거나 provider를 다시 바꾸는 것은 권하지 않는다. 기존 8건의 semantic completeness 병목도 남아 있지만, 이번 비교가 드러낸 공통 후속 실패부터 정확히 식별할 필요가 있다. 이번에는 수정하지 않았다.

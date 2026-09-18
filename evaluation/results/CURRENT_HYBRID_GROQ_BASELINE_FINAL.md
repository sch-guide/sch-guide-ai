# Current Hybrid + Groq Baseline — 최종 요약

## 분석 범위와 평가 환경

- 최종 자료: `current_hybrid_groq_v5_completed.json`의 `results` 10건.
- 기준: `evaluation/label_set.json`의 기존 reference_answer, must_include, critical_error.
- Retrieval: **Current Hybrid Retrieval** (BM25 + FAISS dense + RRF + rule rerank + context expansion). 순수 BM25 실험이 아니다.
- LLM: Groq / `openai/gpt-oss-20b`, temperature 0.
- 문서: `실무지침서_수혈간호.pdf`.
- 원 실행 시각: 2026-09-18T03:48:15.215989+00:00. Resume 시각: 2026-09-18T03:58:17.289949+00:00.
- original: TRF-001, 002, 003, 007. resumed: TRF-004, 005, 006, 008, 009, 010.
- `original_results`는 이전 시도의 보존 기록이다. 최종 통계에는 중복 합산하지 않았다.
- 검색·API·평가 runner·RAGAS를 실행하지 않았다. 코드, 설정, JSON, 기존 보고서를 수정하지 않았다.

## 문항별 결과

`context_ready`는 서비스에서 context를 구성했다는 뜻이며, Label Set 정답 근거를 모두 확보했다는 독립적인 품질 판정은 아니다.

| ID | Retrieval/Context 상태 | LLM 호출 여부 | 최종 상태 | 정확한 reason |
| -- | -- | -- | -- | -- |
| TRF-001 | context_ready, 8 chunks; pre-LLM/budget supported | 예 | error | GuideError / AI_INCOMPLETE; stage=llm_request |
| TRF-002 | context_ready, 3 chunks; pre-LLM/budget supported | 예 | abstained | invalid_citation_or_statement → sentence_matching / unsupported sentence |
| TRF-003 | context_ready, 10 chunks; pre-LLM/budget supported | 예 | abstained | stage=complete; block_reason=llm_abstained |
| TRF-004 | context_ready, 11 chunks; pre-LLM/budget supported | 예 | abstained | stage=complete; block_reason=llm_abstained |
| TRF-005 | context_ready, 9 chunks; pre-LLM/budget supported | 예 | abstained | invalid_citation_or_statement → sentence_matching / unsupported sentence |
| TRF-006 | context_ready, 2 chunks; pre-LLM/budget supported | 예 | abstained | invalid_citation_or_statement → sentence_matching / unsupported sentence |
| TRF-007 | seed 6개 → 선택 context 0개 | 아니오 | no_hits | no_complete_required_parent |
| TRF-008 | context_ready, 5 chunks; pre-LLM/budget supported | 예 | abstained | invalid_citation_or_statement → sentence_matching / unsupported sentence |
| TRF-009 | context_ready, 10 chunks; pre-LLM/budget supported | 예 | abstained | invalid_citation_or_statement → sentence_matching / unsupported sentence |
| TRF-010 | context_ready, 2 chunks; pre-LLM/budget supported | 예 | abstained | invalid_citation_or_statement → sentence_matching / unsupported sentence |

표의 `unsupported sentence`는 실제 trace 문자열이다. 관용적으로 사용하는 `unsupported_sentence`와 구분하여 기록했다.

## 실패 이유와 도달 단계

### TRF-001: AI_INCOMPLETE

저장된 exception은 `GuideError`, 메시지는 “AI 답변이 완성되기 전에 중단되었습니다. 원문을 확인해 주세요. (AI_INCOMPLETE)”이다. `llm_called=true`, `stage=llm_request`, `generated_answer=null`이다. 호출 전 quota 차단이 아니며 post-LLM 답변 검증 도달 기록은 없다. 저장 결과에는 HTTP status, provider error code, 실제 finish_reason이 없어 토큰 한도 등 세부 원인을 확정할 수 없다.

### TRF-002, 005, 006, 008, 009, 010: 문장 검증 차단 6건

공통 trace는 다음과 같다.

- `stage=citation_validation`, `block_reason=invalid_citation_or_statement`.
- `final_validation_stage=sentence_matching`.
- `validation_reason` 및 `final_validation_reason` = `unsupported sentence`.
- `statement_index=1`, `failure_type=sentence_not_in_source`.
- `exact_match_passed=false`, `normalization_attempted=true`, `normalization_passed=false`.
- `label_validation_attempted=false`, `label_validation_passed=null`: 이번 결과의 직접 실패 단계는 label 검증이 아니다.
- 모든 해당 evidence 기록은 `chunk_found=true`, `quote_in_chunk=true`, `sentence_in_chunk=false`다.

TRF-005/006/009는 `sentence_in_quote=true`도 기록되어 있다. 따라서 이 reason만으로 “LLM이 근거 없는 임상 사실을 생성했다”고 단정할 수 없다. TRF-005/006의 문장과 label은 `text_redacted=true`로 가려져 있어 실패 문장 원문을 복원하거나 이전 실행 문장과 같다고 가정할 수 없다.

최종 사용자 답변은 여섯 문항 모두 “등록된 지침서에서 확인할 수 없습니다.”이다. 내부 validation_failure 문장은 사용자에게 제공된 최종 답변으로 채점하지 않는다.

### TRF-003/004: llm_abstained 2건

두 문항 모두 `llm_called=true`, `stage=complete`, `block_reason=llm_abstained`다. 저장된 structured_answer는 `answerable=false`, `statements=[]`, `conflict=false`다. 문장 검증 실패가 기록된 여섯 문항과 별개로, 답변 불가 결과로 종료됐다. 왜 모델이 답변 불가를 선택했는지에 대한 추가 사유는 기록되어 있지 않다.

### TRF-007: no_complete_required_parent

검색 seed 6개와 parent 후보 5개가 존재한다. 후보 parent chunk 수는 각각 6, 2, 2, 4, 1개이며 합계 15개, context limit은 12다. 선택된 chunk는 0개이고 정확한 reason은 `no_complete_required_parent`다. 검색 후보 자체가 없었던 것은 아니다. 합계 15개라는 수치만으로 직접 사유를 `context_budget_exceeded`로 바꾸어 해석하지 않는다. LLM 호출 기록은 없고 generation_trace는 비어 있다.

### Quota 및 호출 수

- 최종 `results`에는 AI_LIMIT_MINUTE, AI_LIMIT_CALLS 등 quota error가 **0건**이다.
- `original_results`에는 이전 6건의 AI_LIMIT_MINUTE 기록이 보존되어 있지만 최종 실패 건수에는 포함하지 않는다.
- LLM 호출: **9/10** (`llm_called=true`). 호출 기록과 정상 답변 제공 성공은 별개다.
- Post-LLM 검증 경로 도달: **8/10**. 문장 검증에서 차단된 6건과 답변 불가 결과로 complete에 도달한 2건이다. 문장별 검증 실패 trace가 명시적으로 있는 문항은 **6건**이다.

## 최종 서비스 Answer Coverage

정의: 문항별 `충족한 must_include 수 / 전체 must_include 수`, 전체 값은 10문항 산술평균이다. reference_answer와 must_include를 기준으로 최종 `generated_answer`만 평가했다. 사용자 지정 원칙에 따라 error/no_hits/abstained는 정답 답변 미제공으로 0점을 부여했다.

| ID | must_include 충족 / 전체 | Service Answer Coverage | Critical Error |
| -- | --: | --: | --: |
| TRF-001 | 0 / 6 | 0% | 0 |
| TRF-002 | 0 / 4 | 0% | 0 |
| TRF-003 | 0 / 4 | 0% | 0 |
| TRF-004 | 0 / 3 | 0% | 0 |
| TRF-005 | 0 / 4 | 0% | 0 |
| TRF-006 | 0 / 4 | 0% | 0 |
| TRF-007 | 0 / 4 | 0% | 0 |
| TRF-008 | 0 / 5 | 0% | 0 |
| TRF-009 | 0 / 3 | 0% | 0 |
| TRF-010 | 0 / 4 | 0% | 0 |

총 41개 필수 항목 중 서비스 답변으로 전달된 항목은 0개이며, Average Service Answer Coverage는 **0.00 (0%)**다. TRF-001은 답변 null, 나머지 9문항은 지침 확인 불가 안내뿐이므로 정답 내용이 포함되지 않았다.

이는 **서비스 전체 파이프라인의 답변 제공 성능**이며 Groq 자체의 임상 답변 성능이 0%라는 의미가 아니다. 검증에서 차단된 내부 생성 문장의 품질이나 제공되지 않은 나머지 답변은 이 점수의 평가 대상이 아니다.

Critical Error는 최종 출력에 위험한 반대 지시가 실제 포함된 경우만 집계했다. Label Set에서 명시한 TRF-007의 수혈 계속/중단하지 않음, TRF-009의 생리식염수 프라이밍 가능 지시는 최종 답변에 없다. 따라서 **0건**이다. 답변 미제공은 coverage 실패이며 위험한 반대 지시와 구분한다. Critical Error 0건이 유용한 답변이나 임상 안전성 검증 완료를 뜻하지는 않는다.

## 최종 요약

| 지표 | 결과 |
| -- | -- |
| Retrieval/Context 성공률 | **90% (9/10)** — context_ready 및 pre-LLM/budget supported 기준 |
| LLM 호출률 | **90% (9/10)** |
| Post-LLM 검증 경로 도달 | **80% (8/10)**; 명시적 문장 검증 실패 6건 |
| 최종 답변 성공률 | **0% (0/10)** — answered 기준 |
| Average Service Answer Coverage | **0%** |
| Critical Error | **0건** — 최종 서비스 출력 기준 |
| unsupported sentence | **6건** — TRF-002, 005, 006, 008, 009, 010 |
| llm_abstained | **2건** — TRF-003, 004 |
| AI_INCOMPLETE | **1건** — TRF-001 |
| no_complete_required_parent | **1건** — TRF-007 |
| 최종 quota error | **0건** |

위 90%는 기존 Retrieval Hit Rate 90%와 수치만 같으며 다른 지표다. 이번에는 source_pages 일치율을 재계산하지 않았다.

현재 가장 큰 관찰된 병목은 LLM 호출 이후의 sentence_matching 검증 차단 6건이다. Context 구성은 9문항에서 호출 가능한 단계까지 진행했고 분당 quota 차단 6건은 resume 결과로 대체됐다. 다만 최종 유효 답변은 아직 0건이며, 문장 검증 6건·모델 답변 불가 2건·응답 미완성 1건·parent 선택 실패 1건을 서로 다른 실패 경로로 다뤄야 한다.

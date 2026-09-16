# Q002 핵심 Live 및 Q001~Q006 조건부 RAG 시범 평가 결과

- 평가일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/25_ABSTENTION_POLICY_RESULT.md`
- 모델: `openai/gpt-oss-20b`
- 실제 Groq 호출: Q002 1회, Q001/Q003/Q004/Q005/Q006 0회
- 상태: **Q002 post-reconstruction citation assessment 실패로 후속 Live 중단**

## 1. 결론

Q006은 실제 Groq transport 호출 0회와 고정 근거 부족 응답을 유지했다. 이어 Q002에 대해 승인된 selected evidence 12 chunks만 외부 전송하고 Groq를 정확히 한 번 호출했다.

Q002는 HTTP 200, 반환 모델 `openai/gpt-oss-20b`, `finish_reason=stop`이었으며 selection-only strict response parsing을 통과했다. 모델은 다섯 group에서 각각 한 개씩 총 5개 source unit을 선택했다. Selection limit 16을 지켰고 required group 5/5와 adult/pediatric branch를 모두 충족했다.

그러나 reconstruction 이후 최종 citation evidence 재검사에서 `citation_assessment=duplicate_evidence`로 안전 차단됐다. 최종 server-derived Answer는 `answerable=false`, verified statement 0개다. `failure_code`와 `validation_reason`은 없지만 이는 성공이 아니라, selection parser 이후 citation sufficiency 경계가 Answer 노출을 막은 결과다.

Q002가 최종 Answer와 citation validation까지 완주해야 나머지 질문을 실행한다는 승인 조건에 따라 Q001, Q003, Q004, Q005는 호출하지 않았다. 실제 Groq 호출은 전체 허용 상한 5회 중 1회만 사용했고 retry, fallback 또는 추가 호출은 없었다.

최종 판정은 **Q002 기준 RAG 엔진 Live 완주 실패 — post-reconstruction citation assessment `duplicate_evidence`**다. 따라서 이번 결과로 “단일 지침서 기준 RAG 시범 테스트 가능 상태”라고 판정하지 않는다.

## 2. 평가한 RAG 구조

```text
질문
  → BM25 + embedding
  → RRF / rerank
  → evidence selection
  → PromptCoverage
  → SourceUnit catalog
  → Groq source-unit selection
  → server reconstruction
  → validate_answer()
  → citation evidence 재검사
  → 최종 Answer 또는 안전 차단
```

이번 Q002는 Groq selection과 server reconstruction 이후 citation evidence 재검사 경계까지 도달했으나, 최종 Answer 노출 전에 차단됐다.

## 3. 실행 순서와 호출 수

1. Q006을 기존 out-of-scope 경로로 실행했다.
2. Q006 transport 0회와 고정 문구를 확인했다.
3. Q002의 selected evidence가 승인된 12 chunks인지 단일-call transport에서 확인했다.
4. Q002를 실제 Groq에 정확히 한 번 호출했다.
5. 최종 `answerable=false`를 확인하고 Q001/Q003/Q004/Q005 실행을 중단했다.

| 질문 | 실제 Groq 호출 | 실행 여부 |
|---|---:|---|
| Q001 | 0회 | Q002 실패로 미실행 |
| Q002 | 1회 | 실행 완료 |
| Q003 | 0회 | Q002 실패로 미실행 |
| Q004 | 0회 | Q002 실패로 미실행 |
| Q005 | 0회 | Q002 실패로 미실행 |
| Q006 | 0회 | 근거 gate에서 정상 차단 |

## 4. Q006 zero-call

| 항목 | 결과 | 판정 |
|---|---|---|
| 질문 | 화성 우주선의 궤도 계산 공식은? | 기존 fixture |
| Transport 호출 | 0회 | 통과 |
| `llm_called` | false | 통과 |
| 차단 경로 | `pre_llm:domain_or_clarification` | 정상 |
| 반환 | `등록된 지침서에서 확인할 수 없습니다.` | 통과 |

## 5. Q002 Retrieval과 evidence

| 항목 | 결과 | 판정 |
|---|---:|---|
| 질문 | 진정간호 절차는? | 기존 fixture |
| Pre-budget required gold recall | 10/10, 100% | 통과 |
| Post-budget required gold recall | 10/10, 100% | 통과 |
| Selected evidence | 12 chunks | 유지 |
| Prompt / response schema | v4 / v4 | 유지 |
| AI version | 17 | 유지 |

BM25, semantic, RRF, reranker와 context 결과는 기존 승인 baseline을 사용했으며 이번 Live를 위해 다시 조정하지 않았다. 상세 retrieval funnel은 `artifacts/2026-09-13_rag-phase1-retrieval/q002_retrieval_funnel.json`에 보존돼 있다.

## 6. Q002 Groq 및 selection

| 항목 | 결과 | 판정 |
|---|---|---|
| HTTP status | 200 | 통과 |
| 반환 모델 | `openai/gpt-oss-20b` | 통과 |
| finish_reason | `stop` | 통과 |
| Prompt / completion / total tokens | 2611 / 110 / 2721 | 기록 |
| Latency | 1744.95 ms | 기록 |
| Response parse stage | `validation` | 최종 complete 미도달 |
| Failure code / validation reason | 없음 / 없음 | selection parser 오류 없음 |
| Group slots | 5 | 유지 |
| Selected units | 5/16 | 한도 통과 |
| Group별 selected count | g1=1, g2=1, g3=1, g4=1, g5=1 | 기록 |
| Required group | 5/5 | 통과 |
| Adult / pediatric branch | 모두 충족 | 통과 |

Raw response와 선택 ID 목록을 저장하지 않았으므로 모델이 선택한 구체 unit은 추정하지 않는다. Group별 count만 안전 메타데이터로 보존했다.

## 7. Reconstruction 및 citation 실패

| 항목 | 결과 | 판정 |
|---|---|---|
| Server-derived answerable | false | 실패 |
| Verified statements | 0 | 실패 |
| Reconstruction exact flag | false | 검증된 최종 Answer 없음 |
| Exact citation assessment | `duplicate_evidence` | 실패 |
| Citation coverage | 0.0 | statement 0건의 형식값, 합격 아님 |
| Unsupported number/unit/condition/negation/action | 판정 불가 | 최종 Answer 없음 |

확정 가능한 실패 단계는 **post-reconstruction citation evidence assessment**다. Selection은 group/branch/limit 검사를 통과했지만, 선택된 citation 집합에 대한 최종 evidence assessment가 `duplicate_evidence`를 반환해 사용자 노출이 차단됐다.

검증되지 않은 모델 문장이나 raw response를 저장하지 않았으므로 구체 source unit 또는 문장 내용을 역추정하지 않는다. 기존 Mock의 14-ID 성공 결과도 이번 Live 성공값으로 재사용하지 않았다.

## 8. Q001~Q006 결과 표

| 질문 | Retrieval | LLM | Answer | Citation | 최종 |
|---|---|---|---|---|---|
| Q001 | 미실행 | 호출 0 | 미실행 | - | NOT RUN |
| Q002 | required gold 10/10 | HTTP 200 / stop | 안전 차단 | `duplicate_evidence` | FAIL |
| Q003 | 미실행 | 호출 0 | 미실행 | - | NOT RUN |
| Q004 | 미실행 | 호출 0 | 미실행 | - | NOT RUN |
| Q005 | 미실행 | 호출 0 | 미실행 | - | NOT RUN |
| Q006 | 근거 없음 | 호출 0 | abstain | - | PASS |

미실행 질문을 성공으로 계산하지 않았다.

## 9. 성능 요약

| 지표 | 결과 |
|---|---:|
| 전체 fixture 수 | 6 |
| 실제 실행 질문 | 2 (Q002, Q006) |
| Positive Live 호출/성공 | 1 / 0 |
| Zero-call abstention 성공 | 1/1, 100% |
| 최종 citation coverage 100% 성공 | 0건 |
| 실제 Groq 호출 | 1/5 허용 상한 |
| 평균 latency | 1744.95 ms (실제 1호출) |
| 평균 prompt/completion/total tokens | 2611 / 110 / 2721 |

Q001/Q003/Q004/Q005가 미실행이므로 전체 positive 질문 성공률이나 단일 지침서 일반 성능으로 확대 해석하지 않는다.

## 10. 합격 기준 판정

| Q002 기준 | 결과 | 판정 |
|---|---|---|
| Q006 실제 호출 0회 | 0회 | 통과 |
| Q002 실제 호출 정확히 1회 | 1회 | 통과 |
| HTTP 200 / 모델 일치 / stop | 모두 충족 | 통과 |
| Response parse stage complete | validation | 실패 |
| Selection 1~16 | 5 | 통과 |
| Required group 5/5 | 5/5 | 통과 |
| Adult/pediatric branch | 모두 충족 | 통과 |
| Server-derived answerable=true | false | 실패 |
| Verified statements 1~16 | 0 | 실패 |
| Exact citation validation | duplicate_evidence | 실패 |
| Citation coverage 100% | 0.0 형식값 | 실패 |
| Unsupported 내용 0 | 판정 불가 | 미충족 |

Q002 완주 조건이 충족되지 않았으므로 제한된 Q001~Q006 시범 테스트 단계로 진행하지 않았다.

## 11. 비변경 및 보안

이번 Live 실행에서 production 코드를 수정하지 않았다.

- BM25, embedding, RRF와 reranker 미변경
- Retrieval/context, selected evidence와 coverage 미변경
- SourceUnit segmentation, eligibility, catalog와 group-slot schema 미변경
- `validate_source_unit_selection()`과 `validate_answer()` 미변경
- System prompt, selection/statement limit, output/request budget 미변경
- 자동 ID 보충, truncation, dedup 및 재정렬 없음
- Retry, fallback과 web search 없음
- API key와 Authorization header 미기록
- 전체 prompt, raw response, refusal 원문 및 검증되지 않은 content 미저장

## 12. 산출물과 작업 중지

새 산출물 디렉터리:

`artifacts/2026-09-14_rag-q002-server-answerability-live/`

- `live_report.json`
- `failure_summary.json`
- `review.html`

결과 문서: `docs/rag/26_RAG_PILOT_LIVE_RESULT.md`

이번 실제 Groq 호출은 Q002 1회로 종료했다. Q002 실패 후 Q001/Q003/Q004/Q005를 호출하지 않았고 코드 자동 수정이나 validator 완화도 하지 않았다. 다음 작업은 `duplicate_evidence`가 발생한 citation completeness 의미와 5-unit under-selection 관계를 실제 호출 없이 분석한 뒤 별도 승인받아야 한다.

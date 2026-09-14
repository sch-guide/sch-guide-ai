# Groq 최소 Group-slot Schema 단일 Live 재평가 결과

- 평가일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/21_GROQ_SCHEMA_COMPATIBILITY_PLAN.md`
- 모델: `openai/gpt-oss-20b`
- 평가 범위: Q006 zero-call 확인 후 Q002 실제 Groq 호출 1회
- 실제 Groq 호출: Q002 1회, Q006 0회
- 상태: **Provider schema 수락 및 응답 parsing 성공, `selection_limit`에서 안전 차단**

## 1. 결론

Q006은 실제 Groq transport 호출 0회를 유지했다. 그 조건을 통과한 뒤 Q002에 대해 승인된 selected evidence 12 chunks만 사용해 Groq API를 정확히 한 번 호출했다.

최소화된 response schema는 실제 provider에서 수락됐다. Q002는 HTTP 200, 반환 모델 `openai/gpt-oss-20b`, `finish_reason=stop`이었고 strict selection JSON parsing을 통과해 서버 selection validator까지 도달했다. 따라서 이전 HTTP 400 schema rejection은 이번 최소 schema에서 재현되지 않았다.

그러나 모델은 허용된 group-slot enum 안에서 총 21개의 source-unit ID를 선택했다. 서버 최대치는 16개이므로 `validate_source_unit_selection()`이 `AI_EVIDENCE / selection_limit`으로 fail closed했다. Provider schema에서 공식 지원이 불명확한 `maxItems`를 제거했어도 총 selection 한도를 서버가 그대로 강제한다는 안전 계약이 실제 Live에서 작동했다.

Selection limit 검사는 required group/branch coverage 검사보다 먼저 실행된다. 따라서 required group 5/5와 adult/pediatric 충족 여부는 이번 응답에서 판정하지 않았으며, 누락이나 충족으로 추정하지 않는다. Reconstruction과 `validate_answer()`도 실행되지 않아 최종 Answer, citation 및 임상 내용 검증은 완료되지 않았다.

승인 조건에 따라 retry, fallback, 두 번째 호출, 자동 dedup·선택 축소·재정렬과 코드 수정은 수행하지 않았다. 최종 판정은 **Q002 기준 RAG 엔진 Live 완주 실패 — selection limit 초과**다.

## 2. 실행 순서와 호출 제한

1. Q006을 기존 근거 부족 경로로 실행했다.
2. Q006 transport 0회와 `pre_llm:domain_or_clarification` 차단을 확인했다.
3. Q002 prompt의 chunk ID 집합이 승인된 12개와 정확히 같은지 transport에서 확인했다.
4. Q002 실제 Groq 요청을 한 번 전송했다.
5. HTTP 200 응답을 parsing한 뒤 총 21개 선택을 서버가 `selection_limit`으로 차단했다.
6. 추가 호출과 코드 변경 없이 안전한 metadata와 결과 문서만 저장했다.

| 실행 항목 | 결과 |
|---|---:|
| Q006 실제 Groq 호출 | 0회 |
| Q002 실제 Groq 호출 | 정확히 1회 |
| 자동 retry | 0회 |
| Fallback model | 0회 |
| Web search/tool call | 0회 |
| 실패 후 추가 Groq 호출 | 0회 |

## 3. Live 응답 메타데이터

| 항목 | 결과 |
|---|---|
| HTTP status | 200 |
| 요청 모델 | `openai/gpt-oss-20b` |
| 반환 모델 | `openai/gpt-oss-20b` |
| finish_reason | `stop` |
| prompt tokens | 2497 |
| completion tokens | 134 |
| total tokens | 2631 |
| latency | 720.41 ms |
| response_parse_stage | `validation` |
| failure_code | `AI_EVIDENCE` |
| failure_detail | 없음 |
| validation_reason | `selection_limit` |
| answerable | `false` — 안전 차단 결과 |
| Response selection schema | v3 |

Completion은 134/2048로 truncation이 아니며 response envelope와 strict JSON shape도 정상 처리됐다. 실패는 provider나 parser가 아니라 서버 selection cardinality 검증 단계다.

## 4. Group selection 판정

| 항목 | 결과 | 해석 |
|---|---:|---|
| Group slot 수 | 5 | schema에 g1~g5 존재 |
| Required group 수 | 5 | 기존 contract 유지 |
| Selected source units | 21 | 허용 최대 16 초과 |
| Required group 충족 수 | 판정 불가 | limit 검사에서 먼저 차단 |
| Adult branch 충족 | 판정 불가 | branch 검사 미도달 |
| Pediatric branch 충족 | 판정 불가 | branch 검사 미도달 |
| Verified statements | 0 | reconstruction 미실행 |

21개 ID가 선택됐다는 사실만 기록한다. Raw response와 선택 ID 목록은 저장하지 않았으므로 어느 group에서 몇 개를 선택했는지, required branch를 실제로 모두 포함했는지는 추정하지 않는다.

## 5. Retrieval 및 evidence 불변성

| 항목 | 결과 |
|---|---:|
| Selected evidence | 12 chunks |
| Pre-budget required gold recall | 10/10, 100% |
| Post-budget required gold recall | 10/10, 100% |
| Prompt evidence schema | v3 |
| Response selection schema | v3 |
| AI version | 15 |
| Output limit | 2048 |
| Request admission budget | 5120 |

전송 범위로 확인된 chunk IDs:

1. `eval-741858d2e9162acf8d38-chunk-00013`
2. `eval-741858d2e9162acf8d38-chunk-00015`
3. `eval-741858d2e9162acf8d38-chunk-00016`
4. `eval-741858d2e9162acf8d38-chunk-00019`
5. `eval-741858d2e9162acf8d38-chunk-00020`
6. `eval-741858d2e9162acf8d38-chunk-00021`
7. `eval-741858d2e9162acf8d38-chunk-00022`
8. `eval-741858d2e9162acf8d38-chunk-00023`
9. `eval-741858d2e9162acf8d38-chunk-00024`
10. `eval-741858d2e9162acf8d38-chunk-00025`
11. `eval-741858d2e9162acf8d38-chunk-00026`
12. `eval-741858d2e9162acf8d38-chunk-00027`

문서 전체나 다른 chunk는 전송하지 않았다.

## 6. Reconstruction과 grounding 검증

Selection이 16개 한도를 넘었으므로 reconstruction 전에 차단됐다.

| 검증 항목 | 저장 결과 | 판정 |
|---|---|---|
| Server reconstruction | 미실행 | 미충족 |
| Reconstructed text exact | `false` 형식값 | 성공값 아님 |
| Reconstructed quote exact | `false` 형식값 | 성공값 아님 |
| Exact quote/chunk citation | 미실행 | 판정 불가 |
| Citation coverage | `0.0` 형식값 | statement 0건, 합격 아님 |
| Source order | `true` 형식값 | 빈 Answer의 형식 결과, 실질 검증 불가 |
| Source order reversal count | `0` 형식값 | 실질 Answer가 없어 합격 근거로 사용하지 않음 |
| Unsupported number | 판정 불가 | reconstruction 미실행 |
| Unsupported unit | 판정 불가 | reconstruction 미실행 |
| Unsupported condition | 판정 불가 | reconstruction 미실행 |
| Unsupported negation | 판정 불가 | reconstruction 미실행 |
| Unsupported action | 판정 불가 | reconstruction 미실행 |

Mock의 14-ID reconstruction과 citation 100% 결과를 이번 Live 성공값으로 재사용하지 않았다.

## 7. 합격 기준 판정

| 기준 | 실제 결과 | 판정 |
|---|---|---|
| Q006 실제 Groq 호출 0회 | 0회 | 통과 |
| Q002 실제 Groq 호출 정확히 1회 | 1회 | 통과 |
| HTTP 200 | 200 | 통과 |
| 반환 모델 일치 | 일치 | 통과 |
| finish_reason=stop | `stop` | 통과 |
| response_parse_stage=complete | `validation` | 실패 |
| failure_code 없음 | `AI_EVIDENCE` | 실패 |
| validation_reason 없음 | `selection_limit` | 실패 |
| answerable=true | `false` | 실패 |
| Required group 5/5 | 판정 불가 | 미충족 |
| Adult branch 충족 | 판정 불가 | 미충족 |
| Pediatric branch 충족 | 판정 불가 | 미충족 |
| Selected units 1~16 | 21 | 실패 |
| Verified statements 1~16 | 0 | 실패 |
| Reconstruction 성공 | 미실행 | 미충족 |
| Exact citation validation | 미실행 | 미충족 |
| Citation coverage 100% | 0.0 형식값 | 미충족 |
| Source order reversal 0 | 실질 검증 불가 | 미충족 |
| Unsupported 내용 0 | 판정 불가 | 미충족 |

최종 판정: **최소화된 provider schema의 실제 수락은 확인했지만 Q002 기준 RAG 엔진은 `selection_limit` 때문에 Live 완주하지 못했다.**

## 8. 변경 및 보안 확인

이번 Live 실행에서 production 코드는 수정하지 않았다.

- BM25, embedding, RRF와 reranker 미변경
- Selected evidence, segmentation과 SourceUnit eligibility 미변경
- PromptCoverage, AnswerCoverage와 reconstruction 미변경
- `validate_source_unit_selection()`과 `validate_answer()` 미변경
- System prompt, output limit과 request budget 미변경
- 자동 보충, dedup, 재정렬 없음
- Retry, fallback과 web search 없음
- API key 및 Authorization header 미기록
- 전체 prompt 미저장
- Raw response/content 미저장
- Selected SourceUnit exact text 및 선택 ID 목록 미저장

## 9. 산출물

새 산출물 디렉터리:

`artifacts/2026-09-14_rag-groq-schema-compatibility-live/`

- `live_report.json`
- `failure_summary.json`
- `review.html`

기존 artifacts는 덮어쓰지 않았다.

## 10. 작업 중지

승인된 실제 호출 1회를 사용했으며 결과와 관계없이 추가 호출하지 않았다. `selection_limit`을 우회하기 위한 schema, prompt, selection limit 또는 validator 변경도 수행하지 않았다.

성공 시 예정했던 Q001~Q006 전체 및 다른 문서 확장 검증으로 진행할 조건은 충족되지 않았다. 다음 단계는 selection cardinality가 21개로 나온 원인을 실제 호출 없이 별도로 분석·설계한 뒤 사용자 승인을 받는 것이다.

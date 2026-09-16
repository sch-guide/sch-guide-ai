    # Groq schema description 적용 후 Q002 단일 Live 재평가 결과

    - 평가일: 2026-09-13
    - 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
    - 기준 구현: `docs/rag/15_UNSUPPORTED_SENTENCE_RESULT.md`
    - 모델: `openai/gpt-oss-20b`
    - 평가 범위: Q006 무호출 확인 후 Q002 실제 호출 1회
    - 실제 Groq 호출: 1회
    - 상태: 단일 호출 완료, 추가 호출 중지

    ## 1. 결론

    Q006은 실제 Groq 호출 0회를 유지했다. Q002는 승인된 selected evidence 12 chunks만 사용해 실제 Groq를 정확히 1회 호출했으며 HTTP 200, 반환 모델 `openai/gpt-oss-20b`, `finish_reason=stop`을 확인했다.

    Prompt tokens는 2548, completion tokens는 924, total tokens는 3472였다. Completion은 `OUTPUT_LIMIT=2048`보다 작아 truncation은 발생하지 않았다. Strict response envelope parsing도 성공했다.

    그러나 응답은 `response_parse_stage=validation`에서 다시 `AI_EVIDENCE / unsupported sentence`로 안전 차단됐다. Schema description 강화만으로는 이번 단일 live 응답이 exact source sentence 계약을 충족하도록 만들지 못했다.

    Raw response/content와 verified statement text를 저장하지 않았으므로 어떤 문장, label 또는 문자열 차이가 원인이었는지는 추정하지 않는다. 검증된 Answer가 만들어지지 않아 `answerable=false`, verified statement 0개이며 citation coverage와 개별 숫자·단위·조건·부정·행동 검증은 합격으로 판정할 수 없다.

    승인 조건에 따라 자동 retry, fallback 또는 두 번째 실제 호출은 수행하지 않았다.

    ## 2. 실제 호출 기록

    | 항목 | 결과 |
    |---|---|
    | Q006 실제 Groq 호출 | 0회 |
    | Q002 실제 Groq 호출 | 정확히 1회 |
    | HTTP status | 200 |
    | 요청 모델 | `openai/gpt-oss-20b` |
    | 반환 모델 | `openai/gpt-oss-20b` |
    | finish_reason | `stop` |
    | prompt tokens | 2548 |
    | completion tokens | 924 |
    | total tokens | 3472 |
    | latency | 1789.71 ms |
    | response_parse_stage | `validation` |
    | failure_code | `AI_EVIDENCE` |
    | validation_reason | `unsupported sentence` |
    | answerable | `false` |
    | verified statement 수 | 0 |
    | prompt schema version | compact evidence schema v2 |
    | AI version | 12 |

    Response가 200/stop으로 완결되고 JSON envelope와 strict Answer 구조 parsing을 지난 뒤 grounding validator에서 차단된 결과다.

    ## 3. Q006 무호출

    | 항목 | 결과 | 판정 |
    |---|---|---|
    | transport 호출 | 0회 | 통과 |
    | `llm_called` | `false` | 통과 |
    | 차단 사유 | `pre_llm:domain_or_clarification` | 정상 |

    Q006은 외부 transport에 도달하기 전에 고정 근거 부족 경로로 종료됐다.

    ## 4. Q002 evidence 및 budget 불변성

    | 항목 | 결과 | 판정 |
    |---|---:|---|
    | selected evidence | 12 chunks | 유지 |
    | pre-budget 필수 gold recall | 10/10, 100% | 유지 |
    | post-budget 필수 gold recall | 10/10, 100% | 유지 |
    | request reservation | 4410 | 유지 |
    | request admission cap | 5120 | 유지 |
    | headroom | 710 | 유지 |
    | output limit | 2048 | 유지 |

    선택 evidence chunk IDs:

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

    ## 5. Answer 및 grounding 검증

    | 검증 항목 | 저장 결과 | 해석 |
    |---|---|---|
    | strict response parsing | 성공 | envelope/schema 경계 통과 |
    | exact source-unit validation | 완료하지 못함 | `unsupported sentence` 감지 |
    | exact quote/chunk citation | 최종 완료하지 못함 | 검증된 Answer 없음 |
    | citation coverage | 0.0 | statement 0건의 기계적 값이며 합격 아님 |
    | source order | `true` | 빈 verified statement 목록의 형식 결과로 실질 검증 불가 |
    | source order reversal count | 0 | 실질 Answer가 없어 합격 근거로 사용하지 않음 |
    | 근거 없는 숫자 | 판정 불가 | raw content 미저장 |
    | 근거 없는 단위 | 판정 불가 | raw content 미저장 |
    | 근거 없는 조건 | 판정 불가 | raw content 미저장 |
    | 근거 없는 부정 | 판정 불가 | raw content 미저장 |
    | 근거 없는 행동 | 판정 불가 | raw content 미저장 |

    `unsupported sentence`는 최소 한 개의 생성 문장이 selected Chunk의 완전한 원문 문장/표 행과 일치하지 않았거나, non-empty label이 quote로 지지되지 않았음을 뜻한다. 이번 평가에서는 어느 경우인지 더 좁힐 원문을 저장하지 않았으므로 구체 원인을 단정하지 않는다.

    ## 6. 합격 기준 판정

    | 기준 | 실제 결과 | 판정 |
    |---|---|---|
    | Q006 실제 호출 0회 | 0회 | 통과 |
    | Q002 실제 호출 정확히 1회 | 1회 | 통과 |
    | HTTP 200 | 200 | 통과 |
    | 반환 모델 일치 | 일치 | 통과 |
    | finish_reason=stop | `stop` | 통과 |
    | completion < 2048 | 924 | 통과 |
    | response_parse_stage=complete | `validation` | 실패 |
    | failure_code 없음 | `AI_EVIDENCE` | 실패 |
    | validation_reason 없음 | `unsupported sentence` | 실패 |
    | answerable=true | `false` | 실패 |
    | verified statement 1~10개 | 0개 | 실패 |
    | 모든 statement가 완전한 원문 문장/표 행 | 검증 완료 못함 | 미충족 |
    | exact quote/chunk citation | 검증 완료 못함 | 미충족 |
    | citation coverage 100% | 0.0 | 미충족 |
    | source order 역전 0건 | 실질 검증 불가 | 미충족 |
    | 근거 없는 숫자·단위·조건·부정·행동 0건 | 개별 판정 불가 | 미충족 |

    호출·응답 envelope·output 길이 조건은 통과했으나 grounded Answer 합격 기준은 충족하지 못했다.

    ## 7. 변경 및 비변경 확인

    이번 live 실행을 위해 production 검색·생성 계약은 수정하지 않았다.

    유지:

    - `AI_VERSION=12`
    - `OUTPUT_LIMIT=2048`
    - `GROQ_REQUEST_TOKEN_BUDGET=5120`
    - strict JSON schema와 네 Field description
    - system prompt와 Answer JSON 구조
    - compact evidence schema v2와 selected evidence 12 chunks
    - validator, `source_sentences()`, `sentence_evidence()`, `validate_answer()`
    - BM25, embedding, RRF, reranker와 evidence selection

    평가 결과를 안전하게 저장하기 위한 `tools/rag_schema_description_live_evaluate.py`만 추가했다. 이 wrapper는 기존 단일-call evaluator를 사용하되 statement text, evidence quote, 전체 prompt와 raw response를 artifacts에서 제외한다.

    ## 8. 보안 및 실행 제약

    - API key 미기록
    - Authorization header 미기록
    - 전체 prompt 미저장
    - raw response/content 미저장
    - refusal 원문 미저장
    - verified statement text 및 evidence quote 미저장
    - 자동 retry 없음
    - fallback 없음
    - web search 없음
    - Q002 외 실제 Groq 호출 없음
    - 실제 호출 이후 추가 호출 없음

    ## 9. 산출물 및 작업 중지

    기존 artifacts는 덮어쓰지 않았다. 새 결과는 다음 경로에 저장했다.

    `artifacts/2026-09-13_rag-groq-schema-description-live/`

    - `groq_evaluation_report.json`
    - `review.html`

    이번 실제 호출은 정확히 1회로 종료했다. 결과와 관계없이 추가 호출하지 않는 조건을 지켰으며, `unsupported sentence`에 대한 추가 prompt/schema/validator 변경도 수행하지 않는다. 다음 작업은 사용자 별도 승인 후에만 진행한다.

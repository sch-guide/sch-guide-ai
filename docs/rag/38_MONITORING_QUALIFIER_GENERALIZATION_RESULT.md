# Monitoring Qualifier Generalization 구현 결과

- 작업일: 2026-09-16
- 브랜치: `boha-rag`
- 범위: Branch × Phase × Monitoring Item × Action 일반화
- 실제 Groq 호출: **0회**
- 검색 버전: `SEARCH_VERSION=15`

## 1. 구현 결과

`QueryPlan`에 다음 request-scoped qualifier를 추가했다.

- `monitoring_branches`: `adult`, `pediatric`
- `monitoring_phase`: `before`, `during`, `after`
- `monitoring_item`: `respiratory_rate`, `pulse`, `oxygen_saturation`, `consciousness`
- `monitoring_action`: `observe`, `check`, `measure`, `assess`

질문 문자열이나 chunk ID를 production에 하드코딩하지 않았다. 정규식은 시간 표현, monitoring 항목과 구어체 동사를 일반 규칙으로 처리한다.

## 2. 시간 표현

다음 표현을 이벤트에 결합된 시간 표현으로 처리한다.

- before: `전`, `전에`, `하기 전`
- during: `중`, `중에`, `시`, `할 때`, `하는 동안`
- after: `후`, `후에`, `하고 나서`

`필요시`, `회복 시`, `동의 시`는 during으로 처리하지 않는다. 전역적인 `시` 변환은 추가하지 않았다.

## 3. Evidence gate

monitoring item이 질문에 명시되면 다음 조건을 모두 만족하는 본문만 seed가 될 수 있다.

1. 기존 topic 호환성
2. 요청 phase 직접 일치
3. 요청 monitoring item 직접 명시
4. monitoring action을 지지하는 동작 표현 존재
5. 기존 evidence group의 requested branch 충족

일반적인 `확인`, `평가`, `모니터링` 표현만으로 특정 항목 질문을 통과시키지 않는다. `호흡수 ⊂ 활력징후` 같은 임상 ontology도 추가하지 않았다.

## 4. 질문별 결과

| ID | 질문 | branch | phase | item | action | pre | post | 선택 근거 | 직접 항목 근거 | 결과 |
|---|---|---|---|---|---|---|---|---:|---:|---|
| MQ01 | 소아 진정 시 호흡수도 봐? | pediatric | during | respiratory_rate | observe | supported | supported | 4 | 3 | PASS |
| MQ02 | 소아 진정 중 호흡수 확인해? | pediatric | during | respiratory_rate | check | supported | supported | 4 | 3 | PASS |
| MQ03 | 소아 진정할 때 맥박도 재? | pediatric | during | pulse | measure | supported | supported | 4 | 3 | PASS |
| MQ04 | 소아 진정 중 산소포화도 봐? | pediatric | during | oxygen_saturation | observe | supported | supported | 6 | 5 | PASS |
| MQ05 | 소아 진정 중 의식상태도 확인해? | pediatric | during | consciousness | check | supported | supported | 4 | 1 | PASS |
| MQ06 | 성인 진정할 때 산소포화도 봐? | adult | during | oxygen_saturation | observe | supported | supported | 5 | 3 | PASS |
| MQ07 | 성인 진정 중 호흡수도 확인해? | adult | during | respiratory_rate | check | missing_requested_branch | 미실행 | 0 | 0 | PASS(안전 차단) |

MQ07은 성인 본문의 `활력징후`를 호흡수로 자동 확장하지 않으므로 직접 근거 불충분으로 차단된다. 소아의 호흡수 근거가 성인 질문을 대신 통과시키지도 않는다.

## 5. 회귀 검증

| 검증 | 결과 |
|---|---|
| 신규 monitoring qualifier fixture | 7/7 PASS |
| 기존 Sedation UAT | 45/45 PASS |
| Q001~Q006 및 주요 RAG 집중 회귀 | PASS |
| Q002 required facet | 41개, 빈 allowlist 0개 |
| Facet schema array 사용 | 없음 |
| selection limit 16 | 기존 테스트 유지 |
| Q006 provider transport | 0회 |
| parent atomicity | 기존 회귀 통과 |
| presentation sidecar | 기존 회귀 통과 |
| topic+aspect normalization | 기존 회귀 통과 |
| 집중 테스트 | 177 passed |
| 핵심 RAG 회귀 | 172 passed |
| 전체 pytest | 442 passed, 1 skipped |
| Ruff | PASS |
| `git diff --check` | PASS |

전체 테스트의 6개 warning은 fastembed의 pooling 변경 안내이며 이번 변경으로 생긴 실패가 아니다.

## 6. 변경 파일

- `mvp/query.py`: monitoring qualifier 구조와 일반화된 항목·동사 정규화
- `mvp/retrieval.py`: 이벤트 결합 temporal grammar 보강
- `mvp/evidence.py`: phase 및 직접 monitoring item을 요구하는 evidence gate
- `mvp/library.py`: query/evidence 캐시 분리를 위해 `SEARCH_VERSION` 15로 증가
- `tests/fixtures/monitoring_qualifier_queries.json`: 7개 평가 질문
- `tests/test_monitoring_qualifier_generalization.py`: qualifier 및 offline funnel 회귀
- `tools/rag_monitoring_qualifier_evaluate.py`: 결과 JSON/CSV/HTML 생성기

BM25 점수식, tokenizer, embedding, RRF, reranker, Facet-slot, validator, retry/fallback/web search는 변경하지 않았다.

## 7. Artifacts

- `artifacts/2026-09-16_rag-monitoring-qualifier-generalization/report.json`
- `artifacts/2026-09-16_rag-monitoring-qualifier-generalization/results.csv`
- `artifacts/2026-09-16_rag-monitoring-qualifier-generalization/review.html`

Artifacts에는 API key, Authorization header, 전체 prompt 또는 provider raw response가 없다.

## 8. 코드 리뷰

변경 diff 전체를 검토했다. 기존 branch group 판정 뒤에 다른 branch 근거가 대체 근거로 남는 경로는 확인되지 않았다. 특정 monitoring item 질문은 item 정규식과 phase 검사를 모두 통과해야 seed가 되며, 최종적으로 기존 requested branch 검사를 다시 통과해야 한다. 기존 validator와 facet selection 계약은 수정하지 않았다.

잔여 제한은 성인 지침의 `활력징후`와 개별 항목 사이의 ontology가 의도적으로 연결되지 않았다는 점이다. 이는 이번 요구사항에 따른 안전한 제한이며 별도 승인 전까지 유지한다.

# Broad procedure AnswerCoverage(B/C) 구현 및 Live 결과

- 작성일: 2026-09-14
- 상태: **production 구현·오프라인·Mock·전체 회귀 통과 / Q002 Live 안전 차단**
- 실제 Groq 호출: **총 1회(Q002 1회, Q006 0회)**
- 최종 판정: **Q002 기준 RAG 엔진 Live 완주 실패**

## 1. 변경 이유

기존 AnswerCoverage는 broad procedure 질문에서도 required group과 branch의 대표 unit만 선택하면 통과할 수 있었습니다. 이번 구현은 group 대표성에 더해 phase, phase별 action family, action diversity와 source order를 서버가 검증하도록 강화했습니다.

## 2. ProcedureAnswerRequirement 구조

`ProcedureAnswerRequirement`는 요청마다 QueryPlan과 실제 prompt catalog에서 계산합니다. production에서는 Q002 gold, stage label, chunk ID, per-group expected count를 사용하지 않습니다.

검증 필드는 broad procedure 여부, required group·branch, `before/during/after` phase, branch·phase별 action family, action diversity, source order와 selection capacity입니다. Q002 capacity witness는 11개로 selection 한도 16 안에 있습니다.

유지된 제한과 버전:

- `OUTPUT_LIMIT=2048`
- `GROQ_REQUEST_TOKEN_BUDGET=5120`
- selection·statement 최대 16
- `AI_VERSION=18`
- `PROMPT_EVIDENCE_SCHEMA_VERSION=5`
- `RESPONSE_SELECTION_SCHEMA_VERSION=4`

## 3. Under-selection 차단 결과

required group 5개에서 각각 selectable unit 1개를 고르는 가능한 조합 72개를 전수 검사했습니다. 72개 모두 `selection_missing_phase`로 차단됐습니다. 21 selectable units 전체 선택은 기존 `selection_limit`으로 차단됐습니다.

자동 보충, 자동 재정렬, 자동 dedup은 추가하지 않았습니다.

## 4. Duplicate 의미와 metadata 결과

기존 duplicate signature `(document_id, server branch, clean(cited text).casefold())`를 유지했습니다.

- 동일 source unit ID 반복: `selection_duplicate_id`
- 같은 문서·같은 branch·정규화된 exact text 동일: `duplicate_evidence`
- 다른 문서 또는 실제 branch가 다름: duplicate 아님
- 의미만 같고 text가 다름: duplicate 아님

citation 축약으로 branch heading이 사라질 때 발생할 수 있는 false duplicate는 서버가 보존한 evidence-group branch metadata를 reassessment에 전달해 방지했습니다. duplicate 정의와 validator는 완화하지 않았습니다.

## 5. Mock 결과

Q002 Mock 결과:

- selected evidence: 12 chunks
- pre/post required gold recall: 10/10
- source-unit gold recall: 10/10
- selected source units: 14
- group count: g1=1, g2=1, g3=1, g4=6, g5=5
- adult/pediatric 및 required phase/action coverage 충족
- reconstruction: 14 statements
- exact text/quote 및 citation coverage: 100%
- citation assessment: `supported`
- duplicate evidence: 없음
- source order reversal: 0
- unsupported number/unit/condition/negation/action: 0
- server-derived answerable: true

Q006 Mock은 catalog 0, transport 0회와 고정 근거 부족 응답을 유지했습니다. 전체 테스트는 `305 passed, 1 skipped`, Ruff는 통과했습니다.

## 6. Q002 Live 결과

Q006 zero-call 확인은 통과했습니다.

- Q006 실제 Groq 호출: 0회
- Q006 `llm_called`: false
- 고정 근거 부족 응답: 유지

Q002는 정확히 1회 호출됐습니다.

| 항목 | 결과 |
|---|---|
| 검색·전송된 evidence | 12 chunks |
| 실제 Groq 호출 | 1회 |
| HTTP | 200 |
| 반환 모델 | `openai/gpt-oss-20b` |
| finish reason | `stop` |
| latency | 1,172.24 ms |
| prompt tokens | 2,651 |
| completion tokens | 67 |
| total tokens | 2,718 |
| response parse stage | `validation` |
| failure code | `AI_EVIDENCE` |
| validation reason | `selection_empty` |
| block reason | `invalid_citation_or_statement` |
| selected source units | 0 |
| server answerable | false |
| verified statements | 0 |
| citation coverage | 0% |
| duplicate evidence | false |

HTTP와 provider completion은 정상 종료됐지만 provider가 non-empty SourceUnit selection을 반환하지 않았습니다. 서버는 `selection_empty`로 안전하게 차단했으며 `response_parse_stage=complete`에 도달하지 못했습니다. 따라서 phase/action/group/branch의 최종 선택 coverage와 exact citation은 평가할 답변이 없습니다.

## 7. Q001~Q006 결과

Q002가 완전히 통과한 경우에만 Q001/Q003/Q004/Q005를 호출하는 조건을 지켰습니다.

| 질문 | 실제 호출 | 결과 | 사유 |
|---|---:|---|---|
| Q001 | 0 | 미실행 | Q002 실패 후 중단 |
| Q002 | 1 | FAIL | `selection_empty` |
| Q003 | 0 | 미실행 | Q002 실패 후 중단 |
| Q004 | 0 | 미실행 | Q002 실패 후 중단 |
| Q005 | 0 | 미실행 | Q002 실패 후 중단 |
| Q006 | 0 | PASS | zero-call 고정 응답 |

전체 실제 Groq 호출은 1회입니다. retry, fallback, web search와 두 번째 Q002 호출은 없었습니다.

## 8. 성능·latency·token

오프라인 prompt reservation은 4,709/5,120 tokens이고 headroom은 411 tokens입니다. 최소 headroom 377 tokens를 충족했습니다.

실제 provider 측정값은 latency 1.172초, prompt 2,651 tokens, completion 67 tokens, total 2,718 tokens입니다. 짧은 completion과 `selection_empty`가 함께 나타났지만 raw response를 저장하지 않았으므로 그 이상의 provider 내부 판단은 추정하지 않습니다.

## 9. 아직 남은 한계

Mock gold 14 IDs는 강화된 phase/action requirement를 통과하지만 실제 모델은 이번 단일 Live에서 0개를 선택했습니다. 서버 안전장치는 의도대로 근거 없는 답변을 막았지만 provider의 selection 품질은 아직 Q002 완주 수준에 도달하지 않았습니다.

API key, Authorization header, 전체 prompt와 raw provider response/content는 저장하지 않았습니다. 검증된 최종 답변이 생성되지 않아 review에는 답변·citation 없음으로 표시합니다.

## 10. 다음 단계

이번 승인 범위에서는 Q002 재호출과 코드 자동 수정을 금지했으므로 여기서 중단합니다. 다음 작업에서는 이번 안전 metadata를 기준으로 provider가 왜 빈 selection을 선택했는지 오프라인에서 분석해야 합니다. PromptCoverage, AnswerCoverage와 validator를 완화하거나 서버가 unit을 자동 보충하는 방식은 사용하지 않습니다.

## 산출물

- [Offline facet report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/offline_facet_report.json)
- [Duplicate contract report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/duplicate_contract_report.json)
- [Mock report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/mock_report.json)
- [Live report](../../artifacts/2026-09-14_rag-procedure-answer-coverage/live_report.json)
- [Review HTML](../../artifacts/2026-09-14_rag-procedure-answer-coverage/review.html)

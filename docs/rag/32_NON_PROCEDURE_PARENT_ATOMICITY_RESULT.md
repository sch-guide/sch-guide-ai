# Non-procedure Parent Atomicity 수정 및 회귀 검증 결과

- 구현·검증일: 2026-09-15
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 실제 Groq 호출: 0회
- 상태: parent partial inclusion 제거 및 전체 오프라인 회귀 통과

## 1. 결론

`expand_context()`의 분기 연결은 정상이다.

- procedure 질문: `_expand_procedure_context()`
- non-procedure 질문: `_expand_non_procedure_context()`

Non-procedure 경로의 기존 inline 중복 로직과 unreachable return은 제거된 상태이며 helper 정의와 dispatch가 각각 한 개다.

첫 parent group이 context limit보다 큰 경우 사용하던 `additions[:limit]` 부분 포함을 제거했다. 이제 가장 관련도 높은 첫 parent조차 통째로 담을 수 없으면 `return []`로 종료한다. 빈 context는 기존 pre-LLM evidence gate에서 `no_topic_evidence`로 fail closed되며 어떤 parent 조각도 prompt로 진행하지 않는다.

## 2. Import 검수

`tests/test_rag_context.py`의 import를 확인했다.

- 중복 import: 0건
- 미사용 import: 0건
- Ruff import/style 검사: 통과

따라서 import를 삭제하거나 재배치할 추가 수정은 필요하지 않았다.

## 3. Parent atomicity 회귀

새 계약은 다음 테스트로 고정했다.

- parent 2개 chunk
- context limit 1
- 반환 hits 0개
- evidence assessment `sufficient=false`
- reason `no_topic_evidence`

Procedure 경로의 기존 limit 처리와 source-order 테스트는 그대로 유지하며 통과했다.

정적 확인:

| 항목 | 결과 |
|---|---:|
| `_expand_non_procedure_context` 정의 | 1개 |
| non-procedure dispatch | 1개 |
| `additions[:limit]` | 0건 |
| oversized first parent `return []` | 확인 |

## 4. Q001~Q006 전후 결과

| Case | 변경 전 pre/post | 변경 후 pre/post | Mock answerable / calls | 판정 |
|---|---|---|---|---|
| Q001 목적 | supported / supported | supported / supported | true / 1 | 유지 |
| Q002 절차 | supported / supported | supported / supported | true / 1 | 유지 |
| Q003 사전 준비 | incomplete_semantic_block / 미도달 | supported / supported | true / 1 | 개선 유지 |
| Q004 목적 표현 변형 | incomplete_semantic_block / 미도달 | supported / supported | true / 1 | 개선 유지 |
| Q005 주의사항 | no_topic_evidence / 미도달 | supported / supported | true / 1 | 개선 유지 |
| Q006 외부 주제 | domain_or_clarification / 미도달 | domain_or_clarification / 미도달 | false / 0 | 안전 유지 |

표의 calls는 로컬 `MockTransport` 호출 수다. 실제 Groq 호출은 0회다.

## 5. Paraphrase 전후 결과

| 질문 | 변경 전 | 변경 후 |
|---|---|---|
| 진정간호 목적 알려줘 | fact, supported | purpose, supported |
| 진정 간호는 왜 시행하나요? | fact, incomplete_semantic_block | purpose, supported |
| 진정의 목적이 뭐야? | fact, supported | purpose, supported |
| 진정 전에 뭘 준비해야 해? | materials, incomplete_semantic_block | preparation, supported |
| 진정 시행 전 확인할 것은? | fact, incomplete_semantic_block | preparation, supported |
| 진정 전 체크사항 알려줘 | fact, incomplete_semantic_block | preparation, supported |
| 진정 시 주의할 점은? | cautions, incomplete_semantic_block | cautions, supported |
| 진정간호에서 조심해야 할 것은? | fact, no_topic_evidence | cautions, supported |
| 진정 중 안전하게 봐야 할 항목은? | fact, incomplete_semantic_block | cautions, supported |

최신 evaluator 결과:

- Q001~Q006 기대값: 6/6
- paraphrase: 9/9
- Q006 zero-call: 통과
- 실제 Groq 호출: 0회

## 6. Q002 Facet-slot 및 Q006 안전성

Q002 SourceUnit/Facet-slot 회귀 전체는 37개 테스트가 통과했다.

- Facet contract 및 allowlist
- Gold reconstruction
- exact citation 및 citation coverage
- selection limit과 source order
- fail-closed negative cases

Q006은 out-of-scope pre-LLM 차단과 transport 0회를 유지했다.

## 7. 테스트와 정적 검사

| 검증 | 결과 |
|---|---|
| Parent atomicity/context | 8 passed |
| Pilot generalization + context | 45 passed |
| Q002 Facet-slot | 37 passed |
| 전체 pytest | 346 passed, 1 skipped |
| Ruff | 통과 |
| `git diff --check` | exit 0 |
| 코드리뷰 | 추가 actionable finding 없음 |

전체 `git diff --check`는 성공했다. 저장소에 남아 있던 과거 pytest 임시 DB에 대한 permission warning이 출력됐지만 이번 변경 파일의 whitespace 오류는 없었다.

## 8. 산출물

`artifacts/2026-09-15_rag-parent-atomicity-verification/`

- `pilot_query_generalization_report.json`
- `test_results.json`
- `review.html`

이번 단계에서는 실제 Groq 호출을 수행하지 않았다.

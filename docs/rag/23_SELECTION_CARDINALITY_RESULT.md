# Source Unit 최소 선택 Prompt 및 Policy 구현 결과

- 구현·검증일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/22_SELECTION_CARDINALITY_PLAN.md`
- 구현안: B. 최소 선택 prompt 강화 + D-min. 일반 selection policy metadata
- 검증 방식: Q002 fixture 및 `httpx.MockTransport`
- 실제 Groq 호출: 0회
- 상태: 구현·Mock·전체 회귀 완료, 실제 재평가 승인 대기

## 1. 결론

`SOURCE_UNIT_SYSTEM`에 `selectable=true`는 선택 가능성일 뿐 의무가 아니며, `required=true`는 group 전체에 적용된다는 의미를 명시했다. 각 required group에서는 질문에 충분한 가장 작은 non-empty subset만 선택하고, 전체 ID를 세어 16개 이하인지 확인하도록 했다. 최소 충분 선택이 16개를 넘으면 모든 group array가 빈 `answerable=false`를 반환해야 하며, 제한을 맞추기 위한 잘라내기·자동 제거·재정렬·중복 제거·ID 생성은 금지했다.

Compact catalog root에는 일반 `selection_policy`를 추가했다. 이 policy에는 Q002, gold stage, chunk ID 또는 group별 기대·최대 개수가 없고, 전 요청에 공통인 최소 충분 선택 목표와 전역 최대 16개만 있다. Wire format 변경으로 `PROMPT_EVIDENCE_SCHEMA_VERSION=4`, 생성 의미 변경으로 `AI_VERSION=16`을 적용했다. Provider response schema는 v3을 유지했다.

Q002 Mock에서는 gold 10/10을 나타내는 14개 ID가 required group 5/5와 adult/pediatric branch를 충족했고, 서버 reconstruction을 거쳐 14개 statement로 정상 완료됐다. Exact text와 quote는 100%, citation coverage는 100%, source order 역전은 0건이었다. 반대로 selectable 21개 전체를 선택한 응답은 자동 축소 없이 기존 `AI_EVIDENCE / selection_limit`으로 차단됐다.

Policy와 instruction 추가 후 request reservation은 4701/5120, headroom은 419 tokens다. 요구 최소 headroom `max(256, ceil(4701 × 8%)) = 377`을 42 tokens 여유로 충족했다. Q006은 catalog 0건, transport 0회였다. 전체 테스트 290 passed, 1 skipped이며 Ruff와 `git diff --check`도 통과했다.

## 2. 구현 내용

### 최소 선택 system instruction

다음 의미를 Q002 전용 표현 없이 추가했다.

- `selectable=true`는 eligible이며 mandatory가 아니다.
- `required=true`는 group에 적용되며 그 group의 모든 unit 선택을 뜻하지 않는다.
- Answerable이면 required group별로 질문에 충분한 가장 작은 non-empty subset을 선택한다.
- Required group이라는 이유만으로 selectable unit을 전부 고르지 않는다.
- 답변에 불필요한 redundant, contextual, supplementary, overlapping unit을 제외한다.
- 반환 전에 모든 group의 선택 ID 수를 합산하고 16개 이하인지 확인한다.
- 최소 충분 선택이 16개를 넘으면 `answerable=false`와 모든 빈 group array를 반환한다.
- 제한을 맞추기 위한 truncate, auto-remove, reorder, deduplicate 또는 ID invention을 금지한다.

기존 source order, required group/branch, unknown·duplicate·non-selectable·wrong-slot 차단 instruction은 유지했다.

### Compact catalog selection policy

Catalog envelope은 다음 일반 metadata를 포함한다.

```json
{
  "selection_policy": {
    "goal": "smallest_sufficient_subset",
    "maximum_total": 16,
    "selectable_means": "eligible_not_mandatory",
    "required_group_means": "non_empty_sufficient_subset"
  }
}
```

`maximum_total`은 request-scoped `BranchAwareSelectionContract.maximum_selected_units`에서 가져오므로 server limit과 별도 상수로 중복되지 않는다. Group별 expected/max count는 제공하지 않는다.

## 3. 버전과 불변식

| 항목 | 결과 |
|---|---:|
| `AI_VERSION` | 16 |
| `PROMPT_EVIDENCE_SCHEMA_VERSION` | 4 |
| `RESPONSE_SELECTION_SCHEMA_VERSION` | 3, 유지 |
| `SEARCH_VERSION` | 12, 유지 |
| `CHUNK_VERSION` | 4, 유지 |
| Selection 최대 | 16, 유지 |
| Answer statements 최대 | 16, 유지 |
| `OUTPUT_LIMIT` | 2048, 유지 |
| `GROQ_REQUEST_TOKEN_BUDGET` | 5120, 유지 |

Response schema, `validate_source_unit_selection()`, reconstruction과 `validate_answer()`는 수정하지 않았다. Selected evidence 12 chunks, segmentation, eligibility, PromptCoverage, AnswerCoverage, BM25, embedding, RRF와 reranker도 유지했다.

## 4. Q002 정상 Mock 결과

| 검증 항목 | 결과 | 판정 |
|---|---:|---|
| Selected evidence | 12 chunks | 유지 |
| Selectable source units | 21 | 유지 |
| Gold 선택 | 14/16 IDs | 통과 |
| Pre-budget required gold | 10/10 | 통과 |
| Post-budget required gold | 10/10 | 통과 |
| Source-unit gold stage | 10/10 | 통과 |
| Required groups | 5/5 non-empty | 통과 |
| Required branches | adult, pediatric | 통과 |
| Reconstructed statements | 14 | 통과 |
| Reconstructed text exact | 100% | 통과 |
| Reconstructed quote exact | 100% | 통과 |
| Exact citation validation | `supported` | 통과 |
| Citation coverage | 100% | 통과 |
| Source order reversal | 0건 | 통과 |
| Response parse stage | `complete` | 통과 |
| Failure code/reason | 없음 | 통과 |

Group별 Mock 선택은 common 세 group에서 각 1개, adult group에서 6개, pediatric group에서 5개로 총 14개다. 이 수치는 평가 fixture에서 검증할 뿐 production policy나 prompt에는 넣지 않았다.

## 5. 과선택 및 기타 fail-closed 검증

Selectable 21개를 모두 선택한 합성 response를 실제 MockTransport 경로에 넣었다.

| 항목 | 결과 |
|---|---|
| MockTransport 호출 | 1회 |
| 입력 selected ID 수 | 21 |
| 자동 잘라내기·선택 축소 | 없음 |
| 결과 | `AI_EVIDENCE / selection_limit` |
| Answerable | false |

기존 회귀 테스트는 다음도 계속 차단한다.

- Required common group 누락: `selection_missing_group`
- Required adult/pediatric branch 누락: `selection_branch`
- Duplicate: `selection_duplicate_id`
- Wrong slot: `selection_wrong_group`
- Unknown ID: `selection_unknown_id`
- Non-selectable ID: `selection_non_selectable`
- `answerable=false`와 ID 동시 반환: `selection_inconsistent`
- Group 내부 또는 전체 source order 역전: `selection_source_order`

Server가 누락 unit을 보충하거나 모델 선택을 dedup·재정렬하지 않는다.

## 6. Catalog와 prompt 일반성 검증

| 점검 | 결과 |
|---|---|
| Policy key가 승인된 네 개뿐임 | 통과 |
| Q002 식별자 포함 | 없음 |
| Gold/stage 포함 | 없음 |
| Chunk ID 포함 | 없음 |
| Group별 expected/max count 포함 | 없음 |
| System에 eligible-not-mandatory 의미 | 포함 |
| System에 required는 group 의미 | 포함 |
| System에 smallest sufficient subset 의미 | 포함 |
| Catalog policy에 smallest sufficient subset 의미 | 포함 |
| Selected 12 chunk ID·순서 매핑 | 기존과 동일 |

평가 도구와 테스트 fixture에는 Q002 검증 데이터가 존재하지만 production `mvp/ai.py`에는 Q002 ID, 단계명, chunk ID나 group별 목표 개수를 추가하지 않았다.

## 7. Budget

| 항목 | 결과 |
|---|---:|
| Request reservation | 4701 |
| Admission cap | 5120 |
| Headroom | 419 |
| 최소 요구 headroom | 377 |
| 최소 대비 추가 여유 | 42 |
| 판정 | 통과 |

이전 최소-schema Mock의 reservation 4528과 비교하면 system instruction 및 policy metadata로 173 tokens 증가했다. Admission cap, output limit, evidence 원문과 response schema는 변경하지 않았다. `Quota.reserve()`에는 cap 5120이 아니라 실제 계산 reservation 4701이 전달됐다.

## 8. Q006 및 회귀

| 항목 | 결과 |
|---|---:|
| Q006 SourceUnit catalog | 0건 |
| Q006 MockTransport | 0회 |
| 실제 Groq 호출 | 0회 |
| 집중 테스트 | 43 passed |
| 전체 테스트 | 290 passed, 1 skipped |
| Ruff | 통과 |
| `git diff --check` | 통과 |

전체 테스트의 warning 2건은 기존 FastEmbed multilingual MiniLM pooling 기본값 안내다. 이번 prompt/policy 변경으로 생긴 실패가 아니며 embedding 설정은 변경하지 않았다.

## 9. Code review

AGENTS.md의 `code-review` 절차로 production diff, version/cache, Mock 경계, artifacts 보안과 금지 범위를 검수했다.

- 추가 조치가 필요한 correctness·safety·regression 결함 없음
- Response schema v3와 기존 validator 변경 없음
- Selection/statement limit 증가 없음
- 자동 truncation, 보충, dedup 또는 재정렬 없음
- Q002/gold/chunk별 목표의 production 하드코딩 없음
- API key, Authorization, 전체 prompt, raw response/content 저장 경로 추가 없음
- Retry, fallback 및 web search 추가 없음

남은 검증 경계는 실제 모델이 새 최소 선택 instruction과 policy metadata를 따라 16개 이하의 충분한 ID를 반환하는지 여부다. Mock은 서버 계약과 fail-closed 동작을 입증하지만 provider 모델 행동을 입증하지 않는다.

## 10. 변경 파일과 산출물

핵심 변경:

- `mvp/ai.py`: 최소 선택 system instruction, catalog `selection_policy`, version 증가
- `tests/test_source_unit_selection.py`: policy 일반성, 12-chunk 매핑, 14-ID 성공 및 21-ID 차단
- 버전·serialization 관련 회귀 테스트
- `tools/rag_source_unit_evaluate.py`: policy audit와 21-ID 과선택 Mock 결과

최종 산출물:

`artifacts/2026-09-14_rag-selection-cardinality-mock-02/`

- `mock_report.json`
- `test_results.json`
- `review.html`

같은 작업 중 생성한 `artifacts/2026-09-14_rag-selection-cardinality-mock/`은 덮어쓰지 않았고, `-02`를 최종 검수본으로 사용한다. 기존 artifacts도 변경하지 않았다.

## 11. 작업 중지

승인된 구현, Mock 검증, 전체 테스트와 결과 문서 작성을 완료했다. 이번 단계의 실제 Groq 호출은 0회다. 실제 Q002 재평가는 사용자의 별도 승인 전까지 수행하지 않는다.

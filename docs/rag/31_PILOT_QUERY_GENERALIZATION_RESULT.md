# Pilot Query Generalization 최소 수정 및 오프라인 검증 결과

- 구현·검증일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 베이스라인: `docs/rag/31_PILOT_QUERY_GENERALIZATION_TEST_RESULT.md`
- 검증 방식: 로컬 안정 corpus, 기존 retrieval pipeline, fixture 및 `httpx.MockTransport`
- 실제 Groq 호출: **0회**
- 상태: **Q001~Q005 expected answerable, Q006 expected abstain/zero-call 충족. Live 재평가 승인 대기**

## 1. 결론

베이스라인에서 확인한 Q003~Q005 차단 원인을 질문 문자열이나 chunk ID에 결합하지 않는 일반 규칙으로 수정했다.

- 띄어쓴 `X 간호`를 topic subject 추출에서만 `X간호`와 동일하게 정규화했다.
- 목적, 사전 준비·확인, 주의·조심·안전 관찰을 각각 일반 aspect로 분류했다.
- 문서 metadata는 topic 연결에 사용할 수 있지만, 요청 aspect는 실제 chunk body가 지지해야만 evidence seed가 되게 했다.
- Non-procedure context는 parent metadata가 있을 때 의미 block을 통째로 포함하거나 제외한다.
- 문장부호가 없는 완결된 독립 bullet/list 행은 selectable로 인정하되 heading, caption, 단계 표지와 orphan fragment는 계속 제외한다.

수정 후 Q001~Q005는 pre/post-budget evidence assessment와 Mock reconstruction/citation 검증을 모두 통과했다. Q006은 명시적 out-of-scope 판정, Groq/Mock transport 0회 및 고정 근거 부족 응답을 유지했다.

전체 테스트는 `346 passed, 1 skipped`, Ruff와 변경 파일 `git diff --check`도 통과했다. 실제 Groq 호출은 수행하지 않았다.

## 2. 기대값과 전후 결과

| Case | 사람 기대값 | 기존 결과 | 수정 후 pre/post | Mock transport | 최종 오프라인 판정 |
|---|---|---|---|---:|---|
| Q001 `진정간호 목적은?` | 답변 | `supported` | `supported / supported` | 1회 | 통과 |
| Q002 `진정간호 절차는?` | 답변 | Live 통과 | `supported / supported` | 1회 | 통과 |
| Q003 `진정 전 준비사항은?` | 답변 | `incomplete_semantic_block` | `supported / supported` | 1회 | 통과 |
| Q004 `진정 간호의 목적은 무엇인가요?` | 답변 | `incomplete_semantic_block` | `supported / supported` | 1회 | 통과 |
| Q005 `진정간호 주의사항은?` | 답변 | `no_topic_evidence` | `supported / supported` | 1회 | 통과 |
| Q006 `화성 우주선의 궤도 계산 공식은?` | 답변 금지 | `domain_or_clarification` | `domain_or_clarification / 미도달` | 0회 | 안전 통과 |

위 Mock transport 호출은 합성 로컬 response에 대한 호출 횟수다. 외부 Groq API 호출 수는 0이다.

## 3. 기존 차단 원인과 최소 수정

### Q001 / Q004 표현 동등성

두 질문은 embedding query와 목적 근거가 같지만, Q004의 띄어쓴 `진정 간호`가 topic extraction에서 넓은 `진정`으로 축소됐다. 그 결과 목적과 무관한 parent block까지 evidence 후보가 되어 일부 block이 불완전하게 포함됐다.

수정 후에는 원 BM25 query나 tokenizer를 바꾸지 않고 topic subject 추출에서만 일반 `X 간호` 패턴을 canonicalize한다. Q001과 Q004는 다음이 완전히 같아졌다.

- QueryPlan kind: `purpose`
- topic: `진정간호`
- pre/post assessment: `supported`
- selected evidence: 목적 본문 `chunk-00003`
- evidence group key 집합

### Q003 사전 준비

기존에도 temporal phase `before`와 사전 평가·설명·동의 근거는 retrieval에 있었다. 그러나 `준비사항`, `확인할 것`, `체크사항`이 `fact` 또는 `materials`로 갈렸고, non-procedure context round-robin이 서로 다른 parent를 부분 포함해 `incomplete_semantic_block`을 일으켰다.

수정 후에는 다음 계약을 적용한다.

- 물품을 명시한 질문만 `materials`
- phase와 준비·확인 의미가 결합된 질문은 `preparation`
- parent metadata가 있는 non-procedure block은 원자적으로 포함
- evidence scope는 빈 section보다 `parent_id`를 우선

Q003은 `kind=preparation`, `domain=hospital`, `requested_phase=before`로 계획되고 사전 진정 근거 `chunk-00015`, `chunk-00016`을 포함해 pre/post 모두 `supported`가 됐다.

### Q005 주의·안전

기존 검색은 주요 증상·징후, 합병증 및 교육 근거가 있는 `chunk-00028`을 BM25/RRF 상위에서 이미 회수했다. 실패 원인은 body에 문서 topic 문자열 `진정간호`가 반복되지 않는다는 이유로 topic evidence seed를 0개로 만든 literal gate였다.

수정 후에는 다음처럼 topic과 aspect 책임을 분리했다.

- document name/title/section metadata: document topic 연결 가능
- chunk body: 요청한 caution/safety aspect를 직접 지지해야 함
- 제목만 일치하고 body aspect가 없는 chunk: 계속 차단
- complete parent에서 함께 회수된 context-only chunk도 topic+aspect를 직접 만족할 때만 seed 가능

Q005는 `kind=cautions`로 안정 분류되고 monitoring/safety 및 증상·징후/합병증 근거를 포함한 5개 selected chunks로 pre/post `supported`가 됐다.

## 4. 구현 범위

### Query normalization 및 QueryPlan

`mvp/query.py`:

- 목적 표현을 `purpose` aspect로 분리
- 일반 사전 준비·확인 표현을 `preparation`으로 분리
- 주의·조심·안전 관찰 표현을 `cautions`로 정규화
- `X 간호` topic subject canonicalization 추가
- `진정` 단독을 전역 hospital topic으로 열지 않고 임상 phase/aspect와 결합될 때만 hospital domain으로 인정
- 명시적 out-of-scope 검사는 항상 먼저 유지

BM25에 전달하는 원 질문, tokenizer와 query expansion은 변경하지 않았다.

### Evidence topic/aspect 및 parent atomicity

`mvp/context.py`, `mvp/evidence.py`:

- parent metadata가 있는 non-procedure context의 부분 포함 방지
- 첫 required parent 자체가 limit을 넘으면 기존 completeness gate가 fail closed
- 서로 다른 blank-section parent를 별도 evidence scope로 유지
- topic은 metadata로 연결 가능하지만 aspect는 body에서 직접 확인
- branch가 명시되지 않은 좁은 non-procedure 질문은 충분한 common evidence가 있을 때 adult/pediatric 예시를 자동 required로 승격하지 않음
- branch metadata 자체는 삭제하거나 변경하지 않음

Evidence completeness, citation, branch 및 Answer validator를 제거하거나 완화하지 않았다.

### SourceUnit bullet eligibility

완결된 독립 bullet/list 행은 문장부호나 colon이 없어도 selectable로 인정한다. 다음은 계속 non-selectable이다.

- heading/header
- caption
- 단계 표지
- 화살표형 설명 표지
- 이전 chunk의 overlap orphan
- 비종결 fragment

의료 문구, Q005 질문 또는 특정 chunk ID를 production 규칙에 사용하지 않았다.

## 5. 표현 변형 검증

| 유형 | 질문 | 최종 kind | Pre/Post | 판정 |
|---|---|---|---|---|
| 목적 | 진정간호 목적 알려줘 | `purpose` | supported/supported | 통과 |
| 목적 | 진정 간호는 왜 시행하나요? | `purpose` | supported/supported | 통과 |
| 목적 | 진정의 목적이 뭐야? | `purpose` | supported/supported | 통과 |
| 준비 | 진정 전에 뭘 준비해야 해? | `preparation` | supported/supported | 통과 |
| 준비 | 진정 시행 전 확인할 것은? | `preparation` | supported/supported | 통과 |
| 준비 | 진정 전 체크사항 알려줘 | `preparation` | supported/supported | 통과 |
| 주의 | 진정 시 주의할 점은? | `cautions` | supported/supported | 통과 |
| 주의 | 진정간호에서 조심해야 할 것은? | `cautions` | supported/supported | 통과 |
| 주의 | 진정 중 안전하게 봐야 할 항목은? | `cautions` | supported/supported | 통과 |

9개 변형은 모두 기대 intent로 묶였고 evidence budget 후에도 supported를 유지했다.

## 6. Q002 및 Q006 안전 회귀

### Q002

- selected evidence 12 chunks 유지
- pre/post required gold recall 10/10 유지
- Facet-slot SourceUnit contract 및 14-statement Gold Mock 통과
- reconstruction, exact citation 및 citation coverage 100% 유지
- SourceUnit segmentation/eligibility의 기존 Q002 경계 유지

### Q006

- QueryPlan domain: `out_of_scope`
- pre-LLM reason: `domain_or_clarification`
- SourceUnit catalog: 0건
- transport: 0회
- final public response: `등록된 지침서에서 확인할 수 없습니다.`

Q003~Q005를 통과시키기 위해 domain/topic gate 전체를 열지 않았다. 명시적 외부 주제 차단이 query normalization보다 먼저 실행되며, metadata topic 연결도 body aspect 지지가 없으면 evidence로 인정되지 않는다.

## 7. Budget

| Case | Reservation | Cap | Headroom | 판정 |
|---|---:|---:|---:|---|
| Q001 | 2539 | 5120 | 2581 | 통과 |
| Q002 | 4259 | 5120 | 861 | 통과 |
| Q003 | 2909 | 5120 | 2211 | 통과 |
| Q004 | 2543 | 5120 | 2577 | 통과 |
| Q005 | 3261 | 5120 | 1859 | 통과 |

모든 answerable case가 admission cap 안에 있고 최소 headroom `max(256, reservation × 8%)`을 충족했다. `OUTPUT_LIMIT=2048`과 `GROQ_REQUEST_TOKEN_BUDGET=5120`은 변경하지 않았다.

## 8. 버전 및 비변경 범위

- `SEARCH_VERSION=13`: query planning/context/evidence semantics가 바뀌어 기존 검색·evidence cache와 분리
- `CHUNK_VERSION=4`: 유지
- `AI_VERSION=19`: prompt와 provider response contract가 바뀌지 않아 유지
- `PROMPT_EVIDENCE_SCHEMA_VERSION=6`: 유지
- `RESPONSE_SELECTION_SCHEMA_VERSION=5`: 유지

변경하지 않은 범위:

- BM25 scoring, tokenizer와 query expansion
- embedding, RRF, reranker와 temporal rerank
- Q002 selected evidence fixture와 gold
- Facet-slot provider schema와 reconstruction
- `validate_source_unit_selection()`, `validate_answer()`
- citation, number, unit, condition, negation, unsupported action, duplicate evidence와 source-order validator
- retry, fallback와 web search

## 9. 테스트와 코드리뷰

| 검증 | 결과 |
|---|---|
| Pilot generalization 집중 테스트 | 37 passed |
| Retrieval/evidence/SourceUnit 관련 회귀 | 95 passed |
| 전체 pytest 최종 | **346 passed, 1 skipped** |
| Ruff (`mvp`, `tests`, `tools`) | 통과 |
| 변경 파일 `git diff --check` | 통과 |
| Offline evaluator | Q001~Q006 6/6, paraphrase 9/9 |
| 실제 Groq 호출 | 0회 |

기존 FastEmbed multilingual MiniLM pooling 기본값 안내 warning 4건이 전체 테스트에서 발생했다. 이번 변경과 무관하며 embedding 설정은 수정하지 않았다.

코드리뷰 결과:

- 추가 조치가 필요한 correctness, safety 또는 regression 결함 없음
- Q001~Q006 질문 문자열, chunk ID 및 기대 count의 production 하드코딩 없음
- evidence/Answer validator 완화 없음
- parent block 자동 잘라내기, 자동 보충 또는 자동 재정렬 없음
- 개인정보, API key, Authorization, 전체 prompt 또는 raw response를 artifacts에 저장하는 경로 없음

## 10. 산출물과 다음 단계

새 최종 산출물:

`artifacts/2026-09-14_rag-pilot-query-generalization-result/`

- `pilot_query_generalization_report.json`
- `test_results.json`
- `review.html`

기존 baseline 및 live artifacts는 덮어쓰지 않았다.

Offline/Mock 합격 기준을 모두 충족했으므로 Q001~Q006 제한 Live 재평가를 진행할 기술적 조건은 갖췄다. 다만 이번 승인 범위에는 실제 Groq 호출이 포함되지 않았으므로 호출은 0회로 유지했다. 다음 Live 실행은 사용자의 별도 승인 후에만 수행한다.

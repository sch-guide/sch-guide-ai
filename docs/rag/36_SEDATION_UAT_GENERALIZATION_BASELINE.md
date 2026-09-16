# 진정간호 실사용 UAT Query Generalization Baseline

- 작성·검증일: 2026-09-15
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/35_TOPIC_ASPECT_QUERY_GENERALIZATION_RESULT.md`
- 평가 범위: QueryPlan, BM25, semantic, RRF, rerank, context, pre/post evidence gate
- UAT 질문: 45개, 9개 유형
- 실제 Groq 호출: 0회
- Production 코드 수정: 0건
- 최종 상태: **Baseline 고정 완료, 17 PASS / 28 FAIL, 공통 원인 개선 승인 대기**

## 1. 결론

개별 질문을 고치는 방식 대신 실제 사용 표현 45개를 평가 전용 fixture로 고정하고, 현재 retrieval/evidence 경로를 전부 실행했다. 전체 결과는 17/45 PASS, 28/45 FAIL이다. 이 수치는 엔진을 유리하게 보이도록 실패를 제외하지 않은 초기 baseline이다.

기존에 검증한 짧은 procedure, purpose, preparation, cautions 표현은 대체로 통과했다. 반면 broad summary, comparison, branch-specific, fact-specific에서 공통 취약점이 드러났다. 가장 큰 원인은 BM25 임계값이 아니라 다음 세 경계다.

1. 구어체·broad·혼합 질문이 단일 `kind`로 안정적으로 정규화되지 않는다.
2. `summary`, `comparison`, 일반 `fact`에는 명시적 aspect support 정책이 없어, 등록 문서 topic이 metadata에만 있는 실질 본문을 `no_topic_evidence`로 차단한다.
3. adult/pediatric와 before/during/after가 독립 qualifier가 아니라 query 문자열과 evidence group에서 간접 처리되어 branch 누락 또는 parent partial inclusion이 발생한다.

Q006은 `out_of_scope`, 고정 근거 부족 응답, Mock transport 0회를 유지했다. 평가기는 `generate()`의 provider 경계에 거부 transport를 연결했으며 외부 네트워크는 사용하지 않았다.

이번 단계에서는 실패를 고치지 않았다. Production query normalization, retrieval, evidence gate, BM25, embedding, RRF, reranker와 validator는 변경하지 않았다.

## 2. UAT 세트

평가 fixture는 `tests/fixtures/sedation_uat_queries.json`에 저장했다. Production에서는 이 파일을 읽지 않는다. 질문 문자열, chunk ID, SourceUnit ID 또는 Gold ID를 production에 하드코딩하지 않았다.

| 유형 | 질문 수 | 범위 |
|---|---:|---|
| procedure | 5 | 절차, 진행, 방법, 순서 |
| purpose | 4 | 목적, 이유, 구어체 `왜 해` |
| preparation | 5 | 사전 준비, 확인, 체크 |
| cautions | 5 | 주의, 조심, 안전, 이상 증상 |
| summary/broad | 5 | topic 단독 설명, 성인/소아 개요, 전체 요약 |
| branch-specific | 7 | 성인/소아 + 절차·phase·action |
| fact-specific | 6 | monitoring 간격, 동의, 산소포화도, 이동 후 관찰 |
| comparison | 4 | 성인/소아 비교와 phase별 차이 |
| negative/out-of-scope | 4 | 우주선, 날씨, 가상자산, 스포츠 |
| 합계 | **45** | 요구 범위 30~50 충족 |

각 case에는 다음 기대값을 사람이 검수할 수 있게 명시했다.

- `expected_behavior`
- `expected_intent`
- `expected_topic`
- `expected_branch`
- `expected_phase`
- `expected_qualifier`

Evaluator는 실제 `kind`, `domain`, `topic`, temporal phase, evidence branch, pre/post reason, selected evidence count와 pass/fail을 기록한다. BM25, semantic, RRF Top-5, rerank/context/selected chunk ID도 원문 없이 진단 metadata로 남긴다.

## 3. 유형별 결과

| 유형 | PASS | FAIL | 통과율 |
|---|---:|---:|---:|
| procedure | 4 | 1 | 80.0% |
| purpose | 2 | 2 | 50.0% |
| preparation | 2 | 3 | 40.0% |
| cautions | 4 | 1 | 80.0% |
| summary/broad | 0 | 5 | 0.0% |
| branch-specific | 1 | 6 | 14.3% |
| fact-specific | 1 | 5 | 16.7% |
| comparison | 0 | 4 | 0.0% |
| negative/out-of-scope | 3 | 1 | 75.0% |
| 전체 | **17** | **28** | **37.8%** |

## 4. 질문별 Baseline

아래는 핵심 결과의 compact view다. 기대 branch/phase/qualifier, 실제 branch/phase, 복수 failure reason과 retrieval Top 결과는 `uat_results.csv`, `uat_report.json`, `review.html`에 모두 기록했다.

| ID | 질문 | 기대/실제 intent | Domain | Topic | Pre / Post | Selected | 판정 | 대표 실패 |
|---|---|---|---|---|---|---:|---|---|
| PROC01 | 진정절차 알려줘 | procedure / procedure | hospital | 진정간호 | supported / supported | 12 | PASS | - |
| PROC02 | 진정은 어떻게 해? | procedure / procedure | hospital | 진정간호 | supported / supported | 12 | PASS | - |
| PROC03 | 진정 진행 순서는? | procedure / procedure | hospital | 진정간호 | supported / supported | 12 | PASS | - |
| PROC04 | 진정 방법 알려줘 | procedure / procedure | hospital | 진정간호 | supported / supported | 12 | PASS | - |
| PROC05 | 진정간호는 어떤 순서로 시행해? | procedure / procedure | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | pre:no_topic_evidence |
| PURP01 | 진정 목적은? | purpose / purpose | hospital | 진정간호 | supported / supported | 1 | PASS | - |
| PURP02 | 진정 왜 해? | purpose / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| PURP03 | 진정하는 이유 알려줘 | purpose / purpose | unknown | 진정하, 이유 | no_topic_evidence / - | 0 | FAIL | domain_not_hospital |
| PURP04 | 진정간호를 시행하는 목적이 뭐야? | purpose / purpose | hospital | 진정간호 | supported / supported | 1 | PASS | - |
| PREP01 | 진정 전에 뭐 확인해? | preparation / preparation | hospital | 진정간호 | supported / supported | 2 | FAIL | temporal_qualifier_mismatch |
| PREP02 | 진정 준비 알려줘 | preparation / preparation | hospital | 진정간호 | supported / supported | 2 | PASS | - |
| PREP03 | 진정 전 체크사항은? | preparation / preparation | hospital | 진정간호 | supported / supported | 3 | PASS | - |
| PREP04 | 진정 전에 환자 상태를 어떻게 확인해? | preparation / preparation | hospital | 진정간호 | supported / supported | 4 | FAIL | temporal_qualifier_mismatch |
| PREP05 | 진정 시행 전에 준비할 것은? | preparation / preparation | hospital | 진정간호 | supported / supported | 2 | FAIL | temporal_qualifier_mismatch |
| CAUT01 | 진정할 때 주의할 점은? | cautions / cautions | hospital | 진정간호 | supported / supported | 1 | PASS | - |
| CAUT02 | 진정 중 조심할 것은? | cautions / cautions | hospital | 진정간호 | supported / supported | 1 | PASS | - |
| CAUT03 | 진정 안전하게 보려면 뭐 확인해? | cautions / cautions | hospital | 진정간호 | supported / supported | 1 | PASS | - |
| CAUT04 | 진정간호 주의사항 알려줘 | cautions / cautions | hospital | 진정간호 | supported / supported | 1 | PASS | - |
| CAUT05 | 진정 후 이상 증상은 무엇을 봐야 해? | cautions / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| SUM01 | 진정에 대해 알려줘 | summary / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| SUM02 | 소아 진정 알려줘 | summary / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| SUM03 | 성인 진정 알려줘 | summary / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| SUM04 | 진정간호 전체적으로 설명해줘 | summary / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| SUM05 | 진정간호를 간단히 정리해줘 | summary / summary | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | pre:no_topic_evidence |
| BR01 | 소아 진정 절차 알려줘 | procedure / procedure | hospital | 진정간호 | incomplete_semantic_block / - | 0 | FAIL | pre:incomplete_semantic_block |
| BR02 | 성인 진정 절차 알려줘 | procedure / procedure | hospital | 진정간호 | incomplete_semantic_block / - | 0 | FAIL | pre:incomplete_semantic_block |
| BR03 | 소아 진정 중 모니터링 알려줘 | cautions / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| BR04 | 성인 진정 후 관찰 알려줘 | cautions / cautions | hospital | 진정간호 | supported / supported | 5 | PASS | - |
| BR05 | 소아 진정 전 확인사항 알려줘 | preparation / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | intent_mismatch |
| BR06 | 성인 진정 중 투약과 관찰 방법은? | procedure / cautions | hospital | 진정간호 | supported / supported | 11 | FAIL | intent_mismatch |
| BR07 | 소아 진정 후 회복 관찰은? | cautions / cautions | hospital | 진정간호 | supported / supported | 6 | FAIL | branch_coverage_mismatch |
| FACT01 | 소아 진정은 몇 분마다 확인해? | fact / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | pre:no_topic_evidence |
| FACT02 | 성인은 몇 분마다 모니터링해? | fact / fact | unknown | 성인, 분마다, 모니터링해 | supported / supported | 6 | FAIL | domain_not_hospital |
| FACT03 | 진정 동의서는 언제 받아? | fact / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | pre:no_topic_evidence |
| FACT04 | 진정 전 산소포화도 확인해? | preparation / preparation | hospital | 진정간호 | supported / supported | 3 | PASS | - |
| FACT05 | 진정 중 활력징후는 얼마나 자주 측정해? | fact / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | pre:no_topic_evidence |
| FACT06 | 진정 후 입원실로 이동하면 어떻게 관찰해? | cautions / cautions | hospital | 진정간호 | missing_requested_aspect / - | 0 | FAIL | pre:missing_requested_aspect |
| COMP01 | 성인과 소아 진정 차이 알려줘 | comparison / comparison | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | pre:no_topic_evidence |
| COMP02 | 성인과 소아 모니터링 간격 비교해줘 | comparison / comparison | unknown | 성인과, 소아, 모니터링, 간격, 비교해줘 | supported / supported | 7 | FAIL | domain_not_hospital |
| COMP03 | 성인과 소아 진정 전 확인사항 비교해줘 | comparison / comparison | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | pre:no_topic_evidence |
| COMP04 | 성인과 소아 진정 후 관찰 방법이 어떻게 달라? | comparison / cautions | hospital | 진정간호 | missing_requested_aspect / - | 0 | FAIL | intent_mismatch |
| NEG01 | 화성 우주선 궤도 공식은? | fact / fact | out_of_scope | 외부 topic | domain_or_clarification / - | 0 | PASS | - |
| NEG02 | 오늘 서울 날씨 알려줘 | fact / fact | out_of_scope | 외부 topic | domain_or_clarification / - | 0 | PASS | - |
| NEG03 | 비트코인 가격 전망 알려줘 | fact / fact | out_of_scope | 외부 topic | domain_or_clarification / - | 0 | PASS | - |
| NEG04 | 진정간호 지침과 상관없는 축구 경기 결과 알려줘 | fact / fact | hospital | 진정간호 | no_topic_evidence / - | 0 | FAIL | negative_domain_not_out_of_scope |

## 5. 실패 유형 집계

한 case에는 여러 원인이 함께 존재할 수 있다. 대표 실패는 가장 이른 query/evidence 경계를 사용하고, 동반 원인도 별도로 집계했다.

### 대표 실패

| 대표 실패 | 건수 |
|---|---:|
| `intent_mismatch` | 10 |
| `pre_budget:no_topic_evidence` | 7 |
| `domain_not_hospital` | 3 |
| `temporal_qualifier_mismatch` | 3 |
| `pre_budget:incomplete_semantic_block` | 2 |
| `branch_coverage_mismatch` | 1 |
| `pre_budget:missing_requested_aspect` | 1 |
| `negative_domain_not_out_of_scope` | 1 |

### 동반 원인 전체

| 원인 | 포함 case 수 |
|---|---:|
| `pre_budget:no_topic_evidence` | 16 |
| `branch_coverage_mismatch` | 15 |
| `intent_mismatch` | 10 |
| `domain_not_hospital` | 3 |
| `topic_mismatch` | 3 |
| `temporal_qualifier_mismatch` | 3 |
| `pre_budget:incomplete_semantic_block` | 2 |
| `pre_budget:missing_requested_aspect` | 2 |
| `negative_domain_not_out_of_scope` | 1 |

Branch mismatch 15건에는 pre-LLM에서 이미 차단되어 branch evidence가 없는 파생 결과가 포함된다. 독립적인 branch 버그로 확정되는 대표 사례는 BR07이며, BR01/BR02는 parent atomicity 실패가 먼저다.

## 6. 공통 원인 분석

### 6.1 단일 intent 분류의 구어체·혼합 표현 취약성

다음 표현이 동일 aspect로 묶이지 않는다.

- `진정 왜 해?`: purpose가 아니라 fact
- `진정 후 이상 증상은 무엇을 봐야 해?`: cautions가 아니라 fact
- topic 단독 `알려줘`, `전체적으로 설명해줘`: summary가 아니라 fact
- `모니터링`, `확인사항`: cautions/preparation trigger로 일관되게 해석되지 않음
- `투약과 관찰 방법`: procedure와 cautions가 함께 있지만 단일 kind 우선순위에서 cautions로 고정
- `어떻게 달라`: comparison trigger가 없어 cautions로 고정

이는 특정 질문 문자열 문제가 아니라 한국어 종결형, 명사형 aspect와 다중 aspect의 일반 문제다.

### 6.2 Broad·comparison·fact의 topic evidence 연결 부재

현재 `relevant_body()`는 preparation/cautions/purpose 등에 `_ASPECT_SUPPORT`를 둔다. 반면 summary/comparison/일반 fact는 본문에 topic 단어가 직접 있거나 제한된 heading context여야 seed가 된다.

진정간호 문서의 임상 본문은 매 행마다 `진정간호`를 반복하지 않는다. 등록 문서 metadata에는 topic이 있지만 substantive body에는 측정·관찰·동의·합병증 같은 내용만 있는 경우가 많다. 이 때문에 검색 후보에 관련 chunk가 있어도 `no_topic_evidence`가 된다.

예를 들어 CAUT05의 semantic/context에는 안전 근거 chunk가, FACT05의 rerank에는 성인·소아 monitoring chunk가 들어온다. 따라서 전체 BM25/RRF 임계값을 낮출 근거는 없다. QueryPlan aspect와 evidence seed 계약을 먼저 맞춰야 한다.

### 6.3 Explicit branch가 context scope에 반영되지 않음

BR01과 BR02는 adult/pediatric를 각각 요청했지만 두 경우 모두 common + adult + pediatric parent를 함께 required로 만들었다. 12 chunk 안에서 pediatric parent `00023~00026`만 포함되고 다음 chunk가 빠져 `incomplete_semantic_block`이 됐다.

BR07은 소아를 요청했지만 실제 supported group은 adult + common이었다. Branch 단어가 topic/entity가 아니며 non-procedure evidence scope에 requested branch 필터가 없기 때문이다.

안전한 수정 방향은 incomplete 검사를 없애는 것이 아니다. QueryPlan에 명시적 branch qualifier를 보존하고:

- 단일 branch 질문: common + 요청 branch만 candidate/required
- comparison: adult + pediatric를 모두 complete required
- branch 없는 broad procedure: 기존 full coverage

로 context scope를 정해야 한다.

### 6.4 Temporal particle 처리

`진정 전 체크사항`은 before로 인식하지만 `진정 전에`, `시행 전에`의 `전에`는 temporal boundary에서 놓친다. PREP01/04/05는 pre/post evidence 자체는 supported지만 expected phase가 기록되지 않아 실패했다.

이는 임상 용어 추가가 아니라 `전/중/후` 뒤 한국어 조사 경계의 일반 normalization 문제다.

### 6.5 Topic morphology와 대화 문맥

PURP03의 `진정하는`은 등록 topic alias `진정`으로 복원되지 않고 `진정하`로 남는다. 이는 활용형 subject normalization 문제다.

FACT02와 COMP02는 질문 자체에 `진정`이 없다. 이 둘을 standalone에서 자동으로 진정간호로 추정하면 다른 지침 질문까지 오연결할 수 있다. 실제 UAT 기대가 이전 진정 질문에 대한 후속 질문이라면 별도 follow-up track에서 이전 topic을 명시적으로 전달해야 한다. Standalone query를 전역 진정 topic으로 보정하지 않는다.

### 6.6 Out-of-scope precedence

NEG04는 명시적 외부 주제인 축구가 있지만 `진정간호`도 포함되어 hospital로 분류된 뒤 `no_topic_evidence`로 안전 차단됐다. 외부 호출은 발생하지 않지만 기대한 out-of-scope 분류는 아니다. 현재 패턴이 `축구 결과`처럼 인접한 표현만 잡고 `축구 경기 결과`는 잡지 못한다.

Q006과 동일하게 명시적 외부 주제는 병원 문구와 함께 있어도 먼저 차단하되, 병원 용어를 포함했다는 이유만으로 일반 질문 전체를 out-of-scope로 만들지 않는 bounded rule이 필요하다.

## 7. 최소 수정 권고 순서

아직 구현 승인이 아니다. 다음 순서로 같은 45개 fixture를 다시 실행하는 것을 권고한다.

### 1순위: QueryPlan intent/aspect/qualifier 정규화

- 구어체 활용형을 일반 grammar로 정규화: `왜 해/왜 하나`, `하는 이유`, `전체적으로/간단히 설명`, `달라/다른가`
- monitoring, 이상 증상, 확인사항처럼 이미 임상 action/aspect인 명사형을 request aspect로 보존
- adult/pediatric와 before/during/after를 topic noise가 아닌 request qualifier로 분리
- mixed query는 단일 kind 우선순위로 정보를 버리지 말고 primary intent와 보조 aspect를 함께 보존
- 등록 topic이 하나일 때만 `진정하는` 같은 활용형 subject를 canonical topic alias와 대조

Q001~Q006 문자열을 조건문에 넣지 않는다.

### 2순위: Intent-aware evidence seed 계약

- 명시적 summary/comparison에 한해 유일하게 연결된 canonical document의 complete substantive parent를 seed 후보로 허용
- fact-specific 질문은 interval, consent, saturation, monitoring 같은 요청 qualifier를 본문이 직접 지지해야만 허용
- metadata는 topic 연결에만 사용하고, clinical answer 근거는 계속 본문 SourceUnit에서만 허용
- `incomplete_semantic_block`, title-only 차단과 post-budget 검사는 유지

이는 evidence gate를 전역 완화하는 방식이 아니다. 현재 없는 intent별 support 계약을 추가하는 것이다.

### 3순위: Branch-aware parent atomicity

- 단일 branch 질문은 non-requested branch를 자동 required로 만들지 않음
- requested branch parent는 전부 포함하거나 전부 제외
- comparison은 두 branch의 complete parent를 요구
- BR07처럼 요청 branch와 selected branch가 다르면 기존 fail closed 유지

### 4순위: Temporal particle 및 외부 주제 표현

- `전에/중에/후에`, `전에는/후에는`을 일반 temporal boundary로 처리
- 외부 주제 핵심어와 `결과/가격/예보` 사이의 짧은 수식어를 허용하는 bounded out-of-scope rule 검토

### 별도 트랙: Topic 없는 follow-up

FACT02/COMP02는 standalone에서 진정 topic을 추측하지 않는다. 이전 대화의 검증된 topic을 전달한 follow-up UAT로 분리해 기대 동작을 검증한다.

## 8. 변경 금지 항목 판단

현재 baseline만으로 다음을 바꿀 근거는 없다.

- BM25 점수 공식과 임계값
- embedding 모델과 threshold
- RRF와 reranker
- selected evidence cap
- parent atomicity와 evidence gate fail-closed 원칙
- SourceUnit, Facet-slot selection과 citation validator

관련 chunk가 BM25/semantic/rerank 후보에 이미 존재하는 실패가 다수다. 가장 큰 공통 원인은 query semantic contract와 evidence seed contract의 불일치다.

## 9. Q006 안전성

| 항목 | 결과 |
|---|---|
| Query domain | `out_of_scope` |
| Pre-budget reason | `domain_or_clarification` |
| Selected evidence | 0 |
| Mock provider transport | 0회 |
| Answerable | false |
| 고정 근거 부족 경로 | 유지 |

UAT 평가 전체의 실제 Groq 호출도 0회다.

## 10. 테스트와 코드 검수

- UAT fixture 검증: `3 passed`
- UAT fixture + 기존 pilot generalization: `82 passed, 3 warnings`
- 전체 pytest: `397 passed, 1 skipped, 5 warnings`
- Ruff 전체: 통과
- 이번 추가 파일 trailing whitespace 검사: 통과
- Code review: 추가 조치가 필요한 correctness/safety 결함 없음

Warning 4건은 기존 FastEmbed multilingual MiniLM pooling 안내다. 나머지 1건은 저장소 `.pytest_cache` 권한 경고이며 테스트 결과에는 영향이 없다. 저장소 전체 `git diff --check`는 과거 pytest 임시 디렉터리의 접근 권한 오류 때문에 깨끗한 전역 판정을 내리지 못했고, 이번 파일은 별도로 검사했다.

## 11. 산출물과 승인 대기

평가 전용 파일:

- `tests/fixtures/sedation_uat_queries.json`
- `tools/rag_sedation_uat_generalization_evaluate.py`
- `tests/test_sedation_uat_generalization.py`

최종 artifacts:

`artifacts/2026-09-15_rag-sedation-uat-generalization-baseline-02/`

- `uat_report.json`: 모든 기대/실제 필드, 전체 failure reason과 raw-free retrieval diagnostics
- `uat_results.csv`: 45개 질문 비교표
- `review.html`: category/status filter와 FAIL 상세 검수 화면
- `test_results.json`: 회귀·정적 검사 결과

먼저 만든 `artifacts/2026-09-15_rag-sedation-uat-generalization-baseline/`은 덮어쓰지 않았고, 동반 failure reason과 parent 진단을 추가한 `-02`가 최종본이다.

이번 작업에서는 production 코드를 수정하거나 실제 Groq를 호출하지 않았다. 위 공통 원인에 대한 최소 production 수정은 사용자 승인 후에만 진행한다.

# SCHAT Hybrid RAG 2차 Evidence 및 Generation 계획

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 설계: `docs/rag/01_ARCHITECTURE.md` C안
- 기준 구현: `docs/rag/02_PHASE1_RETRIEVAL_RESULT.md`
- 단계: 분석·설계 완료, 구현 승인 대기
- 범위: procedure coverage, 원자 evidence-group budget, Groq 전후 안전 gate와 citation 검증

## 1. 결정 요약

2차에서는 승인 후보인 1차 retrieval/context 구현을 그대로 입력으로 사용한다. Q002의 개선 evidence bundle은 필수 gold stage 10/10을 회수하고 있으므로 BM25, embedding, RRF, reranker와 context 확장을 다시 조정하지 않는다.

핵심 변경은 다음과 같다.

1. `mvp/evidence.py`가 검색 hit를 검증된 `EvidenceGroup`과 `ProcedureCoverage`로 해석한다.
2. 같은 `parent_id`의 조각은 나눌 수 없는 원자 group으로 취급한다. parent가 없는 chunk는 단독 group이다.
3. procedure 질문은 LLM 전 충분성 검사에서 substantive action, parent 완전성, 중복, 분기와 source order를 확인한다.
4. `mvp/ai.py`는 hit 하나씩이 아니라 group 전체를 token/byte budget에 넣거나 제외한다.
5. budget 후 동일한 evidence 검사를 다시 실행하고, pre-budget에서 승인된 procedure group coverage가 보존되지 않으면 Groq를 호출하지 않는다.
6. 충분한 경우에만 현재와 같이 Groq를 최대 1회 호출한다. 자동 재시도와 대체 모델 호출은 추가하지 않는다.
7. 응답은 기존 extractive schema와 `validate_answer()`를 유지·강화해 각 문장, 원문 quote와 chunk citation을 검증한다.
8. Q006과 근거 없음 질문은 pre-LLM 또는 post-budget gate에서 종료하며 LLM 호출 횟수는 0회다.

Q002 gold stage는 배포 전 평가 기준이지 운영 규칙이 아니다. 운영 코드에는 `Q002`, 특정 chunk ID, 진정간호의 임상 단계 목록을 넣지 않는다.

## 2. 승인 경계

이 문서는 구현 허가가 아니다. 사용자 승인 전에는 다음을 수행하지 않는다.

- `mvp/evidence.py`, `mvp/ai.py` 또는 테스트 코드 수정
- Groq 또는 다른 LLM 호출
- Q002 gold fixture와 1차 `review.html` 수정
- BM25, tokenizer, query expansion, temporal rerank, embedding, RRF와 reranker 변경
- `mvp/context.py`의 승인 후보 구현 변경
- dependency 설치, DB schema 또는 원본 문서 변경
- 기존 artifacts 덮어쓰기

현재 수동 검수 파일 `artifacts/2026-09-13_rag-phase1-retrieval/review.html`은 2차 구현 중에도 그대로 보존한다. 2차 결과는 새 artifacts 디렉터리에 생성한다.

## 3. 현재 동작과 남은 공백

현재 `generate()`는 이미 다음 안전 순서를 가진다.

1. `assess_evidence(plan, hits)` 실행
2. 불충분하면 LLM 호출 없이 종료
3. `prompt_messages()`로 byte/token budget 적용
4. 선택된 hit에 `assess_evidence()` 재실행
5. parent 일부 누락, 문서·entity 누락 검사
6. Groq 1회 호출
7. JSON schema와 exact quote/citation 검증
8. 실제 인용된 hit만 다시 `assess_evidence()`로 검사

다만 다음 공백이 있다.

- `prompt_messages()`가 hit를 한 개씩 추가하므로 하나의 parent 일부만 선택될 수 있다.
- post-budget 검사는 parent 누락을 별도 반복문으로 확인하며 pre/post procedure coverage 자체를 비교하지 않는다.
- `EvidenceAssessment`는 `sufficient`, `hits`, `reason`만 있어 group, 순서, 분기와 절차 단위 정보를 설명하지 못한다.
- 현재 relevance 범위는 seed의 문서·section 중심이어서 1차에 추가된 병렬 branch parent를 procedure coverage로 명시적으로 설명하지 못한다.
- exact citation 검증은 강하지만, procedure 응답이 충분한 구조적 근거 집합에서 생성됐는지는 별도 결과로 남지 않는다.

## 4. 목표와 비목표

### 목표

- Q002 개선 bundle의 필수 gold stage 10/10을 LLM 직전까지 유지한다.
- 같은 parent의 조각을 prompt budget에서 원자적으로 처리한다.
- budget 적용 전후의 procedure group과 구조적 unit coverage를 비교한다.
- 근거 부족, group 일부 누락 또는 coverage 감소 시 Groq 호출을 막는다.
- 충분한 경우 현재 endpoint에 요청을 정확히 1회만 보낸다.
- 모든 답변 문장을 실제 원문 문장/표 행과 chunk citation으로 검증한다.
- Q006 및 병원 도메인 내 답 없음 fixture에서 LLM 호출 0회를 유지한다.
- trace에 차단 단계와 group 선택·제외 이유를 남긴다.

### 비목표

- Q002 검색 후보나 1차 evidence bundle 재튜닝
- 임상적으로 필요한 단계의 자동 추론
- 답변 paraphrase 또는 외부 의료 지식 사용
- embedding/cross-encoder 변경
- Groq 모델 비교, 재시도, fallback 또는 streaming 추가
- prompt token 한도 확대

## 5. 호출 흐름

```text
1차 Hybrid retrieval/context hits
  → pre_llm assess_evidence(plan, hits)
      → 일반 relevance/aspect/document 검사
      → EvidenceGroup 구성
      → procedure coverage 검사
      → 불충분: 고정 답변, Groq 0회
  → evidence group 단위 prompt budget
      → group 전체 포함 또는 전체 제외
  → post_budget assess_evidence(plan, selected_hits)
      → pre_llm required group/coverage와 비교
      → 감소·불완전: 고정 답변, Groq 0회
  → endpoint/config/quota 확인
  → Groq 최대 1회
  → JSON schema + 문장/quote/chunk 검증
  → 실제 cited hits의 evidence 재검사
  → 검증 답변 또는 고정 근거 부족 답변
```

LLM 호출 가능 여부는 retrieval 점수나 dense similarity가 아니라 evidence gate 결과만 결정한다.

## 6. 데이터 구조 설계

다음은 구현 전 의사코드다.

```python
@dataclass(frozen=True)
class EvidenceGroup:
    key: str
    hits: tuple[Hit, ...]
    document_id: str
    parent_id: str
    branch: str                 # common/adult/pediatric/explicit_other/unknown
    source_start: int
    source_end: int
    complete: bool
    substantive: bool
    required: bool
    requirement_reason: str     # seed/seed_neighbor/explicit_branch/optional_context


@dataclass(frozen=True)
class ProcedureCoverage:
    required_group_keys: tuple[str, ...]
    optional_group_keys: tuple[str, ...]
    required_procedural_unit_keys: tuple[str, ...]
    optional_procedural_unit_keys: tuple[str, ...]
    required_branches: tuple[str, ...]
    source_ordered: bool
    complete: bool
    duplicate_count: int


@dataclass(frozen=True)
class EvidenceAssessment:
    sufficient: bool
    hits: tuple[Hit, ...] = ()
    reason: str = ""
    groups: tuple[EvidenceGroup, ...] = ()
    procedure_coverage: ProcedureCoverage | None = None
```

기존 호출자가 사용하는 `sufficient`, `hits`, `reason` 필드는 유지한다. 새 필드는 기본값을 가져 일반 질문과 기존 테스트의 생성 방식을 깨지 않는다.

### Group key

- `parent_id`가 있으면 `(document_id, parent_id)`가 group identity다.
- parent가 없으면 `(document_id, chunk_id)`가 단독 group identity다.
- group 안의 hit는 `chunk.index` 순서로 정렬한다.
- 서로 다른 document나 parent는 합치지 않는다.
- 분기 marker는 group metadata이며 identity를 임의 병합하는 데 사용하지 않는다.

Group key는 trace와 비교를 위한 안정적 request-scoped 값이다. DB나 chunk payload에는 저장하지 않는다.

## 7. Procedure coverage 정의

### 구조적 unit

`procedural_unit_keys`는 임상 단계명을 추론하지 않는다. 다음 원문 구조만 사용한다.

- 번호 또는 bullet이 붙은 substantive 문장/표 행
- 기존 `ASPECTS`의 procedure action 패턴을 실제로 포함하는 완전한 원문 문장/행
- 명시적 성인/소아 또는 조건 분기 아래의 substantive action 문장/행

정규화 원문, document ID, branch와 source index로 안정적인 signature를 만든다. 제목-only, 표 머리글-only, 빈 문장과 중복 원문은 unit으로 세지 않는다.

### Pre-LLM procedure gate

`plan.kind == "procedure"`일 때 다음을 모두 검사한다.

1. 현재 일반 gate의 topic, entity, aspect, document 조건을 통과한다.
2. 하나 이상의 admitted substantive seed가 존재한다.
3. required evidence group은 모두 완전하며 optional group도 일부 parent만 포함한 상태로 사용하지 않는다.
4. 하나 이상의 명시적 procedure action unit이 존재한다.
5. group 내부와 동일 문서 내 source order가 역전되지 않는다.
6. branch-aware exact duplicate가 없다.
7. admitted seed가 속한 group, seed와 원문 링크로 직접 인접한 procedure group과 명시된 병렬 branch group은 required로, 그 밖의 완전한 보조 문맥은 optional로 구분한다.
8. 필요한 명시적 분기는 `required_branches`에 기록하고 분기별 group을 섞지 않는다.

구조적으로 한 문장에 완결된 단순 절차는 group이 하나라는 이유만으로 거절하지 않는다. 반대로 여러 parent/분기가 이미 회수된 질문에서는 일부 group만 남긴 상태를 충분하다고 간주하지 않는다.

### Q002 gold의 역할

Q002의 필수 10개·보완 3개 gold stage는 오프라인 평가와 release acceptance에만 적용한다.

- 운영 `assess_evidence()`는 chunk ID나 필수 임상 단계를 알지 않는다.
- Q002 평가기는 pre-gate, post-budget, 실제 citation 단계에서 gold recall을 별도로 계산한다.
- 필수 stage recall 100%가 아니면 2차 Q002 결과를 승인하지 않는다.
- 보완 stage 00028이 현재 12-hit bundle에서 빠지는 사실은 별도로 표시하고 필수 stage와 섞지 않는다.

## 8. 관련 evidence 선택 경계

일반 질문은 현재 seed/section 기반 relevance 동작을 유지한다.

procedure 질문에서는 다음 순서로 relevant hits를 정한다.

1. 기존 `relevant_body()`를 통과한 non-context seed를 admission root로 확인한다.
2. root와 같은 문서에서 1차 context builder가 완전하게 확장한 parent group을 검토한다.
3. 각 context hit도 substantive body, topic compatibility와 procedure action 조건을 직접 통과해야 한다.
4. 병렬 branch는 원문 marker와 동일 section/stem의 구조적 연결이 확인된 경우에만 관련 group으로 인정한다.
5. 높은 similarity, 제목 또는 같은 페이지라는 이유만으로 group을 추가하지 않는다.

이 규칙은 1차 bundle을 무조건 신뢰하지 않으며, LLM에 전달할 evidence를 evidence 계층에서 다시 제한한다.

## 9. Evidence-group 단위 prompt budget

현재 `GROQ_REQUEST_TOKEN_BUDGET=3500`, byte budget 14000과 completion 768은 유지한다.

### 선택 규칙

1. pre-LLM assessment가 승인한 `groups`만 입력으로 받는다.
2. required group을 먼저 처리하고, 남는 예산에 optional group을 처리한다.
3. group 안의 모든 hit를 포함한 trial message를 만든다.
4. byte와 token budget을 모두 만족하면 group 전체를 채택한다.
5. 어느 하나라도 초과하면 group 전체를 제외한다. 일부 hit만 넣지 않는다.
6. required group 제외는 post-budget hard gate가 차단한다. optional group 제외는 호출을 차단하지 않고 trace에 이유만 남긴다.
7. 선택된 group과 hit는 최종적으로 문서·분기·source order를 유지한다.
8. 중복 JSON evidence는 만들지 않는다.
9. 제외 사유는 `byte_budget`, `token_budget`, `duplicate` 중 고정 코드로 trace에 기록한다.

일반 질문에서 parent 없는 단독 hit는 기존 hit 단위와 동일하게 동작한다. parent가 있는 일반 질문도 의미 단위 일부가 잘리지 않도록 group 원자성을 적용한다.

### 최소 인터페이스 변경

공개 `generate()` 반환형은 유지한다. `prompt_messages()`도 기존 `(messages, selected_hits)` 반환 계약을 유지하고 선택 진단은 선택적 `trace`에 쓴다.

```python
def prompt_messages(
    question: str,
    assessment: EvidenceAssessment,
    byte_budget: int,
    token_budget: int | None = None,
    plan: QueryPlan | None = None,
    trace: dict | None = None,
) -> tuple[list[dict], list[Hit]]: ...
```

호출자가 임의로 group을 다시 만들지 않도록 pre-LLM assessment가 group의 단일 출처가 된다.

## 10. Budget 후 재검사

선택된 hit에 `assess_evidence()`를 다시 실행하는 현재 흐름을 유지하되 다음 비교를 추가한다.

### 일반 질문

- 기존 topic/aspect/entity/document와 parent completeness 검사를 유지한다.
- pre-budget에서 필수였던 document/entity가 사라지면 차단한다.

### Procedure 질문

- post-budget assessment 자체가 sufficient여야 한다.
- 모든 pre-budget `required_group_keys`가 post-budget에 있어야 한다.
- 모든 pre-budget `required_procedural_unit_keys`가 post-budget에 있어야 한다.
- `required_branches`와 source order가 유지돼야 한다.
- 하나의 parent라도 일부만 남으면 차단한다.
- optional group/unit이 budget에서 제외된 것만으로는 차단하지 않는다.

차단 reason은 다음 고정 코드로 구분한다.

- `after_budget:no_group_fits`
- `after_budget:missing_evidence_group`
- `after_budget:missing_procedure_unit`
- `after_budget:missing_branch`
- `after_budget:incomplete_semantic_block`
- 기존 `after_budget:missing_document_or_entity`

차단 시 반환은 `Answer(answerable=False, statements=[])`와 budget에서 선택된 hit이며, 외부 요청과 quota 예약은 수행하지 않는다.

## 11. Groq 호출 경계

다음 조건을 모두 통과한 경우에만 endpoint와 quota 단계로 진입한다.

- pre-LLM evidence sufficient
- group budget 적용 성공
- post-budget evidence sufficient
- procedure coverage 보존
- 개인정보 검사 통과
- LLM 설정과 관리 승인 확인

호출 동작은 현재 계약을 유지한다.

- 요청당 최대 1회
- `temperature=0`
- JSON response format
- 자동 retry 없음
- fallback 모델 없음
- web search 없음
- 문서 전체가 아니라 선택된 최소 evidence만 전송

429, 인증, 서버, timeout은 근거 부족으로 위장하지 않고 기존 오류 코드로 반환한다.

## 12. 답변 문장과 citation 검증

현재 `validate_answer()`의 다음 계약을 유지한다.

- Pydantic strict schema와 extra field 금지
- statement마다 1개 이상의 evidence 필요
- quote가 실제 chunk 원문의 정규화된 부분 문자열이어야 함
- `statement.text`의 각 문장이 완전한 원문 문장 또는 표 행과 일치
- 숫자와 단위가 quote에 존재
- 근거 없는 action, markup와 conflict 표시 차단
- chunk ID는 prompt로 전달한 선택 집합 안에 있어야 함

2차에서 다음 검증을 명시적으로 추가한다.

1. procedure statement의 citation이 제목-only나 비본문 chunk만 가리키면 실패한다.
2. 동일 statement가 여러 chunk를 필요로 하면 모든 quote를 각각 검증한다.
3. statement 출력 순서는 같은 document/branch 안에서 cited chunk의 source order를 역전하지 않는다.
4. 실제 cited hit에 다시 evidence assessment를 수행한다.
5. Q002 평가에서는 cited chunk 기준 필수 gold stage recall 100%를 별도 측정한다.
6. 근거에 없는 연결 단계, 조건, 용량, 횟수와 행동은 0건이어야 한다.

운영 runtime은 모든 selected group을 반드시 문장으로 출력하라고 강제하지 않는다. 그것은 불필요한 보완 문장까지 생성하게 할 수 있다. 대신 생성된 모든 문장은 완전히 grounded되어야 하며, Q002처럼 필수 단계가 정의된 release fixture에서는 별도 gold coverage를 통과해야 한다.

## 13. Q002 예상 동작

### Pre-LLM

- 입력: 1차 개선 bundle 12개
- chunk: 00013, 00015, 00016, 00019~00027
- 필수 gold recall: 10/10
- 보완 gold recall: 2/3
- 성인/소아 분기: 모두 존재
- final duplicate: 0
- source order 역전: 0
- 모든 parent: complete

### Group 구성 예상

- 독립 또는 공통 parent group
- 진정 전 평가·설명 parent group
- 성인 기록 parent group 00019~00022
- 소아 기록 parent group 00023~00027

실제 group 수와 key는 구현 후 chunk metadata를 기준으로 산출하고 결과 문서에 기록한다. 특정 group 수를 코드에 하드코딩하지 않는다.

### Budget 결과

각 required group 전체가 현재 3500-token/14000-byte 예산에 들어가고 필수 stage 10/10을 유지하면 Groq 경로를 열 수 있다. optional group만 빠지면 차단하지 않고 제외 이유를 기록한다. required group이 하나라도 빠지면 일부 절차 답변을 생성하지 않고 `after_budget:*` reason으로 차단한다.

보완 00028은 1차 bundle에 없으므로 2차 gate가 새로 검색하거나 추가하지 않는다.

## 14. Q006 및 답 없음 질문

Q006은 다음 순서로 끝나야 한다.

1. `plan.domain == "out_of_scope"`
2. pre-LLM `EvidenceAssessment(False, reason="domain_or_clarification")`
3. prompt 작성, token 계산, endpoint 확인과 quota 예약을 수행하지 않음
4. HTTP 요청 0회
5. `등록된 지침서에서 확인할 수 없습니다.` 반환

추가로 병원 도메인처럼 보이지만 등록 지침에 답이 없는 fixture를 최소 2개 둔다.

- semantic similarity만 높고 procedure action 원문이 없는 경우
- 관련 제목/section은 있으나 요청한 조건·용량·단계가 없는 경우

두 경우 모두 Groq 호출 0회와 구체적인 pre/post gate reason을 검증한다.

## 15. Trace 설계

기존 BM25 raw score, temporal phase/tier, semantic, RRF와 rerank trace는 삭제하거나 의미를 바꾸지 않는다.

추가 필드:

- `pre_llm_group_keys`
- `pre_llm_required_group_keys`, `pre_llm_optional_group_keys`
- group별 chunk IDs, parent ID, branch, source range와 complete 여부
- `pre_llm_procedure_coverage`
- `prompt_selected_group_keys`
- `prompt_excluded_groups`와 고정 reason
- `post_budget_group_keys`
- `post_budget_required_group_keys`, `post_budget_optional_group_keys`
- `post_budget_procedure_coverage`
- `coverage_delta`
- `llm_called`
- `validation_reason`
- `citation_group_keys`
- Q002 평가 전용 gold stage recall

trace에는 환자 정보, API key, 모델의 원시 미검증 응답을 저장하지 않는다.

## 16. 모듈 소유권과 예상 변경 파일

| 파일 | 승인 후 변경 책임 |
|---|---|
| `mvp/evidence.py` | `EvidenceGroup`, `ProcedureCoverage`, 확장된 `EvidenceAssessment`, group 구성과 pre/post procedure gate |
| `mvp/ai.py` | group 원자 budget, post-budget coverage 비교, Groq 1회 경계, procedure citation 순서 검증 |
| `mvp/search_trace.py` | group/coverage/budget/citation 진단 기본 필드와 직렬화 |
| `tests/test_rag_contract.py` | pre/post gate, group budget, 호출 횟수와 Q006 회귀 |
| 새 집중 테스트 | Q002 구조 coverage, parent 원자성, 분기와 citation 순서 |
| RAG 평가 도구 | 2차 단계별 coverage, mock/승인된 실제 호출 결과와 HTML 생성 |
| `docs/rag/03_EVIDENCE_AND_GENERATION_RESULT.md` | 구현 결과와 다음 단계 승인 판단 |
| 새 artifacts 디렉터리 | 2차 JSON/CSV/review.html, 기존 artifacts 보존 |

`mvp/context.py`, `mvp/retrieval.py`, embedding과 DB schema는 원칙적으로 수정하지 않는다. 구현상 1차 bundle 계약 변경이 필요하다는 증거가 나오면 작업을 중단하고 다시 승인받는다.

## 17. 테스트 계획

### Evidence 단위 테스트

- 같은 parent의 hit가 하나의 group을 구성한다.
- parent 없는 chunk는 단독 group이다.
- group 내부 hit가 source order를 유지한다.
- 누락된 parent 조각은 incomplete로 판정된다.
- title/header-only는 procedure unit이 아니다.
- action 없는 높은 similarity hit는 충분성을 만들지 못한다.
- branch-aware duplicate는 제거하되 성인/소아 분기를 합치지 않는다.
- 단일 문장으로 완결된 절차는 무조건 multi-group을 요구하지 않는다.
- Q002 fixture의 pre-LLM 필수 stage recall은 10/10이다.

### Budget 단위 테스트

- group 전체가 들어가거나 전체가 제외된다.
- parent 일부만 prompt에 들어가는 경우가 없다.
- byte budget과 token budget 각각의 제외 reason이 정확하다.
- group 하나도 들어가지 않으면 Groq 전에 차단한다.
- post-budget group/unit/branch 감소를 각각 차단한다.
- 일반 parent 없는 fact 질문은 기존 선택 순서를 유지한다.

### Generation 및 citation 테스트

- 충분한 근거에서는 mock HTTP transport 호출이 정확히 1회다.
- pre-gate, budget, post-gate 실패에서는 transport 호출이 0회다.
- Q006 transport 호출은 0회다.
- exact statement/quote/chunk 조합은 통과한다.
- 부분 문장, 잘못된 chunk, 조작한 숫자·단위·조건·행동은 실패한다.
- procedure citation의 source-order 역전은 실패한다.
- invalid LLM output에 자동 재호출하지 않는다.
- quota 예약은 모든 근거 gate 후에만 발생한다.

### 통합·회귀 테스트

- 승인 BM25 Q001~Q006 순위, 원점수와 phase/tier가 불변이다.
- Q002 1차 retrieval/context bundle과 기존 `review.html`이 불변이다.
- 로컬 FAISS와 운영 pgvector가 동일한 evidence/generation 계약을 사용한다.
- 기존 fact, caution, comparison, follow-up과 conflict 검사가 통과한다.
- 전체 테스트와 Ruff가 통과한다.

실제 외부 Groq 없이도 mock transport로 호출 수, payload, 선택 evidence와 citation 검증을 먼저 완료한다.

## 18. 평가 산출물과 수동 검수

기존 `artifacts/2026-09-13_rag-phase1-retrieval/`은 수정하지 않는다. 2차는 새 날짜/기능 디렉터리에 다음을 생성한다.

- pre-LLM evidence groups와 coverage JSON/CSV
- group별 예상 byte/token 사용량
- 선택/제외 group과 reason
- post-budget coverage와 pre/post delta
- Q002 gold stage의 pre-budget/post-budget/citation recall
- Q006 및 답 없음 질문의 `llm_called=false` trace
- mock generation의 statement/quote/chunk 검증 결과
- 사람이 Q002 원문, group, prompt 포함 여부와 citation을 나란히 볼 수 있는 `review.html`

실제 Groq 호출은 코드·mock 검증과 수동 evidence 검수를 먼저 통과한 뒤, 사용자가 별도로 실행을 승인한 경우에만 Q002 등 제한된 평가 질문에 수행한다. 실제 호출 결과도 별도 파일로 저장해 mock 결과와 섞지 않는다.

## 19. 합격 기준

### Evidence 및 budget

1. Q002 pre-LLM 필수 gold stage recall 10/10 유지
2. Q002 post-budget 필수 gold stage recall 10/10 유지
3. parent 일부 포함 0건
4. evidence exact duplicate 0건
5. 성인/소아 분기 유지
6. 동일 document/branch source-order 역전 0건
7. budget으로 required group이 빠지면 Groq 호출 0회

### Generation 및 citation

1. 충분한 mock 요청의 HTTP 호출 정확히 1회
2. Q006과 답 없음 fixture HTTP 호출 0회
3. 답변 statement citation coverage 100%
4. citation quote의 원문 정확 일치 100%
5. Q002 cited 필수 gold stage recall 10/10. 단, 1 procedural unit을 1 statement로 강제하지 않는다.
6. 근거 없는 숫자·단위·조건·행동 0건
7. 잘못된 생성 결과에 재호출 0회

### 회귀

1. 1차 Q002 evidence bundle 불변
2. 승인 BM25 지표·순위·trace 불변
3. 기존 질문 유형 테스트 통과
4. 전체 테스트와 정적 검사 통과

Q002가 token budget에 들어가지 않으면 budget이나 hit 수를 즉시 늘리지 않는다. 어느 group과 token 비용이 원인인지 결과에 기록하고 사용자 승인을 기다린다.

## 20. 구현 순서

사용자 승인 후 다음 순서로 진행한다.

1. 현재 Q002 12-hit bundle과 group 경계를 fixture로 고정한다.
2. `mvp/evidence.py`에 group/coverage 순수 함수와 단위 테스트를 작성한다.
3. 기존 일반 evidence gate 회귀를 확인한다.
4. `prompt_messages()`를 group 원자 선택으로 변경하고 byte/token 경계 테스트를 작성한다.
5. `generate()`에 pre/post coverage 비교와 고정 차단 reason을 연결한다.
6. citation의 substantive body와 procedure source-order 검증을 추가한다.
7. Q006 및 병원 도메인 답 없음 질문에서 mock transport 호출 0회를 검증한다.
8. 충분한 Q002 mock 응답에서 HTTP 호출 1회와 citation/gold coverage를 검증한다.
9. 집중 테스트, 전체 테스트와 Ruff를 실행한다.
10. LLM 없이 evidence/budget 수동 검수 `review.html`을 새 artifacts에 생성한다.
11. `docs/rag/03_EVIDENCE_AND_GENERATION_RESULT.md`에 코드·mock 결과를 작성한다.
12. 작업을 멈추고 사용자의 실제 Groq 평가 승인 또는 추가 수정 지시를 기다린다.

이 순서에서는 계획 승인만으로 실제 Groq를 호출하지 않는다. 외부 호출은 별도 명시 승인을 받은 제한된 평가 단계에서만 수행한다.

## 21. Rollback

2차 변경은 chunk, embedding과 DB를 바꾸지 않는다.

1. `mvp/evidence.py`의 group/coverage 필드와 procedure 분기를 제거해 기존 assessment로 되돌린다.
2. `mvp/ai.py`의 group budget을 기존 hit 단위 budget으로 되돌린다.
3. pre/post coverage 비교와 citation 순서 검사를 제거한다.
4. search trace 추가 필드와 AI 버전 증가를 이전 값으로 되돌린다.
5. 2차 테스트와 artifacts는 1차 결과와 분리해 보존한다.

1차 procedure context, 승인된 BM25, 원본 문서와 기존 artifacts는 rollback 대상이 아니다.

## 22. 위험과 확인 사항

- 구조적 procedure unit은 임상적 완전성의 대체물이 아니다. Q002 gold와 사람 검수가 계속 필요하다.
- required group만 post-budget hard gate 대상으로 삼고 optional group은 제외를 허용한다. required group이 budget을 넘으면 일부 답변을 만들지 않고 명시적으로 차단한다.
- 현재 output 최대 10 statements와 Q002 필수 10단위는 경계값이다. 실제 원문 문장 분할 결과가 10개를 넘으면 임의로 합치지 않고 실패를 기록해야 한다.
- 1 procedural unit을 1 output statement로 강제하지 않는다. 기존처럼 하나의 완전한 grounded statement가 여러 evidence를 가질 수 있지만, statement 수를 맞추기 위한 근거 없는 단계 병합이나 원문 합성은 허용하지 않는다.
- context-only parallel branch를 관련 evidence로 인정할 때는 substantive/action/topic 검사를 다시 수행해야 한다.
- citation source-order 검사는 같은 document/branch에만 적용한다. 서로 다른 문서나 독립 분기의 전역 순서를 임의로 만들지 않는다.
- 현재 FastEmbed 모델의 pooling 경고는 embedding 별도 검토 대상이며 2차 변경에 포함하지 않는다.

## 23. 사용자 승인 대기

2차 권고안은 “1차 evidence bundle을 parent/source group으로 원자화하고, pre-LLM에서 승인된 procedure coverage가 prompt budget 후에도 온전히 유지될 때만 Groq 경로를 여는 구조”다. 생성된 모든 문장은 기존 extractive quote/chunk 검증과 procedure source-order 검증을 통과해야 한다.

이 계획 작성으로 작업을 멈춘다. `mvp/evidence.py`, `mvp/ai.py`, 테스트와 실행 코드는 수정하지 않았고 Groq를 호출하지 않았다. 기존 Q002 수동 검수 HTML과 1차 artifacts도 변경하지 않았다.

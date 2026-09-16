# SCHAT Hybrid RAG 2차 Evidence 및 Generation 결과

- 구현·검증일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/02_EVIDENCE_AND_GENERATION_PLAN.md`
- 기준선: 승인된 BM25와 1차 retrieval/context 결과
- 검증 방식: `httpx.MockTransport`만 사용, 실제 Groq 호출 없음
- 상태: 2차 안전 경계 구현 완료, Q002 생성 준비는 budget 후 필수 근거 손실로 승인 보류

## 1. 결론

Evidence group을 `required`와 `optional`로 구분하고, prompt budget을 개별 hit가 아닌 완전한 group 단위로 적용하도록 구현했다. post-budget hard gate는 모든 required group과 required procedural unit, 필요한 branch, parent group 원자성 및 source order가 유지될 때만 생성 호출을 허용한다. optional group은 예산으로 제외돼도 호출을 차단하지 않고 trace에 제외 이유만 남긴다.

Q002의 현재 evidence bundle은 budget 전 필수 gold stage 10/10을 모두 회수한다. 그러나 현재 Groq 요청 예산 3500 기준에서 전체 필수 group을 담은 요청 예약량이 3501로 계산됐다. 소아 branch의 필수 parent group을 부분 절단하지 않고 통째로 제외한 결과, budget 후 필수 gold stage recall은 7/10이 됐고 `missing_evidence_group` gate가 생성 호출을 차단했다. Q002의 MockTransport HTTP 호출은 0회다.

이는 작은 예산 초과를 이유로 필수 절차를 누락한 부분 답변을 생성하지 않는 의도된 안전 동작이다. 이번 범위에서는 예산, threshold, 검색 또는 group 분류를 임의 조정하지 않았다. Q002에 대해 실제 Groq를 호출할 준비가 됐다고 승인하지 않으며, 다음 조정은 사용자 승인 후 별도 단계로 다룬다.

별도의 충분한 단일-group fixture에서는 MockTransport가 정확히 1회 호출됐고, exact quote/chunk citation 검증을 통과했다. Q006은 검색·생성 전에 차단되어 MockTransport 호출 0회와 고정 문구를 유지했다. 실제 네트워크 또는 Groq 호출은 전혀 발생하지 않았다.

## 2. 필수 설계 보완 반영

### EvidenceGroup

각 group은 다음 정보를 request scope에서 가진다.

- `required`: 생성에 반드시 필요한 group인지 여부
- `requirement_reason`: `seed`, `seed_neighbor`, `explicit_branch` 등 required 판정 근거
- `branch`: `common`, `adult`, `pediatric` 등 명시적으로 관찰된 분기
- `complete`: 동일 parent가 부분적으로 잘리지 않았는지 여부
- source 시작·종료 순서와 포함 hit

운영 코드는 Q002 gold ID나 진정간호 단계명을 하드코딩하지 않는다. required 여부는 검색 seed, 직접 연결된 seed neighbor, 원문에 명시된 branch 같은 일반 구조 신호로만 판정한다. Q002의 필수/보완 gold 구분은 평가 fixture에만 남겼다.

### ProcedureCoverage

coverage는 다음 집합을 분리해 기록한다.

- `required_group_keys`
- `optional_group_keys`
- `required_procedural_unit_keys`
- `optional_procedural_unit_keys`
- `required_branches`
- `source_ordered`, `complete`, `duplicate_count`

post-budget 검사는 pre-budget과 post-budget의 전체 group 집합이 같은지를 요구하지 않는다. 대신 다음 required 불변식만 검사한다.

1. 모든 required group 유지
2. 모든 required procedural unit 유지
3. 필요한 branch 유지
4. parent group 부분 절단 없음
5. 동일 문서·분기 내 source order 유지

optional group이 제외되면 gate를 실패시키지 않으며 `prompt_excluded_groups`에 `token_budget` 등 이유를 남긴다.

### 답변 구조 불변식

- 1 procedural unit을 1 output statement로 강제하지 않는다.
- 하나의 grounded statement가 여러 evidence를 갖는 기존 구조를 유지한다.
- 서로 다른 원문 단계를 근거 없이 한 문장으로 병합하지 않는다.
- 최대 10 statements를 채우거나 맞추기 위해 원문 내용을 합성하지 않는다.
- 각 statement의 quote는 실제 chunk 원문의 완전한 문장 또는 표 행과 연결돼야 한다.

## 3. 구현 내용

### `mvp/evidence.py`

- `EvidenceGroup`, `ProcedureCoverage` 및 확장된 `EvidenceAssessment`를 구현했다.
- procedure evidence를 parent 중심의 원자 group으로 구성한다.
- substantive/action/질문 호환성에 근거해 procedure seed를 인정한다.
- required/optional group, procedural unit과 branch coverage를 계산한다.
- budget 전후 required coverage 손실을 `required_coverage_loss()`로 판정한다.
- source order와 procedure citation 순서를 검증한다.
- 단순 명사만으로 procedure action gate가 열리지 않도록 실제 행위 표현 범위를 제한했다.

### `mvp/ai.py`

- AI 동작 버전을 8에서 9로 올렸다.
- prompt budget을 group 단위로 적용한다.
- required group을 먼저 선택하고 optional group은 남은 예산에서만 선택한다.
- group 일부만 들어가는 선택을 금지하고 선택된 hit는 원문 source order로 복원한다.
- budget 적용 후 evidence를 다시 평가하고 required coverage가 줄면 endpoint·quota·transport 이전에 차단한다.
- 충분한 경우에만 transport를 한 번 호출한다.
- 기존 exact quote/chunk citation 검증에 procedure source-order 검증을 연결했다.

### `mvp/search_trace.py`

기존 BM25 raw score, phase/tier, semantic, RRF와 rerank trace는 유지하고 다음을 추가했다.

- pre/post required·optional group keys
- pre/post procedure coverage
- prompt 선택 group keys
- 제외 group과 제외 이유
- prompt chunk IDs
- budget assessment
- `llm_called`, 차단 stage와 reason

## 4. Q002 Evidence Group 결과

Q002의 1차 context-expanded evidence에서 다음 5개 group이 구성됐다.

| 순서 | 내용 범위 | required 근거 | branch | chunk |
|---:|---|---|---|---|
| 1 | QSED 처방 확인 | seed | common | 00013 |
| 2 | 진정 전 평가·계획 | seed | common | 00015 |
| 3 | 설명·동의 | seed_neighbor | common | 00016 |
| 4 | 성인 진정 전·중·후 절차 | explicit_branch | adult | 00019~00022 |
| 5 | 소아 진정 전·중·후 절차 | seed | pediatric | 00023~00027 |

다섯 group은 모두 완전한 parent 단위이며 duplicate는 0건, source order 역전도 0건이다. 평가 fixture의 gold stage 기준으로 budget 전 recall은 다음과 같다.

| 구분 | 회수 | 전체 | Recall |
|---|---:|---:|---:|
| 필수 | 10 | 10 | 100% |
| 보완 | 2 | 3 | 66.7% |

보완 stage 누락은 운영 required 판정에 하드코딩하거나 강제로 승격하지 않았다.

## 5. Group Budget 및 post-budget gate

| 항목 | 결과 |
|---|---:|
| 요청 token budget | 3500 |
| 전체 group 포함 예약량 | 3501 |
| 선택 후 예약량 | 2651 |
| 선택 chunk | 00013, 00015, 00016, 00019~00022 |
| 원자적으로 제외된 group | 00023~00027 소아 parent group |
| 제외 이유 | `token_budget` |
| budget 후 필수 gold stage recall | 7/10, 70% |
| coverage loss | `missing_evidence_group` |
| Q002 transport 호출 | 0회 |

소아 parent group의 일부만 잘라 예산에 넣지 않았다. required group과 pediatric branch가 사라졌기 때문에 `generate()`는 Groq endpoint나 quota를 사용하기 전에 고정 근거 부족 결과를 반환했다.

현재 Q002 bundle에는 optional group이 없다. 따라서 실제 Q002에서 optional 제외 허용을 관찰하지는 못했지만, 별도 단위 테스트에서 optional group만 budget으로 제외될 때 required coverage를 유지하고 MockTransport 1회 호출이 허용되는 것을 검증했다.

## 6. Mock Generation 및 citation 검증

실제 Groq 대신 `httpx.MockTransport`를 사용했다.

| 사례 | Mock HTTP 호출 | 결과 |
|---|---:|---|
| Q002 현재 bundle | 0회 | budget 후 required group 손실로 차단 |
| 충분한 단일-group fixture | 1회 | answerable, statement 1개, exact citation 통과 |
| Q006 답 없음 | 0회 | LLM 전 차단, 고정 문구 반환 |

추가 테스트에서 하나의 grounded statement가 두 개 evidence를 유지할 수 있음을 확인했다. procedure statement의 citation이 원문 순서를 거꾸로 참조하면 검증에서 거부한다. statement 수를 맞추기 위한 단계 합성 로직은 추가하지 않았다.

## 7. Q006 및 답 없음 동작

Q006은 `out_of_scope`로 판정되어 prompt 구성이나 transport 호출 전에 종료된다.

반환 문구:

> 등록된 지침서에서 확인할 수 없습니다.

MockTransport 호출은 0회이며, semantic similarity나 시점 정보가 0점 BM25 후보를 생성 근거로 승격시키지 않는다.

## 8. 테스트 및 검수 결과

추가 테스트는 다음을 검증한다.

- required seed, 직접 neighbor, 명시 branch와 optional context 분류
- 명사-only 문구의 procedure action 오인 방지
- optional group 손실 허용
- required group/unit/branch 손실 차단
- parent group 원자적 budget 선택
- optional 제외 시 trace 기록과 정확히 1회 mock 호출
- required unit 손실 시 transport 0회
- procedure citation source order 검증
- 한 statement의 multiple evidence 유지

실행 결과:

- 관련 집중 테스트: `69 passed`
- 전체 테스트: `236 passed, 1 skipped`
- Ruff 정적 검사: 통과
- `git diff --check`: 오류 없음
- 코드 검수: 추가 threshold·점수 가감·실제 네트워크 호출·gold ID 하드코딩 없음

## 9. 산출물

기존 artifacts는 덮어쓰지 않았다. 새 산출물은 다음 위치에 저장했다.

`artifacts/2026-09-13_rag-phase2-evidence-generation/`

- `evidence_generation_report.json`
- `q002_evidence_groups.csv`
- `review.html`

`review.html`은 Q002의 group별 required 여부, 판정 이유, branch, chunk 범위, budget 선택·제외 결과와 pre/post gold recall을 사람이 검수할 수 있게 유지한다.

## 10. 변경 및 비변경 범위

이번 단계에서 변경한 파일:

- `mvp/evidence.py`
- `mvp/ai.py`
- `mvp/search_trace.py`
- `tests/test_rag_evidence_groups.py`
- `tools/rag_phase2_evaluate.py`
- `docs/rag/02_EVIDENCE_AND_GENERATION_PLAN.md`
- 이 결과 문서와 새 phase2 artifacts

변경하지 않은 기준선:

- 승인된 SCHAT BM25, tokenizer, query expansion과 temporal rerank
- embedding 모델과 벡터
- RRF
- 1차 `mvp/context.py` 결과와 phase1 artifacts
- 원본 문서, chunk ID, DB schema와 기존 BM25 artifacts
- cross-encoder

실제 Groq 호출, dependency 설치 및 외부 네트워크 호출은 수행하지 않았다.

## 11. 승인 판단과 작업 중지

required/optional evidence 분리, group 원자적 budget, budget 후 required coverage gate, MockTransport 기반 단일 호출과 exact citation 검증은 계획대로 동작했다. 안전 경계 구현 자체는 승인 후보로 판단한다.

다만 현재 Q002는 필수 evidence 전체가 현재 3500 token budget에 들어가지 않아 생성 호출이 차단된다. 이는 부분 절차 생성을 막는 올바른 결과이지만 Q002 end-to-end 생성 준비가 완료됐다는 뜻은 아니다. 예산 조정, 더 작은 근거 표현, required 분류 변경 또는 prompt 압축 중 어떤 방식을 택할지는 별도 분석·승인이 필요하다.

이 결과 문서 작성으로 작업을 멈춘다. 실제 Groq 호출, 추가 RAG 단계, budget·threshold 조정, embedding/cross-encoder 변경 또는 BM25 변경은 사용자 승인 전 진행하지 않는다.

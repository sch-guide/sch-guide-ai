# SCHAT Hybrid RAG 1차 retrieval/context 구현 결과

- 구현·평가일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 설계: `docs/rag/01_ARCHITECTURE.md`의 C안
- 승인 범위: Q002 gold fixture, 현행 retrieval baseline, procedure evidence bundle
- 평가 질문: Q002 “진정간호 절차는?”, Q006 “화성 우주선의 궤도 계산 공식은?”
- 상태: 1차 구현·검증 완료, 다음 단계 승인 대기

## 1. 결론

Q002의 필수 절차 단위 recall은 기존 context-expanded evidence의 5/10(50%)에서 새 procedure evidence bundle의 10/10(100%)로 개선됐다. Hybrid 후보 단계에서는 BM25, semantic, RRF가 필수 10/10을 모두 회수하고 있었으나, 기존 rerank seed는 3/10, 기존 context 확장은 5/10만 남겼다. 따라서 이번 평가에서는 후보 검색보다 검색 이후의 seed 문맥 구성 방식이 주된 손실 지점이었다.

개선 bundle은 `chunk-00013`, `00015`, `00016`, `00019`~`00027`의 substantive chunk 12개로 구성됐다. 성인과 소아 분기를 모두 보존했고, 동일 문서 안에서 chunk index가 12, 14, 15, 18~26 순으로 증가해 원문 순서 역전은 0건이다. 최종 branch-aware exact duplicate도 0건이다.

Q006은 승인된 BM25에서 양수 후보가 0개였고 현재 query plan에서 `out_of_scope`로 판정됐다. 검색 hit와 충분한 evidence가 없으며, Groq를 호출하지 않고 `등록된 지침서에서 확인할 수 없습니다.`로 끝나는 동작을 유지했다.

이번 결과는 retrieval/context 1차 변경에 한정해 승인 가능하다. 아직 `mvp/evidence.py`의 procedure 충분성 gate와 `mvp/ai.py`의 원자 evidence-group prompt budget은 구현하지 않았으며, Groq 답변 생성·citation 평가는 수행하지 않았다.

## 2. 구현 범위

### Q002 gold stage fixture

`tests/fixtures/q002_gold_stages.json`에 사람이 검수한 13개 절차 단위를 기록했다.

- 필수 10개: 공통 4개, 성인 3개, 소아 3개
- 보완 3개: 소아 이동·대체 모니터, 소아 퇴실 기록·기준, 공통 퇴실 교육
- 각 단위별 허용 chunk ID
- `required` / `supplemental` 구분
- `common` / `adult` / `pediatric` 분기
- 원문 순서 1~13
- 원문 PDF SHA-256와 안정적 평가 document ID

chunk ID와 단계명은 평가 fixture에만 존재한다. 운영 검색 코드에는 Q002 ID나 진정간호 단계명을 하드코딩하지 않았다.

### procedure context 구성

`mvp/context.py`의 일반 질문 경로는 유지하고, 절차형 질문에만 다음 정책을 적용했다.

1. reranker가 선택한 여러 seed를 모두 입력으로 받는다.
2. 각 seed의 동일 `parent_id` 조각과 기존 연결 조건을 만족하는 이웃을 확장한다.
3. 원문에 명시된 `[성인]` 또는 `[소아]` 분기와 같은 표제 stem을 가진 병렬 분기를 찾아 해당 parent를 함께 확장한다.
4. 제목·표 머리글 등 substantive body가 없는 chunk는 evidence bundle에서 제외한다.
5. 문서, 분기와 정규화 원문을 기준으로 exact duplicate를 제거한다.
6. 같은 문서 안에서는 `chunk.index` 원문 순서로 정렬한다.
7. 기존 `max_hits=12`를 유지하고, 잘린 parent가 있으면 기존 `context_complete=False` 계약을 유지한다.

성인·소아의 원문이 우연히 같아도 서로 다른 분기 근거이므로 합치지 않는다. 동일 분기 안의 완전 중복만 제거한다.

## 3. Q002 gold stage 구성

| 원문 순서 | 분기 | 구분 | 절차 단위 | 허용 chunk |
|---:|---|---|---|---|
| 1 | 공통 | 필수 | QSED 처방 확인 | 00013 |
| 2 | 공통 | 필수 | 진정 전 환자 평가와 계획 수립 | 00015 |
| 3 | 공통 | 필수 | 설명과 동의서 확인 | 00016 |
| 4 | 공통 | 필수 | 진정 전·중·후 기록 범위 | 00019 |
| 5 | 성인 | 필수 | 진정 전 활력징후와 산소포화도 확인 | 00020 |
| 6 | 성인 | 필수 | 진정 중 투약과 주기적 모니터링 | 00021 |
| 7 | 성인 | 필수 | 진정 후 회복 모니터링과 이동 | 00022 |
| 8 | 소아 | 필수 | 진정 전 활력징후 확인과 투약 | 00023 |
| 9 | 소아 | 필수 | 진정 중 주기적 모니터링 | 00024 |
| 10 | 소아 | 필수 | 조건별 혈압 측정과 진정 후 회복 모니터링 | 00025 |
| 11 | 소아 | 보완 | 이동과 대체 모니터 적용 | 00026 |
| 12 | 소아 | 보완 | 퇴실 기록과 기준 확인 | 00027 |
| 13 | 공통 | 보완 | 귀가 시 퇴실 교육 | 00028 |

이 fixture는 자동 코드가 임상적 완전성을 추론한 결과가 아니다. 향후 gold 변경은 원문을 다시 수동 검수한 뒤 별도로 승인해야 한다.

## 4. Retrieval funnel과 단계 recall

현재 embedding은 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384차원이다. BM25·tokenizer·query expansion·temporal rerank·RRF(`k=60`)와 기존 reranker는 수정하지 않았다.

| Funnel 단계 | 저장 후보 수 | 필수 recall | 보완 recall | 해석 |
|---|---:|---:|---:|---|
| BM25 양수 Top 40 | 33 | 10/10 (100%) | 3/3 (100%) | 승인 BM25가 모든 gold 단위를 후보로 회수 |
| Semantic Top 40 | 40 | 10/10 (100%) | 3/3 (100%) | 현재 로컬 embedding도 전체 gold 회수 |
| RRF Top 40 | 40 | 10/10 (100%) | 3/3 (100%) | 결합 후 손실 없음 |
| 기존 rerank seed | 6 | 3/10 (30%) | 1/3 (33.3%) | 00023, 00028, 00015, 00013, 00034, 00029 |
| 기존 context evidence | 12 | 5/10 (50%) | 3/3 (100%) | 인접 확장만으로 성인 parent와 소아 00025가 누락 |
| 개선 evidence bundle | 12 | 10/10 (100%) | 2/3 (66.7%) | 필수 공통·성인·소아 단위를 모두 보존 |

초기 Top 40 세 경로의 recall이 모두 100%이므로 이번 결과만으로 embedding, RRF 계수나 BM25를 추가 튜닝할 근거는 없다. 손실은 rerank seed와 기존 round-robin context 단계 사이에서 발생했다.

## 5. Multi-seed 전후 비교

### 기존 context evidence

기존 12개 순서는 다음과 같다.

`00023, 00028, 00015, 00013, 00034, 00029, 00024, 00026, 00016, 00014, 00032, 00027`

- 필수 단계: 5/10
- 성인 기록 parent 00019~00022 누락
- 소아 00025 누락
- 00014 표 머리글과 00029·00034 기록 양식 표제가 제한된 자리를 차지
- 검색/rerank 순서 중심이라 원문 단계 순서가 아님

### 개선 evidence bundle

개선된 12개 순서는 다음과 같다.

`00013, 00015, 00016, 00019, 00020, 00021, 00022, 00023, 00024, 00025, 00026, 00027`

- 필수 단계: 10/10
- 공통, 성인, 소아 분기 포함
- 00019~00022 성인 parent와 00023~00027 소아 parent가 각각 완전하게 포함
- 제목·표 머리글 후보 제외
- 모든 hit의 `context_complete=True`
- 동일 문서 원문 순서 유지

추가된 chunk는 00019, 00020, 00021, 00022, 00025다. 제외된 chunk는 00014, 00028, 00029, 00032, 00034다.

00028 퇴실 교육은 보완 단위이며 12-hit 예산에서 제외됐다. 필수 단계는 모두 포함됐지만 보완 recall이 2/3인 점은 남아 있다. 이번 승인 범위에서는 `max_hits` 증가나 prompt budget 정책을 추가하지 않았고, 후속 evidence/prompt 단계에서 원자 그룹과 보완 근거의 우선순위를 별도로 설계·검증해야 한다.

## 6. Duplicate 제거와 분기 보존

여러 seed의 parent/neighbor를 확장하면 원시 확장 목록에는 같은 chunk가 여러 경로에서 반복된다.

| 항목 | 결과 |
|---|---:|
| 원시 확장 occurrence | 27 |
| branch-aware exact signature | 20 |
| 제거된 중복 occurrence | 7 |
| 최종 bundle exact duplicate | 0 |

7건은 seed별 확장 경로가 겹쳐 같은 ID가 반복된 occurrence와, 동일 분기에서 원문이 완전히 같은 00033/00036 그룹을 포함한다. 최종 12개 bundle에는 중복이 없다.

성인·소아 분기는 둘 다 존재한다. 분기 marker를 signature에 포함하므로 서로 다른 분기의 동일 문구는 중복으로 제거하지 않는다. 이 경계는 synthetic 회귀 테스트로도 고정했다.

## 7. Source order 검증

최종 chunk index는 다음과 같다.

`12, 14, 15, 18, 19, 20, 21, 22, 23, 24, 25, 26`

- 문서별 index 오름차순: 통과
- 역전: 0건
- 문서 경계 초과: 0건
- 성인 parent 내부 순서: 통과
- 소아 parent 내부 순서: 통과

retrieval rank와 원점수는 평가 JSON에 별도로 남겼고, evidence를 원문 순서로 정렬하면서 삭제하지 않았다.

## 8. Q006 답 없음 동작

| 항목 | 결과 |
|---|---|
| query domain | `out_of_scope` |
| 양수 BM25 후보 | 0개 |
| 최종 retrieval hit | 0개 |
| 기존 evidence 판정 | `domain_or_clarification`, insufficient |
| LLM 호출 | 0회 |
| 최종 동작 | `등록된 지침서에서 확인할 수 없습니다.` |

Q006 검증에는 현재 `mvp/evidence.py`를 호출했지만 파일이나 gate 동작은 변경하지 않았다. Groq 또는 다른 LLM 호출은 없었다.

## 9. 테스트 및 정적 검사

추가·보강한 테스트는 다음을 검증한다.

- 여러 procedure seed의 parent와 명시적 병렬 분기 확장
- 성인/소아 분기 보존
- branch-aware exact duplicate 제거
- 동일 문서 source-order 정렬
- 제한된 hit 수로 parent가 잘릴 때 `context_complete=False`
- 일반 질문의 기존 seed-first round-robin 순서 유지
- 비-procedure 중복 parent 조각이 잘렸을 때 기존 ID 기반 completeness 유지
- 잘못된 `limit` 거부
- Q002 gold fixture의 단계 순서, 분기, 중요도와 안정적 chunk ID
- recall을 chunk occurrence가 아니라 gold 단위로 계산

실행 결과:

- 관련 집중 테스트: `58 passed`
- 전체 테스트: `228 passed, 1 skipped`
- Ruff: 변경 파일 검사 통과

첫 전체 테스트 시 시스템 사용자 Temp 디렉터리 접근 권한 때문에 81개 fixture가 setup 단계에서 실패했다. 저장소 내부의 격리된 `--basetemp`를 사용해 다시 실행했으며 228개 전체 테스트가 통과했다. 검증용 임시 디렉터리는 결과 확인 후 삭제했다.

## 10. 코드 검수와 범위 확인

코드 검수 중 공통 completeness helper가 비-procedure 경로에서도 exact-text 중복을 완전한 parent로 간주할 수 있는 범위 이탈을 발견했다. procedure 경로에서만 branch-aware duplicate signature를 허용하고, 비-procedure 경로는 기존과 동일하게 chunk ID 누락을 기준으로 불완전성을 판정하도록 수정했다. 해당 회귀 테스트와 전체 테스트가 통과했다.

최종 검수에서 추가로 수정해야 할 확정적 결함은 발견하지 못했다. 남은 수동 검수 위험은 다음과 같다.

- 현재 gold는 1개 지침서와 Q002에 한정된다.
- 병렬 분기 연결은 원문에 명시된 성인/소아 marker와 공통 표제 stem이 존재할 때만 동작한다.
- 개선 bundle의 필수 recall은 100%지만 보완 단위 00028은 12-hit 제한으로 제외됐다.
- 구조적 recall 100%가 임상적으로 완전한 답변이나 LLM 생성 정확성을 보증하지 않는다.

## 11. 산출물

기존 artifacts는 덮어쓰지 않았다. 새 산출물은 다음 디렉터리에 저장했다.

`artifacts/2026-09-13_rag-phase1-retrieval/`

- `q002_gold_stages.json`
- `q002_retrieval_funnel.json`
- `q002_retrieval_funnel.csv`
- `q006_no_answer.json`
- `review.html`

`q002_retrieval_funnel.json`에는 BM25·semantic·RRF Top 40, rerank seed, 기존 context, 개선 evidence의 chunk 원문·점수·순위와 단계 recall을 모두 보존했다. `review.html`은 gold stage와 multi-seed 전후 원문을 나란히 검수할 수 있게 구성했다.

## 12. 변경 및 비변경 확인

변경:

- `mvp/context.py`: procedure multi-seed parent/neighbor/parallel-branch 확장, dedup, source order
- `tests/test_rag_context.py`: 집중 회귀 테스트
- `tests/test_rag_contract.py`: 기존 불완전 parent 테스트의 의도를 명시하는 제한값
- `tests/fixtures/q002_gold_stages.json`: Q002 gold fixture
- `tools/rag_phase1_evaluate.py`: LLM 없는 재현 가능한 retrieval funnel 평가 도구
- 본 결과 문서와 새 artifacts

비변경:

- `mvp/evidence.py` gate
- `mvp/ai.py`와 prompt budget
- Groq 및 LLM 호출 경로
- embedding 모델과 dependency
- cross-encoder
- BM25Index, tokenizer, query expansion과 temporal rerank
- RRF, 기존 reranker와 seed 수
- 원본 문서, chunk ID, DB schema와 기존 artifacts

## 13. 승인 대기

1차 범위의 구현과 결과 작성으로 작업을 멈춘다. 다음 단계인 procedure evidence 충분성 gate, 원자 evidence-group prompt budget, Groq 생성과 citation 평가는 사용자의 별도 승인 전에는 진행하지 않는다.

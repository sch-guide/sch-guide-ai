# SCHAT Hybrid RAG 아키텍처 설계

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준선: `docs/bm25/06_TEMPORAL_RERANK_RESULT.md`에서 최종 승인된 BM25
- 단계: 분석·설계 완료, 구현 승인 대기
- 최종 권고: **C. 현재 구조를 활용하는 최소 변경 Hybrid RAG**

## 1. 결정 요약

현재 SCHAT에는 이미 로컬 FAISS 또는 운영 pgvector 의미 검색, 승인된 SCHAT BM25, RRF, 규칙 기반 reranker, 인접 문맥 확장, LLM 호출 전 근거 검사, Groq 답변의 문장별 원문 인용 검증이 존재한다. 따라서 새 검색 프레임워크나 cross-encoder를 먼저 추가하지 않는다.

권고 구조는 다음과 같다.

1. 승인된 BM25의 원점수, 제목-only 정책, 시점 tier 순서, tokenizer와 query expansion을 고정한다.
2. 현재 로컬 무료 embedding과 FAISS/pgvector 의미 검색을 semantic baseline으로 사용한다.
3. BM25 Top 40과 semantic Top 40을 현재 RRF(`k=60`)로 결합한다.
4. 기존 규칙 기반 reranker의 관련성 입구 조건을 유지하되, `procedure` 질문에서는 하나의 상위 chunk가 아니라 여러 substantive seed와 그 의미 단위·인접 문맥을 근거 묶음으로 구성한다.
5. 중복을 제거한 뒤 같은 문서 안에서는 원문 `chunk.index` 순서로 Groq에 전달한다. 성인/소아처럼 분기된 절차는 섞지 않고 출처 범위별로 유지한다.
6. LLM 호출 전과 prompt 예산 적용 후에 근거 충분성을 각각 검사한다. 불충분하면 Groq를 호출하지 않고 `등록된 지침서에서 확인할 수 없습니다.`를 반환한다.
7. Groq는 검색, 관련성 판정 또는 누락 단계 추론에 쓰지 않고, 검증을 통과한 최소 근거에서 최종 답변 문장을 선택·배열하는 데만 사용한다.
8. 모든 답변 문장은 실제 chunk의 완전한 원문 문장 또는 표 행과 정확히 연결한다.

Q002는 이 구조의 대표 multi-chunk 시험 질문으로 둔다. 성공 조건은 `chunk-00013` 하나가 1위인 것이 아니라, 사람이 정의한 절차 단계 gold set이 최종 evidence bundle에 충분히 들어오고, 중복 없이 원문 순서로 제시되며, 각 단계가 원문 citation을 갖는 것이다.

## 2. 승인 경계와 고정 불변식

이 문서는 구현을 허가하지 않는다. 사용자 승인 전에는 RAG 코드, embedding 모델, dependency, DB schema, 원본 문서, 인덱스와 평가 산출물을 수정하지 않고 LLM을 호출하지 않는다.

다음 BM25 구성은 변경 불가 기준선이다.

- `BM25Index` 계산식과 현재 SCHAT 구현
- 제목-only chunk의 독립 lexical 후보 제외
- `match → neutral → mismatch` 시점 기반 안정적 tier 재정렬
- 현재 tokenizer와 query expansion
- BM25 원점수와 phase/tier 진단 정보
- Q001~Q005 Hit@1/3/5/10 100%, MRR 1.000
- Q006 양수 BM25 후보 0개
- 양성 질문 제목-only Top 10 노출 0/50

Q002의 multi-chunk 문제 때문에 BM25를 추가 튜닝하지 않는다. rank-bm25로 교체하거나 BM25와 dense 점수를 직접 가중 합산하지도 않는다.

## 3. 확인된 현재 구조

`docs/PRD.md`는 현재 저장소에 존재하지 않았다. AGENTS.md가 이 파일을 요구하므로 복원 여부는 별도 문서 정비 사항이지만, 이번 설계는 README와 실제 코드의 현재 계약을 기준으로 했다.

### 현재 호출 흐름

1. `mvp/app.py`가 `plan_query()`로 질문 유형, 후속 질문 문맥과 문서 범위를 정한다.
2. `Embedder.encode()`가 질문 벡터를 로컬에서 만든다.
3. 로컬은 `FAISS IndexFlatIP`, 운영은 Supabase `guide_search` pgvector RPC로 semantic 후보를 찾는다.
4. 같은 권한 범위의 전체 chunk에 승인된 SCHAT BM25를 적용한다.
5. 두 Top 40 순위를 `rrf()`로 결합하고 `rerank()`가 최대 6개 seed를 선택한다.
6. `expand_context()`가 같은 문서·section의 인접 chunk와 같은 semantic block 문맥을 최대 12개까지 추가한다.
7. `assess_evidence()`가 주제, 요청 aspect, entity, 문서 수와 semantic block 완전성을 검사한다.
8. `prompt_messages()`가 byte/token 예산 안에서 검색 근거만 고른다.
9. `generate()`가 Groq를 한 번 호출하고 `validate_answer()`가 JSON schema, 정확한 quote, 숫자·단위·행동과 문장별 citation을 검증한다.
10. Streamlit UI가 문서명, 페이지/위치와 원문을 표시한다.

### 현재 구조의 강점

- 로컬 문서와 질문 embedding은 외부 embedding API로 보내지 않는다.
- 로컬/운영 모두 권한이 허용된 문서만 검색한다.
- BM25 전용 후보와 semantic 전용 후보를 RRF로 함께 살릴 수 있다.
- title-only seed, 주제 불일치, 근거 없는 높은 dense 점수를 후단에서 차단한다.
- `parent_id`, `previous_chunk_id`, `next_chunk_id`, `section`, `page`, `index`가 문맥과 citation에 이미 존재한다.
- 근거가 없으면 LLM 호출 전에 중단하고, LLM 결과도 원문과 일치하지 않으면 노출하지 않는다.

### 남은 구조적 공백

- 기존 seed 선택은 개별 chunk 점수 중심이어서 procedure 질문의 여러 단계 회수를 명시적으로 보장하지 않는다.
- 문맥 확장은 인접성을 보존하지만, procedure coverage를 측정하거나 여러 seed의 중복·분기·단계 순서를 명시적으로 관리하지 않는다.
- prompt budget은 hit를 한 개씩 추가하므로 같은 semantic block의 일부만 빠지는 상황을 사후 차단한다. 처음부터 원자적 근거 그룹 단위로 예산을 배분하는 편이 명확하다.
- trace에 검색 단계는 상세히 남지만 procedure coverage, 중복 제거, prompt 정렬과 예산 제외 이유는 아직 없다.
- 현재 embedding의 SCHAT 한국어/영문 병원 지침 검색 성능은 별도 수동 gold set으로 검증되지 않았다.

## 4. Embedding 후보 검토

### 4.1 현재 기준선: paraphrase-multilingual-MiniLM-L12-v2

현재 `mvp/settings.py`는 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384차원을 사용한다. 모델 카드는 50개 언어, 384차원 sentence/paragraph embedding, 최대 입력 128 토큰을 명시한다. 현재 FastEmbed 지원 목록에도 384차원·약 0.22GB의 multilingual 모델로 포함된다. [Sentence Transformers 모델 카드](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), [FastEmbed 지원 모델](https://qdrant.github.io/fastembed/examples/Supported_Models/)

장점은 이미 설치·색인되어 있고, CPU 로컬 실행과 현재 384차원 FAISS/pgvector schema에 맞으며, 한국어와 영문 혼합 문장을 처리할 수 있다는 점이다. 단점은 범용 paraphrase 모델이므로 SCHAT의 병원 절차 검색 우위를 모델 카드만으로 보장할 수 없다는 점이다.

**결정:** 첫 Hybrid RAG 구현에서는 현재 모델을 유지하고 성능을 측정한다. 모델 변경을 RAG 구조 변경과 동시에 하지 않는다.

### 4.2 후속 실험 후보: multilingual-e5-small

E5-small은 retrieval 용도로 학습된 384차원 모델이고 한국어를 포함한 다국어를 지원한다. 모델 카드는 retrieval 입력에 비영어권도 `query:`와 `passage:` 접두어가 필요하다고 명시한다. [multilingual-e5-small 모델 카드](https://huggingface.co/intfloat/multilingual-e5-small/blob/main/README.md)

차원은 현재 schema와 같지만 모델 ID, pooling, 최대 입력, 질문/본문 전처리가 달라 기존 벡터를 재사용할 수 없다. 전체 문서 재색인과 동일 질문 semantic/hybrid 회귀 평가가 필요하다. 현재 FastEmbed 지원 목록에서 small 변형의 직접 지원도 확인되지 않았으므로 dependency/runtime 경계까지 검증해야 한다.

**결정:** 구현 1차 범위에서 제외한다. 현재 모델이 Q002 gold stage recall 또는 한국어/영문 표현 변형 평가를 충족하지 못할 때 별도 승인된 오프라인 비교 대상으로만 사용한다.

### 4.3 후속 실험 후보: BAAI/bge-m3

BGE-M3 모델 카드는 100개 이상 언어, 1024차원, 최대 8192 토큰과 dense/sparse/multi-vector 기능을 명시하며 hybrid retrieval과 reranking을 권고한다. [BGE-M3 모델 카드](https://huggingface.co/BAAI/bge-m3)

다국어·장문 검색 잠재력은 크지만 SCHAT의 현재 chunk는 110토큰 이하이고, 저장 schema는 `vector(384)`다. 채택하면 FAISS/pgvector 차원, schema/migration, 모든 저장 벡터, 모델 메모리와 배포 이미지를 함께 바꿔야 한다. 또한 BGE sparse를 도입하면 승인된 SCHAT BM25와 역할이 겹친다.

**결정:** 현재 요구에 비해 변경 반경이 커서 기각한다. 장문 청킹 전략 자체가 변경되는 별도 프로젝트에서만 재검토한다.

### 4.4 모델 선택 원칙

모델 카드의 일반 다국어 성능을 병원 지침 검색 성능으로 간주하지 않는다. 향후 모델 비교는 동일 chunk, 동일 질문, 동일 gold label에서 다음을 측정한다.

- 한국어, 영문, 한영 혼용 질문별 semantic Recall@5/10/40
- Hybrid RRF 후 relevant chunk와 Q002 gold stage recall
- Q006 및 병원 도메인 내 답 없음 질문의 false-positive
- CPU cold/warm latency, peak memory와 모델 크기
- 로컬 FAISS와 운영 pgvector 결과 동등성
- 재색인 시간, 배포 크기와 rollback 비용

## 5. 세 아키텍처 대안 비교

| 기준 | A. BM25 + FAISS + RRF + 기존 reranker | B. Hybrid + cross-encoder | C. 현재 구조 활용 최소 변경 Hybrid RAG |
|---|---|---|---|
| 검색 정확도 | 개별 chunk 회수는 높을 가능성. Q002 단계 완전성 보장은 약함 | 상위 후보 정밀도 개선 가능성이 가장 크지만 다단계 coverage를 자동 보장하지 않음 | 현재 회수를 유지하면서 multi-chunk bundle·완전성·순서를 직접 보강 |
| 한국어 성능 | 현재 multilingual model의 실제 SCHAT 성능 검증 필요 | multilingual cross-encoder 선택 시 잠재력은 있으나 병원 지침 검증 필요 | A와 같고, 구조적 단계 회수로 모델 의존도를 낮춤 |
| 응답속도 | 가장 빠름 | 모든 query-passage 쌍 추론으로 CPU 지연 증가 | A보다 소폭 증가. 추가 모델 추론 없이 순수 Python 구조 처리 |
| 로컬 실행 | 현재 그대로 가능 | 가능하지만 모델 다운로드·메모리·cold start 부담 | 현재 그대로 가능 |
| 비용 | embedding/RRF는 무료 로컬, Groq 생성 비용만 발생 | 로컬이면 API 비용은 없지만 자원 비용 증가 | 무료 로컬 retrieval, Groq 생성 비용만 발생 |
| Groq 토큰 | 기존 최대 12 hit에 중복이 섞일 수 있음 | 상위 정밀화로 줄 수 있으나 단계 누락 위험 | 중복 제거·원자 그룹 예산으로 필요한 최소 근거만 전달 |
| 구현 복잡도 | 낮음. 대부분 이미 존재 | 높음. 모델 lifecycle, pair batching, threshold, 배포 필요 | 중간. 기존 함수와 데이터 계약을 유지한 국소 변경 |
| Streamlit/웹 배포 | FAISS는 로컬, 운영은 이미 pgvector adapter 필요 | CPU/RAM과 시작 시간 증가. Community Cloud 제약 위험 | 기존 local/cloud adapter와 cache를 유지해 영향이 가장 작음 |
| citation 정확성 | chunk ID 기반으로 양호하나 절차 순서 보장은 약함 | 관련성은 좋아져도 citation 구조는 별도 필요 | 원문 그룹과 source order가 설계의 일부라 가장 명확 |
| Q002 적합성 | 관련 chunk가 섞여도 한 seed 중심으로 끝날 수 있음 | 관련 chunk 순위는 좋아질 수 있으나 여러 단계 선택 정책은 여전히 필요 | 여러 seed, parent/neighbor 확장, 중복 제거, source-order bundle을 명시적으로 적용 |
| rollback | 기존 구조라 단순 | 모델/dependency/재색인과 코드 rollback 필요 | context/evidence/prompt 정책과 버전만 되돌림 |

### A 판단

A는 현재 로컬 검색의 실제 구조에 가장 가깝다. 빠르고 단순하며 citation도 유지되지만, 현재 Q002 결과가 보여주듯 generic Top-k와 개별 chunk reranking만으로는 여러 단계가 Top 3/5에 모인다는 보장이 없다. 운영에서는 FAISS 대신 pgvector를 이미 쓰므로 FAISS를 공통 배포 구조로 강제하는 것도 불필요하다.

### B 판단

cross-encoder는 query와 passage 쌍을 함께 읽어 bi-encoder보다 정밀한 재정렬을 제공할 가능성이 있다. 예를 들어 BAAI의 multilingual reranker는 query-passage 쌍에 직접 relevance score를 내는 0.6B 모델이다. [bge-reranker-v2-m3 모델 카드](https://huggingface.co/BAAI/bge-reranker-v2-m3)

그러나 추가 모델은 CPU latency, memory, cold start, dependency와 버전 관리 부담을 만든다. 무엇보다 개별 passage relevance를 높이는 것과 절차의 여러 단계·분기·연속성을 확보하는 것은 다른 문제다. Q002 해결을 위해서도 C의 evidence bundle이 별도로 필요하므로 1차 구조로 채택하지 않는다.

### C 판단

C는 A의 검증 가능한 구성 요소를 그대로 쓰되, 검색 이후의 **근거 묶음 생성과 충분성 경계**만 보강한다. 로컬은 FAISS, 운영은 pgvector라는 현재 adapter 차이를 유지하고 두 경로가 같은 BM25, RRF, rerank, context/evidence 정책을 관찰하도록 한다.

변경 목적이 Q002 multi-chunk 처리, 최소 근거 전송, citation과 abstention이므로 가장 직접적이고 rollback 가능한 대안이다. 이를 최종 권고안으로 선택한다.

## 6. 권고 아키텍처의 end-to-end 흐름

```text
질문/후속 질문
  → QueryPlan + 권한 문서 범위
  → 로컬 query embedding
  → [승인 BM25 Top 40] + [FAISS 또는 pgvector semantic Top 40]
  → RRF(k=60)
  → 기존 관련성 admission + 규칙 기반 rerank
  → multi-seed 선택
  → parent/section/neighbor 문맥 확장
  → 중복 제거 + 분기 보존 + 문서 원문 순서 정렬
  → LLM 전 근거 충분성 검사
  → semantic block 단위 prompt budget
  → budget 후 근거 충분성 재검사
  → 충분: Groq 1회 최종 생성
  → exact sentence/quote/citation 검증
  → 검증된 답변 또는 고정 근거 부족 문구
```

의미 검색과 BM25는 후보 회수만 담당한다. RRF는 순위 척도만 결합하며 원점수 의미를 바꾸지 않는다. reranker는 후보의 답변 적합성을 판단하고, context builder는 여러 근거를 연결하며, evidence layer가 LLM 호출 가능 여부를 단독으로 소유한다.

## 7. 호출자 우선 사용 형태

공개 Streamlit 호출 형태는 유지한다.

```python
# 미구현 예시: app.py의 공개 흐름은 바꾸지 않는다.
plan = plan_query(question, previous_question, follow_up, documents, previous_sources)
vector = embedder.encode([bounded_embedding_question(plan.expanded, embedder)])[0]
hits = library.search(plan.query, vector, selected_document_ids, minimum, plan=plan)
answer, used_hits = generate(settings, plan.query, hits, user_id, plan=plan)
```

로컬과 운영 repository도 같은 반환 계약 `list[Hit]`를 유지한다. 새로운 framework/service abstraction을 공개하지 않는다.

검색 내부에서는 기존 context 함수에 plan을 명시적으로 전달한다.

```python
# 미구현 예시
seeds = rerank(plan, fused_candidates, minimum, trace=trace)
hits = expand_context(plan.query, seeds, chunks, limit=plan.max_hits, plan=plan)
```

`generate()`는 현재처럼 hits를 받으며 `assess_evidence()`가 procedure coverage까지 포함해 판단한다.

## 8. 최소 데이터 구조

현재 `Chunk`, `Hit`, `QueryPlan`, `Answer`, `Statement`, `Evidence`를 유지한다. 저장 schema나 chunk payload에는 필드를 추가하지 않는다.

다음 request-scoped 진단 값만 추가한다.

```python
# 미구현 타입
@dataclass(frozen=True)
class ProcedureCoverage:
    substantive_seed_ids: tuple[str, ...]
    evidence_chunk_ids: tuple[str, ...]
    semantic_block_keys: tuple[tuple[str, str], ...]
    procedural_unit_keys: tuple[str, ...]
    source_ordered: bool
    context_complete: bool
    duplicate_count: int

@dataclass(frozen=True)
class EvidenceAssessment:
    sufficient: bool
    hits: tuple[Hit, ...] = ()
    reason: str = ""
    procedure_coverage: ProcedureCoverage | None = None
```

`procedural_unit_keys`는 임상 단계명을 추론하지 않는다. 원문에 명시된 번호·bullet·단계 표제와 완전한 문장/표 행의 안정적 signature만 나타낸다. 이를 “임상적으로 모든 단계가 완전하다”는 자동 보증으로 사용하지 않고, 구조적 누락 여부와 Q002 평가를 위한 관찰값으로 사용한다.

## 9. 모듈 소유권

| 모듈 | 구현 승인 후 책임 |
|---|---|
| `mvp/query.py` | 현재 질문 분류와 후속 retrieval query 유지. `procedure` 외 새 의료 intent 사전은 추가하지 않음 |
| `mvp/retrieval.py` | 승인된 BM25, temporal ranking, semantic 후보, RRF와 기존 관련성 admission 유지. BM25 함수·점수 계약은 수정하지 않음 |
| `mvp/context.py` | procedure multi-seed 문맥 확장, parent/neighbor 원자 그룹, 중복 제거, 분기 보존, prompt 전 source order의 단일 소유자 |
| `mvp/evidence.py` | 구조적 procedure coverage와 LLM 전/예산 후 충분성 판단의 단일 소유자 |
| `mvp/ai.py` | evidence group 단위 token budget, Groq 최종 생성, exact extractive validation |
| `mvp/search_trace.py` | 기존 BM25/dense/RRF/rerank 필드 유지 + bundle/coverage/duplicate/order/budget 사유 추가 |
| `mvp/answer_ui.py`, `mvp/ui.py` | 문장 citation에서 문서명·페이지/위치·section·chunk ID·관련 원문을 추적 가능하게 표시 |
| `mvp/repository.py`, `mvp/cloud.py` | 현재 권한, revision, local/cloud 검색 adapter와 cache lifecycle 유지 |

새 orchestration service나 vector database abstraction은 만들지 않는다. 현재 두 backend가 이미 동일한 검색 계약을 제공하므로 pass-through 계층을 추가할 이유가 없다.

## 10. Hybrid Retrieval 상세

### Semantic 후보

- 현재 normalized 384차원 embedding과 inner product/cosine 계약을 유지한다.
- 로컬 FAISS와 운영 pgvector가 각각 최대 40개의 dense 후보를 반환한다.
- 권한이 허용되고 active/ready인 문서만 대상이 된다.
- 제목-only chunk가 semantic 후보로 들어올 수는 있지만 기존 substantive-body admission을 통과하지 못해 answer seed가 되지 않는다.
- semantic similarity는 정답 확률로 취급하지 않는다.

### BM25 후보

- 현재 승인된 `BM25Index.scores(plan.expanded)`와 `rank_bm25_candidates(plan.original, ...)`를 그대로 호출한다.
- 양수 후보만 lexical ranking에 포함한다.
- raw score, requested phase와 temporal tier를 그대로 trace한다.

### 결합

- 각 경로 Top 40을 chunk ID로 RRF 결합한다.
- 기존 `k=60`을 첫 RAG baseline으로 유지한다.
- dense와 BM25 점수는 척도가 다르므로 직접 더하지 않는다.
- 한 경로에만 나온 후보도 보존한다.
- 동일 chunk는 한 번만 만들고 양쪽 rank/원점수는 trace에서 분리한다.

RRF 계수·후보 수 변경은 Q002 결과가 부족하더라도 즉시 시행하지 않는다. 먼저 semantic recall, RRF recall, rerank coverage, context coverage 중 어느 단계에서 gold가 빠지는지 trace로 확인한 뒤 별도 승인한다.

## 11. Retrieval 이후 reranking

기존 `rerank()`의 다음 admission 조건을 유지한다.

- substantive body가 있어야 한다.
- 질문 주제와 compatible해야 한다.
- lexical 근거가 없으면 더 높은 dense minimum을 충족해야 한다.
- 제목과 section만으로 임상 답변 근거를 만들지 않는다.

일반 fact/caution/material 질문은 현재 최대 seed와 순서를 유지한다. `procedure` 질문만 다음 선택 정책을 추가한다.

1. 기존 rerank 점수 1위의 substantive 관련 후보를 첫 seed로 둔다.
2. 남은 후보 중 exact duplicate는 제거한다.
3. 같은 parent의 연속 조각, 같은 section의 인접 절차 조각, 다른 명시적 원문 단위가 추가 coverage를 만들면 seed quota 안에서 보존한다.
4. 서로 다른 문서·성인/소아·조건 분기는 하나로 합치지 않고 각각의 source group으로 유지한다.
5. coverage가 같으면 기존 rerank 순서를 유지한다.
6. 관련성 admission을 통과하지 않은 chunk를 단계 수를 채우기 위해 승격하지 않는다.

이 정책은 cross-encoder 점수나 의료 단계 사전을 만들지 않는다. “여러 단계”를 얻기 위해 관련성이 낮은 chunk를 강제로 넣지 않고, 이미 hybrid 후보로 회수된 관련 chunk 사이에서 구조적 다양성을 보존한다.

## 12. Q002 multi-chunk 근거 묶음

### 대표 실패 정의

현재 Q002 BM25는 00013의 처방 확인을 1위로 찾지만 Top 5에 서로 다른 substantive 절차 단계가 충분히 모이지 않는다. 00019, 00023, 00028 등 보완 근거는 더 아래 순위 또는 다른 문맥에 있다. RAG 성공은 하나의 chunk를 정답으로 선언하는 것이 아니다.

### 근거 묶음 생성

1. Hybrid Top 40에서 Q002와 관련된 substantive candidates를 유지한다.
2. reranker가 procedure seed를 여러 개 선택한다.
3. 각 seed의 모든 같은 `parent_id` 조각을 원자 그룹으로 묶는다.
4. `previous_chunk_id`/`next_chunk_id`, 같은 document/section, PDF 페이지 거리 조건을 만족하는 이웃을 제한적으로 확장한다.
5. `(document_id, page, location, normalized_text)` signature로 exact duplicate를 제거한다.
6. 한 문서 안의 최종 prompt hits는 `chunk.index` 오름차순으로 정렬한다. retrieval rank는 버리지 않고 trace에 별도로 둔다.
7. 서로 다른 대상군이나 조건 분기는 section/source group을 유지한다. 분기가 불명확하고 모두 token budget에 들어오지 않으면 임의로 합치지 않고 clarification 또는 abstention을 선택한다.

### Q002 평가용 gold 구조

구현 전에 원본 지침과 기존 chunk를 사람이 검수해 별도 평가 파일에 다음을 만든다.

- Q002에 답하기 위해 필요한 절차 단위 목록
- 각 단위를 뒷받침하는 허용 chunk ID 집합
- 필수/보완 구분
- 성인/소아 또는 조건별 분기
- 원문상 선후 순서

자동 코드에 `00013`, `00019` 같은 ID나 진정간호 단계명을 하드코딩하지 않는다. ID는 평가 fixture에만 존재한다.

### Q002 성공 기준

- Hybrid Top 40의 필수 단계 recall 100%
- 최종 evidence bundle의 필수 단계 recall 100%
- 최소 2개 이상의 서로 다른 substantive chunk가 근거에 포함됨
- 같은 문장을 반복한 chunk는 한 번만 포함됨
- 동일 분기 안에서 source order 역전 0건
- semantic block의 일부가 잘렸으면 answerable로 처리하지 않음
- 최종 답변의 모든 단계가 해당 원문 sentence/row와 chunk citation을 가짐
- LLM이 근거에 없는 연결 단계·용량·조건을 추가한 경우 0건이어야 함

gold 자체가 “전체 임상 절차”를 대표하는지는 사람의 검수 영역이다. 자동 coverage는 gold와 구조적 완전성만 검증한다.

## 13. 근거 충분성 및 답변 거절

근거 충분성은 `mvp/evidence.py`가 소유하고 Groq보다 먼저 실행한다.

### 공통 hard gate

- 질문이 허용 도메인이고 clarification이 없어야 한다.
- 권한 있는 active/ready 문서의 substantive seed가 있어야 한다.
- 요청 주제/entity/aspect가 실제 text 또는 허용된 section 문맥에 있어야 한다.
- 요청한 문서 수와 명시 문서 범위를 만족해야 한다.
- 선택된 parent semantic block이 완전해야 한다.
- title-only, 표 머리글-only와 similarity만 높은 후보는 단독 근거가 될 수 없다.

### procedure 추가 gate

- procedure action을 포함한 substantive evidence unit이 있어야 한다.
- 선택한 parent group을 prompt budget이 부분적으로 자르면 불충분하다.
- 원문이 여러 조각으로 이어지는 것으로 확인됐는데 후속 조각이 없으면 불충분하다.
- 다단계로 분류된 질문은 gold가 있는 평가에서는 필수 coverage를 충족해야 한다.
- 운영 일반 질문에서는 구조적으로 여러 단위가 실제로 존재할 때만 여러 단위를 요구한다. 한 문장에 완결된 단순 절차를 무조건 거절하지 않는다.

자동 규칙은 임상적 완전성을 추론할 수 없다. 구조적 완전성을 확인할 수 없으면 보수적으로 abstain하고 원문 검색 결과를 표시한다.

### Q006와 답 없음

Q006은 `out_of_scope`로 embedding과 검색 전에 차단하며 Groq를 호출하지 않는다. 반환 문구는 정확히 다음과 같다.

> 등록된 지침서에서 확인할 수 없습니다.

병원 도메인처럼 보이지만 등록 지침에 답이 없는 질문도 evidence gate에서 같은 문구로 끝난다. semantic similarity만으로는 answerable이 될 수 없다. DB/API/embedding/Groq 장애는 근거 없음으로 위장하지 않고 기존 안전한 오류 코드로 표시한다.

## 14. Prompt 예산과 Groq 경계

- Groq는 최종 답변 생성에만 사용한다.
- 현재 `GROQ_REQUEST_TOKEN_BUDGET=3500`, completion 768과 1회 호출 원칙을 첫 baseline으로 유지한다.
- 문서 전체, 검색되지 않은 chunk, embedding, BM25 postings와 대화의 이전 AI 답변을 전송하지 않는다.
- evidence assessment를 통과한 최소 chunk만 JSON으로 보낸다.
- 단순 질문은 가장 작은 충분 근거를, procedure 질문은 필수 source group을 보낸다.
- budget은 hit 한 개가 아니라 semantic block/source group 단위로 적용한다.
- 필수 그룹이 전부 들어가지 않으면 일부 단계로 답하지 않고 LLM 호출 전에 abstain한다.
- Groq가 `answerable:false`를 반환하거나 exact validation을 통과하지 못하면 고정 근거 부족 결과로 처리한다.

절차 prompt는 retrieval rank가 아니라 문서·분기·`chunk.index` 순서로 근거를 배열한다. 이는 생성 모델이 단계 순서를 재구성하는 부담과 순서 오류 가능성을 줄인다.

## 15. Citation 설계

각 statement는 한 개 이상의 다음 reference로 검증된다.

```python
# 기존 public answer schema 유지
Evidence(chunk_id="...", quote="원문 전체 문장 또는 표 행")
```

`chunk_id`로 서버 측 `Chunk`를 찾고 다음 값을 표시한다.

- `document_name`
- PDF `page` 또는 비PDF `location`
- `section`
- `chunk.id`
- statement를 지지하는 exact `quote`

불변식은 다음과 같다.

1. quote는 해당 chunk 원문의 정규화된 부분 문자열이어야 한다.
2. 답변의 각 문장은 완전한 원문 문장 또는 표 행과 일치해야 한다.
3. 숫자, 단위, 조건, 부정과 행위는 quote에 존재해야 한다.
4. 하나의 statement가 여러 chunk를 필요로 하면 각 chunk를 별도 evidence로 연결한다.
5. 같은 페이지의 여러 chunk를 UI에서 묶더라도 내부 chunk ID 목록과 quote 연결을 잃지 않는다.
6. 서로 다른 페이지나 분기를 하나의 가짜 citation 범위로 합치지 않는다.
7. context-only 제목은 문맥 표시에 사용할 수 있지만 procedure 답변 statement의 단독 citation이 될 수 없다.

UI는 기본 출처 카드에 문서·페이지·section을 보여주고, 상세 보기에서 chunk ID와 사용된 관련 원문을 확인할 수 있게 한다. 현재 page/location 값은 chunk에서 그대로 가져오며 생성 모델이 만들지 않는다.

## 16. 후속 질문 문맥

현재 동작을 유지한다.

- 이전 사용자 질문은 새 retrieval query에서 주제를 식별하는 데만 사용한다.
- 이전 AI 답변은 검색 근거나 새 prompt evidence로 사용하지 않는다.
- 이전 답변에서 실제 인용된 문서 ID만 후속 문서 범위 힌트로 사용할 수 있다.
- 매 질문마다 현재 권한과 corpus revision을 다시 확인한다.
- 새 질문에 다른 명시 entity가 나오면 이전 문맥을 이어 붙이지 않는다.
- “그럼 퇴실 기준은?”처럼 aspect가 바뀌면 최신 aspect를 우선한다.
- 어느 문서/대상을 가리키는지 결정할 수 없으면 검색·LLM 전에 clarification을 반환한다.

후속 질문의 answer 역시 새 검색에서 회수·검증된 chunk만 인용해야 한다.

## 17. Trace와 관측 가능성

현재 trace 필드를 삭제하거나 의미를 바꾸지 않는다.

- indexed chunk IDs
- BM25 raw score와 Top 10
- requested temporal phase와 temporal tier
- semantic similarity와 Top 10
- RRF fusion score와 Top 10
- rerank score, 선택/제외 사유
- 최종 hits와 citation assessment

다음 필드를 추가한다.

- `procedure_seed_ids`
- seed별 `parent_id`, section, original index
- neighbor/parent expansion 사유
- duplicate 제거 ID와 signature 수
- 최종 prompt source order
- `ProcedureCoverage` 요약
- LLM 전/예산 후 충분성 reason
- prompt에서 원자 그룹이 제외된 이유
- `llm_called`, validation과 abstention reason

trace는 현재처럼 관리자 요청에서만 만들고 저장·외부 전송하지 않는다. 환자 정보, API key와 LLM 원문 응답을 기록하지 않는다.

## 18. 상태, lifecycle과 실패 처리

- embedding 모델은 현재 Streamlit resource cache에서 공유하되 문서·질문·직원 정보는 전역 cache에 넣지 않는다.
- multi-chunk bundle과 coverage는 요청별 immutable 값이며 DB에 저장하지 않는다.
- 로컬 repository는 기존 corpus revision snapshot과 search lock을 유지한다. LLM 호출 중 lock을 잡지 않는다.
- 운영은 직원 JWT/RLS와 검색 직전 active 문서 범위를 유지한다.
- embedding 모델이 바뀌는 미래 실험은 모델 ID와 vector dimension을 index metadata로 검증하고 전체 재색인 후 원자적으로 전환한다.
- retrieval backend 실패 시 BM25-only 또는 semantic-only로 조용히 downgrade하지 않는다. 평가·안전 계약이 달라지므로 오류를 표시한다.
- prompt budget 부족은 일부 절차 생성이 아니라 명시적 abstention이다.

RAG retrieval/context 정책이 구현되면 대화 cache 무효화를 위해 검색 또는 AI 버전을 증가시킨다. `CHUNK_VERSION`과 현재 embedding model은 1차 구현에서 유지한다.

## 19. 구현 예정 변경 범위

사용자 승인 후 예상되는 최소 변경은 다음과 같다.

| 파일 | 예상 변경 |
|---|---|
| `mvp/context.py` | procedure용 multi-seed group 확장, dedup, 분기와 source order 보존 |
| `mvp/evidence.py` | `ProcedureCoverage`, procedure 구조적 충분성 reason |
| `mvp/ai.py` | 원자 evidence group 단위 prompt budget과 기존 exact validation 연결 |
| `mvp/search_trace.py` | bundle/coverage/order/budget 진단 필드 추가 |
| `mvp/answer_ui.py`, 필요 시 `mvp/ui.py` | chunk ID·section·관련 quote 추적성 표시 보강 |
| 관련 테스트 | Q002 multi-chunk, Q006 abstention, citation, local/cloud 동등성 회귀 |
| RAG 평가 도구 | retrieval부터 answer validation까지 단계별 결과와 review.html 생성 |

`mvp/retrieval.py`의 승인된 BM25 관련 함수, tokenizer, temporal policy와 RRF baseline은 변경하지 않는다. 구현상 이 경계를 넘어야 한다는 증거가 나오면 중단하고 별도 설계를 승인받는다.

## 20. 검증 계획

### 단위 테스트

- exact duplicate가 한 번만 남는다.
- 같은 parent의 모든 조각이 함께 포함되거나 전체 그룹이 불충분으로 처리된다.
- 이웃 확장이 document/section/page/link 경계를 넘지 않는다.
- procedure bundle은 같은 분기 안에서 chunk index 순서를 유지한다.
- 일반 fact 질문의 hit 순서는 기존과 동일하다.
- title-only와 header-only가 절차 coverage로 계산되지 않는다.
- token budget이 parent group 일부만 허용하면 LLM을 호출하지 않는다.
- answer의 모든 sentence/quote/chunk citation이 기존 검증을 통과해야 한다.

### 통합 테스트

- 로컬 FAISS와 운영 pgvector adapter가 같은 fixture에서 동일한 hybrid 후보 계약을 제공한다.
- 승인 BM25 Q001~Q006 순위, score, phase/tier가 변경 전과 같다.
- Q006은 embedding/LLM 호출 없이 고정 문구를 반환한다.
- 병원 도메인 내 답 없음 질문도 높은 dense similarity만으로 LLM을 호출하지 않는다.
- 후속 질문은 이전 사용자 질문과 cited document scope만 사용한다.
- corpus revision이나 권한이 바뀌면 이전 evidence를 재사용하지 않는다.
- DB, embedding 또는 Groq 실패가 근거 없음으로 오표시되지 않는다.

### Q002 단계별 평가

다음 funnel을 각각 저장한다.

1. BM25 Top 40 stage recall
2. semantic Top 40 stage recall
3. RRF Top 40 stage recall
4. rerank seed stage recall
5. context-expanded evidence stage recall
6. prompt-budget 이후 stage recall
7. 최종 답변 stage coverage와 citation precision

어느 단계에서 빠졌는지 확인하지 않고 embedding, RRF 계수, threshold 또는 reranker를 동시에 바꾸지 않는다.

### 정량 합격 기준

- 승인된 BM25 지표와 Q006 양수 후보 0개 유지
- Q002 Hybrid Top 40 및 최종 evidence 필수 stage recall 100%
- Q002 evidence에 서로 다른 substantive chunk 2개 이상
- Q002 동일 분기 source order 역전 0건
- evidence exact duplicate 0건
- 불완전 semantic block을 포함한 answerable 결과 0건
- 답변 statement citation coverage 100%
- citation quote 정확 일치 100%
- 근거 없는 숫자·단위·조건·행동 추가 0건
- Q006과 답 없음 fixture의 LLM 호출 0회
- 전체 테스트와 정적 검사 통과

### 수동 검수 산출물

`artifacts/YYYY-MM-DD_rag-evaluation/`에 기존 BM25 artifacts를 덮어쓰지 않고 다음을 생성한다.

- 단계별 retrieval JSON/CSV
- Q002 gold stage와 회수 mapping
- prompt에 실제 포함된 최소 evidence
- 생성 없이도 검수 가능한 retrieval/evidence `review.html`
- 별도 명시 승인 시에만 실행한 Groq 답변과 citation 검수 결과
- latency, token, abstention 및 failure reason 요약

사람이 검수해야 하는 Q002 단계 충분성, 분기 해석과 citation은 review.html에서 원문과 나란히 표시한다.

## 21. 구현 순서

사용자 승인 후에도 한 번에 전부 바꾸지 않고 다음 의존 순서로 진행한다.

1. Q002 gold stage/분기/허용 chunk fixture와 답 없음 병원 질문 fixture를 작성한다.
2. 현재 embedding, semantic, BM25, RRF 단계별 baseline을 저장한다. LLM은 호출하지 않는다.
3. `mvp/context.py`에 procedure bundle 확장·dedup·source order를 구현한다.
4. `mvp/evidence.py`에 구조적 coverage와 충분성 gate를 구현한다.
5. search trace에 각 funnel 단계와 제외 이유를 연결한다.
6. prompt budget을 semantic block/source group 원자 단위로 바꾼다.
7. citation UI의 chunk/section/quote 추적성을 보강한다.
8. 집중 테스트, 전체 테스트와 정적 검사를 실행한다.
9. LLM 없이 retrieval/evidence review.html을 생성하고 Q002와 Q006을 수동 검수한다.
10. 사용자가 retrieval/evidence 결과를 승인한 뒤에만 제한된 Groq 최종 생성 평가를 별도 실행한다.
11. 최종 RAG 결과 문서를 작성하고 다시 사용자 승인을 기다린다.

embedding 후보 교체와 cross-encoder는 이 순서에 포함하지 않는다.

## 22. Rollback

1차 권고안은 chunk, embedding과 DB schema를 바꾸지 않는다.

1. procedure bundle/context 정책을 기존 `expand_context()` 동작으로 되돌린다.
2. `ProcedureCoverage`와 추가 evidence gate를 제거한다.
3. prompt group 예산을 기존 hit 단위 선택으로 되돌린다.
4. trace/UI 추가 필드와 해당 버전 증가를 되돌린다.
5. 새 RAG artifacts는 BM25 artifacts와 분리해 보존한다.

승인된 BM25 방식 A, 시점 tier, 현재 model/vector, 원본 문서와 기존 평가 산출물은 rollback 대상이 아니다.

## 23. 위험과 열린 사항

- 현재 자료는 1개 지침서와 6개 BM25 질문 중심이므로 semantic 및 RAG 일반화를 주장할 수 없다.
- Q002 gold stage와 성인/소아 분기는 사람의 원문 검수가 필요하다. 자동 규칙이 임상적 완전성을 정의해서는 안 된다.
- parent/section metadata가 없는 구형 DB 문서는 구조적 완전성 판단이 더 보수적으로 동작할 수 있다.
- 구조적 다양성 선택이 낮은 관련성 chunk를 끌어올릴 위험이 있다. 기존 relevance admission을 먼저 적용하고 trace와 수동 label로 확인한다.
- current MiniLM이 한국어 의료 용어 표현 변형을 충분히 회수하지 못할 수 있다. 이는 RAG baseline 평가 뒤 별도 embedding 비교로 다룬다.
- Streamlit Community Cloud의 실제 CPU/RAM/cold-start 한도는 배포 환경에서 측정해야 한다. 이 때문에 1차에는 cross-encoder를 넣지 않는다.
- `docs/PRD.md` 부재는 이번 설계의 요구사항 출처 공백이다. 문서를 추측해 만들지 않고 사용자와 별도로 정리한다.

## 24. 최종 승인 요청 상태

최종 권고는 C안이다. 현재 multilingual MiniLM + 로컬 FAISS/운영 pgvector semantic search, 승인된 SCHAT BM25, RRF와 기존 규칙 reranker를 유지하고, Q002를 위해 multi-seed evidence bundle, 인접/parent 확장, dedup, 분기 및 원문 순서 보존, LLM 전·예산 후 충분성 gate를 보강한다.

이 문서 작성으로 작업을 멈춘다. RAG 코드, embedding 설치, dependency, DB, 기존 BM25 코드·산출물과 원본 문서는 수정하지 않았고 LLM도 호출하지 않았다. 다음 작업은 사용자의 명시적 구현 승인 후에만 진행한다.

# BM25 시점 기반 안정적 reranking 계획

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 결과: `docs/bm25/04_TITLE_CHUNK_IMPROVEMENT_RESULT.md`
- 대상 질문: Q003 “진정 전 준비사항은?”
- 단계: 분석·설계 완료, 구현 승인 대기
- 권고안: A. 기존 BM25 후보 집합 안에서 시점 tier를 이용한 안정적 재정렬

## 1. 목적과 승인 경계

Q003은 관련 준비사항을 2~4위에서 이미 회수하므로 후보 회수 실패가 아니다. 현재 1위 00026은 진정 후 환자 이동과 검사 중 모니터링을 포함하고 있어 질문의 “진정 전”과 시점이 맞지 않는다. 이 계획은 BM25 점수와 후보를 삭제하지 않고, 명시적인 시점 일치 여부만으로 기존 후보 순서를 안정적으로 보정하는 구조를 정한다.

제목-only 방식 A 구현은 승인된 기준선으로 유지한다. 사용자 승인 전에는 다음 작업을 하지 않는다.

- `mvp/retrieval.py`, `mvp/cloud.py`, 평가 도구 또는 테스트 수정
- Q002 multi-chunk 회수 개선
- tokenizer 또는 query expansion 수정
- 제목-only 정책 변경이나 rollback
- rank-bm25 변경 또는 교체
- RAG 구현, RAG reranker 수정 또는 LLM 호출
- 기존 원본, chunk, 인덱스와 평가 산출물 수정

## 2. 현재 근거와 문제 정의

제목-only 개선 후 SCHAT의 Q003 순서는 다음과 같다.

| 현재 순위 | chunk | BM25 점수 | 기존 판정 | 시점 근거 |
|---:|---|---:|---|---|
| 1 | 00026 | 0.7056 | irrelevant | 검사 중, 진정 치료 진행 시, 진정 후 이동·모니터링 맥락 |
| 2 | 00013 | 0.3695 | relevant | 시점 표현이 없는 처방 확인 |
| 3 | 00016 | 0.3573 | relevant | section에 진정 치료 전, 본문은 설명·동의 |
| 4 | 00015 | 0.3530 | relevant | 진정 치료 전 평가·계획 |
| 5 | 00004 | 0.3505 | irrelevant | 시점 중립 규정 목록 |

00019와 00020도 Top 10에 있으며 진정 전 평가·기록의 일부를 포함한다. 따라서 hard filter나 재검색보다 기존 양수 후보 안에서 명시적 “전” 일치 후보를 먼저 보이는 것이 문제에 비례한 해결이다.

현재 호출 흐름은 다음과 같다.

1. `BM25Index.scores(plan.expanded)`가 chunk 위치와 정렬된 원점수 배열을 만든다.
2. 로컬 `mvp/retrieval.py`와 클라우드 `mvp/cloud.py`가 원점수 내림차순의 양수 Top 40을 lexical 후보로 만든다.
3. lexical 순위와 dense 순위를 RRF로 결합한다.
4. `rerank()`와 `expand_context()`가 최종 seed와 문맥을 선택한다.
5. `tools/bm25_evaluate.py`는 SCHAT 원점수를 직접 정렬해 Top-k를 만든다.
6. `mvp/search_trace.py`도 원점수 순서로 BM25 Top 10을 기록한다.

시점 순서를 한 경로에만 적용하면 운영 검색, 평가와 진단 결과가 서로 달라진다. 하나의 ranking helper가 로컬·클라우드·평가의 순서를 소유하고 진단에는 실제 적용 순서를 전달해야 한다.

## 3. 목표와 비목표

### 목표

- Q003에서 정식 relevant인 시점 일치 본문을 1위로 올린다.
- BM25 양수 후보 집합, 원점수와 chunk ID를 보존한다.
- 각 시점 tier 내부에서는 기존 BM25 순서를 그대로 유지한다.
- 시점이 하나로 명확하지 않은 질문은 기존 순서와 완전히 같게 둔다.
- Q001·Q002·Q004·Q005의 순위를 변경하지 않는다.
- Q006의 전 점수 0과 안정적 동률 순서를 유지한다.
- 제목-only 개선 결과와 Hit@3·5·10을 유지한다.

### 비목표

- “준비사항”의 의미 확장 또는 동의어 추가
- Q002의 절차 단계 다양성 개선
- 문장을 생성하거나 의료 절차를 추론하는 규칙
- 시점이 생략된 본문을 특정 단계로 추정
- dense 검색 또는 최종 RAG 답변 품질 개선

## 4. 대안 비교

| 기준 | A. 시점 tier 안정 재정렬 | B. BM25 점수 보너스·감점 | C. RAG reranking에서만 반영 |
|---|---|---|---|
| Q003 Hit@1 가능성 | 높음. 명시적 before 후보를 최상위 tier로 이동 | 높음이나 계수에 따라 실패 가능 | 최종 답변은 개선 가능하지만 BM25 Hit@1은 불변 |
| Hit@3/5/10 영향 | 낮음. 기존 Top 40 후보를 삭제하지 않고 tier 안의 원순서 유지 | 중간. 점수 간격과 계수에 따라 후보 경계가 바뀔 수 있음 | BM25 지표는 불변, 최종 RAG 순위는 별도 영향 |
| Q001/Q002/Q004/Q005 | 시점이 하나로 검출되지 않으므로 순서 불변을 구조적으로 보장 | 조건부 적용이면 낮지만 점수 보정 분기 검증 필요 | BM25는 불변, RAG 결과에는 영향 가능 |
| Q006 음성 대조 | 시점 요청이 없고 모든 점수가 0이므로 점수·순서 불변 | 0점에 보너스를 더하면 false-positive 위험; 별도 차단 필요 | BM25는 불변이나 RAG 후보가 있다면 별도 방어 필요 |
| 구현 복잡도 | 중간. 공통 ranking helper와 세 호출 경로 정합성 필요 | 중간. 시점 판정 외 계수 설계·튜닝 필요 | 중간~높음. RAG 경로와 평가 체계를 함께 설계해야 함 |
| 의료 규칙 하드코딩 위험 | 낮음~중간. 명시적 문법 표지만 제한적으로 사용 | 중간~높음. 문법 규칙에 문서별 계수까지 추가됨 | 중간. RAG prompt/reranker에 암묵적 규칙이 숨을 수 있음 |
| 원점수 설명 가능성 | 높음. 원점수와 tier를 별도로 표시 | 낮음. 보정 점수가 원 BM25 의미와 섞임 | BM25는 명확하나 최종 순위 원인 추적이 복잡 |
| rollback | helper 호출과 SEARCH_VERSION 12를 되돌림 | 계수·보정 코드와 SEARCH_VERSION을 되돌림 | RAG rerank 변경과 관련 평가를 되돌림 |
| 현재 단계 적합성 | 가장 높음 | 예비 대안 | 부적합; RAG 구현 금지 및 BM25 실패 미해결 |

## 5. 대안 A: 시점 tier 안정 재정렬

### 정렬 정책

질문에서 단 하나의 명시적 시점이 검출될 때만, 기존 BM25 Top 40 후보를 다음 순서로 재정렬한다.

1. `match`: 질문 시점이 후보의 명시적 시점 집합에 포함됨
2. `neutral`: 후보에 명시적 시점 표현이 없음
3. `mismatch`: 후보에 시점 표현이 있지만 질문 시점은 포함하지 않음
4. `non_positive`: BM25 점수가 0 이하인 평가 표시용 위치

각 tier 내부 순서는 입력받은 기존 BM25 순서를 그대로 유지한다. Python의 안정 정렬에 우연히 의존하기보다 원래 순위 위치를 두 번째 정렬 키로 명시한다.

후보에 `before`, `during`, `after`가 함께 있으면 질문 시점을 포함하는 한 `match`다. 예를 들어 00019처럼 “전·중·후”를 모두 다루는 본문은 before 질문에서 제외하지 않는다. 질문에서 서로 다른 시점이 둘 이상 검출되거나 시점이 없으면 정책을 비활성화하고 기존 순서를 그대로 반환한다.

### 후보 집합 보존

먼저 현재와 같은 원점수 내림차순 Top 40을 만든 후 그 집합 내부에서만 tier를 적용한다. 시점 정책이 BM25 Top 40 밖의 문서를 새로 끌어오거나 기존 후보를 삭제하지 않도록 한다.

- 로컬·클라우드 후보 ID 집합은 적용 전후 동일하다.
- `BM25Index.scores()`의 값과 위치 계약은 변하지 않는다.
- RRF에는 같은 lexical ID 집합이 다른 순서로 전달된다.
- evaluator는 운영과 같은 Top 40을 먼저 재정렬하고 그중 요청된 Top-k를 표시한다.
- 0점은 시점이 있더라도 양수 후보로 승격하지 않는다.

## 6. 호출자 관점 사용 예시

다음 코드는 미구현 의사코드다.

### 로컬 검색

```python
scores = library._bm25.scores(plan.expanded)
ranking = rank_bm25_candidates(
    question=plan.original,
    chunks=allowed_chunks,
    scores=scores,
    limit=40,
)
lexical = [position for position in ranking.positions if scores[position] > 0]
fusion = rrf(dense, lexical)
```

### 클라우드 검색

```python
scores = bm25.scores(plan.expanded)
ranking = rank_bm25_candidates(plan.original, chunks, scores, limit=40)
lexical = [position for position in ranking.positions if scores[position] > 0]
fusion = rrf(dense_ids, [chunks[position].id for position in lexical])
```

### 오프라인 BM25 평가

```python
schat_ranking = rank_bm25_candidates(
    plan.original,
    chunks,
    schat_scores,
    limit=max(40, top_k),
)
schat_rows = ranked_rows(..., positions=schat_ranking.positions[:top_k])

# baseline에는 SCHAT의 시점 정책을 적용하지 않는다.
baseline_rows = ranked_rows(..., positions=raw_rank_bm25_positions[:top_k])
```

호출자는 시점 패턴이나 tier 계산을 알지 않는다. 질문, 동일 순서의 chunks와 scores, 기존 후보 제한만 전달한다.

## 7. 데이터와 인터페이스 설계

다음은 미구현 타입·서명이다.

```python
from dataclasses import dataclass
from typing import Literal, Sequence

TemporalPhase = Literal["before", "during", "after"]
TemporalTier = Literal["match", "neutral", "mismatch", "non_positive", "inactive"]

@dataclass(frozen=True)
class BM25CandidateRanking:
    positions: tuple[int, ...]
    requested_phase: TemporalPhase | None
    tiers: tuple[TemporalTier, ...]  # positions와 같은 순서

def rank_bm25_candidates(
    question: str,
    chunks: Sequence[Chunk],
    scores: Sequence[float],
    *,
    limit: int = 40,
) -> BM25CandidateRanking: ...
```

`positions`와 `tiers`는 같은 길이이며, `tiers[i]`는 `positions[i]` 후보의 적용 결과다. 이 최소 결과 객체는 검색 호출자에는 순서를, 평가와 진단에는 적용 이유를 제공한다. phase parser나 패턴 목록은 공개하지 않고 `mvp/retrieval.py` 내부 순수 함수로 둔다.

입력 경계는 다음과 같다.

- `len(chunks) != len(scores)`이면 프로그래밍 오류로 `ValueError`를 낸다.
- `limit < 1`이면 `ValueError`를 낸다.
- 빈 입력은 빈 positions와 tiers를 반환한다.
- NaN 또는 무한대 점수는 기존 검색 계약에 없으므로 테스트에서 거부하거나 명시적으로 후순위 처리한다. 구현 전에 현재 호출 경로와 호환되는 한 가지 동작으로 고정한다.

## 8. 시점 검출 경계

### 질문 시점

`plan.expanded`가 아니라 현재 사용자 질문인 `plan.original`만 읽는다. 따라서 query expansion을 수정하거나 확장 문자열에 섞인 표현을 시점 요청으로 오인하지 않는다.

명시적으로 사건 뒤에 붙는 한국어 시점 표지만 제한적으로 정규화한다.

- before: `전`, `이전`, `사전`
- during: `중`, `동안`
- after: `후`, `이후`
- `전·중·후`, `전/중/후`처럼 구분자로 연결된 복수 표현

한 글자 자체를 tokenizer 토큰으로 추가하지 않는다. NFKC와 공백·구분자 정리는 phase parser 내부 비교에만 사용한다. `전 직원`, `중환자`, `후배`, `오전`처럼 단어 일부이거나 사건 뒤의 시점 표지가 아닌 표현은 검출하지 않는다. `시`, `직전`, `직후`, `회복`, `준비`, 번호 순서만으로는 시점을 추론하지 않는다.

### 후보 시점

후보의 `chunk.text`와 `chunk.section`에서 같은 명시적 문법 표지를 찾고 `frozenset` 성격의 phase 집합을 파생한다. 저장 필드나 DB schema를 추가하지 않는다.

- 00015·00016의 “진정 치료 전”은 `before`다.
- 00019의 “진정 전·중·후”는 세 phase를 모두 가진다.
- 00026의 “검사 중”은 `during`이다.
- 시점 표현이 없는 00013은 `neutral`이다.

병원별 절차명, 약물명, 단계 번호 또는 “준비사항=진정 전” 같은 의미 규칙은 넣지 않는다. 명시된 문법 표지만 사용해 의료 규칙 하드코딩 범위를 제한한다.

## 9. 모듈 소유권과 흐름

| 파일 | 승인 후 책임과 변경 |
|---|---|
| `mvp/retrieval.py` | phase 파생, tier 계산, 안정적 Top 40 재정렬과 `BM25CandidateRanking`의 단일 소유자. 로컬 lexical 순서에 적용 |
| `mvp/cloud.py` | 공통 helper를 호출해 클라우드 lexical 순서를 RRF에 전달. 시점 규칙은 구현하지 않음 |
| `mvp/search_trace.py` | 원점수, 실제 적용 순서, requested phase와 tier를 진단에 기록하도록 기존 호출을 수용 |
| `tools/bm25_evaluate.py` | SCHAT에만 공통 helper 순서를 적용하고 tier 필드를 CSV·JSON·HTML에 표시. rank-bm25는 원순서 유지 |
| `mvp/library.py` | 검색 순서 정책 변경을 구분하기 위해 `SEARCH_VERSION` 11→12. `CHUNK_VERSION` 유지 |
| `tests/test_bm25_evaluation.py` | Q003 및 baseline 분리 평가 회귀 테스트 |
| 검색 관련 기존 테스트 또는 집중 테스트 | 로컬·클라우드·trace의 동일 순서, 후보 집합과 RRF 입력 계약 검증 |
| `docs/bm25/06_TEMPORAL_RERANK_RESULT.md` | 구현 후 전후 결과, Q003과 전체 지표, 승인 판단 기록 |
| 새 artifacts 디렉터리 | CSV·JSON·review.html 생성. 기존 산출물은 보존 |

대표 흐름은 `plan.original`에서 요청 phase를 파생하고, 기존 BM25 원점수 Top 40을 만든 뒤, 후보별 phase 집합으로 tier를 정한다. 안정적 tier 순서를 local/cloud RRF와 evaluator에 공통 전달하고, trace에는 원점수와 적용 tier를 함께 남긴다. 영속 상태와 동시 쓰기는 추가되지 않는다.

## 10. Q003 예상 동작

현재 Top 10을 대상으로 A를 적용하면 다음 방향을 예상한다. 정확한 순위는 구현 후 동일 평가셋으로 확정한다.

| 후보 | 예상 tier | 이유 | 예상 영향 |
|---|---|---|---|
| 00016 | match | section의 “진정 치료 전” | 기존 3위에서 최상위권 |
| 00015 | match | 본문·section의 “진정 치료 전” | 기존 4위에서 최상위권 |
| 00019·00020 | match | “전·중·후” 또는 진정 전 내용 포함 | 원 BM25 순서에 따라 match tier 후속 |
| 00013 | neutral | 명시적 시점 없음 | match 후보 뒤, mismatch 후보 앞 |
| 00026 | mismatch | “검사 중”이 명시되고 before는 없음 | match·neutral 뒤로 이동 |

현재 원점수 순서를 match tier 내부에 적용하면 00016이 00015보다 먼저 온다. 두 chunk 모두 기존 Q003 정식 label이 `relevant`이므로 첫 관련 결과가 1위가 될 가능성이 높다. 특정 ID를 억지로 1위로 고정하지 않고 “정식 relevant이며 before가 명시된 substantive 본문”을 성공 조건으로 삼는다.

## 11. 다른 질문과 음성 대조 영향

- Q001, Q002, Q004, Q005: 질문에 단일 명시 시점이 없으므로 `requested_phase=None`, 모든 후보 tier는 `inactive`이며 기존 순서를 그대로 반환한다. Q002 multi-chunk 문제는 개선도 악화도 하지 않는 것이 이번 범위의 계약이다.
- Q006: 시점이 없고 모든 BM25 점수가 0이다. 점수, 동률 순서와 양수 후보 0건을 그대로 유지한다.
- 제목-only 정책: BM25 점수가 0인 제목-only chunk를 양수 후보로 승격하지 않으며 기존 mask, 상속 문맥과 `SEARCH_VERSION=11` 동작을 기반으로 순서 계층만 추가한다.
- rank-bm25: 비교 baseline이므로 시점 tier를 적용하지 않는다.

시점 질문이 추가되면 순서는 바뀔 수 있지만 후보 집합과 원점수는 바뀌지 않는다. 이 제한 때문에 A는 hard filter가 아니다.

## 12. 불변식과 호환성

1. `BM25Index.scores()`의 서명, 점수 값, 길이와 chunk 위치 대응은 변하지 않는다.
2. 재정렬 전후 Top 40 후보 ID 집합은 동일하다.
3. 같은 tier 안의 상대 순서는 기존 BM25 순서와 동일하다.
4. 단일 요청 phase가 없으면 전체 순서가 기존과 동일하다.
5. 점수 0 이하는 tier 때문에 양수 후보가 되지 않는다.
6. 제목-only chunk의 점수 0 정책은 유지한다.
7. 로컬·클라우드·평가·trace가 같은 SCHAT 순서를 관찰한다.
8. rank-bm25에는 SCHAT phase 정책을 적용하지 않는다.
9. chunk ID, text, section, page/location과 citation 값은 변경하지 않는다.
10. 정책은 요청마다 파생되는 순수 계산이며 캐시, DB field 또는 mutable 전역 상태를 만들지 않는다.

## 13. 방식 B: BM25 점수 보너스·감점

질문과 후보 시점이 같으면 원점수에 보너스를 더하거나 곱하고, 명시적 불일치면 감점하는 방식이다.

장점은 기존 점수 정렬 코드에 보정값을 넣기 쉽고, tier 사이에도 BM25 강도를 연속적으로 반영할 수 있다는 점이다. 그러나 Q003의 00026과 관련 본문 점수 차이를 뒤집는 계수는 현재 1개 문서에 맞춘 튜닝이 되기 쉽다. 새 문서에서 점수 범위가 달라지면 재조정해야 하며, “BM25 점수”가 원 lexical 점수인지 시점 보정 점수인지 불명확해진다.

특히 0점에 가산 보너스를 주면 Q006류 음성 대조에서 근거 없는 양수 후보가 생길 수 있다. 이를 막는 별도 조건이 추가되며 정책이 더 복잡해진다. A가 너무 강한 tier 이동을 만든다는 재평가 증거가 있을 때만 예비 대안으로 검토한다.

Rollback은 보정 계수와 점수 합성 코드, `SEARCH_VERSION`을 이전 상태로 되돌리는 방식이다. 계수별 평가 산출물을 섞지 않도록 별도 버전 관리가 필요하다.

## 14. 방식 C: RAG reranking 단계에서만 반영

BM25+dense RRF 이후의 최종 `rerank()` 또는 향후 RAG reranker에서 시점 일치 신호를 반영하는 방식이다.

최종 답변 근거의 순서를 개선할 가능성은 있지만 SCHAT BM25Index Top 10과 Q003 BM25 Hit@1은 그대로다. 현재 BM25 단계의 관찰된 실패를 해결하지 못하고, BM25 승인을 받기 전에 RAG 동작을 변경한다는 단계 규칙에도 맞지 않는다. 또한 dense·RRF·시점 점수가 한 단계에 섞이면 원인 추적과 rollback 범위가 커진다.

따라서 이번 설계에서는 기각한다. 향후 BM25가 승인된 후 최종 답변 수준의 추가 시점 판단이 필요하다는 별도 증거가 있을 때만 새로운 RAG 설계로 검토한다.

## 15. 테스트 및 검증 계획

### 단위 테스트

- “진정 전”, “진정 치료 전”, “전·중·후”에서 phase를 올바르게 파생한다.
- “전 직원”, “중환자”, “후배”, “오전”을 phase로 오인하지 않는다.
- 질문에 before 하나가 있고 후보가 before/during/after/neutral일 때 tier 순서가 match→neutral→mismatch다.
- 복수 phase 후보가 요청 phase를 포함하면 match다.
- 질문에 시점이 없거나 둘 이상이면 기존 순서와 완전히 같다.
- 같은 tier 후보는 원래 BM25 순서를 유지한다.
- 재정렬 전후 후보 ID 집합과 원점수가 같다.
- 0점 후보가 양수 후보보다 앞서거나 lexical 후보로 승격되지 않는다.
- chunks와 scores 길이 불일치, 잘못된 limit의 오류 계약을 검증한다.

### 통합·회귀 테스트

- 로컬·클라우드가 동일 입력에서 같은 lexical 순서를 RRF에 전달한다.
- search trace와 실제 lexical 순서, phase와 tier가 일치한다.
- evaluator의 SCHAT 순서만 변경되고 rank-bm25 결과는 기존 파일과 동일하다.
- 제목-only 양성 Top 10 노출 0/50과 Q006 전 점수 0을 유지한다.
- Q001·Q002·Q004·Q005의 SCHAT chunk 순서와 점수가 변경 전과 동일하다.
- 전체 기존 테스트와 새 집중 테스트를 실행한다.

### 동일 평가셋 재평가

기존 산출물을 덮어쓰지 않고 `artifacts/2026-09-13_bm25-temporal-rerank/` 같은 새 디렉터리에 다음을 생성한다.

- SCHAT BM25Index Top 10
- 변경하지 않은 rank-bm25 Top 10
- CSV, JSON, review.html
- phase와 tier가 보이는 검수 정보
- 기존 수동 label을 chunk ID로 연결한 Hit@k와 MRR
- Q001~Q006 전후 순위 및 Q003 상세 비교

새 Top 10 조합에 기존 label이 없으면 임의 판정하지 않고 판정 누락 수를 보고한다. 첫 관련 순위 앞의 미판정 결과가 있으면 해당 질문의 Hit와 MRR을 확정값으로 표시하지 않는다.

## 16. 합격 기준

1. Q003의 첫 정식 관련 결과가 1위다.
2. Q003 1위는 `before`가 명시된 substantive 본문이어야 하며 제목-only 또는 표 머리글이어서는 안 된다.
3. Q003 Hit@3·5·10은 모두 유지한다.
4. 양성 Q001~Q005 전체 Hit@3·5·10은 100%를 유지한다.
5. Q001·Q002·Q004·Q005의 SCHAT 순위와 원점수는 구현 전과 동일하다.
6. Q002 Top 3/5 단계 회수 결과는 이번 변경 전과 동일하며, 개선된 것으로 주장하지 않는다.
7. Q006의 SCHAT·rank-bm25 점수는 모두 0이고 양수 후보가 없다.
8. SCHAT 양성 Top 10 제목-only 노출은 0/50을 유지한다.
9. rank-bm25 Top 10은 기존 baseline과 동일하다.
10. 로컬·클라우드·평가·trace의 SCHAT 시점 순서가 일치한다.
11. 전체 테스트와 정적 검사가 통과한다.

Q003 Hit@1을 달성하지 못하면 score 보정이나 tokenizer/query expansion으로 즉시 전환하지 않는다. phase 검출, tier 분류, 비본문 후보와 평가 label 적용 범위를 먼저 분석해 결과 문서에 실패를 그대로 기록한다.

## 17. 구현 순서

사용자 승인 후에만 다음 순서로 진행한다.

1. Q003 현재 순서와 시점 표현, 시점 없는 질문·복수 시점·오탐 방지 문구를 테스트 fixture로 고정한다.
2. `mvp/retrieval.py`에 내부 phase parser와 `rank_bm25_candidates()` 순수 함수를 구현한다.
3. 로컬 검색의 기존 원점수 Top 40에 공통 helper를 적용한다.
4. 클라우드 검색도 같은 helper를 호출하고 RRF 후보 집합 불변을 검증한다.
5. trace가 실제 순서와 tier를 표시하도록 연결한다.
6. evaluator의 SCHAT 경로에만 같은 순서를 적용하고 rank-bm25 경로는 유지한다.
7. `SEARCH_VERSION`을 12로 올리고 `CHUNK_VERSION`은 유지한다.
8. 집중 테스트, 전체 테스트와 정적 검사를 실행한다.
9. 기존 산출물을 보존한 채 새 디렉터리에 두 엔진 평가 결과를 생성한다.
10. `docs/bm25/06_TEMPORAL_RERANK_RESULT.md`에 결과와 BM25 단계 승인 판단을 기록한다.
11. 작업을 멈추고 사용자 승인을 기다린다. RAG 단계로 자동 진행하지 않는다.

## 18. Rollback

이 설계는 chunk와 BM25 원점수를 변경하지 않으므로 rollback은 코드와 검색 버전 단위다.

1. 로컬·클라우드·평가의 `rank_bm25_candidates()` 호출을 제거해 기존 원점수 안정 정렬로 되돌린다.
2. trace의 phase/tier 표시 연결을 제거한다.
3. `SEARCH_VERSION`을 11로 되돌린다.
4. 이 변경을 위해 추가한 테스트를 함께 되돌린다.
5. 시점 실험 artifacts와 결과 문서는 제목-only 개선 산출물과 분리해 보존한다.

제목-only 방식 A, 원본 문서, chunk, 임베딩, DB와 기존 평가 산출물은 rollback 대상이 아니다.

## 19. 위험과 확인 사항

- 문법 기반 phase parser도 “오전 중”, “치료 전반” 같은 표현을 잘못 읽을 수 있으므로 경계·반례 테스트가 필요하다.
- section에는 여러 후속 chunk가 공유하는 상위 문장이 들어갈 수 있다. 복수 phase section은 요청 phase를 포함하면 match로 두되 실제 본문 적절성은 기존 label과 substantive 검사를 함께 확인해야 한다.
- A는 시점 일치를 BM25 점수보다 우선하므로 매우 낮은 점수의 match 후보가 높은 점수의 neutral 후보보다 앞설 수 있다. Top 40 안에서만 적용하고 같은 tier 원순서를 유지하는 것이 이 위험의 제한선이다.
- 현재 근거는 1개 문서와 Q003 중심이다. 구현 후 다른 시점 질문을 추가 검수하기 전에는 모든 병원 지침에 일반화됐다고 주장하지 않는다.
- 시점 재정렬이 RRF 순서를 바꾸므로 최종 로컬·클라우드 hit도 확인해야 하지만, 이번 단계에서 RAG 생성 품질을 평가하거나 구현하지 않는다.

## 20. 사용자 승인 대기

권고안은 “기존 BM25 Top 40 후보 집합과 원점수는 유지하고, 질문의 단일 명시 시점에 따라 match→neutral→mismatch 순으로 안정 재정렬”하는 방식 A다. 각 tier 내부는 기존 BM25 순서를 유지하며 시점이 없는 질문과 0점 음성 대조는 기존 순서를 그대로 반환한다.

이 문서 작성으로 작업을 멈춘다. 실제 검색 코드, Q002, tokenizer, query expansion, 제목-only 정책, rank-bm25와 RAG는 수정하지 않았으며 사용자 승인 후에만 구현한다.

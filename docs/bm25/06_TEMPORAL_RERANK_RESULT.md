# BM25 시점 기반 안정적 reranking 결과

- 구현·평가일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/bm25/05_TEMPORAL_RERANK_PLAN.md` A안
- 기준선: 승인된 제목-only 방식 A, `SEARCH_VERSION=11`
- 개선 평가 생성 시각: 2026-09-13T05:10:23+09:00
- 상태: 시점 tier 구현 완료, BM25 단계 최종 승인 대기

## 1. 결론

`match → neutral → mismatch` 안정적 tier 재정렬을 적용한 결과, Q003의 첫 정식 관련 결과가 2위에서 1위로 개선됐다. 새 1위는 `before`가 명시된 substantive 본문 00016이며 기존 수동 판정은 `relevant`다. 기존 1위였던 00026은 BM25 원점수가 그대로인 상태에서 시점 불일치 tier 뒤로 이동했다.

Q001·Q002·Q004·Q005는 Top 10 chunk ID 순서와 BM25 원점수가 모두 구현 전과 동일하다. Q006도 전 점수 0과 동률 순서를 유지했다. 제목-only 개선 결과인 양성 Top 10 제목-only 0/50도 유지됐다. 양성 Q001~Q005의 Hit@1과 MRR은 각각 80%→100%, 0.900→1.000으로 개선됐고 Hit@3·5·10은 100%를 유지했다.

추가 조건으로 요청된 낮은 점수 match의 부적절한 1위 승격은 Q003에서 발견되지 않았다. 새 1위 00016은 정식 relevant이고 원점수 0.3573으로, 최고 neutral 관련 후보 00013의 0.3695보다 약 3.3% 낮은 수준이다. 다만 tier 정책은 낮은 순위에서 irrelevant match 후보 00017도 neutral 후보보다 앞세웠다. 이 잔여 위험을 기록하되 승인 범위에 따라 threshold, 보너스 또는 감점은 추가하지 않았다.

시점 reranking 변경 자체는 계획의 합격 기준을 충족한다. 그러나 이전 단계에서 남은 Q002 multi-chunk Top 3/5 회수 부족은 그대로이므로 BM25 단계 전체의 최종 승인 여부는 사용자가 별도로 판단해야 한다. RAG 단계로 자동 진행하지 않는다.

## 2. 구현 내용

`mvp/retrieval.py`에 `BM25CandidateRanking`과 `rank_bm25_candidates()`를 추가했다.

- 현재 사용자 질문인 `QueryPlan.original`에서 명시적 시점을 파생한다.
- 시점은 `before`, `during`, `after`의 제한된 문법 표지만 사용한다.
- 질문에서 정확히 하나의 시점만 검출될 때 정책을 활성화한다.
- 기존 BM25 원점수 순서로 Top 40 후보를 먼저 고정한다.
- 고정된 후보 집합 안에서 `match → neutral → mismatch → non_positive` 순으로 재정렬한다.
- 각 tier 내부에서는 기존 BM25 순서를 유지한다.
- 질문에 시점이 없거나 둘 이상이면 `inactive`로 기존 순서를 그대로 반환한다.
- 0 이하 점수는 시점이 일치해도 양수 후보로 승격하지 않는다.
- chunks와 scores 길이, limit, 유한한 점수 입력을 검증한다.

로컬 `mvp/retrieval.py`와 클라우드 `mvp/cloud.py`는 같은 helper의 lexical 순서를 RRF에 전달한다. `mvp/search_trace.py`는 실제 적용된 requested phase와 tier를 기록한다. `tools/bm25_evaluate.py`는 SCHAT 결과에만 같은 순서를 적용하고 CSV·JSON·review.html에 phase와 tier를 표시한다.

`mvp/library.py`의 `SEARCH_VERSION`은 11에서 12로 올렸고 `CHUNK_VERSION`은 4로 유지했다. 제목-only `BM25CorpusPolicy`, tokenizer, query expansion과 BM25 원점수 계산은 변경하지 않았다.

## 3. 후보·점수 불변성

시점 정책은 점수를 다시 계산하거나 후보를 삭제하지 않는다.

| 검증 항목 | 결과 |
|---|---|
| Q003 재정렬 전후 Top 40 후보 집합 | 동일 |
| Q003 chunk별 SCHAT BM25 원점수 | 동일 |
| 같은 tier 내부 원 BM25 상대 순서 | 유지 |
| 제목-only score 0 정책 | 유지 |
| chunk ID, text, section, page/location | 변경 없음 |
| rank-bm25 chunk 순서와 점수 | 전체 6문항 동일 |

RRF에는 동일한 lexical 후보 ID 집합이 시점 tier 순서로 전달된다. dense 검색, 최종 `rerank()`와 context 확장 로직은 변경하지 않았다.

## 4. Hit@k와 MRR

기존 계산 규칙과 같이 `relevant`와 `partially_relevant`를 관련 결과로 취급했다.

### 답이 존재하는 Q001~Q005

| 엔진·시점 | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR |
|---|---:|---:|---:|---:|---:|
| 시점 reranking 전 SCHAT | 80.0% (4/5) | 100.0% | 100.0% | 100.0% | 0.900 |
| 시점 reranking 후 SCHAT | 100.0% (5/5) | 100.0% | 100.0% | 100.0% | 1.000 |
| rank-bm25 baseline | 20.0% (1/5) | 100.0% | 100.0% | 100.0% | 0.600 |

### Q006을 포함한 전체 6문항

| 엔진 | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR |
|---|---:|---:|---:|---:|---:|
| 시점 reranking 후 SCHAT | 83.3% (5/6) | 83.3% (5/6) | 83.3% (5/6) | 83.3% (5/6) | 0.833 |
| rank-bm25 baseline | 16.7% (1/6) | 83.3% (5/6) | 83.3% (5/6) | 83.3% (5/6) | 0.500 |

Q006은 답이 없는 음성 대조이므로 일반 Hit와 reciprocal rank는 0으로 포함했다.

## 5. 질문별 첫 관련 순위와 불변 확인

| 질문 | 시점 reranking 전 SCHAT | 적용 후 SCHAT | 변화 |
|---|---:|---:|---|
| Q001 진정간호 목적은? | 1위, 00003 | 1위, 00003 | Top 10 ID·점수 순서 동일 |
| Q002 진정간호 절차는? | 1위, 00013 | 1위, 00013 | Top 10 ID·점수 순서 동일 |
| Q003 진정 전 준비사항은? | 2위, 00013 | 1위, 00016 | 목표 달성 |
| Q004 진정 간호의 목적은 무엇인가요? | 1위, 00003 | 1위, 00003 | Top 10 ID·점수 순서 동일 |
| Q005 진정간호 주의사항은? | 1위, 00028 | 1위, 00028 | Top 10 ID·점수 순서 동일 |
| Q006 화성 우주선의 궤도 계산 공식은? | 없음 | 없음 | Top 10 전 점수 0·순서 동일 |

Q001·Q002·Q004·Q005와 Q006의 `requested_temporal_phase`는 `null`, tier는 `inactive`다. 이 다섯 질문은 시점 정책을 통과하지 않고 기존 순서를 그대로 사용했다.

## 6. Q003 상세 결과

### 적용 전

| 순위 | chunk | 원점수 | 판정 | 시점 성격 |
|---:|---|---:|---|---|
| 1 | 00026 | 0.7056 | irrelevant | during/mismatch |
| 2 | 00013 | 0.3695 | relevant | neutral |
| 3 | 00016 | 0.3573 | relevant | before/match |
| 4 | 00015 | 0.3530 | relevant | before/match |
| 5 | 00004 | 0.3505 | irrelevant | neutral |

### 적용 후

| 순위 | chunk | 원점수 | tier | 기존 판정 |
|---:|---|---:|---|---|
| 1 | 00016 | 0.3573 | match | relevant |
| 2 | 00015 | 0.3530 | match | relevant |
| 3 | 00019 | 0.3441 | match | partially_relevant |
| 4 | 00020 | 0.3434 | match | partially_relevant |
| 5 | 00017 | 0.3318 | match | irrelevant |
| 6 | 00021 | 0.3249 | match | 기존 Q003 label 없음 |
| 7 | 00023 | 0.3057 | match | 기존 Q003 label 없음 |
| 8 | 00003 | 0.2972 | match | 기존 Q003 label 없음 |
| 9 | 00022 | 0.2808 | match | 기존 Q003 label 없음 |
| 10 | 00030 | 0.2686 | match | 기존 Q003 label 없음 |

00016과 00015는 질문과 같은 before가 section 또는 본문에 명시돼 있다. 00019와 00020은 전·중·후 중 before를 포함하므로 설계 계약에 따라 match다. 00026은 삭제되거나 감점된 것이 아니라 match와 neutral 후보 뒤로 이동했다.

Q003의 새 Top 10에 기존 판정이 없는 조합이 5개 생겼다. 첫 관련 결과 1~4위는 모두 기존 정식 관련 판정이므로 Hit@1과 MRR은 확정할 수 있지만, Top 10 전체의 relevance 분포를 완전 판정으로 주장하지 않는다.

## 7. 낮은 점수 match 과승격 점검

새 1위 00016을 두 비교점과 대조했다.

| 비교 | 점수 | 판정 | 해석 |
|---|---:|---|---|
| 새 1위 match 00016 | 0.357257 | relevant | before가 명시된 substantive 준비 본문 |
| 최고 neutral 관련 00013 | 0.369534 | relevant | 00016보다 0.012277, 약 3.3% 높음 |
| 기존 원점수 1위 mismatch 00026 | 0.705628 | irrelevant | 질문 시점과 다른 검사 중·진정 후 맥락 |

00016의 점수는 최고 neutral 관련 후보의 약 96.7%다. 따라서 “BM25 관련성이 매우 낮고 부적절한 match가 높은 점수의 neutral 관련 후보를 누르고 1위가 된 사례”로 판단하지 않는다. 더 낮은 점수의 관련 후보를 무조건 고른 것이 아니라, 점수가 근접하고 정식 relevant인 시점 일치 본문을 우선한 결과다.

다만 tier의 일반적 위험은 실제로 관찰됐다.

- 00017은 `성인 | 소아` 표 머리글로 기존 판정이 irrelevant지만 상속 section에 before가 있어 match 5위가 됐다.
- 여러 match 후보가 앞서면서 기존 neutral relevant 00013은 새 Top 10 밖으로 이동했다.
- 새 Top 10의 미판정 후보 5개는 시점 일치만으로 내용 적절성이 보장되지 않음을 보여준다.

이 현상은 요청된 “부적절한 1위”에는 해당하지 않지만 시점 tier가 lexical 관련성을 압도할 수 있는 잔여 위험이다. 이번 구현에서는 임의 threshold, 보너스, 감점, substantive hard filter를 추가하지 않았다. 향후 조정이 필요하면 별도 분석·설계와 사용자 승인을 거친다.

## 8. Q002 및 제목-only 회귀

Q002의 Top 10 ID와 원점수는 시점 reranking 전과 완전히 같다. 따라서 다음 기존 결과도 그대로다.

- 첫 관련 chunk 00013은 1위
- Top 3 substantive 절차 본문 1/3
- Top 5의 명확한 서로 다른 절차 단계 1개
- multi-chunk 충분 회수 기준 미충족

Q002를 개선하거나 개선된 것으로 해석하지 않는다.

SCHAT 양성 질문의 제목-only 노출은 0/50이고 Q001·Q002·Q005에서 제목-only가 본문보다 앞서는 현상도 재발하지 않았다. 승인된 제목-only 방식 A는 유지됐다.

## 9. 판정 연결 범위

시점 reranking 후 두 엔진 Top 10 합집합은 78개 query-chunk 조합이며 기존 정식 label이 있는 조합은 64개, 없는 조합은 14개다. 순위 변화로 새 조합이 생겼지만 label을 임의 생성하지 않았다.

모든 질문에서 첫 관련 결과 이전의 후보에는 정식 판정이 존재한다. Q003은 1위가 기존 relevant이고 나머지 양성 질문도 1위가 기존 관련 판정이며, Q006 Top 10은 기존 irrelevant다. 따라서 이 문서의 Hit@k와 MRR 계산에는 선행 미판정으로 인한 불확정성이 없다.

## 10. 합격 기준 판정

| 기준 | 결과 | 판정 |
|---|---|---|
| Q003 첫 정식 관련 결과 1위 | 00016 relevant | 통과 |
| Q003 1위가 before substantive 본문 | 진정 치료 전 설명·동의 본문 | 통과 |
| Q003 Hit@3·5·10 유지 | 모두 100% | 통과 |
| 양성 전체 Hit@3·5·10 | 모두 100% | 통과 |
| Q001·Q002·Q004·Q005 순위·원점수 | Top 10 모두 동일 | 통과 |
| Q002 결과 불변 | multi-chunk 실패 상태 동일 | 통과 |
| Q006 전 점수 0·양수 후보 없음 | 유지 | 통과 |
| 양성 제목-only 0/50 | 유지 | 통과 |
| rank-bm25 순위·점수 | 전체 6문항 동일 | 통과 |
| 로컬·클라우드·평가·trace 공통 순서 | 집중 테스트로 확인 | 통과 |
| 매우 낮은 부적절 match의 1위 승격 없음 | 1위는 relevant, neutral 대비 96.7% 점수 | 통과 |
| 전체 테스트·정적 검사 | 모두 통과 | 통과 |

## 11. 테스트와 코드 검수

추가·갱신한 테스트는 다음을 검증한다.

- match→neutral→mismatch의 안정적 순서
- 같은 tier 안의 기존 BM25 순서 유지
- 시점 없는 질문과 복수 시점 질문의 완전 비활성
- `전 직원`, `중환자`, `후배`, `오전` 오탐 방지
- 0점 match의 양수 후보 승격 방지
- Top 40 후보 집합과 원점수 불변
- 잘못된 길이, limit, 비유한 점수 입력 거부
- SCHAT evaluator에만 tier 적용, rank-bm25 격리
- 로컬 trace에 실제 phase/tier 순서 기록
- 클라우드 RRF에 같은 temporal lexical 순서 전달

실행 결과:

- 관련 집중 테스트: `96 passed`
- 전체 테스트: `220 passed, 1 skipped`
- Ruff 정적 검사: 통과
- 변경 diff 검수: 추가 threshold·점수 가감·RAG 변경 없음

## 12. 산출물

기존 평가 산출물은 덮어쓰지 않았다. 새 결과는 다음 디렉터리에 생성했다.

`artifacts/2026-09-13_bm25-temporal-rerank/`

- `bm25_results.csv`
- `schat_bm25_top10.csv`
- `rank_bm25_top10.csv`
- `bm25_results.json`
- `review.html`

review.html과 CSV·JSON에는 SCHAT의 `requested_temporal_phase`와 `temporal_tier`를 표시한다. rank-bm25는 `baseline`으로 분리되며 결과 순서와 점수를 변경하지 않는다.

## 13. 변경 및 비변경 범위

변경:

- `mvp/retrieval.py`: 시점 parser, 안정적 tier ranking, 로컬 연결
- `mvp/cloud.py`: 공통 ranking을 클라우드 RRF에 연결
- `mvp/search_trace.py`: 실제 phase/tier 진단 정보
- `mvp/library.py`: `SEARCH_VERSION=12`
- `tools/bm25_evaluate.py`: SCHAT tier 평가 및 HTML·CSV·JSON 표시
- 관련 테스트

비변경:

- Q002 multi-chunk 검색 정책
- tokenizer와 query expansion
- 제목-only 방식 A
- rank-bm25 계산과 순위
- BM25 원점수 계산식
- 원본 문서, chunk ID, page/location, 임베딩과 DB schema
- RAG, LLM과 최종 답변 생성
- 기존 평가 artifacts

## 14. 승인 권고와 작업 중지

시점 기반 안정적 reranking A안은 계획된 합격 기준을 충족했으므로 변경 자체의 승인을 권고한다. Q003 Hit@1은 개선됐고 비대상 질문, 음성 대조, 제목-only 정책과 baseline은 유지됐다.

다만 match tier가 낮은 순위의 비본문 또는 미판정 후보도 neutral 후보보다 앞세울 수 있다는 위험과 Q002 multi-chunk 문제가 남아 있다. 이를 이번 범위에서 임의 수정하지 않는다.

이 결과 문서 작성으로 작업을 멈춘다. 사용자 승인 전에는 threshold·보너스·감점 추가, Q002 수정, tokenizer·query expansion 변경 또는 RAG 구현을 진행하지 않는다.

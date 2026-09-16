# Pilot Query Generalization 테스트 우선 분석 결과

- 작성·검증일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 구현: `docs/rag/30_FACET_SLOT_SELECTION_RESULT.md`
- 단계: **테스트 추가 및 베이스라인 원인 분석 완료, production 수정 승인 대기**
- 실제 Groq 호출: 0회
- Production query/retrieval/evidence 코드 수정: 0건
- 검수 HTML: `artifacts/2026-09-14_rag-pilot-query-generalization-baseline/review.html`

## 1. 결론

요청한 표현 일반화 경계를 먼저 테스트로 고정하고 현재 production 베이스라인에서 실행했다. 핵심 경계 6개 중 Q006 zero-call과 기존 Q002/Q006 안전 경계 2개는 통과했고, 나머지 4개는 기대대로 실패했다.

- Q001은 `supported`, Q004는 `incomplete_semantic_block`으로 서로 다른 evidence assessment를 반환한다.
- Q003은 temporal phase `before`는 정상 인식하지만 intent가 `fact`이며, 관련 사전평가·설명·동의 근거가 검색된 뒤 부분 semantic block 때문에 차단된다.
- 준비 표현 변형은 `fact` 또는 `materials`로 갈리고 모두 `incomplete_semantic_block`에 도달한다.
- Q005 및 주의 표현 변형은 `cautions` 또는 `fact`로 갈리며 `no_topic_evidence` 또는 `incomplete_semantic_block`으로 차단된다.
- Q006은 `out_of_scope`, Groq/Mock transport 0회와 고정 근거 부족 응답을 유지한다.

BM25, semantic, RRF와 reranker는 Q003~Q005의 관련 근거를 이미 회수한다. 따라서 검색 점수나 gate 임계값을 느슨하게 할 문제가 아니라, **일반 query intent/topic 정규화와 non-procedure semantic-block 구성의 불일치**가 직접 원인이다.

이번 단계에서는 이를 고치지 않았다. 다음 절의 최소 수정안은 제안일 뿐이며 별도 승인 전 production에 적용하지 않는다.

## 2. 추가한 테스트

`tests/test_pilot_query_generalization.py`에 다음 경계를 추가했다.

1. Q001과 Q004의 pre/post reason, selected evidence와 group key가 동일한지 비교
2. Q003이 `preparation` intent이며 pre/post 모두 sufficient이고 사전 진정 근거를 포함하는지 확인
3. Q003과 준비 표현 3개가 동일한 supported evidence core를 공유하는지 확인
4. Q005와 주의 표현 3개가 supported safety/caution evidence를 공유하는지 확인
5. 9개 표현 변형의 intent/topic 및 pre/post evidence 통과 확인
6. Q001/Q003/Q004/Q005의 로컬 `MockTransport` reconstruction/citation 경계 확인
7. Q002 기존 Facet-slot 경계와 Q006 catalog/transport 0회 회귀 확인

질문 문자열과 평가 chunk ID는 테스트 fixture에서만 사용한다. Production 코드에는 Q001~Q006 문자열, chunk ID 또는 기대 개수를 추가하지 않았다.

## 3. 테스트 결과

### 목표별 집중 테스트

| 경계 | 결과 | 실제 reason |
|---|---|---|
| Q001/Q004 assessment 동등성 | 실패 | Q001 `supported`, Q004 `incomplete_semantic_block` |
| Q003 pre-sedation sufficient | 실패 | `kind=fact`, `incomplete_semantic_block` |
| 준비 표현 공통 evidence | 실패 | `fact/materials`, 모두 `incomplete_semantic_block` |
| 주의 표현 safety evidence | 실패 | `fact/cautions`, `no_topic_evidence` 또는 `incomplete_semantic_block` |
| Q006 out-of-scope | 통과 | `domain_or_clarification`, zero-call |
| 기존 Q002/Q006 Mock 경계 | 통과 | Q002 answerable, Q006 zero-call |

실행 결과: `4 failed, 2 passed, 31 deselected, 2 warnings`.

새 파일 전체 베이스라인은 `26 failed, 7 passed, 2 warnings`다. 실패 수가 많은 이유는 같은 원인이 intent 단위, evidence 단위, end-to-end 단위 테스트에서 각각 재현되기 때문이다.

기존 테스트는 새 red test 파일을 제외하고 프로젝트 내부 전용 `--basetemp`로 실행했다.

- 기존 회귀: `309 passed, 1 skipped, 2 warnings`
- 새 테스트/evaluator Ruff: 통과
- 처음 기본 임시 폴더를 사용한 전체 실행의 setup error 106건은 `C:\Users\...\Temp\pytest-of-...` 접근 거부였고, 프로젝트 내부 basetemp 재실행에서 모두 사라졌다.

## 4. Q001~Q006 기대값과 현재 베이스라인

| Case | 사람이 정한 기대 | 현재 pre-budget | 현재 post-budget | Mock transport | 판정 |
|---|---|---|---|---:|---|
| Q001 목적 | 답변 | `supported` | `supported` | 1회 | 기존 통과 |
| Q002 절차 | 답변 | `supported` | `supported` | 1회 | 기존 통과 |
| Q003 진정 전 준비 | 답변 | `incomplete_semantic_block` | 미도달 | 0회 | 실패 |
| Q004 목적 표현 변형 | 답변 | `incomplete_semantic_block` | 미도달 | 0회 | 실패 |
| Q005 주의사항 | 답변 | `no_topic_evidence` | 미도달 | 0회 | 실패 |
| Q006 화성 우주선 | abstain | `domain_or_clarification` | 미도달 | 0회 | 안전 통과 |

Q003~Q005 전용 정답 stage fixture는 현재 없다. 다만 안정 corpus의 실제 원문과 기존 retrieval 결과에서 아래 근거 존재를 확인했다.

- Q003: 사전 환자평가·진정 계획, 설명·동의, 성인/소아 진정 전 평가 근거
- Q004: Q001과 동일한 목적 본문인 `chunk-00003`
- Q005: `chunk-00028`의 주요 증상·징후, 합병증 및 응급상황 교육 행과 관련 monitoring/safety 근거

따라서 “근거가 없어 차단”되는 사례가 아니라, 검색된 근거가 evidence gate의 의미 구조로 전달되지 못하는 사례다.

## 5. Q001과 Q004 비교

| 항목 | Q001 `진정간호 목적은?` | Q004 `진정 간호의 목적은 무엇인가요?` | 분석 |
|---|---|---|---|
| QueryPlan kind | `fact` | `fact` | 둘 다 목적 intent가 명시되지 않음 |
| terms | `진정간호`, `목적` | `진정`, `간호`, `목적` | 띄어쓰기 때문에 subject token이 분리됨 |
| topic words | `진정간호` | `진정` | Q004에서 `간호`가 generic intent term으로 빠짐 |
| embedding question | `진정 간호 목적` | `진정 간호 목적` | 동일 |
| Semantic Top 결과 | 동일 | 동일 | embedding은 원인이 아님 |
| BM25 1위 | `chunk-00003`, 5.489635 | `chunk-00003`, 5.757761 | 둘 다 정답 본문 회수 |
| Pre assessment | `supported` | `incomplete_semantic_block` | Q004의 넓어진 topic 범위가 불필요 block을 함께 필수화 |

Q004는 검색 실패가 아니다. 목적 chunk가 Q001과 똑같이 검색되고 semantic vector도 동일하다. 차이는 띄어쓴 문서 주제가 `진정간호`가 아닌 넓은 `진정`으로 해석되어 context의 여러 parent가 evidence 후보로 들어오고, 그중 `chunk-00026~00027` block이 부분 포함되는 데 있다.

## 6. Q003 원인

- Temporal rerank는 `requested_phase=before`를 올바르게 기록한다.
- BM25 상위에는 `chunk-00016`(0.357257), `chunk-00015`(0.353008), `chunk-00019`, `chunk-00020`, `chunk-00017`이 있다.
- Rerank seed에도 `chunk-00016`, `chunk-00015`, `chunk-00023`, `chunk-00020`이 포함된다.
- 즉 pre-sedation assessment/consent/preparation 근거는 retrieval funnel에 존재한다.

차단 원인은 두 겹이다.

1. `준비사항`을 일반 준비/확인 aspect가 아니라 `fact`로 둔다. `준비해야` 표현은 물품 질문이 아닌데도 `materials`로 분류된다.
2. Non-procedure context가 여러 seed parent를 round-robin으로 섞어 12개 한도를 채우면서 pediatric `chunk-00023~00027`과 adult `chunk-00019~00020` parent가 부분 포함된다. Evidence gate는 이 불완전 block을 정상적으로 `incomplete_semantic_block`으로 차단한다.

따라서 temporal rerank를 바꾸거나 completeness 검사를 없애지 않고, 준비 intent를 분리하고 parent block을 원자적으로 취급해야 한다.

## 7. Q005 원인

- BM25 1위는 주의·합병증 원문이 있는 `chunk-00028`(1.037273)이다.
- RRF 1위도 `chunk-00028`이며 rerank seed에도 들어간다.
- `chunk-00028`, `chunk-00029`는 context-expanded evidence 안에 있다.

그런데 `relevant_body()`는 Q005 topic token `진정간호`가 body에 직접 있어야 한다고 해석한다. 실제 body는 주의 증상·징후와 합병증을 담고 있고 `진정간호`는 문서/section metadata에 있으므로 seed 0개가 되어 `no_topic_evidence`가 발생한다.

또한 일부 안전·주의 목록 행은 완전한 독립 bullet이지만 문장부호나 colon이 없어 현재 SourceUnit eligibility에서 선택 불가다. 이는 pre-LLM topic 차단과 별개의 다음 경계이며, gate 수정 뒤 Mock reconstruction까지 통과하려면 일반 bullet-row eligibility 테스트도 필요하다.

## 8. 표현 변형 베이스라인

| 유형 | 질문 | 현재 kind | Pre reason |
|---|---|---|---|
| 목적 | 진정간호 목적 알려줘 | `fact` | `supported` |
| 목적 | 진정 간호는 왜 시행하나요? | `fact` | `incomplete_semantic_block` |
| 목적 | 진정의 목적이 뭐야? | `fact` | `supported` |
| 준비 | 진정 전에 뭘 준비해야 해? | `materials` | `incomplete_semantic_block` |
| 준비 | 진정 시행 전 확인할 것은? | `fact` | `incomplete_semantic_block` |
| 준비 | 진정 전 체크사항 알려줘 | `fact` | `incomplete_semantic_block` |
| 주의 | 진정 시 주의할 점은? | `cautions` | `incomplete_semantic_block` |
| 주의 | 진정간호에서 조심해야 할 것은? | `fact` | `no_topic_evidence` |
| 주의 | 진정 중 안전하게 봐야 할 항목은? | `fact` | `incomplete_semantic_block` |

표현 변형은 같은 의미로 안정 분류되지 않는다. 특히 질문 어미와 `왜`, `체크사항`, `조심`, `안전하게 봐야`가 topic noise로 남거나 intent trigger에 포함되지 않는다.

## 9. 승인 요청용 최소 수정안

### A. Query normalization — 일부 채택 제안

- `X 간호`와 `X간호`를 **topic subject 추출에서만** 같은 canonical subject로 본다.
- 목적, 사전 준비/확인, 주의/조심/안전 관찰의 일반 표현을 request aspect로 분류한다.
- `진정` 단독을 전역 병원 도메인으로 열지 않고, 간호·환자·치료 또는 임상 phase/aspect와 함께 있을 때만 hospital query로 인정한다.
- 원 BM25 query/tokenizer/expansion은 건드리지 않는다.

### B. QueryPlan topic/aspect 분리 — 채택 제안

- Topic은 `진정간호` 같은 문서 주제, aspect는 `purpose/preparation/cautions`로 분리한다.
- 문서명·title·section은 topic을 연결할 수 있지만, 실제 body가 요청 aspect 표현을 포함해야 seed가 된다.
- 제목만 일치하는 chunk를 근거로 허용하지 않는다.

### C. BM25 query expansion — 기각

Q003~Q005 모두 관련 chunk가 BM25/RRF/rerank에 이미 들어온다. 승인된 BM25/tokenizer/query expansion을 바꿀 근거가 없다.

### D. Semantic block 판정 — 원자성 개선 제안

- Parent metadata가 있는 non-procedure context는 parent를 통째로 포함하거나 통째로 제외한다.
- Evidence scope는 빈 section 문자열보다 `parent_id`를 우선해 서로 다른 parent가 한 block으로 합쳐지지 않게 한다.
- Branch를 명시하지 않은 좁은 질문에서 충분한 common group이 있으면 adult/pediatric 예시 parent를 자동 필수화하지 않는다. Branch metadata 자체는 보존한다.
- 첫 required parent 자체가 한도를 넘는 경우에는 기존처럼 fail closed한다.

### SourceUnit bullet eligibility — 제한적 동반 수정 제안

- 완결된 독립 bullet/list 행은 종결 punctuation이나 colon이 없어도 selectable로 인정한다.
- heading, caption, 단계 표지, orphan continuation은 계속 non-selectable이다.
- 의료 문구나 Q005 chunk ID가 아니라 목록 구조만 사용한다.

### E. Evidence gate 완화 — 기각

`incomplete_semantic_block`, topic, branch, citation 검사를 제거하거나 threshold를 낮추지 않는다. 필요한 것은 입력 의미와 parent 경계를 정확히 만드는 것이며, Q006과 불완전 parent 차단은 그대로 유지한다.

## 10. 안전성 및 중단 조건 검토

- Q003~Q005 근거는 실제 corpus에 존재한다: 구현 검토 가능
- Q006은 명시적 out-of-scope pattern으로 retrieval/transport 전에 차단된다: 유지 가능
- Production 질문 문자열/chunk ID 하드코딩은 필요 없다
- Validator 완화는 필요 없다
- BM25, embedding, RRF와 reranker 변경은 필요 없다

따라서 다음 구현 단계로 갈 수 있는 설계 조건은 충족한다. 다만 이번 단계에서는 production 변경을 적용하지 않았다.

## 11. 변경·비변경 범위와 승인 대기

이번 단계에서 추가한 파일/변경은 테스트와 오프라인 분석 도구뿐이다.

- `tests/test_pilot_query_generalization.py`
- `tools/rag_pilot_query_generalization_evaluate.py`
- `artifacts/2026-09-14_rag-pilot-query-generalization-baseline/`
- 이 결과 문서

변경하지 않은 production 범위:

- `mvp/query.py`, `mvp/context.py`, `mvp/evidence.py`, `mvp/library.py`
- BM25, tokenizer, query expansion, temporal rerank
- Embedding, RRF, reranker
- Evidence/Answer validator와 Q006 차단
- Groq provider 및 prompt

실제 Groq 호출은 0회다. 제안한 A/B/D 및 제한적 bullet eligibility 변경은 사용자 승인 전까지 구현하지 않는다.

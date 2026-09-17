# SCHAT 최종 MVP 완성 결과

- 완료일: 2026-09-18
- 작업 전 안정 버전: **v0.8**
- 현재 안정 버전: **v0.9**
- Branch: `boha-rag`
- 외부 Groq/Gemini/vision 호출: 0회
- 병원 데이터 외부 전송: 0회
- Git 저장: 하나의 안정 commit, annotated `v0.9` tag, `boha-rag`와 tag push
- 최종 상태: **v0.9 안정 복구 기준점**

## 1. 최종 판정

Retrieval 병목, table MM003, controlled generation 안전 경계, multimodal routing, local web UAT, 다문서 확장 경계와 전체 회귀를 완료했다. Provider Live와 TF027 image workflow는 승인/사람 검수가 필요하므로 fail-closed pending으로 유지했다.

Production retrieval은 변경하지 않았다. BM25+E5 raw RRF가 evaluation 정확도는 가장 높았지만 always-on E5의 약 195 ms 지연, 약 1.54 GB RSS, 기존 안전 reranker와의 상호작용 문제가 남아 있기 때문이다.

따라서 최종 production 구조는 다음과 같다.

**BM25 + MiniLM semantic/vector + RRF + existing reranker**

## 2. Reranker 병목과 최종 retrieval

동일 수혈 105 chunks, 90문항, approved positive 78, 동일 gold/Top-K/metric으로 비교했다.

| 전략 | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR | Recall@10 | Precision@10 | Mean/P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 current | 0.3974 | 0.6538 | 0.7949 | 0.8846 | 0.5602 | 0.8379 | 0.1192 | 1.010 / 1.628 |
| E5-large | 0.5897 | 0.8205 | 0.8718 | 0.9359 | **0.7151** | 0.9084 | 0.1308 | 193.474 / 282.720 |
| BM25+E5 raw RRF | 0.5128 | 0.7949 | **0.9231** | **0.9487** | 0.6769 | **0.9359** | **0.1359** | 194.651 / 283.856 |
| Existing reranker | 0.3205 | 0.6923 | 0.8333 | 0.9103 | 0.5184 | 0.8681 | 0.1244 | 265.158 / 372.321 |
| Candidate-aware rerank | **0.5256** | 0.7949 | 0.9103 | 0.9487 | 0.6836 | 0.9359 | 0.1359 | 194.669 / 283.871 |
| Top-N rerank | 0.5000 | 0.7949 | 0.9231 | 0.9487 | 0.6626 | 0.9359 | 0.1359 | 194.685 / 283.887 |

Existing reranker는 raw fusion Top-40에서 lexical/coverage 중심으로 다시 선택하며 E5 semantic candidate 일부를 Top-10 밖으로 밀었다. Candidate-aware/rank-preserving 변형은 raw Top-10 identity를 보존해 recall 손실을 막았지만 E5 runtime 비용은 그대로였다.

Ranking 함수는 gold ID와 question ID를 입력으로 받지 않는다. Production query 문자열, chunk ID 또는 gold 기반 특례를 추가하지 않았다.

## 3. BM25와 E5의 역할

- BM25: exact medical term, fact-specific, 매우 낮은 latency에 강함
- E5: procedure, preparation, paraphrase, product/temporal recall에 강함
- BM25+E5 RRF: evaluation 정확도 최고
- Production: 운영 latency/memory/safe reranker 근거가 부족해 기존 hybrid 유지

## 4. Vector storage

E5가 production에 채택되지 않아 ChromaDB/Qdrant production dependency를 추가하지 않았다.

| 항목 | 결과 |
|---|---:|
| E5 model cache | 2,252,997,322 bytes |
| Model RSS delta | 1,541,464,064 bytes |
| Evaluation snapshot | 389,672 bytes |
| Snapshot load | 11.46 ms |
| Passage re-embedding per startup | 불필요 |

현재 105-chunk evaluation에는 raw-free NumPy snapshot이 충분하다. E5 production 채택과 수천/수만 chunks 확장이 확정될 때 vector DB를 재평가한다.

## 5. Table retrieval/citation/UI

MM003를 gold·row link·header inheritance·parser·retrieval·citation으로 분리 진단했다. 직접 원인은 PDF grid가 다중 열 body value를 비운 채 첫 열 label만 추출한 sparse parser 결과였다.

일반 수정:

- 다중 열 표의 body row 75% 이상이 한 셀 이하이면 `sparse_body`
- 기존 substantive catalog parent group을 exact row evidence로 재사용
- 숫자+단위/시간 exact token을 table-only search에서 우선
- Korean helper n-gram 가중치 축소

| 결과 | 변경 전 | 변경 후 |
|---|---:|---:|
| Tables | 5 | 5 |
| Rows | 33 | 38 |
| Approved cases Hit@10 | 0.8 | **1.0** |
| MM003 first gold rank | 없음 | **2** |

Citation의 document/page/table ID/row identity와 exact local rendering은 유지했다. Exact table text는 local UI 메모리에서만 표시하고 artifact에 저장하지 않았다.

Production repository에는 table cell/geometry schema가 없으므로 main answer path 연결은 보류했다. 이를 연결하려면 Local/Cloud schema migration이라는 별도 대규모 변경이 필요하다.

## 6. Answer generation/UX

현재 production은 verified extractive Answer와 presentation sidecar를 유지한다. Procedure, checklist, warning, summary, comparison, temporal/branch heading은 임상 text를 바꾸지 않고 표시한다.

Controlled generation은 다음을 준비했다.

- request-scoped SourceUnit enum
- supporting IDs
- server citation regeneration
- schema/invariant validators
- invalid/pending extractive fallback
- retry 0

모든 구조 검증을 통과해도 semantic support가 별도 검증되지 않으면 기존 Answer를 출력한다. Caller-supplied boolean으로 semantic 검증을 우회하는 경로는 제거했다. Provider Live와 semantic entailment는 승인 대기다.

## 7. Image/diagram과 multimodal routing

- Figure metadata candidates: 55
- TF027: `needs_human_review=true`
- Approved image-dependent gold: 0
- Vision API call: 0

Text/table/image/mixed candidate routing은 advisory metadata다. Image+table cue는 `mixed`로 기록하지만 `pending_image_review` 상태를 유지한다. Routing은 production evidence gate나 answerability를 바꾸지 않는다.

## 8. UAT와 multi-document

- Sedation offline UAT 45/45 계약 유지
- Q006 provider zero-call 유지
- Table approved 5/5 Top-10 통과
- Local text/table Streamlit AppTest 통과
- Repository document filter와 context document boundary 통과
- Multi-document conflict는 양쪽 citation을 요구하며 임의 병합하지 않음

## 9. Performance

| 항목 | 결과 |
|---|---:|
| BM25 query mean/p95 | 1.010 / 1.628 ms |
| E5 query embedding mean/p95 | 192.98 / 282.16 ms |
| Existing reranker mean/p95 | 70.51 / 100.91 ms |
| Table lookup mean/p95 | 61.91 / 88.74 ms |
| Table cold extraction | 18,480.12 ms |
| Display rows mean/p95 | 0.027 / 0.043 ms |

Table extraction은 local Streamlit `cache_resource`로 재사용된다. E5는 production startup에 추가하지 않았다.

## 10. Test/Regression

- Full pytest: **534 passed, 4 skipped, 7 warnings**
- Focused integrated regression: **156 passed, 2 warnings**
- Reranker TDD: 4 passed
- Table TDD/UAT: 8 passed
- Controlled generation/presentation: 21 passed
- Ruff: 통과
- `git diff --check`: 통과
- Code review: actionable defect 0

Code review 중 caller-supplied semantic publication bypass 가능성을 발견해 제거했으며 집중 테스트를 다시 통과했다.

## 11. Security

최종 artifact와 프로젝트 현황판의 텍스트 파일 17개를 catalog 147 chunks와 비교했다.

- Exact source match: 0
- Forbidden field: 0
- Secret marker: 0
- API key/Authorization/full prompt/raw response: 0
- Hospital data external transfer: 0
- External provider/vision call: 0

## 12. 변경한 Production 파일

- `mvp/controlled_generation.py`: fail-closed publication decision
- `mvp/evidence_routing.py`: image+table mixed pending routing

`tools/structured_table_evidence.py`는 local/evaluation-only 도구다.

## 13. 변경하지 않은 영역

- Production BM25/MiniLM/RRF/reranker
- Production embedding/catalog vectors/dependencies
- Facet-slot, SourceUnit, selection limit 16
- Parent atomicity, PromptCoverage, AnswerCoverage
- `validate_answer()`와 모든 임상 validator
- Provider prompt/schema/live transport
- Retry/fallback/web search 정책

## 14. Pending

1. 병원 데이터 외부 전송 승인 후 provider Live/semantic entailment
2. TF027 사람 검수와 approved image-dependent gold
3. Production table persistence용 Local/Cloud repository schema 설계
4. E5 production을 재검토하려면 low-latency serving과 safe candidate-aware reranker 실증 필요

## 15. 버전과 복구

- 작업 전 안정 버전: **v0.8**
- 현재 안정 버전: **v0.9**
- `v0.8` commit/tag: 변경 없이 이전 복구 기준점으로 보존
- `v0.9` tag: 최종 문서와 검증 결과를 포함한 안정 commit을 가리키는 annotated tag
- GitHub 저장: `boha-rag`와 `v0.9` tag push 완료
- 새 안정 복구 기준점 저장: **완료**

사용자가 변경 파일과 pending 범위를 확인한 뒤 별도로 Git 저장을 승인해야 한다.

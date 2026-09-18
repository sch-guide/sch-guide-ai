# SCHAT v1.0 최종 안정화 보고서

- 검증일: 2026-09-18
- 공식 안정 복구 기준점: `v0.9`
- 대상 브랜치: `boha-rag`
- 최종 준비 상태: **READY_EXCEPT_IMAGE_AND_LIVE_RAGAS**
- Production retrieval 변경: **0건**
- 실제 Groq/Gemini/Vision/LLM judge 호출: **0회**
- Git commit/tag/push: **0회**

## 1. 결론

사람이 검수한 운영 Positive Gold 32건을 동결해 다시 검증했다. 이 중
`final_gold_approved=true`인 21건만 정량 평가에 포함했고, 판단 보류 11건은 어떤
aggregate에도 넣지 않았다. 기존 approved out-of-scope 4건도 그대로 유지했다.

동일한 승인 Gold와 Top-10 조건에서 현재 production hybrid가 Hit@10 0.9524,
MRR 0.7370, ID Context Recall 0.8210으로 가장 균형이 좋았다. BM25+E5 RRF도
Hit@10은 0.9524였지만 MRR과 multi-Gold recall이 더 낮았고 E5는 evaluation-only
상태다. 따라서 production의 `BM25 + MiniLM semantic/vector + RRF + existing
reranker` 구조를 변경하지 않았다.

보류 11건에는 일반화 가능한 단일 production 결함이 확인되지 않았다. 표 구조/매핑
4건, 후보 근거 부족 3건, 사람 최종 판단 대기 2건, 질문 모호성 1건, 이미지 사람 검수
1건으로 분류했다. Gold를 변경하거나 validator를 완화하지 않고 11건 모두 사람 검수
queue에 남겼다.

## 2. Gold 최종 상태

| 항목 | 결과 |
|---|---:|
| Positive review 전체 | 32 |
| 최종 승인 Positive Gold | **21** |
| 판단 보류 | **11** |
| 기존 approved abstention Gold | **4** |
| 2차 검수 필요 | 0 |
| 자동 승인 | 0 |
| 중복 case ID | 0 |
| 중복 evidence ID | 0 |
| Primary/Acceptable overlap | 0 |
| 승인 case의 빈 critical fact | 0 |

승인된 21건의 Primary/Acceptable evidence는 production catalog 및 structured-table
index의 실제 ID와 대조했다. 검수 결과와 기존 승인 Gold는 수정하지 않았다.

## 3. 승인 Gold 기준 retriever 비교

Positive 21건만 aggregate에 포함했다. Gold는 Primary와 Acceptable ID의 합집합이며,
negative 및 deferred case는 IR/RAGAS 평균에서 제외했다.

| Retriever | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR | Recall@10 | Precision@10 / ID Precision | ID Recall | Mean / P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 current | 0.4286 | 0.7143 | 0.7619 | 0.9048 | 0.5822 | 0.4523 | 0.2571 | 0.4523 | 0.884 / 1.241 |
| **Production hybrid** | **0.5714** | **0.9048** | 0.9048 | **0.9524** | **0.7370** | **0.8210** | **0.5365** | **0.8210** | 110.084 / 207.006 |
| E5 large, evaluation-only | 0.3810 | 0.7143 | 0.8095 | 0.8095 | 0.5317 | 0.5015 | 0.3000 | 0.5015 | 208.465 / 309.905 |
| BM25+E5 RRF, evaluation-only | 0.4286 | 0.8095 | **0.9048** | **0.9524** | 0.6155 | 0.5352 | 0.3143 | 0.5352 | 209.544 / 310.980 |

Production hybrid의 공통 Top-10 miss는 `UAT-T11` 한 건이다. 이 case는 표 row
evidence를 요구하며 별도 structured-table route에서는 Top-10 안에 포함된다.

### Table subset

운영 승인 Gold 중 table/mixed case 2건에서 일반 text retriever만으로는 table row ID를
충분히 회수하지 못했다. 별도 structured-table route는 두 case 모두 Top-5/Top-10에
성공했다.

| Table route | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR | Recall@10 | Precision@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Structured table | 0.5000 | 0.5000 | **1.0000** | **1.0000** | 0.6000 | 0.6429 | 0.2500 |

기존 approved table regression 5건도 5/5, Hit@10 1.0을 유지했다.

## 4. Deferred 11건 원인과 재평가

| Category | 수 | Case ID | 질문 | 개선 layer |
|---|---:|---|---|---|
| `table_structure_or_mapping` | 4 | UAT-T05 | 적혈구 제제 투여 시 확인사항은? | table row mapping 또는 사람 확인 |
|  |  | UAT-T06 | 혈소판 제제 간호를 알려줘 | table row mapping 또는 사람 확인 |
|  |  | UAT-T09 | 혈액제제별 보관 조건을 알려줘 | table row mapping 또는 사람 확인 |
|  |  | UAT-T10 | 적혈구와 혈소판 제제 기준을 비교해줘 | table row mapping 또는 사람 확인 |
| `insufficient_candidate_evidence` | 3 | UAT-S10 | 진정간호 전체 내용을 핵심만 알려줘 | 후보 생성 또는 사람 확인 |
|  |  | UAT-T08 | 수혈 시작 후 언제 활력징후를 확인해? | 후보 생성 또는 사람 확인 |
|  |  | UAT-T14 | 수혈이 끝난 뒤 기록과 관찰은 어떻게 해? | 후보 생성 또는 사람 확인 |
| `other` | 2 | UAT-S07 | 진정 동의서는 언제 받아? | 사람 최종 판단 |
|  |  | UAT-T04 | 수혈 이상반응이 의심되면 어떻게 해야 해? | 사람 최종 판단 |
| `ambiguous_question` | 1 | UAT-T07 | 신선동결혈장 사용 기준은? | 질문 범위 사람 확인 |
| `image_human_review_required` | 1 | UAT-T18 | 혈액제제 요청부터 수령까지 그림의 순서대로 알려줘 | 이미지 사람 검수 |

- `retrieval_miss`: 0건
- 안전하게 자동 개선된 deferred: **0건**
- 다시 사람 검수가 필요한 deferred: **11건**

모든 case에는 text 또는 table 후보가 존재했다. 그러나 사람의 보류 결정을 뒤집을 만큼
명확한 공통 오류는 없었다. 질문별 특례, Gold 기반 boosting, Gold 자동 승인, validator
threshold 완화는 수행하지 않았다.

## 5. Image / TF027

- Page 11 figure candidate: 7개
- 사람 확인 checklist: 10개
- 확인 완료: **0/10**
- `needs_human_review=true`
- `production_gold_approved=false`
- Multimodal aggregate: 제외
- Vision/provider 호출: 0회

Figure bbox, page, caption/nearby-text 연결 후보와 fingerprint만 review queue로 만들었다.
Node, edge, 화살표 방향, branch, workflow 순서, 숫자·단위·시간은 자동 확정하지 않았다.

## 6. Live RAGAS readiness

상태는 **BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL**이다. 다음 승인이 없으므로 실제
Groq/Gemini/LLM judge를 호출하지 않았다.

- 병원 evidence exact text 외부 전송 정책
- provider 및 model
- region 및 retention/logging 조건
- 허용 case 범위
- credential 사용

Offline에서는 ID Context Precision/Recall, citation, critical fact 및
number/unit/time/condition/negation 안전 계약을 검증했다. Faithfulness와 Answer
Relevancy는 LLM judge 승인을 기다리는 `pending` 상태다. Synthetic Mock은 실행 가능하지만
real evidence 전송은 승인 전 차단된다.

## 7. UAT와 회귀

| 검증 | 결과 |
|---|---:|
| Operational UAT | **36/36 PASS** |
| 진정 | 14/14 |
| 수혈 | 18/18 |
| Out-of-scope | 4/4, provider zero-call |
| Stabilization focused tests | 18 passed |
| 통합 안전 회귀 | 219 passed, 4 warnings |
| Full pytest | **637 passed, 4 skipped, 10 warnings** |
| Ruff (`mvp`, `tests`, `tools`) | PASS |
| `git diff --check` | PASS |
| Artifact security audit | PASS, 14 files |
| Code review | Actionable defect 0 |

Warning 10건은 기존 FastEmbed MiniLM mean-pooling 안내다. Production embedding과
dependency를 변경하지 않았다.

## 8. 성능

| 항목 | 결과 |
|---|---:|
| Local app/model/catalog 준비 | 3,835.899 ms |
| 운영 UAT local total mean / p95 | 195.822 / 262.817 ms |
| Query planning mean / p95 | 3.977 / 7.107 ms |
| MiniLM query embedding mean / p95 | 21.252 / 33.922 ms |
| Retrieval + reranker mean / p95 | 88.382 / 127.626 ms |
| Process RSS, 평가 중 두 모델 적재 | 1,858,658,304 bytes |
| E5 local snapshot reload | 10.757 ms |
| E5 passage rebuild | 0 ms, snapshot 재사용 |

E5 snapshot은 local ignored 평가 파일이며 production에는 연결하지 않았다.

## 9. Security와 변경 범위

Artifact 14개 감사 결과 exact source match, forbidden raw field, secret marker는 모두 0건이다.
병원 PDF, SourceUnit 원문, 전체 prompt, raw provider response, API key와 Authorization은
artifact에 저장하지 않았다.

이번 작업에서 production module은 변경하지 않았다. 추가한 것은 evaluation-only 도구,
테스트, raw-free artifact와 문서다. 기존 retrieval, embedding, Facet-slot, selection limit,
AnswerCoverage, `validate_answer()`, citation 및 임상 안전 validator를 유지했다.

Code review에서 추가 조치가 필요한 correctness, safety 또는 regression 결함은 발견되지
않았다. 사람이 진행해 21건으로 늘어난 실제 reviewed fixture 때문에 과거 “승인 2건”을
가정하던 draft 단위 테스트 한 건만 실패했으며, 실제 fixture를 변경하지 않고 synthetic
2-case 상태로 테스트를 격리했다.

## 10. v1.0 readiness와 다음 사람 작업

최종 상태는 **READY_EXCEPT_IMAGE_AND_LIVE_RAGAS**다. Core text/table retrieval과
36문항 UAT는 통과했지만 다음 두 작업은 반드시 사람이 결정해야 한다.

1. TF027 원본 figure의 10개 항목을 검수하고 필요 시 독립 2차 확인을 완료한다.
2. 병원 evidence 외부 전송 정책, provider/model/region/retention/case 범위/credential을
   승인한 뒤 제한 Live RAGAS를 실행한다.
3. Deferred 11건을 review app에서 다시 확인한다. 보류 유지도 유효한 사람 결정이다.

실제 `v1.0` tag는 생성하지 않았고, 현재 공식 복구 기준점 `v0.9`도 변경하지 않았다.

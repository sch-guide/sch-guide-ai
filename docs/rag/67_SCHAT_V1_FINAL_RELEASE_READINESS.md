# SCHAT v1.0 최종 Release Readiness

- 검증일: 2026-09-18
- 공식 안정 복구 기준점: `v0.9`
- 대상 브랜치: `boha-rag`
- 최종 판정: **READY_EXCEPT_LIVE_RAGAS**
- Production retrieval/validator 변경: **0건**
- 실제 Groq/Gemini/Vision/LLM judge 호출: **0회**
- 병원 데이터 외부 전송: **0회**
- Git commit/tag/push: **0회**

## 1. Gold와 Deferred

사람이 검수한 Positive Gold 32건 중 `final_gold_approved=true`인 21건만 확정 Gold로
유지했다. 판단 보류 11건은 자동 승인하거나 aggregate에 포함하지 않았다. 기존 approved
abstention Gold 4건도 별도 safety set으로 유지한다.

| 항목 | 수 |
|---|---:|
| Approved Positive Gold | **21** |
| Deferred | **11** |
| Approved abstention Gold | **4** |
| 자동 승인 | **0** |

Deferred 분류는 table structure/mapping 4건, candidate evidence 부족 3건, 사람 최종
판단 2건, ambiguous question 1건, image human review 1건이다. 11건 모두 사람 재검수
후보일 뿐 `final_gold_approved` 값은 변경하지 않았다.

## 2. TF027 사람 검수

상태는 **`READY_IMAGE_EXCLUDED_BY_HUMAN_DECISION`**이다. 사람이 10개 항목을 모두
검수한 뒤 `approval_decision=reject`로 저장했으므로, Production image Gold를 승인하지
않고 v1.0 범위에서 제외하는 보수적 결정을 release gate에 반영했다.

- Page 11 figure candidate: 7개
- 사람이 확인한 checklist: **10/10**
- `needs_human_review=true`
- `production_gold_approved=false`
- `image_excluded_by_human_decision=true`
- Vision/provider 호출: 0회
- Image aggregate 및 production answerability: 제외

검수 화면은 원본 page와 bbox 후보를 왼쪽에, 기존 안전 저장 양식과 10개 확인 순서를
오른쪽에 한 화면으로 표시한다. Node, edge, 순서와 임상 의미는 자동으로 채우지 않는다.

```powershell
.\.venv\Scripts\streamlit.exe run tools\tf027_human_review_app.py
```

## 3. Live RAGAS readiness

상태는 **`BLOCKED_BY_EXTERNAL_TRANSFER_APPROVAL`**다. 병원 evidence exact text의 외부
전송 승인, provider/model, region, retention/logging, case 범위와 credential 사용 승인이
없어 실제 호출을 수행하지 않았다.

승인 후 실행 순서는 비민감 synthetic 검증 → 승인 Positive Gold 중 대표 3~5건 → 중대한
안전 오류가 0건일 때만 승인 21건 확대 순으로 동결했다. Deferred 11건은 제외하고 approved
abstention 4건은 provider zero-call safety set으로만 사용한다.

외부 전송 후보는 request-local SourceUnit ID, 선택된 exact SourceUnit text, normalized
intent, JSON schema와 safety instruction뿐이다. 원 질문, 문서명·페이지·production chunk
ID, 대화 이력, 환자·직원 식별자, 전체 문서, table/figure 원본은 제외한다. 전송 전 로컬
식별자 검사와 사람 이름 확인이 필수다. Artifact에는 raw evidence, prompt, response,
credential 또는 Authorization을 저장하지 않는다.

현재 evaluation harness는 wire blueprint와 Mock validator까지만 가지며 네트워크 transport와
credential loader는 없다. 실제 Live 실행은 별도 사용자 승인 아래 승인된 adapter/credential
경로를 연결한 뒤 수행해야 한다.

## 4. Retrieval, Table과 ChromaDB

Production은 기존 **BM25 + MiniLM semantic/vector + RRF + existing reranker**를 유지한다.
승인 Gold 21건 기준 production hybrid는 Hit@10 0.9524, MRR 0.7370, ID Context
Precision 0.5365, ID Context Recall 0.8210이다. E5와 BM25+E5는 evaluation-only다.

Structured table은 기존 approved 5/5와 운영 승인 subset 2/2 Top-10 계약을 유지했다.

- BM25: lexical retrieval
- MiniLM/E5: embedding model
- ChromaDB: vector store/search infrastructure
- Qdrant: 향후 production vector DB 후보

ChromaDB와 Qdrant는 v1.0 production에 통합하지 않았다. 차기에는 동일 승인 Gold, 동일
chunk, 동일 Top-K/metric으로 BM25, Chroma+MiniLM, Chroma+E5와 hybrid를 비교한다.

## 5. 최종 회귀와 보안

| 검증 | 결과 |
|---|---:|
| Operational UAT | **36/36 PASS** |
| Release-readiness focused | **44 passed** |
| Integrated safety regression | **184 passed, 4 warnings** |
| Full pytest | **648 passed, 4 skipped, 10 warnings** |
| Ruff (`mvp`, `tests`, `tools`) | **PASS** |
| `git diff --check` | **PASS** |
| Artifact security audit | **PASS** |
| Code review | **Actionable defect 0** |

Warning 10건은 기존 FastEmbed MiniLM mean-pooling 안내다. Retrieval, citation,
number/unit/time, condition/negation, branch/phase, AnswerCoverage, `validate_answer()`,
Facet-slot, parent atomicity, table, image routing, fallback/abstention 및 out-of-scope
zero-call 계약을 회귀 검증했다.

Release artifact에는 hospital source exact match, forbidden raw field, secret marker와 PDF가
없다. 실제 외부 호출과 병원 데이터 외부 전송은 모두 0회다.

## 6. 최종 판정과 사람의 다음 행동

최종 상태는 **READY_EXCEPT_LIVE_RAGAS**다. Core text/table/search와 운영 UAT, 안전
회귀가 통과했고 TF027 image blocker도 사람의 제외 결정으로 닫혔다. 다음 결정만 남았다.

1. 병원 evidence 외부 전송을 승인하고 provider/model/region/retention/case/credential을
   지정해 제한 Live RAGAS를 별도 승인한다. 외부 provider를 사용하지 않기로 결정하는
   경우에도 그 정책 결정을 명시한다.

Live RAGAS blocker가 사람 결정으로 닫히고 동일 회귀가 다시 통과하기 전에는 `READY` 또는 정식
`v1.0`으로 표시하지 않는다. 현재 `v0.9` tag는 그대로이며 새 commit/tag/push를 수행하지
않았다.

## 7. 산출물

- `tools/schat_v1_release_readiness.py`
- `tests/test_schat_v1_release_readiness.py`
- `tools/tf027_human_review_app.py`
- `tests/test_tf027_human_review.py`
- `artifacts/2026-09-18_schat-v1-final-release-readiness/`
- `docs/superpowers/progress/2026-09-18-schat-v1-final-release-readiness.md`

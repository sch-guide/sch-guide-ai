# SCHAT Multimodal MVP Text/Table 안전 확장 결과

- 구현·검증일: 2026-09-17
- 대상 브랜치: `boha-rag`
- 실행 범위: text presentation, controlled generation Mock, structured table, citation, routing, offline UAT
- 실제 Groq/Gemini generation 호출: 0회
- Git commit/push: 0회
- 최종 상태: **승인된 text/table 범위 완료, provider와 image track은 pending**

## 1. 결론

기존 retrieval, Facet-slot SourceUnit selection, reconstruction, AnswerCoverage와 모든 안전 validator를 유지하면서 text/table presentation 계층을 확장했다.

Text 답변은 검증된 exact SourceUnit과 citation을 변경하지 않고 질문 intent에 맞는 표시만 적용한다. Fact/purpose는 외부 번호 없는 문단, preparation/materials는 정적 checklist 형태, cautions/release는 warning bullet, summary는 핵심 요약 heading, procedure는 기존 branch/phase 구조로 표시한다. Statement 순서, 임상 문장, quote와 chunk ID는 바뀌지 않는다.

Controlled paraphrasing은 provider-neutral schema/prompt/validator까지만 구현했다. Unknown/duplicate evidence ID, 새 number/unit/time, condition/negation 변경, branch/phase 혼합과 evidence 누락을 fail closed한다. 실제 `mvp.ai` generation 경로에는 연결하지 않았고 semantic support는 pending으로 명시한다.

수혈 PDF에서는 structured table 5개와 row 33개를 추출했다. PDF가 실제 cell 구조를 제공한 표 3개는 header/row/cell 관계를 보존했고, borderless body 2개는 열 관계를 추론하지 않고 exact catalog row fallback으로 표시했다. Table citation은 document/page/table/row까지 추적한다. 표 원문은 local UI에서만 보이며 artifact에는 ID/hash/bbox만 저장한다.

승인 table case 5개의 evaluation-only row retrieval은 Hit@10 0.8이었다. 기존 평가에서 table-aware retrieval이 standalone BM25보다 명확히 우수하지 않았고 이번에도 1개 case가 miss였으므로 production table retrieval은 연결하지 않았다. Production retrieval, BM25/RRF/reranker, Facet-slot과 validator는 그대로다.

## 2. Text answer presentation

### Server sidecar

`AnswerPresentation`에는 임상 text가 아닌 다음 metadata만 존재한다.

- statement/source-unit identity
- branch와 phase
- source order
- leading marker
- intent와 answer format

Sidecar는 reconstruction, `validate_answer()`와 citation assessment 통과 뒤에만 붙는다. Public Answer JSON/schema에는 포함되지 않는다.

### UI routing

| Intent | 표시 |
|---|---|
| fact / purpose | outer numbering 없는 exact paragraph rows |
| procedure | branch → phase headings + exact statements |
| preparation / materials | 정적 checklist 모양 |
| cautions / release | warning bullet |
| summary / synthesis | `핵심 요약` + exact bullets |
| comparison | 기존 exact statement Markdown table |

UI heading은 원래 statement index 순회 중 바뀌는 지점에만 삽입한다. 재정렬, 의미 dedup, 임상 요약 생성은 하지 않는다.

## 3. Controlled paraphrasing 준비

Mock-only response 계약은 natural statement와 `supporting_source_unit_ids`만 받는다. Schema enum은 현재 request의 검증된 SourceUnit ID로 제한한다. 서버는 이 ID에서 exact chunk ID와 quote를 다시 파생한다.

Fail-closed 검사:

- unknown/duplicate/empty evidence ID
- statement/evidence 한도
- selected evidence coverage
- unsupported number, percentage, unit와 time
- condition/negation 추가 또는 누락
- adult/pediatric branch 혼합
- 서로 다른 phase 혼합

이 검사는 실제 semantic entailment를 완전히 증명하지 않는다. 따라서 `semantic_support_pending=true`이고 production provider path에는 연결하지 않았다.

## 4. Structured table와 citation

| 항목 | 결과 |
|---|---:|
| Table records | 5 |
| Rows | 33 |
| `pdf_cells` | 3 |
| `catalog_row_fallback` | 2 |
| Pages | 2, 6, 9, 15, 17 |

Table record는 stable table ID, page, index, bbox, fingerprint, exact header/row/cell 및 source chunk links를 runtime 메모리에 가진다. Persisted manifest에는 clinical text 대신 SHA-256만 저장한다.

Borderless table은 header만 grid로 감지되는 경우가 있었다. 이때 열 위치를 임의로 추정하지 않고 기존 catalog parent의 exact row를 단일 보존 행으로 연결했다. UI에도 extraction mode를 표시한다.

로컬 table 검수 UI:

```powershell
.\.venv\Scripts\python.exe -m streamlit run tools\table_evidence_app.py
```

이 UI는 PDF/catalog를 runtime에만 읽으며 artifact write, generation API와 vision API를 수행하지 않는다.

## 5. Evidence-type routing

QueryPlan trace에 retrieval과 분리된 후보 metadata를 추가했다.

- 일반 임상 질의: `text`
- 비교, 제품/제제별, 보관, 온도, 속도, 용량, 간격, 수치: `table`, `text`
- 명시적 image/화면/workflow: `image`, `pending_image_review`

Router는 gold나 chunk ID를 읽지 않으며 evidence gate와 answerability를 바꾸지 않는다. Q006은 계속 `out_of_scope`다.

## 6. Offline evaluation

| Metric | 결과 |
|---|---:|
| Approved table cases | 5 |
| Hit@1 | 0.8000 |
| Hit@3 | 0.8000 |
| Hit@5 | 0.8000 |
| Hit@10 | 0.8000 |
| Recall@10 | 0.8000 |

MM003 table numeric case 1건은 Top-10 miss다. 이 때문에 structured table retrieval은 production 개선으로 판정하지 않았다.

`TF027`은 workflow image sequence를 사람 검수 없이 확정할 수 없으므로 `needs_human_review=true`, aggregate 제외, production 미사용을 유지한다. Vision description 생성은 0건이다.

## 7. 안전 및 비변경 확인

- BM25, embedding, RRF, reranker 변경 없음
- Facet-slot selection과 selection limit 변경 없음
- SourceUnit reconstruction 변경 없음
- AnswerCoverage와 `validate_answer()` 완화 없음
- citation/number/unit/time/condition/negation/source-order validator 완화 없음
- Q006 zero-call 유지
- Groq/Gemini generation 호출 0회
- Vision API 호출 0회
- API key/Authorization/raw provider response 저장 없음
- Hospital exact source text artifact 저장 0건
- Git commit/push 0회

## 8. 테스트와 코드 리뷰

- Multimodal 집중 테스트: **28 passed**
- Query/UAT generalization: **130 passed, 4 warnings**
- 전체 pytest: **501 passed, 4 skipped, 6 warnings**
- Ruff: 통과
- `git diff --check`: 통과
- Artifact audit: 6 files, exact source match 0, secret marker 0

Warning 6건은 기존 FastEmbed multilingual MiniLM pooling 기본값 안내다. Embedding은 변경하지 않았다.

Code review 결과 기존 trust boundary를 우회하거나 validator를 약화하는 correctness/safety 결함은 발견되지 않았다. Table fallback은 열 의미를 추론하지 않으며 routing metadata는 retrieval에 연결되지 않는다.

## 9. 산출물

설계와 진행 기록:

- `docs/superpowers/specs/2026-09-17-schat-multimodal-mvp-design.md`
- `docs/superpowers/plans/2026-09-17-schat-multimodal-mvp.md`
- `docs/superpowers/progress/2026-09-17-schat-multimodal-mvp-phase-00-readiness.md`
- Phase 01~05 progress checkpoints

검수 artifact:

`artifacts/2026-09-17_schat-multimodal-mvp/`

- `summary.json`
- `table_manifest.json`
- `table_results.json`
- `environment.json`
- `test_results.json`
- `review.html`

## 10. Pending 및 다음 승인 범위

완료:

- Exact-source text presentation과 style routing
- Controlled generation schema/prompt/Mock validator
- Structured table parser, row citation, local search/UI
- Evidence-type routing metadata
- Offline UAT와 raw-free artifacts

Pending:

1. 병원 데이터 외부 전송 정책과 provider 승인 후 실제 controlled generation 품질 평가
2. `TF027` workflow image 사람 검수
3. 승인된 image-dependent gold 확보 후 vision/image retrieval 평가
4. MM003 miss 분석 후 table retrieval의 production 재평가

현재 상태에서는 production retrieval을 바꾸지 않는 것이 안전한 결론이다. 자동 commit/push는 수행하지 않았으며, 검토 후 `boha-rag`에 하나의 검증된 commit으로 묶는 것을 권고한다.

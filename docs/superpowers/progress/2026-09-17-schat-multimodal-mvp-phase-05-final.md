# SCHAT Multimodal MVP Phase 05 — 통합 검증 및 종료

- 상태: 완료
- 실제 Groq/Gemini generation 호출: 0회
- Git commit/push: 0회

## 완료 항목

- Text style presentation, controlled paraphrasing Mock contract, structured table/citation/UI, evidence routing을 통합 검증했다.
- 수혈 catalog 105 chunks와 승인 table gold를 재검증했다.
- 승인 table case 5개를 evaluation-only structured row search로 평가했다.
- TF027은 aggregate와 production에서 제외하고 pending human review를 유지했다.
- Raw-free summary, manifest, results, environment, review HTML을 생성했다.
- 전체 회귀, Ruff, diff check, code review와 artifact audit를 완료했다.

## 변경 파일

- Production presentation/trace: `mvp/presentation.py`, `mvp/answer_ui.py`, `mvp/ai.py`, `mvp/query.py`, `mvp/evidence_routing.py`
- Mock-only generation: `mvp/controlled_generation.py`
- Evaluation-only table/tooling: `tools/structured_table_evidence.py`, `tools/table_evidence_app.py`, `tools/schat_multimodal_mvp_evaluate.py`
- 관련 tests, specs, plans, progress, result document와 artifact

## 테스트 결과

- Multimodal 집중: 28 passed
- Query/UAT generalization: 130 passed, 4 warnings
- 전체 pytest: **501 passed, 4 skipped, 6 warnings**
- Ruff: 통과
- `git diff --check`: 통과
- Artifact audit: exact source leak 0, secret marker 0

## 평가 결과

- Structured tables: 5 records, 33 rows
- Extraction mode: `pdf_cells` 3, `catalog_row_fallback` 2
- Approved table cases: 5
- Table row Hit@1/3/5/10: 0.8 / 0.8 / 0.8 / 0.8
- TF027: `needs_human_review=true`, aggregate 제외
- Generation API calls: 0

## Pending track

- 실제 provider 자연어 품질 및 semantic entailment 평가
- TF027 workflow image 사람 검수
- 승인된 image-dependent gold와 vision path
- Table miss MM003의 gold/row-link 분석 및 production table retrieval 재평가

## 다음 단계

- 병원 데이터 외부 전송 정책과 provider를 승인한 뒤 controlled generation을 별도 Live/Mock gate로 평가한다.
- TF027을 사람이 검수한 뒤에만 image track을 시작한다.
- 현재 production retrieval과 validator는 유지한다.

## 중단 조건 여부

- 작업 중단 조건 없음. 승인된 안전 범위를 모두 완료했다.

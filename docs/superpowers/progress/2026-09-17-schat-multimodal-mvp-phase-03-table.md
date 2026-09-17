# SCHAT Multimodal MVP Phase 03 — Structured table evidence

- 상태: 완료
- Production retrieval 반영: 없음
- 실제 generation/vision API 호출: 0회
- Git commit/push: 0회

## 완료 항목

- PDF table의 stable table ID, page, table index, bbox, fingerprint를 구현했다.
- Exact header/row/cell과 가능한 cell bbox를 메모리에서만 보존한다.
- Header-only grid + borderless body는 열 관계를 추측하지 않고 exact catalog row를 단일 행으로 연결하며 `catalog_row_fallback`으로 표시한다.
- Table citation은 document, page, table ID, row index까지 추적한다.
- Markdown renderer는 exact parsed cell만 표시하고 row별 `[Tn]` citation을 붙인다.
- Local lexical table-row search와 별도 Streamlit evaluation UI를 구현했다.
- Safe manifest는 clinical text 없이 ID, bbox, fingerprint, header/row SHA-256, chunk links만 저장한다.

## 구조 결과

- Structured table records: 5
- Pages: 2, 6, 9, 15, 17
- Rows: 33
- `pdf_cells`: 3 records
- `catalog_row_fallback`: 2 records

## 변경 파일

- `tools/structured_table_evidence.py`
- `tools/table_evidence_app.py`
- `tests/test_structured_table_evidence.py`
- `tests/test_table_evidence_app.py`

## 테스트 결과

- Red 확인: module 미구현 `ModuleNotFoundError`
- 실제 문서 경계 확인: page 15가 header-only grid로 감지되어 초기 test 실패
- 안전 수정 후 structured table + local UI: **6 passed**

## 안전 계약

- 표의 누락 column 값을 생성하거나 추론하지 않는다.
- Exact table source text는 runtime local UI에만 표시하고 artifact/Git에는 저장하지 않는다.
- 기존 BM25/RRF/reranker 및 production answer path를 변경하지 않았다.
- 기존 평가에서 table-aware retrieval이 명확한 개선이 아니므로 production retrieval은 유지한다.

## 실행 명령

```powershell
.\.venv\Scripts\python.exe -m streamlit run tools\table_evidence_app.py
```

## Pending track

- Image/vision과 TF027은 pending이다.
- Table answer generation 품질은 provider 승인 전까지 pending이다.

## 다음 단계

- Query evidence-type routing metadata를 TDD로 구현하고 retrieval과 분리된 상태를 검증한다.

## 중단 조건 여부

- 없음. Borderless table은 추론 대신 명시적 fallback으로 안전하게 처리했다.

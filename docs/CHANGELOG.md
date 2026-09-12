# 변경 이력

## 2026-09-12 · 1단계 BM25 기준선 평가

### 변경 파일

- `mvp/bm25_evaluation.py` 신규
- `mvp/diagnostic_ui.py`
- `mvp/admin_ui.py`
- `mvp/app.py`
- `tests/test_bm25_evaluation.py` 신규
- `tests/test_retrieval_diagnostics.py`
- `README.md`
- `docs/00_PROJECT_GOAL.md`
- `docs/01_BM25_PLAN.md`
- `docs/02_BM25_RESULT.md`
- `docs/CHANGELOG.md`

### 변경 내용

- 관리자용 BM25 일괄 기준선 평가 기능 추가
- 기본 5개 질문과 사용자 질문 묶음 지원
- 저장된 등록 chunk만 대상으로 BM25 원시 Top-10 계산
- 질문별 실제 query와 expanded query 기록
- 화면에 질문별 Top-5, 점수, 문서, 페이지, section, chunk ID와 300자 미리보기 표시
- `artifacts/bm25_results.csv`와 `artifacts/bm25_results.json` 저장
- 같은 결과의 관리자 다운로드 지원
- score 0 결과 보존과 검색 성공 제외 안내
- 관리자 권한의 실행 전·후 재검사
- 직원 화면 비노출
- 앱 표시 버전을 `2026.09.12-bm25.1`로 변경

### 변경하지 않은 내용

- `mvp/retrieval.py`의 BM25 수식
- 한국어 tokenizer와 bigram 가중치
- chunk 크기와 overlap
- embedding 모델
- FAISS·pgvector 검색
- RRF와 reranking
- Groq 답변 생성과 출처 검증
- 일반 직원의 채팅 흐름

### 변경 이유

RAG 답변을 평가하기 전에 현재 BM25가 실제 지침의 적절한 근거 chunk를 찾는지 독립적으로 검증하고, 사람이 확인할 수 있는 재현 가능한 결과를 만들기 위해 변경했다.

### 테스트 결과

- BM25 전용 합성 테스트 통과
- 관리자 UI 실행과 직원 비노출 테스트 통과
- CSV 8개 고정 컬럼·UTF-8 BOM·전체 원문 검증 통과
- JSON corpus fingerprint·질문 형태·Top-10 검증 통과
- 관리자 권한과 문서 범위 검증 통과
- LLM 미호출 검증 통과
- `ruff check mvp tests`: 통과
- `pytest -q`: **208 passed, 1 skipped**

실제 병원 지침 데이터가 작업 환경에 없어 실제 Top-5 적합성 평가는 아직 실행하지 않았다.

### 롤백 가능 여부

가능하다.

- 이 단계의 변경 커밋을 되돌리면 BM25 일괄 평가 화면과 결과 저장 기능만 제거된다.
- 기존 질문별 BM25 디버그, hybrid 검색, RAG 채팅과 지침서 데이터는 영향을 받지 않는다.
- `artifacts/` 파일은 검색 인덱스나 운영 DB가 아니므로 별도로 삭제해도 기존 앱 동작에 영향을 주지 않는다.

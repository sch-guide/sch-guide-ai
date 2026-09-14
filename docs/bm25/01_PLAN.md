# SCHAT BM25 검색 성능 검증 계획

- 작성일: 2026-09-12
- 상태: 사용자 승인 대기
- 범위: BM25 검색 단계의 재현 가능한 오프라인 검증
- 제외 범위: 검색 로직 변경, 재색인, RAG/LLM 구현 및 호출, 운영 데이터 변경

## 1. 목적과 승인 경계

현재 SCHAT의 문서 추출·의미 단위 분할·토큰화·BM25 인덱싱이 실제 병원 지침 질문에서 적절한 원문을 상위 결과로 찾는지 검증한다. 점수가 높다는 사실만으로 성공을 선언하지 않고, 사람이 문서명·페이지 또는 위치·섹션·원문을 직접 확인해 질문별 정답 근거 포함 여부를 판정한다.

이 문서는 구현 전 계획이다. 사용자 승인 전에는 검색 코드, 문서 인덱스, 테스트 코드 및 기존 산출물을 수정하지 않는다. BM25 결과가 별도로 승인되기 전에는 RAG 구현이나 LLM 호출로 넘어가지 않는다.

## 2. 현재 구조

### 확인된 프로젝트 상태

- 원본 입력 기본 위치는 `data/`이며 현재 PDF 1개(`실무지침서_진정간호.pdf`)가 확인된다. 병원 원문이므로 파일명 이상의 내용을 계획 문서에 복제하지 않는다.
- 기본 로컬 저장 인덱스 위치는 `data/library/catalog.sqlite3`이나 현재 파일은 없다. 따라서 기존 등록 인덱스를 전제로 한 평가는 바로 실행할 수 없다.
- `docs/PRD.md`, `docs/bm25/`, `docs/rag/`는 점검 시점에 없었다. 이번 승인 전 단계에서는 `docs/bm25/01_PLAN.md`만 새로 만든다.
- `data/`, `artifacts/`, PDF·DOCX·XLSX는 `.gitignore` 대상이다. 원문이 포함된 평가 결과를 공개 저장소에 포함하지 않는다.

### 병존하는 검색 경로

1. `bm25_test.py`
   - `data/` 아래 PDF만 `pypdf.PdfReader`로 읽는다.
   - 페이지별 추출 텍스트를 공백 정규화한 뒤 500자, overlap 100자로 분할한다.
   - 정규식 `[가-힣a-z0-9]+`로 소문자 토큰화한다.
   - `rank_bm25.BM25Okapi`를 사용하고 대화형 질문의 Top 5를 콘솔에 표시한다.
   - 문서명·페이지·원문은 보존하지만 `document_id`, `section_title`, `chunk_id`는 없다.

2. 실제 애플리케이션 경로
   - `mvp.documents.read_document()`가 PDF·DOCX·XLSX를 지원한다.
   - PDF는 `pypdf` 기본 추출 후 `mvp.pdf_layout.enhance_pdf()`를 적용하며 실제 PDF 페이지 번호를 보존한다. 설정 시 OCR 경로가 있으나 현재 기본 설정은 비활성이다.
   - DOCX는 문단·표 위치와 제목 계층을, XLSX는 보이는 시트의 행·셀 주소를 보존한다. Office 문서는 고정 페이지 번호를 추측하지 않는다.
   - `mvp.structure.semantic_blocks()`가 제목·문단·표의 의미 단위를 먼저 만들고, `mvp.library.make_chunks()`가 임베딩 모델 tokenizer 기준 최대 110토큰, overlap 20토큰으로 큰 단위만 추가 분할한다. 표는 헤더를 보존하며 별도 예산과 overlap을 적용한다.
   - 각 chunk는 문서 ID·문서명·페이지 또는 위치·제목·섹션·순서·원문·정규화 원문·부모 및 앞뒤 chunk ID를 보존한다.
   - `mvp.retrieval.BM25Index`가 권한 범위의 전체 chunk 본문과 섹션을 자체 토큰화하여 색인한다. 이 구현은 `rank-bm25` 패키지가 아니라 프로젝트 자체 BM25 계산이다.
   - 로컬 검색은 BM25 Top 40과 FAISS Top 40을 RRF로 결합한다. 운영 검색은 허용된 전체 chunk의 로컬 BM25 Top 40과 Supabase pgvector 후보를 결합한다.
   - `mvp.search_trace`는 BM25 Top 10, vector Top 10, RRF Top 10, 재정렬 Top 5와 최종 결과를 기록할 수 있다.

### 기존 테스트와 진단

- `tests/test_mvp_chat.py`, `tests/test_mvp_upgrade.py`, `tests/test_rag_contract.py`에는 BM25가 dense Top 40 밖의 근거를 복구하는지, 한국어·영문 약어를 구분하는지, 권한 범위를 지키는지 확인하는 합성 테스트가 있다.
- `tests/test_retrieval_diagnostics.py`는 실제 검색 trace에 BM25 Top 10과 메타데이터가 기록되는지 확인한다.
- `mvp.evaluate_cases`는 합성 코퍼스 기반 전체 검색 평가이며 BM25 단독 평가는 아니다.
- `mvp.evaluate`는 등록된 로컬 인덱스를 대상으로 혼합 검색 recall과 시간을 측정하지만 현재 로컬 인덱스가 없고, 결과에 질문·원문을 저장하지 않는다.
- 현재 질문별 BM25 단독 결과를 CSV·JSON·HTML로 일괄 저장하고 수동 판정하는 전용 경로는 없다.

## 3. BM25 적용 대상 데이터

### 1차 검증 범위

- `data/`에 있는 실제 병원 지침 파일 중 사용자가 검증 대상으로 승인한 파일만 사용한다.
- 현재 확인된 `실무지침서_진정간호.pdf`를 최초 대상으로 제안한다.
- 파일을 수정하거나 기존 앱 인덱스에 등록하지 않고, 평가 프로세스 메모리에서 읽기 전용으로 추출·청킹한다.
- 환자 식별정보 탐지 시 해당 문서 처리를 중단하고 원문을 결과나 오류 메시지에 복제하지 않는다.

### 확장 범위

- 추가 PDF·DOCX·XLSX는 사용자가 대상 목록과 취급 권한을 확인한 뒤 같은 방식으로 포함한다.
- `.doc`, `.xls`, HWP 등 현재 `read_document()`가 지원하지 않는 형식은 이번 평가 대상에서 제외한다.

## 4. 문서 추출 방식

- 평가의 주 경로는 실제 앱과 동일한 `mvp.documents.read_document()`를 재사용한다.
- PDF는 페이지 번호, 추출 성공 여부, 문자 수 및 원문을 유지한다.
- 이미지가 있고 추출 문자가 80자 미만인 페이지는 OCR 후보로만 표시하며 자동으로 스캔 문서라고 단정하지 않는다.
- OCR 비활성 결과와 OCR 활성 결과가 모두 필요한 경우 별도 실험군으로 구분한다. 사용자 승인 없이 OCR 결과로 기준 코퍼스를 교체하지 않는다.
- 추출 실패 페이지와 빈 페이지도 통계에 포함하여 페이지 번호가 당겨지지 않게 한다.
- DOCX/XLSX를 추가할 경우 페이지 대신 문단·표 또는 시트·셀 위치를 사용한다.

## 5. Chunking 방식

### 기준 방식

- 실제 앱의 `semantic_blocks()`와 `make_chunks()`를 그대로 사용한다.
- 의미 단위: 제목, 문단, 표를 우선 분리한다.
- 일반 chunk: 임베딩 tokenizer 기준 최대 110토큰, overlap 20토큰이다.
- 표 chunk: 헤더를 반복 보존하고, 헤더 길이를 제외한 예산으로 나누며 overlap은 최대 12토큰이다.
- 빈 페이지·추출 실패 페이지는 chunk로 만들지 않되 실패 통계에는 포함한다.
- 문서당 1~5,000개 chunk 제한과 128토큰 임베딩 입력 제한을 확인한다.

### 비교 기준

- `bm25_test.py`의 500자/100자 overlap 방식은 실제 앱과 다른 독립 기준선으로 기록한다.
- 두 방식의 결과를 혼합하지 않는다. 비교를 수행한다면 동일 질문·동일 원본에 대해 각각 별도 결과로 표시한다.
- chunking 변경은 이번 검증 범위에 포함하지 않는다. 실패 원인이 chunk 경계로 확인되면 결과 문서에 개선 후보로만 기록한다.

## 6. Tokenization 방식

### 실제 앱 기준

- `mvp.retrieval.lexical_tokens()`를 사용한다.
- 유니코드 NFC 및 공백 정규화 후 영문·숫자·하이픈 토큰과 2자 이상 한글 토큰을 추출한다.
- 한글 조사의 제한적 제거, 한글 2-gram 보조 토큰, 의료 개체 alias용 `entity:` 토큰을 사용한다.
- 한글 2-gram은 전체 단어나 약어보다 낮은 가중치(0.25)를 적용한다.
- 원문은 변경하지 않고 색인 및 질의용 표현만 정규화한다.

### 검증할 표현 차이

- `진정간호`와 `진정 간호`
- 한글 조사 포함/제거
- 한글·영문 혼합 및 대소문자
- 등록된 약어와 전체 명칭
- `irrigation`과 `세척`처럼 코드에 명시된 제한적 확장
- 짧은 약어의 부분 일치 방지
- 영문 철자 교정이 원래 의도를 잘못 바꾸지 않는지

`rank_bm25`는 설치되어 있고 `bm25_test.py`에서만 사용된다. 실제 앱 성능 판정은 자체 `BM25Index`를 주 평가 대상으로 한다. 사용자 승인에 따라 동일 질문·동일 chunk·동일 `lexical_tokens()` 결과를 `rank_bm25.BM25Okapi`에도 입력해 baseline을 필수 산출한다. 두 엔진의 순위와 지표는 분리하며 합산 순위를 만들지 않는다.

## 7. BM25 인덱스 생성 방식

- 승인된 문서를 앱과 같은 방식으로 추출·청킹한 뒤 전체 대상 chunk로 `mvp.retrieval.BM25Index`를 메모리에 생성한다.
- 문서별·전체 chunk 수, 빈 chunk 수, 완전 중복 원문 수, 지나치게 짧은 본문 수, 최대 토큰 길이를 기록한다.
- BM25의 입력은 `chunk.text + " " + chunk.section`으로 고정한다.
- 권한 범위 또는 문서 선택 범위 밖의 chunk가 인덱스에 들어가지 않는지 확인한다.
- 평가 실행 중 기존 `catalog.sqlite3`, 저장 원본, 앱 캐시 또는 운영 Supabase 데이터를 수정하지 않는다.
- 재사용이 필요하면 실행 단위 메모리 캐시만 사용하고, 영속 캐시 추가는 별도 승인 대상으로 남긴다.

## 8. 검색 Query 처리 방식

질문마다 다음 세 값을 구분해 저장한다.

1. `original_query`: 사용자가 작성한 원문 질문
2. `normalized_query`: NFKC·공백 정규화와 제한적 철자 보정을 거친 실제 질의
3. `expanded_query`: `plan_query()`가 명시된 alias와 제한적 동의 표현을 추가한 BM25 입력

BM25 단독 평가는 `expanded_query`를 `BM25Index.scores()`에 전달한다. 후속 질문 문맥, 문서명 지정, 비교·종합 질문은 `QueryPlan`의 선택 문서와 확장 결과까지 기록한다. 자동 확장이 검색 품질을 올렸는지 또는 잘못된 후보를 끌어왔는지는 원문 질문 기준과 확장 질문 기준을 나누어 비교한다.

## 9. Top-k 설정

- 모든 질문에서 `SCHAT BM25Index Top 10`과 `rank-bm25 Top 10`을 점수와 함께 별도 저장한다.
- Top-1, Top-3, Top-5, Top-10 포함 여부를 각각 판정한다.
- 점수가 0인 결과도 상위 10개 구성에 필요하면 삭제하지 않고 `zero_score: true`로 표시한다.
- 동점은 원래 chunk 순서를 보조 기준으로 사용해 재현 가능하게 유지한다.
- BM25 점수는 정답 확률이 아니며 서로 다른 코퍼스 간 절대값 비교에 사용하지 않는다.

## 10. 결과 저장 형식

승인 후 다음 디렉터리에만 평가 산출물을 생성한다.

`artifacts/2026-09-12_bm25-evaluation/`

### `bm25_results.csv`

질문·순위별 한 행으로 저장한다.

- `query_id`
- `original_query`
- `normalized_query`
- `expanded_query`
- `rank`
- `bm25_score`
- `document_id`
- `document_name`
- `page_number`
- `location`
- `section_title`
- `chunk_id`
- `chunk_text`
- `source_type`
- `engine`
- `relevance_label`
- `failure_type`
- `review_note`

두 엔진을 파일 수준에서도 분리해 확인할 수 있도록 같은 열 구조의 `schat_bm25_top10.csv`와 `rank_bm25_top10.csv`도 생성한다. `bm25_results.csv`는 모든 행에 `engine`을 명시한 전체 내보내기이며 합산 순위를 의미하지 않는다.

### `bm25_results.json`

- 실행 설정, 코드·chunk 버전, 입력 문서 해시, 추출 통계, 인덱스 통계, 질문별 Top 10 전체를 계층 구조로 저장한다.
- 검색 실패, 추출 실패 및 점수 0 결과를 생략하지 않는다.

### `review.html`

- 질문 선택, Top 10 목록, 점수·문서명·페이지/위치·섹션·chunk 원문을 표시한다.
- 동일 질문의 `SCHAT BM25Index Top 10`과 `rank-bm25 Top 10`을 좌우에 나란히 표시한다. 두 결과를 합치거나 하나의 순위로 재정렬하지 않는다.
- 적절함/부분 적절함/부적절함/판단 보류 표시와 검수 메모를 지원한다.
- 문서·점수 필터, 점수 정렬, 원문 펼치기 및 질문 간 비교를 제공한다.
- 로컬에서만 열 수 있는 자체 포함 HTML로 만들고 외부 CDN·분석 도구·네트워크 요청을 사용하지 않는다.
- 수동 판정값은 브라우저에서 JSON으로 내려받아 보존할 수 있게 하되, 원문 포함 경고를 명시한다.

## 11. 테스트 질문

### 최초 후보

실제 원문 정답을 아직 판정하지 않았으므로 아래 질문은 평가 후보이며, 답을 추측하거나 계획서에 작성하지 않는다.

1. 진정간호 목적은?
2. 진정간호 절차는?
3. 진정 전 준비사항은?
4. 진정 간호의 목적은 무엇인가요?
5. 진정간호 주의사항은?

### 변형 질문군

- 띄어쓰기: `진정간호` / `진정 간호`
- 조사와 문장형: 핵심어형 / 자연어 질문형
- 항목 구분: 목적 / 준비 / 절차 / 주의사항
- 문서에 실제로 없는 항목 또는 외부 주제의 음성 대조군

평가 실행 전에 사람이 원문을 확인해 각 질문의 기대 문서·페이지/위치·필수 원문 구절을 별도 정답표로 확정한다. 지침에 없는 질문은 “정답 없음”으로 표시하며 임의의 의료 답변을 만들지 않는다. 문서 주제와 맞지 않는 CRE, PCN, 반코마이신, thoracentesis 질문은 해당 지침 파일에 실제 근거가 확인되지 않는 한 양성 질문으로 사용하지 않는다.

## 12. 평가 기준

### Retrieval quality

- 질문별 Hit@1, Hit@3, Hit@5, Hit@10
- 전체 Recall@1, Recall@3, Recall@5, Recall@10
- 첫 관련 결과의 평균 역순위(MRR)
- 정답 없음 질문에서 양성 근거를 반환하지 않는 비율
- 검색 결과 없음과 점수 0 결과의 구분

정답 판정은 최소 `document_name`과 페이지/위치가 일치하고, 지정한 필수 원문 구절이 chunk에 포함되는지를 기준으로 한다. 여러 chunk가 함께 있어야 완성되는 근거는 각 필수 chunk를 별도로 표시한다.

### Metadata quality

- 문서명 정확성
- PDF 실제 페이지 번호 정확성 또는 Office 위치 정확성
- 섹션 제목 정확성
- chunk 원문이 추출 원문과 일치하는지
- 문서 ID·chunk ID의 유일성과 앞뒤/부모 연결의 유효성

### Corpus/index quality

- 입력 문서 수, 총 페이지/단위 수, 추출 성공·실패 수
- 전체·문서별 chunk 수
- 빈 chunk, 중복 chunk, 지나치게 짧거나 긴 chunk
- BM25 인덱스 크기와 실제 대상 chunk 수의 일치

### 회귀 및 안전 기준

- 평가 도구 단위 테스트
- 기존 `pytest`와 `ruff` 검사
- 기존 문서·인덱스·운영 저장소가 변경되지 않았는지 전후 확인
- LLM과 외부 네트워크가 호출되지 않았는지 확인

## 13. 예상 문제점과 실패 분류

| 분류 | 예상 문제 | 확인 방법 |
|---|---|---|
| 문서 추출 | 스캔/이미지 PDF, 표·다단 읽기 순서, 누락 페이지 | 페이지별 문자 수·이미지 수·원본 육안 대조 |
| Chunking | 제목과 본문 분리, 답이 경계에서 잘림, overlap 중복 | 부모/인접 ID와 원문 범위 비교 |
| Tokenizer | 합성어·띄어쓰기·조사·영문 약어 차이 | 질의 변형별 토큰과 순위 비교 |
| Vocabulary | 원문과 질문의 표현 불일치 | 원문/확장 query별 결과 비교 |
| Query planning | 철자 보정·alias 확장이 잘못된 주제를 추가 | original/normalized/expanded query 비교 |
| Metadata | 페이지·섹션·위치 누락 또는 잘못된 연결 | 추출 단위와 결과 행 교차 검증 |
| Index scope | 선택하지 않은 문서 포함, 전체 chunk 누락 | 대상 ID와 indexed chunk ID 집합 비교 |
| Ranking | 짧은 표제나 중복 chunk가 상위 점유 | Top 10 원문과 중복 서명 확인 |
| Evaluation | 정답표가 불완전하거나 검수자 판단 불일치 | 판단 보류 허용, 근거 구절과 검수 메모 보존 |

실패 결과는 `extraction_failure`, `ocr_candidate`, `chunk_boundary`, `tokenizer_mismatch`, `vocabulary_mismatch`, `query_expansion`, `metadata_missing`, `index_scope`, `duplicate_chunk`, `no_source_in_guideline`, `needs_review` 중 하나 이상으로 기록한다.

## 14. 승인 후 수정 예정 파일

아래 목록은 계획이며 아직 수정하지 않는다.

- `bm25_test.py`
  - 가능하면 기존 대화형 실험 코드를 유지한다.
  - 평가 공용 로직을 새 모듈로 분리할 필요가 확인되는 경우에만 최소 수정한다.
- `tests/test_bm25_evaluation.py`
  - 신규 평가 코드의 직렬화, Top-k, 동점, 실패 보존, 메타데이터 및 네트워크 미사용을 검증한다.

실제 앱 검색 파일인 `mvp/retrieval.py`, `mvp/documents.py`, `mvp/structure.py`, `mvp/library.py`, `mvp/query.py`는 이번 검증에서 원칙적으로 수정하지 않는다. 검증으로 결함이 확인되더라도 먼저 `docs/bm25/02_RESULT.md`에 근거와 개선안을 기록하고 별도 승인을 받는다.

## 15. 승인 후 새로 생성할 파일

- `tools/bm25_evaluate.py`: 실제 앱의 추출·청킹·질의 계획·BM25 구현을 재사용하는 읽기 전용 배치 평가 CLI
- `tests/test_bm25_evaluation.py`: 평가 도구 테스트
- `docs/bm25/02_RESULT.md`: 검증 결과와 실패 원인, 개선 필요 여부, RAG 진행 권고
- `artifacts/2026-09-12_bm25-evaluation/bm25_results.csv`
- `artifacts/2026-09-12_bm25-evaluation/bm25_results.json`
- `artifacts/2026-09-12_bm25-evaluation/review.html`
- 필요 시 `artifacts/2026-09-12_bm25-evaluation/relevance_labels.json`: HTML에서 내려받은 수동 판정

테스트 질문과 정답표를 저장할 별도 파일의 경로와 원문 포함 범위는 실제 문서 검수 후 결정하며, 사용자 승인 없이 공개 추적 파일로 만들지 않는다.

## 16. 실행 순서

사용자 승인 후 다음 순서로 진행한다.

1. 평가 대상 파일과 질문 목록을 확정한다.
2. 원본을 읽기 전용으로 추출하고 페이지/위치별 추출 품질을 기록한다.
3. 실제 앱 방식으로 의미 단위와 chunk를 생성하고 메타데이터를 검증한다.
4. 실제 앱 tokenizer와 `BM25Index`로 전체 대상 chunk를 색인한다.
5. 질문별 original/normalized/expanded query의 Top 10을 산출한다.
6. CSV·JSON·자체 포함 HTML을 생성한다.
7. 단위 테스트, 기존 회귀 테스트 및 정적 검사를 실행한다.
8. 사용자가 HTML에서 관련성을 직접 검수한다.
9. 검수 결과로 지표와 실패 유형을 확정해 `docs/bm25/02_RESULT.md`를 작성한다.
10. BM25 승인, tokenizer/chunking 개선 또는 추가 질문 필요 여부를 확인한다.
11. 별도 승인 전에는 검색 로직 수정이나 RAG 단계로 진행하지 않는다.

## 17. 롤백 방법

- 평가 구현 전에는 현재 문서 하나만 추가된 상태이므로 `docs/bm25/01_PLAN.md`를 제거하면 계획 단계 변경을 되돌릴 수 있다.
- 승인 후 생성할 평가 코드는 새 파일 중심으로 격리한다. 롤백 시 이번 작업에서 새로 만든 `tools/bm25_evaluate.py`, `tests/test_bm25_evaluation.py`와 날짜별 평가 산출물만 제거한다.
- 기존 앱 코드나 데이터가 승인된 변경 범위에 포함되지 않았으면 수정·삭제하지 않는다.
- 평가 중에는 `data/library/catalog.sqlite3`, 원본 문서, Supabase 데이터 및 기존 artifacts를 변경하지 않으므로 데이터 복구 작업이 필요하지 않게 한다.
- 향후 검색 로직 개선이 별도로 승인되면 변경 전 테스트 결과와 diff를 보존하고, 해당 승인 범위의 커밋 또는 파일 단위로만 되돌린다.

## 18. 승인 요청 사항

다음 단계로 진행하려면 아래 범위를 승인받아야 한다.

- 실제 앱의 자체 `BM25Index`를 주 평가 대상으로 사용할 것
- 현재 확인된 진정간호 PDF를 읽기 전용 평가 대상으로 사용할 것
- 최초 후보 질문과 원문 검수로 확정할 추가 질문을 사용할 것
- `tools/bm25_evaluate.py`, 테스트 및 날짜별 CSV·JSON·HTML 산출물을 새로 만들 것
- BM25 검증 중에는 LLM을 호출하지 않고 RAG 구현으로 넘어가지 않을 것

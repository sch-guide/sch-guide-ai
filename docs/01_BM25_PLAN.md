# 1단계 BM25 검색 데이터 추출 및 검증 계획

> 상태: **사용자 승인 대기**
> 기준 코드: GitHub `main` 커밋 `8c44851`
> 구현 여부: **구현하지 않음**
> LLM 호출 여부: **호출하지 않음**

## 0. 현재 코드 분석 결론

현재 앱은 BM25가 없는 상태가 아니다. `mvp/retrieval.py`에 BM25가 구현되어 있고, 로컬에서는 FAISS와 BM25를, 운영에서는 Supabase pgvector와 앱 서버 메모리의 BM25를 함께 사용한다.

관리자 질문 아래에 BM25 Top-10 원시 결과와 CSV를 표시하는 기능도 이미 있다. 다만 다음 기준선이 아직 없다.

- 정해진 질문 묶음을 같은 문서 버전에서 반복 실행하는 기능
- 질문별 BM25 결과를 하나의 CSV와 JSON으로 모으는 기능
- 실제 문서 수·chunk 수·검색 조건을 함께 기록하는 기능
- 사람이 Top-5 관련성을 판정하고 실패 원인을 기록하는 절차
- 결과를 `02_BM25_RESULT.md`로 정리하는 절차

1단계에서는 기존 hybrid 검색이나 LLM 로직을 변경하지 않고, 현재 BM25의 실제 성능을 독립적으로 측정한다. 기준 결과를 확인하기 전에 tokenizer, 가중치, chunk 크기를 임의로 조정하지 않는다.

## 1. 현재 문서 저장 구조

### 로컬 실행

`mvp/repository.py`의 `Repository`가 다음을 관리한다.

- 원본 파일: `source_store`로 분리 저장
- 문서 목록: SQLite `documents` 테이블
- chunk: SQLite `chunks` 테이블의 JSON payload
- embedding: 같은 `chunks` 테이블의 float32 vector blob
- 문서 버전: `corpus` 테이블
- 문서 상태: pending, error, ready, replaced, deleted

BM25 인덱스는 디스크에 별도 파일로 저장하지 않는다. 검색 가능한 chunk 스냅샷에서 메모리 인덱스를 만들고 재사용한다.

### 운영 실행

운영에서는 다음 구조를 사용한다.

- 원본: 비공개 Supabase Storage
- 문서 metadata: `guide_documents`
- chunk와 embedding: `guide_chunks`
- 직원·관리자 권한: Supabase Auth, `guide_profiles`, RLS
- 벡터 검색: `guide_search` RPC와 pgvector
- BM25: 로그인한 사용자가 읽을 수 있는 전체 chunk를 앱 서버 메모리에 올려 생성

운영 BM25 캐시는 사용자 ID와 문서 ID, file hash, indexed date, updated date를 기준으로 무효화한다. 문서별 chunk는 500개씩 조회하며 현재 코드상 문서당 최대 5,000개까지 읽는다.

### 현재 분석의 제한

현재 코드 사본에는 실제 병원 지침 원본과 운영 DB 내용이 없다. E: 드라이브도 연결되지 않은 상태이므로 이번 계획 단계에서 실제 문서 수와 chunk 수는 산정하지 않는다. 해당 수치는 승인 후 실제 평가를 실행할 때만 기록한다.

## 2. PDF·Word·Excel 텍스트 추출 방식

### PDF

관련 파일: `mvp/documents.py`, `mvp/pdf_layout.py`

- `pypdf`를 strict 모드로 사용해 페이지별 텍스트를 추출한다.
- PDF 첫 장을 `page_number=1`로 저장한다.
- 추출 실패 페이지도 빈 페이지로 남겨 이후 페이지 번호가 밀리지 않게 한다.
- `pdfplumber`로 표를 찾아 행과 열의 관계를 ` | ` 형식으로 보존한다.
- PDF 책갈피가 있으면 section 후보로 사용한다.
- 이미지가 있고 텍스트가 80자 미만인 페이지는 선택적으로 로컬 Tesseract OCR 대상이 된다.
- OCR은 한국어와 영어를 사용하며 외부 OCR API로 문서를 보내지 않는다.
- 암호화 PDF, 손상 파일, 20MB 초과 파일, 1,000페이지 초과 파일은 거절한다.

### Word

- `python-docx`로 문단과 표를 원래 순서대로 읽는다.
- Heading/제목 스타일을 계층형 section으로 저장한다.
- 표는 행별로 ` | ` 형식으로 저장한다.
- Word의 고정 페이지 번호는 추측하지 않고 `문단 N`, `표 N` 위치를 사용한다.
- 머리말, 각주, 텍스트 상자, 이미지 속 글은 누락될 수 있다.

### Excel

- `openpyxl`의 read-only, data-only 모드로 보이는 시트의 저장된 셀 값을 읽는다.
- 행을 기본 검색 단위로 만들고 `시트명!A행:마지막열행` 위치를 저장한다.
- 첫 데이터 행을 열 제목 후보로 다음 행에 함께 넣는다.
- 인쇄 페이지는 추측하지 않고 시트명과 셀 주소를 출처로 사용한다.
- 계산 결과가 저장되지 않은 수식, 지나치게 큰 시트, 매크로 문서는 거절한다.

## 3. 현재 chunking 방식

관련 파일: `mvp/structure.py`, `mvp/library.py`

1. 페이지, Word 문단·표, Excel 행의 위치를 먼저 보존한다.
2. `semantic_blocks()`가 빈 줄, 표, 번호 제목, 목적·준비물·주의사항·절차 같은 제목을 기준으로 의미 블록을 나눈다.
3. 각 의미 블록에 section과 parent ID를 부여한다.
4. 의미 블록이 임베딩 모델 입력 한도를 넘을 때만 `RecursiveCharacterTextSplitter`로 다시 나눈다.
5. 일반 chunk는 110 tokenizer token, overlap 20 token을 사용한다.
6. 분리 우선순위는 빈 줄, 줄바꿈, 문장 경계, 공백, 문자 순이다.
7. 표가 나뉘면 최대 40 token의 표 머리행을 후속 chunk에도 붙인다.
8. 각 chunk에 앞·뒤 chunk ID와 다음 metadata를 보존한다.

- document ID
- document name
- page number 또는 문단·표·셀 위치
- title
- section
- updated date
- source type
- chunk index
- parent ID
- chunk text

### BM25 검증 시 원칙

1단계 기준선은 현재 저장된 chunk를 그대로 사용한다. 평가를 위해 chunk를 새로 분할하지 않는다. 재추출한 임시 chunk와 운영 검색에 실제 사용되는 저장 chunk를 섞지 않는다.

## 4. 현재 한국어 tokenization 방식

관련 함수: `mvp/retrieval.py::lexical_tokens`

현재 형태소 분석기 없이 다음 규칙을 사용한다.

1. Unicode NFC 정규화와 소문자 변환
2. 영문은 `[a-z][a-z0-9-]*` 단위로 추출
3. 한글은 연속된 2글자 이상의 문자열로 추출
4. 일부 조사와 어미를 제한적으로 제거
5. 한글 문자열에서 2글자 character bigram 생성
6. bigram token에는 `ko:` 접두사를 붙이고 BM25 점수 가중치를 0.25로 낮춤
7. PCN, CRE, CPE, VRE 등 등록된 의료 용어와 별칭은 `entity:` token으로 추가
8. 질문은 `plan_query()`에서 NFKC 정규화, 제한적 영문 오타 교정, 세척/irrigation·격리/isolation 같은 명시적 확장을 거친 뒤 BM25에 전달

### 장점

- 별도 한국어 형태소 분석기 설치 없이 빠르다.
- 띄어쓰기 차이와 한글 복합어 일부를 bigram으로 보완한다.
- 영문 약어를 부분 문자열로 잘못 매칭할 가능성을 줄인다.
- 서버에서 동작하며 외부 API 비용이 없다.

### 검증이 필요한 한계

- 의료 복합명사와 조사 분리가 완전하지 않다.
- 2글자 bigram이 짧은 공통 표현에 점수를 줄 수 있다.
- 등록되지 않은 한글·영문 동의어는 연결되지 않는다.
- 문서명과 title은 BM25 본문 인덱스에 직접 포함되지 않고 현재는 chunk text와 section이 중심이다.
- 서로 다른 corpus 크기의 BM25 절대 점수는 직접 비교할 수 없다.

1단계에서는 이 규칙을 먼저 평가한다. 형태소 분석기 도입이나 가중치 변경은 결과 문서에서 실패 원인이 확인된 뒤 별도 승인 대상으로 제안한다.

## 5. 현재 BM25 인덱스 생성 방식

관련 클래스: `mvp/retrieval.py::BM25Index`

- 인덱스 입력: 검색 권한이 있는 chunk의 `chunk.text + chunk.section`
- 자료구조: token별 `chunk_index -> term frequency` postings dictionary
- 문서 길이: 한 chunk의 전체 lexical token 수
- 평균 길이: 검색 대상 chunk들의 평균 token 수
- IDF: `log(1 + (N - df + 0.5) / (df + 0.5))`
- `k1=1.5`
- `b=0.75`
- 한글 bigram weight: 0.25
- 질문의 중복 token은 한 번만 점수 계산
- 정렬: 점수 내림차순, 동점이면 원래 chunk 순서를 유지하는 stable sort

로컬은 선택된 chunk 집합이 바뀌면 FAISS와 BM25 인덱스를 함께 다시 만든다. 운영은 권한 있는 전체 chunk와 문서 버전을 기준으로 BM25를 메모리에 캐시한다.

## 6. 질문 입력 방식

초기 평가 질문은 다음 5개를 그대로 사용한다.

1. 진정간호 목적은?
2. 진정간호 절차는?
3. 진정 전 준비사항은?
4. CRE 격리 기준은?
5. PCN irrigation 방법은?

각 질문에 대해 다음 세 문자열을 구분해 기록한다.

- `original_query`: 사람이 입력한 원문
- `actual_query`: 후속 질문 문맥 등이 반영되어 실제 검색에 사용된 query
- `expanded_query`: 별칭과 명시적 동의어를 추가해 BM25 점수 계산에 사용한 문자열

사용자 요청 CSV의 `query`에는 `actual_query`를 저장한다. JSON에는 세 문자열을 모두 저장해 query normalization이 결과에 미친 영향을 확인한다.

추가 질문은 관리자 화면에서 입력할 수 있게 하되, 기본 5개 질문 결과를 삭제하거나 덮어쓰지 않는다.

## 7. Top-k 검색 방식

### 기준선 검색

1. 승인된 문서의 저장 chunk 전체를 대상으로 BM25 인덱스를 만든다.
2. 질문마다 모든 chunk의 BM25 score를 계산한다.
3. score 내림차순으로 stable sort한다.
4. 원시 Top-10을 저장한다.
5. 관리자는 그중 Top-5를 직접 검토한다.
6. score가 0인 결과는 원시 순위 확인을 위해 보존하되 **검색 성공으로 판정하지 않는다**.
7. BM25 기준선에는 vector score, RRF score, rerank score를 섞지 않는다.
8. 이 단계에서는 LLM을 호출하지 않는다.

현재 hybrid 검색은 BM25 양수 후보 Top-40과 dense Top-40을 RRF로 합친다. 이 값은 2단계 비교에만 사용하고 1단계 BM25 기준 점수에는 포함하지 않는다.

## 8. 출력 데이터 형식

### `artifacts/bm25_results.csv`

CSV는 한 행에 질문 하나가 아니라 **질문과 검색 chunk 한 개**를 저장한다. UTF-8 BOM 형식으로 저장해 Windows Excel에서 한글이 깨지지 않게 한다.

필수 컬럼과 순서는 다음과 같다.

| 컬럼 | 내용 |
|---|---|
| query | 실제 검색 query |
| rank | 질문 안에서의 BM25 순위, 1부터 시작 |
| bm25_score | BM25 원시 점수 |
| document_name | 문서명 |
| page_number | PDF 페이지, Word/Excel은 비어 있을 수 있음 |
| section_title | chunk section |
| chunk_id | 저장 chunk의 고유 ID |
| chunk_text | 축약하지 않은 전체 chunk 원문 |

Top-10까지 저장한다. score 0도 원시 결과 검토를 위해 남기고 결과 문서에서 별도로 표시한다.

### `artifacts/bm25_results.json`

JSON에는 CSV 행 외에 재현에 필요한 정보를 함께 저장한다.

- 실행 시각
- corpus revision 또는 문서 버전 fingerprint
- 총 문서 수
- 총 chunk 수
- 문서별 chunk 수
- tokenizer 규칙 버전
- BM25 파라미터
- original, actual, expanded query
- 질문별 Top-10
- 검색 소요 시간
- 오류와 제외 사유

### 보안

두 결과 파일에는 병원 지침 원문이 포함될 수 있다. 현재 `.gitignore`의 `artifacts/` 제외 규칙을 유지하고 GitHub에 커밋하지 않는다.

## 9. 평가 방법

### 사람 검토 기준

관리자가 질문별 Top-5 결과를 다음 기준으로 판정한다.

- **적합**: 질문에 직접 답할 수 있는 근거가 chunk에 있음
- **부분 적합**: 주제는 맞지만 질문에 필요한 내용이 불완전함
- **부적합**: 질문과 관련 없는 chunk
- **추출 문제**: 원문에는 있으나 텍스트·표·section 추출이 잘못됨
- **미등록**: 평가 대상 지침서 자체가 등록되지 않음

### 질문별 성공 기준

- Top-1이 적합하면 Top-1 성공
- Top-5 안에 적합 chunk가 하나 이상 있으면 Top-5 검색 성공
- score 0인 chunk는 적합으로 보지 않음
- 관련 지침이 실제로 등록되지 않은 질문은 검색 실패가 아니라 corpus 미등록으로 구분
- 정답 문서가 등록되어 있는데 Top-5에서 찾지 못하면 BM25 실패

### 기록할 값

- 총 문서 수
- 총 chunk 수
- 테스트 질문 수
- 질문별 Top-5 문서·페이지·section·점수·원문 일부
- Top-1 성공 질문 수
- Top-5 성공 질문 수
- 실패 질문과 실패 유형
- 질문별 검색 시간
- 관리자의 판정 근거

정답 chunk ID를 미리 지정할 수 있으면 Top-5 Recall을 계산한다. 정답 목록이 없는 경우 Recall이라는 이름으로 추정값을 만들지 않고, 먼저 사람 판정을 완료한다.

## 10. 예상되는 문제점

| 문제 | 확인 방법 | 1단계 처리 |
|---|---|---|
| 실제 평가 문서에 질문 주제가 없음 | 등록 문서 목록과 원문 확인 | 미등록으로 분류 |
| 스캔 PDF 또는 표의 추출 누락 | 페이지 문자 수와 원문 대조 | 추출 문제로 기록 |
| PDF 표시 쪽수와 파일 페이지 번호 차이 | 원본 페이지 직접 확인 | 두 번호의 차이를 결과에 명시 |
| Word·Excel에 페이지 번호가 없음 | location metadata 확인 | 문단·표·셀 위치로 검토 |
| section 제목 추출 오류 | Top-5 원문과 section 대조 | chunking 문제로 기록 |
| 한글 bigram의 공통어 과대 점수 | 관련 없는 Top 결과의 token 확인 | tokenizer 문제로 기록 |
| 약어·영문·한글 동의어 누락 | CRE, PCN irrigation 결과 비교 | alias 문제로 기록 |
| 문서명에만 있는 주제 검색 실패 | chunk text·section과 문서명 비교 | 인덱스 필드 문제로 기록 |
| score 0 동점 결과가 Top-10에 나타남 | score 확인 | 성공으로 판정하지 않음 |
| corpus 변경으로 점수 변동 | revision/fingerprint 비교 | 결과 실행 단위를 고정 |
| Supabase 전체 chunk 조회 지연 | 첫 실행과 캐시 실행 시간 비교 | 성능 문제로 기록 |
| 결과 파일의 원문 노출 | Git 상태와 저장 경로 확인 | artifacts 외 저장 금지 |

## 11. 승인 후 수정 예정 파일

다음 목록은 계획이며 아직 수정하지 않는다.

| 파일 | 예정 변경 |
|---|---|
| `mvp/bm25_evaluation.py` | BM25 전용 batch 평가, CSV·JSON 생성, 통계 수집 |
| `mvp/diagnostic_ui.py` | 관리자용 기본 5개 질문 실행, Top-5 검토와 결과 다운로드 화면 |
| `mvp/admin_ui.py` | 기존 관리자 진단 화면에 BM25 batch 평가 진입점 연결 |
| `mvp/search_trace.py` | 필요한 경우 original/actual/expanded query와 실행 metadata를 명확히 분리 |
| `README.md` | BM25 평가 실행 방법과 보안 주의사항 추가 |
| `tests/test_bm25_evaluation.py` | Top-k, 동점, 0점, CSV/JSON, 권한, LLM 미호출 검증 |

기준선 실행 전에는 `mvp/retrieval.py`의 tokenizer와 BM25 수식을 변경하지 않는다. 평가 결과에서 문제가 확인되면 수정안과 예상 영향을 `02_BM25_RESULT.md`에 제안하고 별도 승인을 받는다.

## 12. 승인 후 새로 생성할 파일

| 파일 | 생성 시점과 내용 |
|---|---|
| `mvp/bm25_evaluation.py` | 승인 후 구현하는 BM25 전용 평가 모듈 |
| `tests/test_bm25_evaluation.py` | 합성 문서 기반 자동 테스트 |
| `artifacts/bm25_results.csv` | 실제 등록 문서 평가 후 생성, Git 제외 |
| `artifacts/bm25_results.json` | 실제 등록 문서 평가 후 생성, Git 제외 |
| `docs/02_BM25_RESULT.md` | 실제 결과와 사람 검토를 마친 뒤 작성 |
| `docs/CHANGELOG.md` | 첫 코드 변경이 발생한 시점에 생성 |

필요한 경우 기본 질문과 기대 문서를 재현 가능하게 관리하는 평가 설정 파일을 추가할 수 있다. 실제 병원 문서명이나 원문을 공개 저장소에 넣어야 하는 구조라면 생성하지 않고 로컬 설정으로 분리한다.

## 13. 구현·검증 순서

사용자 승인 후에도 1단계 범위만 진행한다.

1. 현재 corpus revision, 문서 목록, 저장 chunk 수를 관리자 권한으로 읽는다.
2. 기존 `BM25Index`를 그대로 사용하는 평가 모듈을 만든다.
3. 기본 5개 질문을 normalization한다.
4. 각 질문의 BM25 원시 Top-10을 계산한다.
5. CSV와 JSON을 `artifacts/`에 저장한다.
6. 관리자 화면에서 Top-5를 읽고 검토할 수 있게 한다.
7. 합성 문서 테스트로 순위, 점수, 파일 형식, 권한과 LLM 미호출을 검증한다.
8. 실제 지침서 결과를 사람이 검토한다.
9. `docs/02_BM25_RESULT.md`와 `docs/CHANGELOG.md`를 작성한다.
10. 작업을 멈추고 사용자 검토를 기다린다.

## 14. 승인 전 중지 조건

현재는 이 계획 문서 작성까지만 완료한다.

- BM25 코드 수정 금지
- tokenizer와 BM25 파라미터 변경 금지
- 실제 문서 평가 실행 금지
- artifacts 생성 금지
- RAG 아키텍처 문서 작성 금지
- LLM 호출 금지
- 2단계 자동 진행 금지

사용자가 이 계획을 명시적으로 승인한 뒤에만 1단계 구현을 시작한다.

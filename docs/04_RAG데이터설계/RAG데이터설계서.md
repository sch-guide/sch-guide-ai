# RAG·데이터 설계서

기준일: 2026-09-17 · main `8c44851` · 공통 정본

## 1. 공통 데이터 계약

두 구현 버전이 비교할 데이터와 답변 계약을 하나로 관리한다. 아래 값은 현재 main에서 확인한 값이다. ChromaDB 담당 브랜치의 동일 적용 여부는 확인 필요하며, 검증 전까지 동등 구현이라고 주장하지 않는다.

| 항목 | 현재 구현 | 근거 |
|---|---|---|
| 입력 | 관리자가 검토한 PDF, DOCX, XLSX. 최대 20MiB(20×1024×1024 바이트) | `documents.py` |
| PDF | pypdf strict 추출, 암호화·손상·빈 파일 거절, 최대 1,000페이지. 첫 파일 페이지를 1로 기록 | `read_pdf()` |
| 추출 실패 | PDF 실패 페이지도 빈 항목으로 남겨 후속 페이지 번호 보존 | `read_pdf()` |
| PDF 표 | pdfplumber로 행·셀 관계를 ` | ` 구분 텍스트로 보존. 복잡한 병합·읽기 순서는 검토 필요 | `pdf_layout.py` |
| PDF 이미지 | 이미지 자체의 의미 분석 없음. 이미지가 있고 텍스트가 80자 미만인 페이지는 OCR 후보 | `enhance_pdf()` |
| OCR | 설정을 켜고 로컬 Tesseract와 한국어·영어 데이터를 준비한 경우 실행. 기본 비활성 | `pdf_layout.py`, `settings.py` |
| DOCX | 문단·표 순서, 제목 계층 추출. 고정 페이지 대신 문단/표 위치 사용 | `_read_word()` |
| XLSX | 보이는 시트의 저장 값, 시트·셀 범위. 첫 데이터 행은 열 제목 후보. 시트당 20,000행·100열 제한 | `_read_excel()` |
| Office 제한 | doc/xls 미지원, 매크로 거절, 수식의 저장 계산값 없으면 거절. 그림·각주·텍스트상자 등 누락 가능 | `documents.py` |
| 추출 용량 | 전체 추출 문자 수 3,000,000 초과 거절. Office 압축 해제 합계 100MiB·파일 항목 10,000 초과 거절 | `_read_document()` |
| 원문 보존 | 저장 chunk에는 원문 텍스트와 위치 유지. 정규화 필드는 검색용 별도 표현 | `Chunk`, `chunk_payload()` |

## 2. Chunking

Chunk는 검색·인용에 사용하는 작은 원문 조각이다.

1. PDF 페이지, Word 문단·표, Excel 행의 위치를 먼저 보존한다.
2. `structure.py::semantic_blocks()`가 제목·빈 줄·표 등의 경계를 이용해 의미 단위를 만든다.
3. 임베딩 모델 tokenizer 기준 일반 chunk 크기 **110 token**, overlap **20 token**을 사용한다. 글자 수 기준이 아니다.
4. 분리 순서는 빈 줄, 줄바꿈, `. `, 공백, 문자 경계다.
5. 표/머리행이 있고 40 token 이하이면 분리된 부분에 함께 붙인다. 표 본문 예산은 `max(30, 106 - 머리행 token 수)`, overlap은 `min(12, 예산 // 4)`이며 마지막에 일반 splitter로 다시 길이를 제한한다.
6. Embedding 입력 한도는 128 token. 문서당 생성 chunk 수는 1~5,000개다.
7. 앞뒤 chunk ID와 같은 의미 단위의 `parent_id`를 보존한다. 일부 의미 단위 누락은 근거 검사에서 거절 사유가 될 수 있다.

버전 상수: `CHUNK_VERSION=4`, `EXTRACTION_VERSION=3`, `SEARCH_VERSION=10`. 모델 변경 시 기존 벡터와의 호환성·재색인 필요 여부를 확인해야 한다.

## 3. 핵심 데이터와 저장 구조

| 데이터 | 주요 필드 | 의미 |
|---|---|---|
| 문서 metadata | id, document_name, title, section, updated_date, file_hash, page_count, model, source_type, chunk_count | 문서 식별·개정·출처와 색인 설명 |
| 추출 metadata | extraction_version, chunk_version, warnings, missing_locations | 추출 방식과 누락 위치. 운영 DB에 모든 필드가 동일하게 저장된다고 보장하지 않음 |
| `Chunk` | id, document_id, document_name, page, title, section, updated_date, text, index | 원문 조각과 순서 |
| `Chunk` 문맥 | source_type, location, normalized_text, previous_chunk_id, next_chunk_id, parent_id | 파일 종류·실제 위치·검색 표현·연결 관계 |
| `Hit` | chunk, similarity, lexical, bm25_score, fusion_score, rerank_score, context_only, context_complete | 검색 점수·문맥 확장·완전성 |
| `QueryPlan` | original, query, expanded, kind, format, focus, document_ids, min_documents, entities, clarification, corrections, max_seeds, max_hits, domain | 질문 원문·검색 표현·유형·범위·추가 확인 |
| `Answer` | answerable, statements, format, conflict | 답변 가능 여부와 문장 목록 |
| 문장별 근거 | statement.text, evidence의 chunk_id와 quote | 실제 chunk의 완전한 문장/행과 연결 |

문서와 chunk는 document_id로 연결한다. `page`는 PDF 실제 페이지이며 DOCX/XLSX는 null 가능하고 `location`을 사용한다. `parent_id`는 의미 단위 연결용 UUID다. `chunk_payload()`는 외부 직렬화를 위해 chunk_id/page_number/section_title/raw_text 별칭도 만든다.

| 저장 영역 | 로컬 | 운영 |
|---|---|---|
| 문서·상태 | SQLite documents, corpus(version) | guide_documents(active/retired), metadata 기반 revision |
| chunk·벡터 | SQLite chunks: JSON payload + little-endian float32 blob | guide_chunks: 본문·metadata + vector(384) |
| 승인 체크리스트 | checklists | guide_checklists |
| 검토 요청 | review_requests | guide_review_requests(migration) |
| 계정 | accounts.sqlite3 | Supabase Auth + guide_profiles |
| 사용량 | data/usage.sqlite3 | 동일한 앱 서버 로컬 파일 |
| 원본 | 로컬 originals | 설정에 따른 저장소; 운영 권장 Supabase 비공개 버킷 |

기본 SQL과 migration 적용 상태에 따라 선택 metadata 열이 없을 수 있다. 서버가 없는 것으로 확인한 선택 열만 조회에서 제외한다. 필수 열·권한 오류는 성공으로 취급하지 않는다. 실제 DB 적용 상태는 확인 필요다.

## 4. Retrieval 구현 비교

| 항목 | BM25 기반 Version A 관련 확인 | ChromaDB 기반 Version B |
|---|---|---|
| 구현 상태 | main에 BM25 구현. 앱 실행 경로는 Hybrid | 현재 브랜치 미구현, 다른 브랜치 확인 필요 |
| 검색 방식 | 단어 빈도·문서 길이·희소성 점수 + Vector 후보의 순위 결합 | Vector Retrieval이라는 목표만 전달됨. 실제 구현 확인 필요 |
| Embedding 사용 | BM25 점수 자체에는 불필요. 현재 앱 Hybrid에는 사용 | 확인 필요 |
| Vector DB | BM25 자체는 없음. 현재 앱은 로컬 FAISS / 운영 pgvector | ChromaDB 목표. collection·거리 함수·영속 경로 확인 필요 |
| Top-k | BM25 후보 최대 40. Vector도 최종 후보 40. 재정렬 seed 기본 6/넓은 질문 8, 문맥 확장 최대 12/14 | 확인 필요 |
| Query 처리 | NFKC·공백·제한적 영문 철자 보완·별칭 확장·후속 질문 계획 | 확인 필요 |
| 관련 모듈 | `retrieval.py`, `query.py`, `cloud.py`, `context.py` | 확인 필요 |
| 비고 | 순수 BM25 결과와 Hybrid 결과를 혼용하지 않음. 다른 브랜치 BM25 일괄 평가는 별도 이력 | 현재 문서의 공통 요구 적용은 담당자 확인 필요 |

### BM25와 현재 Hybrid의 상세 값

- 인덱스 입력: 허용 chunk의 `text + section`. 문서명/title을 BM25 인덱스에 직접 이어 붙이지 않는다.
- 한글 연속 문자열·영문 토큰, 제한적인 조사 제거, 한글 2글자 조각(가중치 0.25), 의료 별칭 entity 토큰 사용.
- BM25 수식 상수: k1=1.5, b=0.75. RRF 순위 결합 상수: 60. BM25 원점수는 확률이 아니다.
- FastEmbed 모델: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384차원, CPU, 단위 길이 정규화.
- 로컬 FAISS: IndexFlatIP. BM25에만 잡힌 후보도 저장 벡터로 유사도를 계산한다.
- 운영: RPC에 match_count=80을 요청한 뒤 허용 chunk와 대조하여 Vector 상위 40을 사용한다. Vector 상위 후보 밖의 BM25 후보 유사도 0은 미측정 대체값이다.
- 재정렬: 주제·본문·질문 항목·유사도·문서/개체 범위를 고려한다. 별도 cross-encoder 없음.
- 설정 기본 유사도는 0.38. 키워드 근거가 없는 후보에는 `max(설정값, 0.55)`를 적용한다. 실제 배포 설정값은 미확인.
- 이웃 문맥은 동일 문서·section·연결/주제 조건을 만족할 때 앞뒤 최대 2개씩 확인한다.
- 진단 표의 BM25 Top-10·재정렬 Top-5는 표시 범위이며 최종 LLM 입력 Top-k와 다르다.

## 5. Prompt·LLM·답변 계약

- Prompt는 `ai.py::SYSTEM` 규칙과 질문, 선택 근거의 JSON으로 구성한다. 외부 지식·새 절차·용량·출처를 만들지 않고 원문의 완전한 문장/표 행을 선택·배열하도록 지시한다.
- 현재 `AI_VERSION=8`은 AI 로직 버전이며 독립된 Prompt Version은 없다. 비교 실행에는 SYSTEM 문자열의 SHA-256와 코드 커밋을 함께 기록할 계획이다.
- provider는 disabled/groq_free/internal. 기본은 disabled. Groq 허용 모델은 `openai/gpt-oss-20b`, `openai/gpt-oss-120b`이며 실제 선택 모델은 설정 확인 필요다.
- temperature=0, JSON 응답, 출력 한도 768 token. Groq 요청 token 예산 3,500. 별도로 직렬화된 입력의 UTF-8 바이트 크기와 출력 한도 상수를 이용한 14,000 예산 검사도 있다. 바이트와 token 한도를 같은 값으로 취급하지 않는다.
- 요청 timeout=30초. 자동 재시도와 다른 모델로 자동 전환 없음.
- `Answer`는 최대 10문장, 문장 text 최대 700자, 문장당 근거 1~4개, 인용 quote 4~1,600자. 출력 형식·인용 출처·완전한 원문 일치·필수 정보·비교 대상·충돌 표시를 검사한다.
- 근거 검사 → 예산 적용 후 재검사 → LLM → 인용된 문장만으로 다시 검사. 부족하면 고정 안내로 종료한다.
- 이전 질문은 문맥 파악용이다. 이전 AI 답변은 새로운 원문 근거가 아니다.

## 6. 보존·보안·미확인 사항

원본·계정·벡터·평가 원문은 공개 문서에 복사하지 않는다. 검사 패턴이 모든 개인정보를 찾아주는 것은 아니므로 등록 전 관리자 검토가 필요하다. 이 문서에는 설정의 이름과 코드 기본값만 기록하고 키·비밀번호·Token 값은 기록하지 않았다.

확인 필요: ChromaDB 브랜치, 비교용 동일 chunk 데이터셋·정답표, 실제 모델/Prompt·Top-k 합의, 실데이터 추출 누락, 운영 스키마 적용 상태. 이미지 의미 이해·RAGAS 평가는 현재 미구현이다.

근거: [문서 추출](../../mvp/documents.py), [PDF 보완](../../mvp/pdf_layout.py), [Chunk·Embedding](../../mvp/library.py), [질문](../../mvp/query.py), [검색](../../mvp/retrieval.py), [운영 검색](../../mvp/cloud.py), [문맥](../../mvp/context.py), [AI](../../mvp/ai.py), [SQL](../../mvp/schema.sql), [migration](../../mvp/migrations/20260910_operational_pgvector.sql).

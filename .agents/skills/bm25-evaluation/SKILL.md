---
name: bm25-evaluation
description: 병원 지침서 기반 BM25 검색 성능을 검증하고, 질문별 Top-k 검색 결과를 분석·저장·시각화할 때 사용하는 SCHAT 전용 스킬이다.
---

# BM25 Evaluation

## 목적

이 스킬은 SCHAT 프로젝트의 1단계인 BM25 검색 성능 검증에 사용한다.

RAG 챗봇을 구현하기 전에,
현재 문서 추출·chunking·토큰화·BM25 인덱싱이 실제 질문에서 적절한 근거를 찾는지 먼저 검증한다.

BM25 검증이 승인되기 전에는 RAG 구현으로 넘어가지 않는다.

---

## 사용 시점

다음 상황에서 이 스킬을 사용한다.

- BM25 검색 성능 검증
- 특정 질문이 왜 검색되지 않는지 분석
- 질문별 Top-k 검색 결과 확인
- BM25 score 비교
- 문서명 / 페이지 / chunk 확인
- 검색 결과 CSV / JSON 저장
- 사람이 직접 검수할 HTML 결과 화면 생성
- BM25 검색 실패 원인 분석

---

## 작업 원칙

1. 구현 전에 현재 구조를 먼저 분석한다.
2. 사용자 승인 전에는 실제 검색 로직을 수정하지 않는다.
3. 기존 정상 기능은 임의로 삭제하거나 대규모 리팩터링하지 않는다.
4. BM25 검증 결과는 반드시 사람이 확인할 수 있는 형태로 남긴다.
5. 검색 결과의 원문과 메타데이터를 유지한다.
6. 문서명과 페이지 정보가 가능한 경우 반드시 보존한다.
7. 검색 결과를 임의로 좋게 보이도록 필터링하지 않는다.
8. 실패한 검색도 결과에 포함한다.

---

## 1단계: 현재 구조 분석

먼저 다음 항목을 확인한다.

- 지침서가 저장되는 위치
- 지원 파일 형식
  - PDF
  - DOCX
  - XLSX
  - 기타
- 문서 텍스트 추출 방식
- PDF 페이지 번호 보존 여부
- chunk 생성 방식
- chunk 크기
- overlap 여부
- section / title metadata 여부
- 한국어 tokenization 방식
- `rank-bm25` 사용 여부
- 기존 BM25 관련 코드 위치
- 기존 검색 함수
- 기존 테스트 코드
- 기존 검색 결과 저장 방식

분석 결과를 코드 수정 전에 사용자에게 요약한다.

---

## 2단계: 계획 문서 작성

다음 파일을 작성한다.

`docs/bm25/01_PLAN.md`

최소 포함 항목:

1. 현재 구조
2. BM25 적용 대상 데이터
3. 문서 추출 방식
4. chunking 방식
5. tokenization 방식
6. BM25 인덱스 생성 방식
7. 검색 query 처리 방식
8. Top-k 설정
9. 결과 저장 형식
10. 테스트 질문
11. 평가 기준
12. 예상 문제점
13. 수정 예정 파일
14. 새로 생성할 파일
15. 롤백 방법

계획 작성 후 작업을 멈추고 사용자 승인을 기다린다.

---

## 3단계: BM25 데이터 준비

사용자 승인 후 진행한다.

문서에서 최소 다음 metadata를 유지한다.

- document_id
- document_name
- page_number
- section_title
- chunk_id
- chunk_text

가능하면 다음도 유지한다.

- source_type
- created_at
- updated_at
- document_version

---

## 4단계: Tokenization

한국어 문서 검색 품질을 고려한다.

기본 단순 정규식 tokenization만 사용할 경우,
다음 문제를 반드시 검토한다.

예:
- `진정간호` vs `진정 간호`
- `PCN irrigation` vs `PCN 세척`
- 영어 약어
- 한글/영문 혼합
- 조사 포함
- 띄어쓰기 차이
- 대소문자 차이

tokenizer 변경이 필요하면
기존 방식과 변경 방식의 검색 결과를 비교한다.

---

## 5단계: BM25 인덱스 생성

BM25 인덱스 생성 시 다음을 확인한다.

- 전체 chunk 수
- 인덱싱 성공 여부
- 비어 있는 chunk 제외 여부
- 중복 chunk 존재 여부
- 지나치게 짧은 chunk 처리 여부
- 지나치게 긴 chunk 처리 여부

BM25 인덱스는 가능하면 재사용할 수 있도록 구성한다.

---

## 6단계: 검색 테스트

질문별로 Top 10 결과를 추출한다.

기본 테스트 질문 예:

- 진정간호 목적은?
- 진정간호 절차는?
- 진정 전 준비사항은?
- CRE 격리 기준은?
- PCN irrigation 방법은?
- 반코마이신 투여 시 주의사항은?
- Thoracentesis 준비물은?

프로젝트 실제 지침서에 맞는 질문으로 교체하거나 추가한다.

---

## 7단계: 결과 저장

각 질문별로 최소 다음 필드를 저장한다.

- query
- rank
- bm25_score
- document_name
- page_number
- section_title
- chunk_id
- chunk_text

결과 파일:

`artifacts/YYYY-MM-DD_bm25-evaluation/bm25_results.csv`

`artifacts/YYYY-MM-DD_bm25-evaluation/bm25_results.json`

---

## 8단계: HTML 검수 화면

사람이 검색 결과를 직접 검수할 수 있도록 HTML 화면을 생성한다.

파일 예:

`artifacts/YYYY-MM-DD_bm25-evaluation/review.html`

HTML 화면에는 최소 다음 기능을 포함한다.

- 질문 선택
- 질문별 Top 10 결과
- rank
- BM25 score
- 문서명
- 페이지
- section title
- chunk 원문
- 검색 결과 개수
- 적절함 / 부적절함 수동 표시 가능 여부 검토

가능하면 다음도 제공한다.

- 검색어 필터
- 문서별 필터
- score 정렬
- 원문 펼쳐보기
- 질문별 결과 비교

---

## 9단계: 평가 기준

다음 항목을 평가한다.

### Retrieval quality

- Top-1 정답 포함 여부
- Top-3 정답 포함 여부
- Top-5 정답 포함 여부
- Top-10 정답 포함 여부

### Metadata quality

- 문서명 정확성
- 페이지 정확성
- section 정확성
- chunk 원문 정확성

### Failure analysis

다음 실패 유형을 구분한다.

- 문서 추출 실패
- OCR 필요
- chunking 문제
- tokenizer 문제
- BM25 vocabulary mismatch
- query 표현 차이
- metadata 누락
- 인덱스 반영 실패
- 잘못된 문서 필터링

---

## 10단계: 결과 문서 작성

검증 후 다음 파일을 작성한다.

`docs/bm25/02_RESULT.md`

최소 포함 항목:

1. 테스트 문서 수
2. 총 페이지 수
3. 총 chunk 수
4. 테스트 질문 수
5. 질문별 Top-5 결과 요약
6. 성공 사례
7. 실패 사례
8. 검색 실패 원인
9. 개선 필요사항
10. tokenizer 개선 필요 여부
11. chunking 개선 필요 여부
12. BM25 단독 사용 한계
13. RAG 단계 진행 권고 여부

---

## 11단계: 사용자 승인

BM25 결과 보고 후 자동으로 RAG 구현으로 넘어가지 않는다.

반드시 사용자에게 다음을 확인한다.

- BM25 검색 결과를 승인하는가?
- tokenizer 개선이 필요한가?
- chunking 개선이 필요한가?
- 테스트 질문을 추가할 것인가?
- RAG 단계로 넘어갈 것인가?

사용자 승인 전에는 RAG 관련 구현을 시작하지 않는다.

---

## 금지 사항

- BM25 검증 전 RAG 구현 시작 금지
- 검색 결과를 임의로 삭제하거나 숨기지 않는다
- 낮은 score 결과도 필요하면 기록한다
- 문서 전체를 LLM에 전달하지 않는다
- BM25 결과 검증 없이 "검색 정상"이라고 판단하지 않는다
- UI 상태값만 보고 정상이라고 판단하지 않는다
- 기존 사용자 데이터를 삭제하지 않는다
- 시스템 경로를 수정하지 않는다
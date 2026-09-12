# 병원 실무지침 AI

등록된 병원 실무지침을 검색하고, 검색된 근거만 Groq에 전달해 답변과 출처를 표시하는 Streamlit 앱입니다.

- 직원: AI 채팅, 이전 질문 이어 묻기, 답변 근거와 관련 원문 확인
- 관리자: PDF·DOCX·XLSX 등록·교체·삭제·재색인
- 검색: 무료 다국어 로컬 임베딩 + BM25/Vector 혼합 검색 + RRF + 규칙 기반 재정렬
- 답변: 등록 지침의 검색 근거가 있을 때만 Groq로 생성
- 출처: 문서명, 항목, 페이지 또는 문단 위치, 개정일

병원 원문, API 키, 계정 DB와 검색 인덱스는 이 공개 저장소에 올리지 않습니다.

## 근거 기반 검색·답변 계약

문서의 제목·문단·표 단위로 분할한 뒤 모델 입력 한도를 넘는 부분만 추가 분할합니다.
문서명, 실제 페이지/문단 위치, 섹션, 개정일, 같은 의미 단위와 인접 chunk ID를 보존합니다.
임베딩은 FastEmbed CPU 모델로 앱 서버에서 생성합니다. 최초 공개 모델 다운로드 후 문서/질문을 임베딩 API로 전송하지 않습니다.

질문은 유니코드·띄어쓰기 정규화와 제한적인 철자/약어 보완을 거칩니다.
명백한 업무 외 질문은 임베딩 전에 거절합니다. 미등록 업무 용어는 무조건 차단하지 않고 원문 근거 검사로 판단합니다.
로컬에서는 FAISS와 BM25, 운영에서는 Supabase pgvector와 **허용된 전체 chunk의 BM25 인덱스**에서 각각 후보를 찾습니다.
운영 BM25는 직원 세션의 앱 서버 메모리에 보관하고, 매 질문의 권한·문서 버전 확인 후 재사용/재생성합니다.
기존 SQL의 단순 문자열 개수는 BM25로 취급하지 않습니다. 기존 `guide_search` RPC는 dense 후보만 가져오는 데 사용합니다.
후보를 RRF로 합친 뒤 주제 일치·질문 항목·의미 유사도로 재정렬합니다. 별도 cross-encoder 모델은 사용하지 않습니다.

LLM 호출 전 주제 근거, 요청한 용량·간격·주의·해제 기준 등의 존재, 비교 대상 문서/개체,
의미 단위의 누락 여부를 검사합니다. 토큰 예산으로 근거를 줄인 후에도 다시 검사합니다.
근거가 부족하면 Groq를 호출하지 않고 **등록된 지침서에서 확인할 수 없습니다.** 를 표시합니다.

Groq는 관련 원문 문장을 선택·배열하는 추출형 답변을 만듭니다. 임의 의역이나 일반 의학지식의 추가는 허용하지 않습니다.
각 문장은 제공한 chunk의 완전한 문장/행과 일치해야 하고, 그 문장을 포함하는 출처만 연결됩니다.
부분 인용으로 조건·부정을 제거하거나, 문장/출처를 새로 만들거나, 질문의 필수 항목을 빠뜨리면 동일한 근거 부족 문구로 끝납니다.
답변에는 문장별 인용과 문서명·페이지/위치·관련 원문을 표시합니다.

규칙 기반 주제/충분성 검사는 모든 임상적 의미를 판정하지 못합니다. 실제 등록 지침으로 정답 문서·원문을 지정한 평가가 필요합니다.
추출형 검증은 의역보다 보수적이므로 근거가 있어도 답변을 거절할 수 있습니다.
검색은 기본 `schema.sql`과 운영 migration 적용 DB 모두 지원합니다. 서버가 없는 것으로 확인한 선택 메타데이터 열만 조회에서 제외하고,
문서명·페이지·본문과 실제로 존재하는 문맥 메타데이터는 보존합니다. 권한·필수 열·검색 함수 오류는 우회하지 않습니다.
새 chunk의 `parent_id`는 PostgreSQL UUID 형식으로 생성합니다.
기존 문서에 의미 단위/인접 정보가 없다면 관리 화면에서 재색인해야 문맥 누락 검사를 모두 적용할 수 있습니다.

## 검증

### 관리자 실데이터 검색 진단

관리자 로그인 → **지침서 관리 → 검색 실패 진단 · 관리자 전용**에서 등록 문서와 질문을 선택합니다.
기본 질문은 `진정간호 목적에 대해 알려줘`, 인덱스 비교 검색어는 `진정간호 목적`입니다.
**실제 검색 파이프라인 진단**을 누르면 다음을 확인할 수 있습니다.

- 저장 원본의 현재 재추출 문자 수·페이지별 문자 수·키워드 존재, 일반 텍스트와 OCR 결과 구분
- 실제 저장 chunk의 ID·문서·페이지·섹션·200자 샘플 (재생성한 가상 chunk가 아님)
- 실제 저장 embedding 열/벡터 blob의 개수·차원·누락·유효성·정규화 범위
- 실제 질문 임베딩과 BM25 top 10 / FAISS 또는 pgvector top 10 / RRF / 재정렬 top 5
- 저장 벡터 전수 코사인 비교와 실제 검색 결과의 차이, 문서 상태·필터·선택/제외 사유
- 근거 검사·입력 예산·인용 검증 단계별 차단 이유와, AI 재시험 선택 시 최종 답변·인용 출처

AI 재시험은 기본으로 꺼져 있습니다. 켜서 실행하면 기존 승인 설정·사용량 한도로 실제 LLM을 한 번 호출합니다.
진단은 문서나 인덱스를 수정하지 않습니다. UI와 서버 양쪽에서 관리자 역할을 검사하며 진단을 전역 캐시에 저장하지 않습니다.
다운로드한 JSON에는 병원 지침 원문이 포함되므로 공개 저장소나 승인되지 않은 서비스에 올리지 마세요.

`indexed_at` 등의 값이 없는 구형 DB는 **미확인(null)** 으로 표시합니다. 화면 상태만으로 색인 성공을 판정하지 않습니다.
원본 접근 실패도 별도 오류로 남기고 가능한 저장 chunk·검색 검사는 계속합니다.
원본 재추출은 현재 코드의 결과이지 등록 당시 추출 로그가 아닙니다. 저장된 chunk와 대조해야 합니다.
스캔 후보는 이미지가 있고 추출 텍스트가 80자 미만인 페이지이며, 빈 페이지/그림만 있는 페이지와 구별하려면 원본을 확인해야 합니다.

목적/정의 답변은 문서 주제와 **실제 해당 항목의 본문**이 함께 있는 경우에만 항목 문맥을 사용합니다.
구형 DB가 섹션을 저장하지 않았어도 원문 속 명시적 제목 범위를 확인할 수 있으면 인용 문맥을 복원합니다.
제목만으로 답하거나 부족한 용량·절차 정보를 보충하지 않습니다. 인용 후에도 같은 검사를 적용합니다.
기본 유사도 0.38, 키워드 근거 없는 후보의 하한 `max(설정값, 0.55)`는 일괄 완화하지 않았습니다.

### 회귀 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check mvp tests
.\.venv\Scripts\python.exe -m mvp.evaluate_cases
```

마지막 명령은 합성 문서와 실제 로컬 임베딩으로 검색을 평가합니다. 기본 답변은 검증용 fixture이며 실제 Groq 응답 품질 평가가 아닙니다.
`tests/test_rag_contract.py`는 운영 Supabase 요청/응답 대역을 통해 BM25 누락 복구·권한·캐시·거절·문장별 인용을 확인합니다.
기본/부분/전체 DB migration 조합, 500개를 넘는 chunk 조회, 선택 열 자동 조정과 권한 오류 시 중단도 검사합니다.

DB 요청 실패는 `DATABASE/SOURCE/HTTP400/42703`처럼 실패 단계와 안전한 코드만 표시합니다.
`SOURCE`는 원문 조회, `SEARCH`는 벡터 검색 함수, `DOCUMENTS`는 문서 목록 조회입니다.
오류 본문·질문·원문·토큰·키는 진단 메시지에 포함하지 않습니다.
PostgREST 오류 코드의 의미는 [공식 오류 문서](https://docs.postgrest.org/en/stable/references/errors.html)를 참고하세요.

## Streamlit Community Cloud

| 설정 | 값 |
|---|---|
| Repository | sch-guide/sch-guide-ai |
| Branch | main |
| Main file path | mvp/app.py |
| Python | 3.12 |

배포 전에 Supabase SQL Editor에서 mvp/schema.sql, mvp/storage_schema.sql, mvp/migrations/20260910_operational_pgvector.sql 순서로 실행하고, Streamlit Cloud의 Settings → Secrets에 아래 항목을 실제 값으로 등록합니다. 기존 설치에는 마지막 migration만 추가 실행합니다.

~~~toml
GUIDE_MODE = "staff"
GUIDE_SUPABASE_URL = "https://프로젝트주소.supabase.co"
GUIDE_SUPABASE_PUBLISHABLE_KEY = "publishable 또는 anon 키"
GUIDE_STORAGE_BACKEND = "supabase"
GUIDE_STORAGE_BUCKET = "guide-originals"

GUIDE_LLM_PROVIDER = "groq_free"
GUIDE_LLM_API_KEY = "Groq API 키"
GUIDE_LLM_MODEL = "openai/gpt-oss-20b"
GUIDE_LLM_APPROVED = "true"
GUIDE_GROQ_FREE_CONFIRMED = "true"
GUIDE_DAILY_LLM_LIMIT = "900"
GUIDE_USER_DAILY_LLM_LIMIT = "30"
GUIDE_MIN_SIMILARITY = "0.50"
GUIDE_OCR_ENABLED = "false"
~~~

GUIDE_SUPABASE_PUBLISHABLE_KEY에는 관리자용 service_role 비밀 키를 사용하지 않습니다. Streamlit Cloud에 Secrets가 설정되지 않으면 직원 로그인과 지침서 저장 기능은 시작되지 않습니다.

## 로컬 실행

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r mvp\requirements.txt
Copy-Item mvp\.env.example mvp\.env
.\.venv\Scripts\python.exe -m streamlit run mvp\app.py --server.address 127.0.0.1 --server.port 8502
~~~

로컬에서만 mvp/.env의 GUIDE_MODE=local을 사용할 수 있습니다. 외부 배포는 staff 모드와 Supabase 인증을 사용합니다.

## 보안

- 환자 개인정보를 질문이나 문서에 포함하지 않습니다.
- 관리자가 등록한 원문은 비공개 Supabase Storage에 저장합니다.
- 직원 화면에는 원본 다운로드·업로드·삭제 기능을 표시하지 않습니다.
- .env, .streamlit/secrets.toml, data/, PDF·Word·Excel 파일은 Git에서 제외합니다.

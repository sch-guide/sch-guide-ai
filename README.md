# 병원 실무지침 AI

등록된 병원 실무지침을 검색하고, 검색된 근거만 Groq에 전달해 답변과 출처를 표시하는 Streamlit 앱입니다.

- 직원: AI 채팅, 이전 질문 이어 묻기, 답변 근거와 관련 원문 확인
- 관리자: PDF·DOCX·XLSX 등록·교체·삭제·재색인
- 검색: 무료 다국어 임베딩 + FAISS + BM25 혼합 검색
- 답변: 등록 지침의 검색 근거가 있을 때만 Groq로 생성
- 출처: 문서명, 항목, 페이지 또는 문단 위치, 개정일

병원 원문, API 키, 계정 DB와 검색 인덱스는 이 공개 저장소에 올리지 않습니다.

## Streamlit Community Cloud

| 설정 | 값 |
|---|---|
| Repository | sch-guide/sch-guide-ai |
| Branch | main |
| Main file path | mvp/app.py |
| Python | 3.12 |

배포 전에 Supabase SQL Editor에서 mvp/storage_schema.sql을 실행하고, Streamlit Cloud의 Settings → Secrets에 아래 항목을 실제 값으로 등록합니다.

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
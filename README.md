# 병원 실무지침 AI — 1단계

PDF를 업로드하고 **페이지별 원문과 출처**를 확인하는 Streamlit 앱입니다.
현재는 PDF 읽기 단계이며, 벡터 검색·AI 답변·로그인은 아직 구현하지 않았습니다.
API 키와 유료 LLM 호출 없이 실행할 수 있습니다.

## Streamlit Community Cloud 배포 설정

| 설정 | 값 |
|---|---|
| Repository | `sch-guide/sch-guide-ai` |
| Branch | `main` |
| Main file path | `mvp/app.py` |
| Python | `3.12` |

`streamlit_app.py`를 입력하면 파일을 찾을 수 없습니다. 실제 실행 경로는 `mvp/app.py`입니다.
패키지는 실행 파일과 같은 폴더의 `mvp/requirements.txt`에서 설치합니다.
`mvp` 폴더 구조를 유지해야 `mvp.documents`를 불러올 수 있습니다.

Streamlit의 Create app에서 위 값을 입력하고 배포합니다.
이미 실패한 앱이 있다면 실행 파일 경로가 위 값인지 확인해 다시 배포하세요.
이 저장소에 코드를 올리는 작업과 Streamlit에서 앱을 배포하는 작업은 별도입니다.

## Windows + VS Code에서 실행

Python 3.12(64비트)와 Git을 설치한 뒤 PowerShell에서 실행합니다.

```powershell
git clone https://github.com/sch-guide/sch-guide-ai.git
Set-Location -LiteralPath sch-guide-ai
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r mvp/requirements.txt
.\.venv\Scripts\python.exe -m streamlit run mvp/app.py --server.address 127.0.0.1 --server.port 8502
```

이미 내려받은 경우 프로젝트 폴더에서 가상환경의 Python으로 실행합니다.
브라우저에서 http://127.0.0.1:8502 를 엽니다. 서버 종료는 터미널에서 Ctrl+C입니다.
실행 파일의 전체 경로를 사용하므로 PowerShell 실행 정책을 바꿀 필요가 없습니다.

## 파일 역할

| 파일 | 역할 |
|---|---|
| `mvp/app.py` | 업로드, 페이지 선택, 원문·출처 표시 |
| `mvp/documents.py` | PDF 형식 검사, 텍스트 추출, 오류 안내 |
| `mvp/__init__.py` | mvp 폴더를 Python 패키지로 표시 |
| `mvp/requirements.txt` | Streamlit·pypdf 설치 버전 |
| `.streamlit/config.toml` | 업로드 크기, 테마, 사용 통계 설정 |
| `tests/test_mvp_pdf.py` | 가상 PDF로 페이지 번호와 화면 동작 검사 |
| `requirements-dev.txt` | 검사 도구 설치 목록 |
| `.gitignore` | API 키·가상환경·원문 파일의 새 추가 방지 |

## 확인 방법과 제한

1. 개인정보가 없는, 텍스트를 선택할 수 있는 PDF를 업로드합니다.
2. 전체 페이지 수와 페이지별 본문을 원본 PDF와 비교합니다.
3. 빈 페이지가 있어도 PDF의 실제 페이지 번호가 유지되는지 확인합니다.
4. 다른 파일로 교체하거나 업로드 지우기를 누르면 이전 원문이 사라지는지 확인합니다.

- 한 번에 PDF 1개, 파일당 20MB 이하, 최대 1,000페이지입니다.
- 출처의 페이지는 인쇄된 쪽수가 아니라 PDF 파일의 실제 순서입니다.
- 스캔 PDF의 OCR, 복잡한 표 복원, Word·Excel은 이후 단계입니다.
- 숫자·단위·부정 표현·표의 행과 열은 원본과 대조해야 합니다.
- 암호화·손상·형식 오류와 텍스트 없는 페이지를 구분해 안내합니다.
- 원문 추출 성공은 개인정보 검사나 의료적 정확성 검증을 뜻하지 않습니다.

## 문서와 API 키

앱은 저장소의 PDF를 자동으로 읽지 않습니다. 사용자가 화면에서 선택한 파일만 처리합니다.
업로드한 PDF는 앱 서버의 메모리에서 처리하며 앱 코드가 별도 파일로 저장하지 않습니다.
로컬 실행 시 서버는 내 PC이고, 클라우드 실행 시 PDF가 해당 서비스 서버로 전송됩니다.
외부 AI API를 호출하지 않지만 클라우드 호스팅은 외부 서버에서의 문서 처리입니다.

환자 개인정보와 외부 전송이 허용되지 않은 내부 자료를 클라우드에 올리지 마세요.
로그인·문서 권한을 구현하기 전에는 비기밀 시험 자료로 검증하세요.
이 단계의 앱은 직원 공동 사용을 위한 운영 배포가 아닙니다.

`.env`, `.venv`, 원문 PDF와 검색 인덱스는 GitHub에 새로 올리지 않습니다.
**.gitignore는 이미 Git에 등록된 파일이나 이전 이력을 제거하지 않습니다.**
기존에 등록한 문서가 있다면 저장소 공개 범위와 병원의 자료 공개 허용 여부를 별도로 확인하세요.
향후 LLM 키는 로컬 `.env` 또는 배포 서비스의 Secrets에서 관리하며 코드에 넣지 않습니다.

## 개발 검사

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests/test_mvp_pdf.py -q
```

실제 병원 자료와 API 키 없이 가상 PDF로 검사합니다.
다음 단계는 Chunk 분할과 출처 metadata 연결입니다.

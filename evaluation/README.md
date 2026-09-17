# BM25 Baseline v1 실행 환경

## 범위와 현재 상태

이 단계는 실행 환경 준비만 수행한다. 실제 Label Set 10문항 실행, LLM 호출,
자동채점은 하지 않았다. `results/bm25_baseline_v1.json`은 아직 없다.

2026-09-17 로컬 카탈로그에서 확인한 검색 가능 문서는
`실무지침서_수혈간호.pdf` 한 개이며 17페이지, 94개 청크이다.
이는 로컬 `data/library/catalog.sqlite3`의 목록이며 원격 Supabase 목록을 의미하지 않는다.
PDF 원본 SHA-256은 `0d7887cdceda2ec4758b892945d7ec20607494cfb6a082bd3c3624de23160956`이다.
기존 문서 메타데이터에는 7·8페이지의 이미지 중심 내용에 대한 OCR 확인 경고가 있다.
이번 작업에서 추출·청킹·등록 상태를 바꾸지 않는다.

**현재 구현은 순수 BM25가 아니라 BM25 + FAISS 벡터 검색의 하이브리드이다.**
기존 RRF 결합, 규칙 재정렬, 주변 문맥 확장을 그대로 사용한다.
요청한 `retrieval_engine: "BM25"`와 Baseline 이름은 유지하되,
`retrieval_implementation`, `is_pure_bm25: false`에 실제 구현을 명시한다.
이 결과를 순수 BM25 성능이라고 해석하면 안 된다.

CLI의 현재 설정은 `llm_provider=disabled`, `llm_model=null`이다.
실제 실행 전 기존 서비스에 사용하려던 LLM 설정을 확인해야 한다.
실행기는 모델이나 provider를 선택·변경하지 않으며, 설정이 준비되지 않으면
`--run`도 결과 파일과 LLM 호출 없이 종료한다.

## 설계와 재사용 경로

1. `label_set.json`의 10개 항목과 고유 ID를 검증한다. 정답 필드는 출력에만 복사한다.
2. `mvp.evaluate.load_local_index`로 기존 SQLite를 읽기 전용으로 로드한다.
3. 대상 PDF의 기존 청크와 벡터를 메모리에서 함께 선택한다. 파일 스캔, PDF 재등록,
   재청킹, 임베딩 재생성, 서비스 인덱스 수정은 없다. Label Set은 corpus가 될 수 없다.
4. 각 질문은 새 대화로 `mvp.query.plan_query` → `bounded_embedding_question` →
   `Embedder.encode` → `LocalLibrary.search` → `mvp.retrieval.search`를 거친다.
5. 서비스와 같이 범위 밖·추가질문 필요·검색 결과 없음은 생성 호출 없이 처리한다.
   검색 결과가 있으면 `mvp.ai.generate`와 `answer_text`를 그대로 호출한다.
   기존 근거 검사, 프롬프트 예산, 출력 검증, 거절 동작도 그대로다.
6. 질문 순서대로 실제 시도 결과만 UTF-8 JSON에 보존한다. 점수 계산은 하지 않는다.

서비스 인증/UI·체크리스트·대화 캐시는 실행 대상이 아니다. 로컬 RAG의 검색·답변 경로를
독립 질문으로 측정한다. 로컬 corpus는 실행 시작 때 메모리에 고정한다.
다른 ready 문서가 추가되면 실행 전 목록에 출력하지만 평가 corpus는 대상 PDF 하나이다.
동일 이름의 ready 문서가 여러 개면 임의로 선택하지 않고 중단한다.

## 확인된 설정

| 항목 | 기존 값 / 위치 |
|---|---|
| BM25 / 벡터 후보 수 | 각각 최대 40, `mvp/retrieval.py:search` |
| 재정렬 후 seed | 일반 최대 6, 비교·종합·요약 최대 8 |
| 문맥 확장 후 반환 수 | 일반 최대 12, 비교·종합·요약 최대 14, `mvp/query.py:plan_query` |
| Chunk size / overlap | 기본 110 / 20 임베딩 토큰, `mvp/library.py:make_chunks` |
| 표 청킹 예외 | size=`max(30, 106-header_tokens)`, overlap=`min(12, budget//4)` 후 기본 splitter 적용 |
| 임베딩 모델 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| LLM | 현재 disabled / 모델 null |
| Temperature | 0, `mvp/ai.py:generate`의 요청 payload |
| Prompt | `mvp/ai.py:SYSTEM`, `prompt_messages`; AI_VERSION=8 |
| Git | `lagom-bm25`, `af547cc4ee85ffef6f34f2931d0d9919a58034d4` (준비 시점) |

Top-k는 한 숫자로 고정하지 않는다. 실제 질문별 `query_plan`과 전체 반환 contexts를 기록한다.
기본 chunk 설정, Top-k, temperature는 소스 AST에서 읽고, 확인 불가 시 null이다.
등록된 청크는 그대로 사용하며 현재 소스 설정과 등록 당시 버전 정보도 함께 남긴다.
Git 정보는 실행 시 자동 조회한다. 다른 브랜치에서는 실행을 차단한다.

## 실행 명령 (프로젝트 루트 PowerShell)

설정·등록 문서 확인만 수행하며 결과 JSON이나 사용량 DB를 만들지 않는다:

```powershell
.\.venv\Scripts\python.exe evaluation/run_bm25_baseline.py --preflight
```

옵션 없이 실행해도 같은 사전 점검만 한다. `ready=false`와 `blockers`를 확인한다.
설정은 기존 `load_settings(use_streamlit=False)`에 따라 `mvp/.env`와 프로세스 환경변수를
사용한다. Streamlit secrets 파일이 있으면 서버 설정과의 차이를 무시하지 않고 실행을 막는다.

**사용자가 확인한 후에만 실행할 명령:**

```powershell
.\.venv\Scripts\python.exe evaluation/run_bm25_baseline.py --run
```

LLM API 사용량이 발생할 수 있다. 기존 `Quota` 규칙을 적용하되 평가용 카운터는
`evaluation/.runtime/usage.sqlite3`에 분리하여 서비스 사용량 DB를 수정하지 않는다.
서비스와 카운터를 공유하지 않으므로 동시 사용량까지 합산하지는 않는다.
외부 provider 제한은 그대로 적용되며 rate limit 오류도 원본 시도로 보존한다.
자동 재시도·대기는 하지 않는다.

기존 결과 파일은 덮어쓰지 않는다. 실행 중 중단되면 실제 완료한 시도까지 저장한다.
`run_complete`는 모든 질문의 시도 완료 여부이며, 답변 성공/채점 통과를 뜻하지 않는다.
`has_execution_errors`, 각 항목의 `status`와 `error`도 확인한다.
실행 중 강제 종료로 `.runtime/baseline.lock`이 남았다면 실행 프로세스가 없는지 확인 후 제거해야 한다.
종료 코드: 사전 점검 자체 완료 0 (`ready` 별도 확인), 실행 완료 0,
문항 실행 오류 포함 1, 준비 실패/차단 2.

## 결과 구조와 해석

- `evaluation_date`는 실제 실행 시작 시 UTC ISO 8601로 기록한다. 사전 점검에서는 null.
- 환경, 소스 코드·SYSTEM·실행기·Label Set·corpus 해시, 원본 문서 메타데이터를 보존한다.
- `results`는 Label Set 순서이며 정답·필수항목·치명적 오류 필드는 채점 없이 그대로 복사한다.
- `retrieved_contexts`는 생성 전 전체 반환 결과이다. `rank`는 최종 서비스 반환 순서이다.
  순수 BM25 순위는 `retrieval_trace.bm25_top10`에 별도로 제공된다.
- `context_only` 청크의 내부 점수 0은 측정 점수가 아니므로 `bm25_score=null`로 저장한다.
  일반 후보의 실제 0점은 0으로 유지한다. 페이지 미제공 시 null이다.
- 최상위 `retrieved_document/page`, `retrieval_rank`, `bm25_score`는 contexts와 순서가 같은 배열이다.
- `generation_selected_contexts`는 생성 함수가 반환한 선택 결과이며, 실제 호출 여부와
  예산 처리 후 prompt 청크 ID는 `generation_trace`에서 확인한다.
- `generated_answer`는 서비스의 `answer_text` 결과이다. 기존 근거 검증 거절도 그대로 보존한다.
  실행 오류 시 null이며 참조 정답으로 채우지 않는다.
- 응답 시간은 계획·질문 임베딩·검색·답변 생성 포함, 모델 초기화 제외이다.
- Label Accuracy / Retrieval Hit Rate / Critical Error / RAGAS 점수는 계산하지 않는다.

결과와 사용량 DB는 이 폴더의 `.gitignore`로 제외한다. 준비 단계의 `results/.gitkeep`은
빈 폴더 유지용이며 가짜 결과 데이터가 아니다.

## 안전 검증

```powershell
.\.venv\Scripts\python.exe -m unittest evaluation.test_bm25_baseline -v
```

테스트는 별도의 합성 질문·청크로 기존 검색 결과와 비교하며 실제 Label Set을 검색하거나
LLM을 호출하지 않는다. 스코프/벡터 정렬, 점수 null 처리, 정답 누출 방지,
생성 실패 시 원본 검색 결과 보존, 기본 실행·차단 시 무실행을 확인한다.
준비 완료 시 서비스 추적 파일과 Label Set·catalog 내용 해시도 변경 전후 비교한다.
LLM 비활성 상태이므로 실제 서비스의 답변 생성 정상 여부까지 검증한 것은 아니다.

# BM25 + Gemini Answer Baseline v1

프로젝트 루트에서, Gemini 환경변수가 설정된 PowerShell로 실행한다:

```powershell
.\.venv\Scripts\python.exe evaluation/run_bm25_answer_baseline.py --run
```

`--run` 없이 실행하거나 `--preflight`를 지정하면 설정과 corpus만 확인한다.
API Key는 기존 서비스 설정 로더가 프로세스의 `GEMINI_API_KEY`에서 읽으며 출력하지 않는다.
provider/model/승인/키가 준비되지 않으면 실제 검색·생성을 시작하지 않는다.
모델은 정확히 `gemini-3.1-flash-lite`여야 한다. 설정을 자동 변경하지 않는다.

기존 `run_bm25_baseline.py`의 준비·실행·직렬화 함수를 재사용한다.
서비스의 `plan_query`, 질문 임베딩, `LocalLibrary.search`, `ai.generate`,
`gemini_provider.completion_response`, `answer_text`가 그대로 사용된다.
서비스 검색은 실제로 BM25 + FAISS + RRF + 재정렬 + 주변 문맥 확장이다.
요청한 retrieval_engine 이름과 함께 실제 하이브리드 구현을 메타데이터에 명시한다.

TRF-001부터 TRF-010까지 원래 질문을 순서대로 독립된 새 대화로 처리한다.
정답·필수항목·치명적 오류 라벨은 결과 복사용이며 검색이나 프롬프트에 전달하지 않는다.
수혈간호 PDF의 기존 등록 청크·벡터만 읽기 전용으로 가져온다.
기존 Retrieval Baseline 결과는 읽거나 수정하거나 재계산하지 않는다.
이번 답변 실행에 필요한 검색은 기존 서비스 함수로 새로 수행한다.

결과는 `results/bm25_answer_baseline_v1.json`에 실제 문항 처리 후 저장된다.
기존 파일은 덮어쓰지 않는다. 문항마다 체크포인트를 남기며, 중단된 실행은
`run_complete=false`이고 처리된 문항까지만 포함한다.
실행 잠금은 `.runtime/bm25_answer_baseline_v1.lock`이다.
강제 종료 후 잠금이 남으면 실행 프로세스가 없는지 확인한 뒤 제거한다.

`generated_answer`는 서비스의 검증 후 최종 문장이다. 원문을 재작성·개선하지 않는다.
구조화된 답변과 생성용으로 선택된 context, 검색·생성 trace도 함께 보존한다.
원시 API 텍스트는 기존 서비스가 검증 전에 폐기할 수 있으므로 별도로 수집하지 않는다.
검색 결과는 생성 전 전체 반환 순서이며, 문맥 전용 청크의 BM25 점수는 null이다.
실행 오류 시 답변은 null이며 오류와 검색 결과를 보존한다. 근거 부족 거절도 그대로 저장한다.
시간은 질문 계획·임베딩·검색·생성·검증을 포함하고 모델 초기화는 제외한다.

기존 Quota 규칙을 쓰되 평가 카운터는 `.runtime/usage.sqlite3`에 분리되어 있다.
서비스 및 provider와 동시 사용 시 외부 rate limit이 발생할 수 있다. 자동 재시도는 없다.
실행 상태는 채점 결과가 아니다. Accuracy, 필수항목 충족률, Critical Error, RAGAS,
합격/불합격은 계산하지 않는다. 기존 문서의 chunk 버전과 현재 코드 설정도 기록한다.

오프라인 안전 검증:

```powershell
.\.venv\Scripts\python.exe -m unittest evaluation.test_bm25_answer_baseline evaluation.test_bm25_baseline -v
```

위 테스트는 합성 fixture/모의 호출만 사용하며 실제 Label Set 평가와 API 호출은 하지 않는다.

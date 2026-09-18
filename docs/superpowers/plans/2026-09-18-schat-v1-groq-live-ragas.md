# SCHAT v1.0 Groq Live RAGAS 연결 계획

- 기준 안정 tag: `v0.9`
- 실행 범위: evaluation-only Groq adapter와 단계적 Live 검증
- provider/model: Groq / `openai/gpt-oss-20b`
- production retrieval/generation/validator 변경: 금지
- Git commit/tag/push: 금지

## 고정 안전 계약

1. `GROQ_API_KEY` 환경변수만 사용하고 값은 출력·저장하지 않는다.
2. Groq payload에는 request-local SourceUnit ID, 선택된 exact text, normalized intent,
   closed JSON schema와 safety instruction만 포함한다.
3. 원 질문, 문서명, page, production chunk ID, 대화 이력, PDF/table/image 원본,
   환자·직원 식별자, deferred와 image case는 전송하지 않는다.
4. synchronous chat completion, temperature 0, strict JSON Schema,
   `include_reasoning=false`, streaming/tools/browser/code 실행 없음으로 고정한다.
5. raw payload·prompt·response·Authorization은 파일과 일반 로그에 남기지 않는다.
6. synthetic Live가 실패하면 real evidence는 전송하지 않는다.
7. 대표 real subset에서 critical safety error가 1건이라도 있으면 전체 21건으로
   확대하지 않는다.
8. 승인 abstention 4건은 provider zero-call을 유지한다.

## TDD와 실행 순서

1. evaluation-only transport의 payload allowlist, credential redaction, timeout/retry,
   PII 차단, safe result projection 테스트를 먼저 작성하고 RED를 확인한다.
2. 최소 Groq adapter와 runner를 구현해 GREEN을 확인한다.
3. 비민감 synthetic 1건을 실제 Groq API에 전송한다.
4. synthetic가 통과한 경우 승인 Gold 중 text와 table/mixed를 포함한 대표 3~5건을
   전송한다.
5. deterministic validator와 Gold critical-field 검사를 통과하고 critical error가
   0건일 때만 승인 21건으로 확대한다.
6. 외부 전송 allowlist상 원 질문과 generated answer를 별도 judge payload로 보낼 수
   없으므로, LLM-judge Faithfulness/Answer Relevancy를 임의 점수로 대체하지 않는다.
   실행 가능한 ID Context Precision/Recall과 deterministic safety 결과를 분리 기록한다.
7. Live 완료 후 UAT, 전체 pytest, 안전 회귀, Ruff, `git diff --check`, code review와
   artifact security audit를 수행한다.
8. `docs/rag/68_SCHAT_V1_LIVE_RAGAS_FINAL.md`, 현재정본과 progress를 실제 결과로 갱신한다.

## 중단 조건

- synthetic schema/transport 실패
- identifier/PII 탐지
- real subset의 critical safety error
- provider credential·model 접근·네트워크 실패
- raw clinical text/secret artifact 탐지
- production 변경 또는 validator 완화 필요

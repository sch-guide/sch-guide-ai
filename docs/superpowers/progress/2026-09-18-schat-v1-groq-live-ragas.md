# SCHAT v1.0 Groq Live RAGAS 진행 기록

- 날짜: 2026-09-18
- 기준 tag: `v0.9`
- 상태: `STOPPED_ON_CRITICAL_FACT_PRESERVATION`

## 완료 항목

- `GROQ_API_KEY`를 환경변수로만 연결하고 값 비출력 확인
- Groq `openai/gpt-oss-20b`, temperature 0, strict JSON Schema,
  `include_reasoning=false` evaluation-only transport 구현
- PII/identifier local preflight와 request-local SourceUnit ID 변환 구현
- 승인 Positive Gold 21건 준비, deferred/image/abstention 외부 호출 차단 확인
- Synthetic 실제 호출 및 fixed-property schema preflight 완료
- Representative subset 시작
- `UAT-S01` 1건 호출 후 `critical_fact_preservation` 미검증으로 즉시 중단
- Raw-free artifact와 security audit 생성

## 변경 파일

- `tools/groq_live_ragas_evaluate.py`
- `tests/test_groq_live_ragas_evaluate.py`
- `docs/superpowers/plans/2026-09-18-schat-v1-groq-live-ragas.md`
- `docs/rag/68_SCHAT_V1_LIVE_RAGAS_FINAL.md`
- `artifacts/2026-09-18_schat-v1-live-ragas-final/`
- 현재정본, 변경이력, 복구기준점 상태 문서

## 테스트 결과

- Groq Live adapter/provider harness focused: 21 passed
- Final artifact security: PASS
- Production module 변경: 0건
- Full regression: Live safety gate 중단으로 재실행 단계 미진입; 직전 기준
  648 passed, 4 skipped 유지

## 다음 단계

1. `UAT-S01` generated statement의 Gold critical fact 보존을 사람 검수하거나,
   generated answer judge 전송 범위를 별도로 승인한다.
2. Groq Console에서 Data Controls/ZDR 계정 상태를 확인한다.
3. Critical safety PASS 전에는 subset 추가 호출과 21건 확대를 실행하지 않는다.

## Pending / 중단 조건

- Faithfulness/Answer Relevancy: 원 질문·generated answer judge 전송 권한 없음
- Semantic support: pending
- `UAT-S01` critical fact preservation: 미검증
- Git commit/tag/push: 0회

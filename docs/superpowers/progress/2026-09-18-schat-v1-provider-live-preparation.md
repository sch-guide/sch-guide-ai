# SCHAT v1.0 Provider Live 준비 Progress

## 완료 항목

- Provider-neutral evidence/schema/instruction 계약
- Groq/Gemini offline request blueprint
- Provider별 Mock envelope normalization
- 기존 controlled-generation fail-closed validator 연결
- Groq 호환성 불명확 `minItems`/`maxItems` wire 제거와 서버 validator 유지
- Raw-free comparison manifest/result/review와 실제 artifact audit
- TF027 10항목 사람 검수 checklist
- 진정·수혈·out-of-scope 36-case 운영 UAT fixture
- v1.0 완료 기준과 현재정본/변경이력/복구기준점/현황판 갱신

## 변경 파일

- `tools/provider_controlled_generation_evaluate.py`
- `tests/test_provider_controlled_generation_harness.py`
- `tests/test_schat_v1_readiness_contracts.py`
- `tests/fixtures/schat_v1_operational_uat.json`
- `tests/fixtures/tf027_image_human_review_checklist.json`
- `docs/rag/61_TF027_IMAGE_HUMAN_REVIEW_CHECKLIST.md`
- `docs/rag/62_SCHAT_V1_COMPLETION_CRITERIA.md`
- `docs/rag/63_PROVIDER_LIVE_CONTROLLED_GENERATION_PREPARATION_RESULT.md`
- 관련 Superpowers spec/plan, 현재정본, 이력, 복구 문서와 raw-free artifact

Production retrieval, embedding, provider adapter와 validator 파일은 변경하지 않았다. 작업 시작 전부터 Git status에 보였던 6개 tracked EOL/stat 항목과 이전 untracked artifact/tmp는 수정·정리하지 않았다.

## 테스트 결과

- Harness TDD: 7 passed
- Readiness fixture: 4 passed
- Focused regression: 38 passed
- Full pytest: 545 passed, 4 skipped, 7 warnings
- Ruff: pass
- `git diff --check`: pass
- Artifact security audit: pass
- Code review: actionable defect 0

## 다음 단계

1. 사용자/기관이 외부 전송, provider/model, 보존·학습·지역 조건, case와 호출 상한 승인
2. 별도 승인된 동일 evidence Groq/Gemini Live 평가
3. Semantic support 평가와 실제 Streamlit 운영 UAT
4. TF027 원본 page 11 사람 검수와 이중 승인
5. 최종 v1.0 회귀 후 `v1.0-rc1` 복구 기준점 저장 여부 승인

## Pending

- Provider Live와 실제 외부 전송
- Controlled candidate semantic publication
- TF027 image gold
- 36-case 실제 운영 UAT

## 중단 조건 여부

중단 조건은 발생하지 않았다. 외부 provider와 vision 호출이 필요한 단계는 승인 경계 뒤에 유지했고 이번 단계에서는 0회다.

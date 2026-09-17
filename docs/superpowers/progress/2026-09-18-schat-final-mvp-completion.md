# SCHAT Final MVP Completion Checkpoint

- 완료일: 2026-09-18
- 현재 공식 기준점: `v0.9`
- 이전 공식 기준점: `v0.8` — 기존 tag/commit 보존
- 상태: 안정 복구 기준점 저장 완료

## 완료 항목

- Frozen reranker bottleneck 분석과 6개 안전 변형 비교
- Production retrieval 최종 유지 판정
- E5 vector storage 불필요 판정
- MM003 sparse table parser/search 해결
- Approved table UAT 5/5 Top-10
- Controlled-generation invalid/pending extractive fallback
- Mixed image+table routing과 image pending gate
- Local web UAT, Q006 zero-call, multi-document 회귀
- Performance 측정
- Full regression, Ruff, diff check, code review, security audit
- 현재정본/변경이력/복구기준점/현황판 갱신

## 변경 파일

Production:

- `mvp/controlled_generation.py`
- `mvp/evidence_routing.py`

Evaluation/test/docs:

- `tools/reranker_bottleneck_evaluate.py`
- `tools/structured_table_evidence.py`
- 관련 5개 test 파일
- `docs/rag/60_SCHAT_FINAL_MVP_COMPLETION_RESULT.md`
- `docs/현재정본/`, `변경이력.md`, `복구기준점.md`
- `artifacts/2026-09-18_schat-final-mvp-completion/`

## 테스트 결과

- Full pytest: 534 passed, 4 skipped, 7 warnings
- Focused regression: 156 passed, 2 warnings
- Ruff: passed
- git diff --check: passed
- Security: exact source 0, forbidden field 0, secret marker 0
- Code review: actionable defect 0

## Pending

- Provider Live와 semantic entailment: 외부 전송 승인 대기
- TF027 image workflow: human review 대기
- Production table persistence: repository schema migration 별도 설계 필요
- E5 production: latency/memory/safe reranker 근거 부족

## 중단 조건

전체 작업을 막는 중단 조건은 발생하지 않았다. Provider/image/table-production track은 승인 또는 대규모 구조 변경이 필요해 fail-closed pending으로 분리했다.

## 저장 결과

사용자 승인에 따라 선택 파일 stage → staged security re-audit → commit → annotated `v0.9` tag → `boha-rag`와 tag push 순서로 하나의 안정 복구 기준점에 저장했다. 제외 대상과 기존 `v0.8` tag는 변경하지 않았다.

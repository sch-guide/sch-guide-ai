# SCHAT Multimodal MVP Phase 00 준비 상태

- 기록일: 2026-09-17
- 브랜치: `boha-rag`
- 상태: **완료 — text/table track 진행 가능**
- Production 변경: 0건
- 실제 Groq/Gemini generation 호출: 0회
- Git commit/push: 0회

## 완료 항목

- 기존 ingestion, retrieval baseline, Facet-slot, validator, presentation sidecar 범위를 확인했다.
- generation provider가 `disabled`, `llm_approved=false`임을 확인했다.
- 실제 외부 provider 호출 없이 interface/schema/prompt/Mock validator를 준비할 수 있음을 확인했다.
- `TF027`은 workflow image sequence를 사람 검수 없이 확정할 수 없으므로 `needs_human_review=true`를 유지한다.
- `TF027`과 image-dependent track을 현재 aggregate와 production 대상에서 제외했다.
- 기존 table-aware retrieval은 standalone BM25보다 명확한 개선이 아니므로 production retrieval 변경 대상에서 제외했다.

## 변경 파일

- 이 progress checkpoint만 갱신했다.

## 테스트 결과

- 이 phase는 read-only readiness 확인 단계이므로 신규 pytest 대상이 없다.
- catalog 105 chunks, 기존 retrieval/UAT 결과와 production module 무변경 상태는 직전 검증 결과를 사용한다.

## Pending track

- 실제 LLM generation 품질 평가는 provider 및 병원 데이터 외부 전송 승인 전까지 pending이다.
- Image/vision retrieval과 `TF027` gold 확정은 사람 검수 전까지 pending이다.

## 다음 단계

1. 검증된 Answer를 보존하는 text style routing과 UI를 TDD로 구현한다.
2. Production에 연결하지 않는 controlled paraphrasing 계약과 fail-closed Mock validator를 구현한다.
3. Evaluation-only structured table record, citation, retrieval/UI를 구현한다.
4. Text/table/image/mixed evidence-type routing metadata를 추가하되 retrieval 자체는 바꾸지 않는다.
5. 통합 UAT, 전체 회귀, artifact 보안 감사를 수행한다.

## 중단 조건 여부

- 현재 발생한 중단 조건 없음.
- Provider 미승인과 image gold 부족은 해당 track의 pending 사유일 뿐 text/table 전체 작업의 중단 조건이 아니다.

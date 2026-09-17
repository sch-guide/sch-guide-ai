# SCHAT Multimodal MVP Phase 04 — Evidence-type routing

- 상태: 완료
- Production retrieval 동작 변경: 없음
- 실제 generation/vision API 호출: 0회
- Git commit/push: 0회

## 완료 항목

- Generic text/table/image candidate router를 구현했다.
- QueryPlan에 `evidence_types`와 `evidence_route_status` trace metadata를 추가했다.
- 비교, 제품/제제별, 보관, 온도, 속도, 용량, 간격, 수치 lookup은 `table + text` 후보로 기록한다.
- 일반 procedure/fact/purpose/preparation/cautions는 text 후보를 유지한다.
- 명시적 image/화면/workflow 질의는 `image`, `pending_image_review`로 기록한다.
- Routing metadata는 retrieval, evidence gate 또는 answerability에 연결하지 않았다.

## 변경 파일

- `mvp/evidence_routing.py`
- `mvp/query.py`
- `tests/test_evidence_routing.py`

## 테스트 결과

- Red 확인: module 미구현 `ModuleNotFoundError`
- Routing 집중 테스트: 3 passed
- 기존 pilot/UAT/monitoring/query generalization 회귀: **130 passed, 4 warnings**
- Q006은 `domain=out_of_scope`를 유지한다.

## 안전 계약

- Router는 gold, chunk ID 또는 질문별 예외를 읽지 않는다.
- Image route는 retrieval/vision을 실행하지 않고 pending으로만 기록한다.
- Q006 zero-call, evidence gate와 validator를 변경하지 않았다.

## Pending track

- TF027 및 image/vision은 사람 검수와 승인 전까지 pending이다.
- Table routing의 production retrieval 연결은 기존 table-aware 성능이 명확히 개선되지 않아 적용하지 않는다.

## 다음 단계

- 통합 offline UAT, 전체 regression, raw-free artifact, 결과 문서와 code review를 수행한다.

## 중단 조건 여부

- 없음.

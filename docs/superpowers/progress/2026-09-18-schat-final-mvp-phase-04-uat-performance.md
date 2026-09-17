# SCHAT Final MVP Phase 04 — Routing, Web UAT, Multi-document, Performance

- 완료일: 2026-09-18
- 외부 API 호출: 0회
- Image 임상 해석: 0건

## 완료 항목

- text/table/image/mixed evidence-type routing metadata를 재검증했다.
- image와 table cue가 동시에 있는 요청은 `mixed` 후보로 기록하되 `pending_image_review`로 안전 차단하도록 완성했다.
- TF027은 계속 human review pending이며 production gold/answerability에 연결하지 않았다.
- Local Streamlit text renderer와 table evaluation UI를 AppTest로 검증했다.
- Q006 out-of-scope/provider zero-call을 재검증했다.
- 기존 repository document filter, context document boundary, multi-document conflict citation 검증을 재실행했다.

## 집중 회귀

- Routing, local web UAT, table, controlled generation, presentation
- SourceUnit/Facet-slot, AnswerCoverage, parent atomicity
- Sedation UAT 45/45 및 Q006 zero-call
- Local repository document isolation과 cross-document conflict

결과: **156 passed, 2 warnings**. Warning은 기존 FastEmbed MiniLM mean-pooling 안내이며 dependency/model을 변경하지 않았다.

## 성능 측정

| 항목 | 결과 |
|---|---:|
| Table PDF extraction cold startup | 18,480.12 ms |
| Table lookup mean / p95 | 61.91 / 88.74 ms |
| 16-statement display-row assembly mean / p95 | 0.027 / 0.043 ms |
| E5 snapshot load (기존 검증값) | 11.46 ms |
| E5 query embedding mean / p95 (기존 검증값) | 192.98 / 282.16 ms |
| Existing reranker mean / p95 (기존 검증값) | 70.51 / 100.91 ms |

Table record extraction은 Streamlit `cache_resource`로 세션 중 재사용된다. E5는 production에서 채택하지 않았으므로 passage 재임베딩이나 snapshot loading을 production startup에 추가하지 않는다.

## 변경 파일

- `mvp/evidence_routing.py`
- `tests/test_evidence_routing.py`
- 이 progress 문서

## 다음 단계

- Final full regression, Ruff, diff check, raw-free artifact/security audit, code review와 현재정본 갱신을 수행한다.

## Pending / 중단 조건

- Image/TF027: 사람 검수 및 승인된 image-dependent gold 대기
- Provider Live/semantic entailment: 외부 전송 승인 대기
- Production table persistence: repository schema migration 범위이므로 보류
- 중단 조건 발생: 없음. 각 pending track은 fail-closed 유지

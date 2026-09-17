# SCHAT Multimodal MVP Phase 06 — 로컬 웹 UAT 및 최종 회귀

- 상태: 완료
- 기준: `2026-09-17-schat-multimodal-mvp-phase-05-final.md`
- 실제 Groq/Gemini generation 호출: 0회
- Production retrieval 변경: 0건
- Git commit/push: 0회

## 완료 항목

- 완료된 Phase 01~05를 재구현하지 않고 현재 상태를 검토했다.
- Text answer UX는 검증된 `Answer`와 metadata-only presentation sidecar를 production Streamlit renderer로 렌더링했다.
- Procedure/list 원문 marker와 UI outer numbering이 중복되지 않고 citation source button이 유지되는 것을 Streamlit `AppTest`로 확인했다.
- Controlled generation은 provider-neutral Mock contract와 fail-closed validator까지만 존재하며 production/provider 경로에는 연결되지 않은 상태를 확인했다.
- 실제 로컬 수혈 PDF와 catalog를 사용하는 table evidence Streamlit UI에서 승인된 질문의 구조화 표 검색과 citation metadata 표시를 확인했다.
- Evidence-type routing은 trace metadata만 기록하고 retrieval, evidence gate, answerability를 변경하지 않는 것을 회귀 검증했다.
- Image/vision과 TF027은 `pending_human_review` 상태를 유지했다.
- Q006 out-of-scope/zero-call 경계와 기존 Q002 Facet-slot 및 query generalization 회귀를 유지했다.

## 이번 단계 변경 파일

- `tests/test_multimodal_web_uat.py`
  - production answer renderer의 로컬 Streamlit UAT
  - 실제 로컬 수혈 catalog/PDF 기반 table evidence UI UAT
- `AGENTS.md`
  - 기존 내용 변경 없이 공백 오류 2건만 정리
- 이 progress checkpoint

Production retrieval 모듈과 설정에는 실제 내용 diff가 없다. 기존 `BM25 + semantic + RRF + reranker` 구조를 유지한다.

## 웹 UAT 결과

| 경로 | 입력/근거 | 결과 | 외부 호출 |
|---|---|---|---:|
| Text answer UX | 합성 verified Answer + production renderer | marker 중복 없음, citation button 유지 | 0 |
| Table evidence UI | 로컬 수혈 PDF/catalog + 승인 table 질문 | 구조화 row 검색 및 citation metadata 표시 | 0 |

수동 로컬 검수는 다음 명령으로 가능하다.

```powershell
.venv\Scripts\streamlit.exe run tools\table_evidence_app.py
```

이 UI는 evaluation-only이며 source text를 artifact에 저장하지 않는다.

## 테스트 및 정적 검사

- 새 Streamlit UAT: **2 passed**
- Multimodal/SourceUnit/query 집중 회귀: **155 passed, 3 warnings**
- 전체 pytest: **503 passed, 4 skipped, 6 warnings**
- Ruff (`mvp`, `tests`, `tools`): 통과
- `git diff --check`: 통과
- Code review: 추가 조치가 필요한 production correctness, safety 또는 regression 결함 없음
- Artifact/raw-text 안전 계약: 기존 raw-free audit 회귀 통과

Warning은 기존 FastEmbed multilingual MiniLM pooling 기본값 안내다. Embedding과 production dependency는 변경하지 않았다.

`pip check`는 현재 개발 환경의 `huggingface-hub 1.31.0`에 optional transfer helper `hf-xet`가 없다고 보고했다. 이번 단계에서 추가하거나 변경한 dependency가 아니고 전체 회귀는 통과했으므로, 승인되지 않은 package 설치는 수행하지 않았다.

## 상태 판정

- Text answer UX: 로컬 웹 UAT 가능
- Controlled generation: Mock validator 완료, provider 미연결·Live 보류
- Table evidence/retrieval/citation/UI: evaluation-only 로컬 웹 UAT 가능
- Evidence-type routing: metadata-only 회귀 통과
- Image/vision: pending 유지
- Provider Live: 금지 유지, 실제 호출 0회

## 다음 단계

- 병원 데이터 외부 전송 정책과 provider Live가 별도로 승인되기 전에는 controlled generation을 연결하지 않는다.
- TF027 사람 검수와 안전한 vision 경로가 승인되기 전에는 image/vision 임상 해석을 시작하지 않는다.
- Production table retrieval 연결은 명확한 성능 개선과 별도 승인 전까지 보류한다.

## 중단 조건 여부

- 새 중단 조건 발생 없음.
- 승인된 로컬/offline 범위는 완료했으며, 남은 항목은 외부 전송 또는 image 임상 해석 승인이 필요한 pending track이다.

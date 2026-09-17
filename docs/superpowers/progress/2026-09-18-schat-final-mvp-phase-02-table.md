# SCHAT Final MVP Phase 02 — Table Evidence

- 완료일: 2026-09-18
- 기준 복구점: `v0.8`
- 외부 API 호출: 0회
- Production text retrieval 변경: 0건

## 완료 항목

- MM003 miss를 gold, row link, header inheritance, parser, retrieval, citation 경계로 분리 진단했다.
- Gold와 citation mapping은 유효했지만 PDF grid parser가 다중 열 본문의 셀 값을 비워 둔 채 첫 열 label만 추출해, 실제 substantive catalog row가 검색 단위에 포함되지 않는 것이 직접 원인이었다.
- 다중 열 표에서 본문 행의 75% 이상이 한 셀 이하만 추출되는 경우를 `sparse_body`로 일반 판정하고, 기존 catalog parent-group fallback을 재사용했다.
- table-only scorer에서 정확한 숫자+단위/시간 token을 helper Korean n-gram보다 높게 가중했다.
- 질문 문자열, chunk ID, gold ID 특례를 production/evaluation logic에 추가하지 않았다.

## 결과

| 항목 | 변경 전 | 변경 후 |
|---|---:|---:|
| Structured tables | 5 | 5 |
| Search rows | 33 | 38 |
| Approved cases | 5 | 5 |
| Hit@1 | 0.8 | 0.4 |
| Hit@3 | 0.8 | **1.0** |
| Hit@5 | 0.8 | **1.0** |
| Hit@10 | 0.8 | **1.0** |
| Recall@10 | 0.8 | **1.0** |
| MM003 first gold rank | 없음 | **2** |

Hit@1 하락은 sparse page의 substantive rows가 새로 포함되면서 MM001/MM005의 gold가 1위에서 2위가 된 결과다. Top-3부터 모든 승인 case가 회수되므로, 값이 없는 grid label을 1위로 두던 이전 결과보다 실제 table evidence 계약에 맞다.

## 변경 파일

- `tools/structured_table_evidence.py`
- `tests/test_structured_table_evidence.py`
- `tests/test_schat_multimodal_mvp.py`
- `artifacts/2026-09-18_schat-final-mvp-completion/table/`
- 이 progress 문서

## 테스트 결과

- Table scorer/parser/UAT 집중: `8 passed`
- Artifact 원문 저장: 0건
- Citation document/page/table/row identity: 유지

## 다음 단계

- Controlled generation의 publish/fallback 결정을 명시적으로 고정한다.

## Pending / 중단 조건

- Production repository에 table cell/geometry를 저장하는 작업은 Local/Cloud schema migration이 필요한 대규모 변경이므로 이번 범위에서 보류한다.
- Local evaluation/UAT UI는 exact table text를 메모리에서만 표시한다.
- 중단 조건 발생: 없음

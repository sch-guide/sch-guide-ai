# SCHAT Q002 Prompt Budget 최적화 결과

- 구현·검증일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 계획: `docs/rag/04_PROMPT_BUDGET_OPTIMIZATION_PLAN.md`
- 적용안: C안 1차, A안 group-level evidence serialization 경량화
- 요청 token budget: 3500 유지
- 검증 방식: MockTransport만 사용, 실제 Groq 호출 없음
- 상태: 모든 합격 기준 통과, 실제 Groq 호출 승인 대기

## 1. 결론

Q002의 12개 evidence 원문을 축약·요약·삭제하지 않고, 같은 EvidenceGroup에서 반복되던 document, page, section과 location metadata를 group-level로 한 번만 표현하도록 변경했다. group 안에서 값이 다르면 각 source에 override를 유지한다.

보수적 요청 예약량은 3501에서 3130으로 371 tokens 감소했다. `GROQ_REQUEST_TOKEN_BUDGET=3500`에서 headroom은 370 tokens이며, 합격 기준 `max(256, ceil(3130 × 8%)) = 256`을 충족했다. 따라서 budget을 4096으로 올리지 않았고 system prompt도 줄이지 않았다.

Q002는 pre-budget과 post-budget 모두 필수 gold stage 10/10, required group 5/5, 성인·소아 branch와 원문 순서를 유지했다. parent partial inclusion은 0건이다. MockTransport는 정확히 1회 호출됐고 5개의 exact extractive statement와 chunk citation이 서버측 실제 Chunk를 기준으로 검증됐다.

Q006은 기존과 같이 생성 전에 차단됐으며 MockTransport 호출은 0회다. 실제 Groq endpoint나 외부 네트워크는 호출하지 않았다.

## 2. 구현 내용

### Compact evidence schema

`mvp/ai.py`에 prompt evidence schema version 2를 추가했다.

```json
{
  "schema_version": 2,
  "groups": [
    {
      "group_id": "g4",
      "branch": "adult",
      "document": "실무지침서_진정간호.pdf",
      "page": 4,
      "section": "...",
      "location": "...",
      "sources": [
        {"chunk_id": "...00019", "order": 18, "text": "원문"},
        {"chunk_id": "...00020", "order": 19, "text": "원문"}
      ]
    }
  ]
}
```

다음 불변식을 적용했다.

- 모든 source의 `chunk_id`, `order`, 원문 `text` 유지
- 원문 text의 공백·문장·표 행을 serializer가 변경하지 않음
- 동일 group의 document/page/section/location은 group-level에 한 번만 기록
- 하나라도 값이 다르면 group-level 대표값을 사용하지 않고 각 source에 실제 값을 override
- group ID는 prompt 구조용이며 citation ID로 인정하지 않음
- selected group과 hit는 최종적으로 기존 source order를 유지

### Citation 신뢰 경계

compact prompt metadata는 모델이 문맥을 읽기 위한 표현일 뿐 citation의 신뢰 원본이 아니다. `validate_answer()`와 생성 후 citation 검증은 계속 서버측 `selected` Hit의 실제 Chunk map을 사용한다.

- quote가 실제 `Chunk.text`에 있는지 검사
- `chunk_id`가 실제 선택된 Chunk인지 검사
- 표시할 page, section, location과 document는 서버측 Chunk에서 조회
- 숫자·단위·행동·완전 문장과 procedure source order 검증 유지

compact metadata가 누락되거나 변조돼도 서버측 Chunk와 일치하지 않는 citation은 통과할 수 없다.

### Token trace

기존 trace에 다음 숫자 중심 진단을 추가했다.

- `prompt_schema_version`
- group별 `source_tokens`, `metadata_tokens`, `serialized_tokens`
- `estimated_request_tokens`
- `request_token_budget`
- `request_token_headroom`

trace에는 원문이나 전체 prompt를 추가 복제하지 않는다. `AI_VERSION`은 prompt/cache 계약 변경을 구분하기 위해 9에서 10으로 증가했다.

## 3. Q002 group별 token 결과

현재 로컬 `o200k_harmony` 계산 결과다.

| group | branch | chunk | 원문 token | metadata token | 직렬화 합계 |
|---:|---|---|---:|---:|---:|
| G1 | common | 00013 | 19 | 75 | 94 |
| G2 | common | 00015 | 60 | 84 | 144 |
| G3 | common | 00016 | 19 | 83 | 102 |
| G4 | adult | 00019~00022 | 313 | 206 | 519 |
| G5 | pediatric | 00023~00027 | 431 | 203 | 634 |
| 합계 | 성인·소아 유지 | 12 chunks | 842 | 651 | 1493 |

원문 token 합계 842는 계획 시 측정과 동일하다. 경량화는 원문이 아니라 반복 metadata에서만 이루어졌다.

## 4. Budget 전후 비교

| 항목 | 경량화 전 | 경량화 후 |
|---|---:|---:|
| 보수적 요청 예약량 | 3501 | 3130 |
| request budget | 3500 | 3500 |
| headroom | -1 | 370 |
| 요구 headroom | 해당 없음 | 256 |
| 필수 group 전체 포함 | 실패 | 성공 |
| post-budget 필수 gold recall | 7/10 | 10/10 |
| Q002 transport 호출 | 0회 | MockTransport 1회 |

경량화 후 headroom 비율은 약 10.6%이며 최소 요구치 256 tokens보다 114 tokens 크다. 이에 따라 조건부 B안은 활성화하지 않았다.

- `GROQ_REQUEST_TOKEN_BUDGET`: 3500 유지
- completion 상한: 768 유지
- system prompt: 변경 없음
- 4096 증가: 미적용

## 5. Q002 합격 기준

| 기준 | 결과 | 판정 |
|---|---|---|
| pre-budget 필수 gold recall | 10/10 | 통과 |
| post-budget 필수 gold recall | 10/10 | 통과 |
| required group 유지 | 5/5 | 통과 |
| required procedural unit 유지 | coverage loss 없음 | 통과 |
| parent partial inclusion | 0건 | 통과 |
| 성인 branch | 유지 | 통과 |
| 소아 branch | 유지 | 통과 |
| source order 역전 | 0건 | 통과 |
| duplicate | 0건 | 통과 |
| evidence 원문 text | 12/12 exact 보존 | 통과 |
| MockTransport 호출 | 정확히 1회 | 통과 |
| exact citation | supported | 통과 |
| headroom | 370 ≥ 256 | 통과 |

mock 응답은 required group별로 실제 원문에 존재하는 완전한 action 문장 하나씩, 총 5 statements를 반환하도록 구성했다. 1 procedural unit을 1 statement로 강제하지 않았고, statement 수를 맞추기 위한 단계 병합이나 원문 합성은 하지 않았다.

## 6. Q006 회귀

Q006 “화성 우주선의 궤도 계산 공식은?”은 기존 `out_of_scope` 경계에서 종료됐다.

- MockTransport 호출: 0회
- 실제 Groq 호출: 0회
- 반환 문구: `등록된 지침서에서 확인할 수 없습니다.`

compact serializer는 evidence gate를 통과한 요청에서만 사용되므로 Q006 동작에 영향을 주지 않았다.

## 7. 테스트와 정적 검사

추가·갱신한 테스트는 다음을 검증한다.

- 공통 document/page/section/location의 group-level 단일 표현
- 서로 다른 metadata의 source-level override
- 모든 chunk ID와 원문 text의 정확 보존
- source order 보존
- compact payload에서 5개 이후의 필수 source도 유지
- 서버측 Chunk page/section/location을 유지한 citation 검증
- 기존 UI와 RAG MockTransport payload 계약
- required/optional, parent 원자성 및 기존 생성 안전 gate 회귀

실행 결과:

- compact/evidence 집중 테스트: `11 passed`
- RAG contract 포함 집중 테스트: `61 passed`
- 전체 테스트: `239 passed, 1 skipped`
- Ruff 정적 검사: 통과
- `git diff --check`: 오류 없음
- acceptance assertion 재실행: 통과

## 8. 코드 검수 결과

이번 변경 diff와 호출 경로를 검수한 결과 수정이 필요한 추가 결함은 발견되지 않았다.

- actual citation source는 compact JSON이 아니라 서버측 Chunk map이다.
- required/optional 판정과 `mvp/evidence.py`는 이번 단계에서 변경하지 않았다.
- parent 원자 선택과 post-budget coverage gate는 유지됐다.
- BM25, tokenizer, query expansion, temporal rerank, embedding, RRF와 `mvp/context.py`는 변경하지 않았다.
- system prompt와 budget 값은 변경하지 않았다.
- 외부 HTTP는 MockTransport로만 처리했다.

잔여 위험은 token 추정치가 실제 Groq usage와 완전히 동일하지 않다는 점이다. 현재 계산에는 메시지 포맷 여유와 10% 안전 여유가 이미 포함돼 있고 추가 headroom 370도 확보했지만, 실제 usage/latency 확인은 사용자가 Groq 호출을 별도로 승인한 뒤에만 가능하다.

## 9. 산출물

기존 phase1/phase2 및 BM25 artifacts는 덮어쓰지 않았다. 새 산출물은 다음 디렉터리에 저장했다.

`artifacts/2026-09-13_rag-prompt-budget-optimization/`

- `prompt_budget_report.json`
- `q002_group_tokens.csv`
- `review.html`

`review.html`에서 Q002 pre/post 필수 recall, required group 보존, parent partial 여부, source order, 원문 보존, group별 원문/metadata token과 MockTransport 호출 결과를 사람이 확인할 수 있다.

## 10. 변경 파일

이번 단계의 변경 범위:

- `mvp/ai.py`: compact group serializer, prompt schema v2, token trace, AI version 10
- `mvp/search_trace.py`: compact prompt token 진단 기본 필드
- `tests/test_rag_evidence_groups.py`: serializer/override/서버 citation 테스트
- `tests/test_rag_contract.py`: compact payload 계약 반영
- `tests/test_mvp_chat.py`: UI mock payload 계약 반영
- `tools/rag_prompt_budget_evaluate.py`: Q002/Q006 전용 mock 평가와 artifacts 생성
- 새 prompt-budget artifacts와 이 결과 문서

이번 단계에서 수정하지 않은 파일과 동작:

- `mvp/evidence.py`의 required/optional 및 coverage 정책
- `mvp/context.py`의 1차 multi-chunk bundle
- BM25/retrieval/embedding/RRF 관련 코드
- 기존 평가 artifacts와 원본 문서

## 11. 승인 권고와 작업 중지

group-level evidence serialization A안은 모든 합격 기준을 충족했다. 현재 3500 budget에서 안정 여유가 확보됐으므로 system prompt 중복 병합이나 4096 증가는 필요하지 않다. 이 변경은 승인 후보로 권고한다.

이 결과 문서 작성으로 작업을 멈춘다. 실제 Groq 호출, 추가 RAG 단계, system prompt 축소와 budget 증가는 사용자 승인 전 진행하지 않는다.

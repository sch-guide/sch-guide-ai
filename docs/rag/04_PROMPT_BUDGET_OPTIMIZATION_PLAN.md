# SCHAT Q002 Prompt Budget 최적화 계획

- 작성일: 2026-09-13
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 결과: `docs/rag/03_EVIDENCE_AND_GENERATION_RESULT.md`
- 대상 문제: Q002 필수 evidence의 보수적 요청 예약량 3501 tokens / 현재 한도 3500
- 단계: 분석·설계 완료, 구현 승인 대기
- 최종 권고: **C. A를 먼저 적용하고, mock 재측정에서 안전 여유가 부족할 때만 4096으로 소폭 증가**

## 1. 결정 요약

현재 Q002는 retrieval이나 evidence 부족 문제가 아니다. pre-budget에서 필수 gold stage 10/10, 성인·소아 branch, 다섯 required group과 parent 원자성을 모두 확보했다. 실패 원인은 보수적 요청 예약량이 3501로 현재 `GROQ_REQUEST_TOKEN_BUDGET=3500`을 1 token 초과하면서 소아 parent group 전체가 제외되는 budget cliff다.

우선 evidence 원문을 그대로 유지하면서 반복 metadata를 group-level로 한 번만 직렬화한다. 현재 system prompt를 그대로 둔 보수적 로컬 측정에서 예상 예약량은 3501에서 약 3121로 감소한다. 3500 한도 안에서 379 tokens의 여유가 생기므로, 이 측정이 구현 후에도 재현되면 budget 값은 올리지 않는다.

다만 379 tokens는 현재 Q002 한 사례의 예상치다. 실제 구현의 JSON schema, 충돌 정보, 질문 길이 또는 tokenizer 포맷 변화로 안전 여유가 작아질 수 있다. 따라서 mock 검증에서 아래 headroom 기준을 충족하지 못할 때만 budget을 4096으로 올리는 조건부 C안을 권고한다. 4096은 단순히 3501에 맞춘 값이 아니라 현재 completion 예약 768과 로컬 분당 한도 8000 안에서 한 요청에 약 15%의 추가 여유를 주는 명시적 경계다.

실제 Groq 호출은 이번 계획과 다음 구현 검증에서도 하지 않는다. MockTransport가 Q002 generation gate를 통과하는 지점까지만 확인한다.

## 2. 승인 경계와 고정 불변식

이 문서는 구현을 승인하지 않는다. 사용자 승인 전에는 `mvp/ai.py`, budget 값, prompt, evidence schema, 테스트와 artifacts를 수정하지 않는다.

다음 기준선은 변경하지 않는다.

- 승인된 SCHAT BM25, tokenizer, query expansion과 temporal rerank
- 현재 embedding, semantic search, RRF와 1차 context bundle
- Q002 pre-budget 필수 gold recall 10/10
- 현재 `EvidenceGroup` required/optional 판정
- parent group 원자성, 성인·소아 branch와 source order
- exact quote/chunk citation 검증
- completion 상한 768
- Q006의 생성 호출 0회
- 실제 Groq 호출 금지

## 3. 현재 budget 의미와 실패 원인

`GROQ_REQUEST_TOKEN_BUDGET=3500`은 Groq 모델의 context window가 아니다. SCHAT가 요청 전에 예약하는 보수적 총량이다.

현재 계산은 다음을 포함한다.

1. `o200k_harmony`로 system/user message content를 계산
2. 메시지 포맷 고정 여유
3. 계산된 입력에 10% 안전 여유
4. completion 예약 768 tokens

Q002 전체 required group의 예약량은 3501이다. 선택기는 group을 원자적으로 취급하므로 1-token 초과를 해결하기 위해 소아 00023~00027 중 일부만 자르지 않고 group 전체를 제외한다. 그 결과 선택된 예약량은 2651, 필수 gold recall은 7/10이 되고 `missing_evidence_group`에서 MockTransport 호출도 0회로 차단된다.

즉 3501이라는 값은 실제 prompt 입력만의 크기가 아니며, 131K 모델 context에 근접했다는 의미도 아니다. 문제는 현재 애플리케이션의 보수적 per-request 경계가 원자 group 하나를 채택하거나 제외하는 임계점에 정확히 걸린 것이다.

## 4. Q002 group별 token 분석

아래 값은 현재 프로젝트의 `o200k_harmony` tokenizer로 로컬 측정했다. 원문 token은 각 chunk의 `text`만 합한 값이다. metadata overhead는 JSON key, chunk ID, document, section, page, location, order, 구조 구분자를 포함한 직렬화 token에서 원문 token을 뺀 값이다.

경량화 예상치는 다음 보존 schema를 기준으로 한다.

- 동일 group의 document, section, page, location과 branch는 group-level에서 한 번 표현
- 각 source에는 `chunk_id`, source `order`, 원문 `text` 유지
- group 내 값이 다른 metadata만 해당 source의 override로 유지
- JSON 공백만 제거하며 원문 공백과 문장은 변경하지 않음

| group | branch | chunk | 원문 token | 현재 metadata overhead | 현재 직렬화 | 예상 metadata overhead | 예상 직렬화 | 예상 절감 |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| G1 | common | 00013 | 19 | 73 | 92 | 75 | 94 | -2 |
| G2 | common | 00015 | 60 | 83 | 143 | 84 | 144 | -1 |
| G3 | common | 00016 | 19 | 81 | 100 | 83 | 102 | -2 |
| G4 | adult | 00019~00022 | 313 | 419 | 732 | 206 | 519 | 213 |
| G5 | pediatric | 00023~00027 | 431 | 343 | 774 | 203 | 634 | 140 |
| 합계 | 성인·소아 유지 | 12 chunks | 842 | 999 | 1841 | 651 | 1493 | 348 |

singleton group G1~G3은 group wrapper 때문에 각각 1~2 tokens 늘어난다. singleton만 별도 schema로 최적화하면 조금 더 줄일 수 있지만 serializer 분기와 검증 복잡도가 커진다. 전체적으로 348 tokens를 절감하므로 1차 구현에서는 모든 group에 하나의 일관된 schema를 쓰는 편이 안전하다.

현재 system 지시를 그대로 유지하고 위 group schema만 적용한 전체 보수적 예약량 예상치는 약 3121이다.

| 항목 | 현재 | A 적용 예상 |
|---|---:|---:|
| 전체 예약량 | 3501 | 약 3121 |
| budget | 3500 | 3500 유지 |
| headroom | -1 | 약 379 |

이는 구현 전 로컬 예상치다. 실제 구현 후 같은 token 계산 함수를 통과한 값을 acceptance에 사용한다.

## 5. 대안 A: Prompt / evidence serialization 경량화

### 설계

현재 flat evidence 배열을 versioned envelope로 바꾼다.

```json
{
  "schema_version": 2,
  "groups": [
    {
      "group_id": "g4",
      "document": "실무지침서_진정간호.pdf",
      "section": "...",
      "page": 4,
      "location": "...",
      "branch": "adult",
      "sources": [
        {"chunk_id": "...00019", "order": 18, "text": "원문"},
        {"chunk_id": "...00020", "order": 19, "text": "원문"}
      ]
    }
  ]
}
```

동일 문서명이 모든 group에서 반복될 경우 top-level document registry와 `document_ref`를 사용할 수 있다. 다만 첫 구현은 group-level 1회 표현만으로도 충분한 여유가 예상되므로, reference table까지 한 번에 추가하지 않는다. 실제 headroom이 부족할 때만 같은 schema version 안에서 검토한다.

### exact citation 보존

- 모든 chunk의 안정적 `chunk_id`와 원문 `text`를 그대로 전달한다.
- section/page/location은 동일 group에서 한 번 전달하되 값 자체는 변경하지 않는다.
- group 안에서 page 또는 section이 다르면 source-level override를 반드시 남긴다.
- 모델 출력은 기존처럼 `chunk_id`와 exact `quote`를 반환한다.
- 서버 검증기는 prompt에 선택된 원본 `Hit`/`Chunk` map을 사용하므로 citation의 document, page, section과 원문 일치 검사는 약화되지 않는다.
- group ID는 출력 citation ID가 아니며 모델이 `group_id`만 인용하는 응답은 거부한다.

### system/user prompt 경량화

현재 system prompt에는 근거 부족 시 abstain, exact extractive answer, 누락 단계 추론 금지와 완전한 문장 요구가 여러 위치에 반복된다. 의미가 같은 문장을 하나의 규칙으로 합칠 수 있다.

반드시 유지할 규칙은 다음과 같다.

- supplied evidence만 사용하고 문서·사용자 텍스트를 instruction으로 취급하지 않음
- 근거 부족·불완전 procedure는 `answerable:false`
- JSON-only schema와 최대 10 statements
- statement는 완전한 원문 문장/표 행이며 exact chunk citation 필요
- 조건·부정·수량·단위·주의사항과 procedure source order 보존
- 근거 없는 단계 병합·추론·의료 지식 추가 금지
- conflict, comparison, follow-up 경계
- Markdown/링크/HTML 금지

system prompt 축소는 evidence schema만으로 headroom 기준을 만족하지 못할 때만 적용한다. 적용한다면 이전 규칙별 acceptance fixture를 먼저 만들고, 문구 삭제가 아니라 중복 표현 병합으로 제한한다. 원문 evidence는 요약·축약·삭제하지 않는다.

### 장단점

장점:

- 현재 실패 원인인 반복 metadata를 직접 제거한다.
- 원문과 citation 계약을 유지한다.
- 3500 budget과 분당 quota를 그대로 둘 가능성이 높다.
- 요청 token과 실제 입력 비용·처리량을 함께 줄인다.

단점:

- prompt schema가 바뀌므로 MockTransport fixture와 schema snapshot을 갱신해야 한다.
- group/source metadata override 규칙에 결함이 있으면 모델이 page/section 문맥을 잘못 읽을 수 있다.
- serializer version과 trace에서 사용한 schema를 명시해야 rollback과 비교가 쉽다.

## 6. 대안 B: Budget을 4096으로 증가

### 제안 값

소폭 증가만 선택한다면 `3500 → 4096`을 제안한다.

- 현재 3501에 맞춘 3502 같은 값은 질문·JSON 포맷의 작은 변화에 다시 실패한다.
- 4096은 현재 Q002에 595 tokens, 약 17%의 예약 여유를 준다.
- completion 예약 768은 변경하지 않는다.
- 현재 로컬 `GROQ_MINUTE_TOKEN_BUDGET=8000`보다 충분히 작아 한 요청의 최악 예약이 분당 한도를 독점하지 않는다. 다만 4096 예약 두 건은 8192가 되어 같은 1분 창에서 둘째 요청이 대기 또는 차단될 수 있다.

현재 허용 모델인 Groq `openai/gpt-oss-20b`와 `openai/gpt-oss-120b`는 공식 문서상 context window 131,072, 최대 output 65,536을 제공한다. SCHAT의 4096 총 예약과 completion 768은 모델 context 제한보다 매우 작다. 실제 운영에서 더 직접적인 경계는 Free Plan의 8K TPM과 SCHAT 자체의 분당 8000-token quota다. [Groq GPT-OSS 20B 모델 문서](https://console.groq.com/docs/model/openai/gpt-oss-20b), [Groq 모델 및 rate-limit 문서](https://console.groq.com/docs/models), [Groq Free Plan rate limits](https://console.groq.com/docs/rate-limits)

### 비용과 속도 영향

budget 증가는 예약 가능한 상한일 뿐 실제 요청 token을 자동으로 늘리지 않는다. 현재 Q002 prompt가 그대로 3501 예약을 사용한다면 4096로 설정해도 실제 입력 내용은 변하지 않는다.

공식 GPT-OSS 20B 가격은 입력 $0.075/1M tokens, 출력 $0.30/1M tokens다. 추가 허용폭 596 tokens가 모두 입력으로 실제 사용되는 최악 가정에서도 호출당 추가 입력 비용은 약 $0.000045다. completion 상한을 유지하므로 출력 비용 상한은 증가하지 않는다. [Groq GPT-OSS 20B 모델 문서](https://console.groq.com/docs/model/openai/gpt-oss-20b)

속도도 실제 prompt가 길어질 때만 증가한다. 모델 페이지의 약 1000 tokens/s는 주로 출력 처리량 지표이므로 596-token 상한 증가를 그대로 0.596초 지연으로 환산하지 않는다. 구현 후에는 MockTransport로 gate만 검증하고, 실제 latency는 별도 Groq 호출 승인이 난 뒤 측정한다.

### 장단점

장점:

- 구현이 가장 단순하고 현재 Q002의 3501 예약을 즉시 수용한다.
- prompt schema와 citation 검증을 바꾸지 않는다.
- rollback은 상수 한 개를 3500으로 되돌리면 된다.

단점:

- 반복 metadata 낭비를 그대로 유지한다.
- 작은 입력 증가가 누적되면 8K TPM에서 동시 요청 여유가 줄어든다.
- Q002 한 사례에 맞춘 상수 조정으로 보일 수 있고, 다음 큰 group에서 같은 cliff가 재발할 수 있다.
- 비용은 작지만 불필요한 input token과 처리량을 계속 사용한다.

## 7. 대안 C: A 적용 후 조건부 B

다음 두 단계로 적용한다.

1. group-level serialization을 구현하고 현재 3500 budget에서 전체 required group을 mock 평가한다.
2. 구현 후 실제 예약량과 headroom이 합격 기준을 충족하면 3500을 유지한다.
3. 합격 기준을 못 채울 때만 동일 변경 단위에서 원인을 기록하고, 별도 승인된 budget 변경으로 4096을 적용한다.

조건부 증가 기준은 다음과 같다.

- 필수 조건: 전체 required group을 넣은 예약량이 3500 이하여야 한다.
- 안전 여유 목표: 남은 headroom이 최소 `max(256 tokens, 예약량의 8%)`이어야 한다.
- 현재 예상 3121은 headroom 379, 약 10.8%이므로 두 조건을 충족한다.
- headroom이 조건보다 작으면 system prompt 중복 병합을 먼저 검증한다.
- evidence/schema 경량화와 안전한 지시 병합 후에도 조건을 못 맞출 때만 4096 증가를 제안한다.

이 방식은 prompt 낭비를 줄인 뒤 실제 필요가 입증된 만큼만 상한을 조정한다. 최종 권고안이다.

## 8. 대안 비교

| 기준 | A. 직렬화 경량화 | B. 4096 증가 | C. A 후 조건부 B |
|---|---|---|---|
| Q002 10/10 가능성 | 높음. 예상 예약 3121 | 높음. 현재 3501 수용 | 가장 높음 |
| parent 원자성 | 유지 | 유지 | 유지 |
| citation 정확성 | schema/override 테스트 필요, 원문·ID 유지 | 변화 없음 | A 검증 후 유지 |
| token 효율 | 가장 좋음 | 개선 없음 | 좋음 |
| 안전 headroom | 예상 379 | 현재 prompt 기준 595 | 측정 기반으로 보장 |
| 비용 | input 감소 | 실제 입력 증가 시 소폭 증가 | 불필요한 증가 최소화 |
| 속도 | input 감소 가능 | 실제 입력 증가 시 소폭 영향 | 측정 기반 최소 영향 |
| 구현 복잡도 | 중간 | 낮음 | 중간 |
| 운영 8K TPM 영향 | 개선 | 두 최대 예약 동시 처리 여유 감소 | 필요할 때만 감소 |
| rollback | serializer/schema version 복귀 | 상수 3500 복귀 | 각 단계 독립 rollback |
| 권고 | 1차 적용 | 단독 채택 기각 | 최종 권고 |

## 9. 명시적 기각안

다음 방식은 구현하지 않는다.

### required group을 optional로 임의 변경

budget 통과를 위해 evidence 의미를 바꾸는 방식이다. 성인 또는 소아 branch가 빠진 부분 답변을 허용하므로 기각한다.

### 필수 parent group 일부만 전송

현재 atomic group 불변식을 깨고 조건·주의·후속 문장이 잘릴 수 있다. 기각한다.

### gold stage를 줄여 통과

평가 기준을 구현 결과에 맞춰 낮추는 행위다. Q002 필수 gold 10개는 그대로 유지한다.

### 원문 evidence 요약 또는 삭제

exact quote와 문장 단위 citation 검증을 약화시키고 요약 과정에서 임상 조건이 사라질 수 있다. 원문 `text`는 그대로 전송한다.

### statement 수를 맞추기 위한 근거 없는 단계 병합

1 procedural unit과 1 statement를 일대일로 강제하지 않으며, 10 statements 제한을 맞추기 위해 서로 다른 근거를 임의로 합성하지 않는다. 한 grounded statement가 여러 evidence를 갖는 기존 구조만 유지한다.

## 10. 구현 예정 인터페이스

공개 `generate()`와 `Answer`/`Statement`/`Evidence` schema는 유지한다. 내부 serializer만 분리한다.

```python
# 미구현 의사코드
@dataclass(frozen=True)
class PromptEvidenceEnvelope:
    schema_version: int
    groups: tuple[PromptEvidenceGroup, ...]

def serialize_evidence_groups(
    groups: Sequence[EvidenceGroup],
) -> str: ...

def build_prompt_messages(
    question: str,
    plan: QueryPlan,
    groups: Sequence[EvidenceGroup],
) -> list[dict[str, str]]: ...
```

`prompt_messages()`는 현재 required-first group 선택과 원자 budget 책임을 유지하고 trial message 구성만 공통 serializer에 맡긴다. 호출자가 별도의 compact/non-compact 옵션을 선택하게 하지 않는다.

trace에는 다음을 추가한다.

- `prompt_schema_version`
- group별 `source_tokens`
- group별 `metadata_tokens`
- group별 `serialized_tokens`
- 전체 `estimated_request_tokens`
- `request_token_budget`
- `request_token_headroom`
- optional/required 제외 이유

token 수는 원문이나 prompt 내용을 trace에 복제하지 않고 숫자만 기록한다.

## 11. 테스트 및 mock 검증 계획

### Serializer 단위 테스트

- 모든 selected chunk ID와 원문 text가 정확히 한 번씩 존재한다.
- 동일 group metadata는 group-level에서 한 번만 직렬화된다.
- group 안에서 다른 page/section/location은 source override로 보존된다.
- source order가 JSON 배열에서 유지된다.
- 원문 공백·문장·표 행을 serializer가 변경하지 않는다.
- group ID를 citation으로 반환하면 검증이 실패한다.
- 기존 `chunk_id + exact quote` 응답은 검증을 통과한다.

### Budget 테스트

- Q002 전체 required group의 pre/post 필수 gold recall이 모두 10/10이다.
- 다섯 required group key와 모든 required procedural unit이 유지된다.
- parent partial inclusion은 0건이다.
- adult/pediatric branch가 모두 유지된다.
- source order 역전은 0건이다.
- 현재 3500에서 예상 예약량과 headroom을 snapshot이 아닌 상한 조건으로 검증한다.
- optional group이 예산으로 제외되면 gate를 막지 않고 trace에 이유가 남는다.
- required group이 실제로 초과하면 기존과 같이 MockTransport 0회로 차단된다.

### Mock generation 테스트

- Q002가 모든 필수 근거를 유지한 상태로 generation gate를 통과한다.
- MockTransport HTTP 호출은 정확히 1회다.
- mock 답변의 각 statement/quote/chunk citation이 exact 검증을 통과한다.
- 하나의 statement가 여러 evidence를 가질 수 있다.
- 근거 없는 단계 병합과 source-order 역전 응답은 거부된다.
- Q006은 MockTransport 호출 0회와 고정 답변을 유지한다.

실제 Groq endpoint, key, quota와 네트워크는 사용하지 않는다.

## 12. 합격 기준

1. Q002 pre-budget 필수 gold recall 10/10
2. Q002 post-budget 필수 gold recall 10/10
3. required group 100% 유지
4. parent partial inclusion 0건
5. 성인·소아 branch 모두 유지
6. source order 역전 0건
7. exact citation 검증 가능
8. Q006 LLM/MockTransport 호출 0회 유지
9. 실제 Groq 호출 0회
10. MockTransport 기준 Q002 generation gate 통과 및 정확히 1회 호출
11. 원문 evidence token과 text 불변
12. 현재 3500 유지 시 headroom 최소 `max(256, 예약량의 8%)`
13. 전체 테스트와 정적 검사 통과

3500에서 1~11은 충족하지만 12만 실패하면 system 지시 중복 병합을 검토한다. 안전한 중복 병합 후에도 12가 실패할 때만 4096 변경을 별도로 명시한다. 필수 coverage를 낮춰 합격시키지 않는다.

## 13. 구현 순서

사용자 승인 후에만 다음 순서로 진행한다.

1. 현재 Q002 group별 원문·metadata·전체 token 수를 회귀 fixture로 고정한다.
2. group-level versioned serializer와 metadata override를 구현한다.
3. 기존 system prompt를 그대로 둔 채 3500에서 Q002 mock 평가를 실행한다.
4. pre/post gold recall, group/unit/branch, parent 원자성, source order와 headroom을 검증한다.
5. headroom 기준이 부족할 때만 규칙별 테스트를 먼저 작성하고 system prompt의 의미 중복을 병합한다.
6. 그래도 기준이 부족하면 결과를 기록하고 4096 budget 변경 승인을 다시 요청한다. 자동 증가하지 않는다.
7. Q002 MockTransport 1회, Q006 0회와 exact citation을 검증한다.
8. 집중 테스트, 전체 테스트와 Ruff를 실행한다.
9. 기존 artifacts를 덮어쓰지 않고 새 디렉터리에 JSON/CSV/review.html을 생성한다.
10. 결과 문서를 작성하고 작업을 멈춘다. 실제 Groq 호출로 진행하지 않는다.

## 14. Rollback

- A rollback: group serializer 호출을 이전 flat evidence 배열로 되돌리고 prompt schema version을 복귀한다.
- system prompt를 병합했다면 해당 상수만 이전 문구로 복귀한다.
- B가 별도 승인되어 적용된 경우 `GROQ_REQUEST_TOKEN_BUDGET`을 3500으로 되돌린다.
- 새 mock artifacts는 기존 phase1/phase2 artifacts와 분리해 보존한다.

EvidenceGroup required/optional 판정, post-budget safety gate, BM25, retrieval/context, 원본 chunk와 기존 artifacts는 rollback 대상이 아니다.

## 15. 위험과 확인 사항

- token 추정기는 10% 포맷 여유와 completion 예약을 이미 포함하지만 실제 Groq usage와 완전히 같지는 않다. 실제 호출 승인 전에는 로컬 추정치와 mock payload 크기만 검증한다.
- group metadata가 동일하다는 판정은 값의 정확한 동등성으로 수행해야 한다. 다른 page 또는 section을 대표값 하나로 덮어쓰지 않는다.
- system prompt 단축은 작은 token 절감보다 안전 규칙 보존이 우선이다. group serializer만으로 기준을 만족하면 변경하지 않는다.
- 4096 증가는 모델 context상 안전하지만 Free Plan 8K TPM에서 최대 예약 두 건의 동시 여유를 줄인다.
- 현재 수치는 Q002에 한정된다. 더 긴 질문과 conflict metadata가 있는 fixture도 headroom 회귀 테스트에 포함해야 한다.

## 16. 사용자 승인 대기

최종 권고는 C안이다. 먼저 원문과 citation 정보를 그대로 유지한 group-level 직렬화로 예상 예약량을 약 3121까지 낮추고 3500 budget을 유지한다. 구현 후 headroom이 최소 256 tokens와 예약량의 8% 중 큰 값을 충족하지 못할 때만 system 지시 중복 병합을 검증하고, 그래도 부족할 경우 4096 증가를 별도로 승인받는다.

이 계획 작성으로 작업을 멈춘다. `mvp/ai.py`, budget 값, evidence 코드, 테스트와 artifacts는 수정하지 않았으며 실제 Groq 또는 MockTransport 호출도 수행하지 않았다.

# 한국어 붙임형 Topic + Aspect Query Generalization 결과

- 구현·검증일: 2026-09-15
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 결과: `docs/rag/31_PILOT_QUERY_GENERALIZATION_TEST_RESULT.md`
- 대상 UAT 질문: `진정절차에 대해 알려줘`
- 실제 Groq 호출: 0회
- 상태: 일반 query normalization 최소 수정 및 전체 오프라인 회귀 완료

## 1. 결론

한국어에서 붙여 쓴 `topic + aspect`를 일반 구조 규칙으로 분리하고, 질문 subject가 현재 등록 문서 topic 하나에만 대응할 때 그 문서 topic을 canonical subject로 사용하도록 수정했다.

UAT 질문은 변경 전 `kind=procedure`, `topic=진정절차`, `domain=unknown`, `incomplete_semantic_block`이었으나 변경 후 다음 결과가 됐다.

- normalized query: `진정 절차에 대해 알려줘`
- canonical topic: `진정간호`
- canonical retrieval query: `진정간호 절차`
- kind/domain: `procedure` / `hospital`
- pre/post evidence: `supported` / `supported`
- selected evidence: 기준 Q002와 동일한 12 chunks

기준 질문 `진정간호 절차는?`과 UAT 질문은 selected evidence ID 목록과 EvidenceGroup key가 모두 같았다. Q006은 `out_of_scope`, 고정 abstention, provider transport 0회를 유지했다.

BM25 점수식, embedding, RRF, reranker, evidence gate와 validator는 변경하지 않았다. 실제 Groq 호출도 수행하지 않았다.

## 2. 테스트 우선 베이스라인

Production 수정 전에 새 테스트를 먼저 추가해 실행했다.

| 테스트 경계 | 변경 전 결과 |
|---|---|
| 등록 topic/aspect canonicalization | 붙임형 topic 잔존 또는 `domain=unknown` |
| UAT procedure evidence | `incomplete_semantic_block` |
| `진정 시행 순서는?` | `incomplete_semantic_block` |
| 붙임형 목적/준비/주의 | 일부 `no_topic_evidence` |
| Q006 안전 경계 | 통과, zero-call |

초기 집중 실행 결과는 `24 failed, 9 passed`였다. 실패는 retrieval engine이 관련 근거를 찾지 못해서가 아니라 query plan의 subject/aspect 경계가 표현마다 달라졌기 때문이었다.

## 3. 변경 전후 QueryPlan

| 항목 | 기준 질문 변경 전 | UAT 질문 변경 전 | 기준 질문 변경 후 | UAT 질문 변경 후 |
|---|---|---|---|---|
| 질문 | `진정간호 절차는?` | `진정절차에 대해 알려줘` | 동일 | 동일 |
| kind | procedure | procedure | procedure | procedure |
| domain | hospital | unknown | hospital | hospital |
| topic | `진정간호` | `진정절차` | `진정간호` | `진정간호` |
| normalized query | 원문 | 원문 | `진정간호 절차는?` | `진정 절차에 대해 알려줘` |
| expanded query | 표현별 상이 | 표현별 상이 | `진정간호 절차` | `진정간호 절차` |
| pre-budget | supported | incomplete semantic block | supported | supported |
| post-budget | supported | 미도달 | supported | supported |
| selected evidence | 12 | 0 | 12 | 12 |

변경 후 두 질문의 selected evidence ID는 다음과 같이 완전히 같았다.

1. `eval-741858d2e9162acf8d38-chunk-00013`
2. `eval-741858d2e9162acf8d38-chunk-00015`
3. `eval-741858d2e9162acf8d38-chunk-00016`
4. `eval-741858d2e9162acf8d38-chunk-00019`
5. `eval-741858d2e9162acf8d38-chunk-00020`
6. `eval-741858d2e9162acf8d38-chunk-00021`
7. `eval-741858d2e9162acf8d38-chunk-00022`
8. `eval-741858d2e9162acf8d38-chunk-00023`
9. `eval-741858d2e9162acf8d38-chunk-00024`
10. `eval-741858d2e9162acf8d38-chunk-00025`
11. `eval-741858d2e9162acf8d38-chunk-00026`
12. `eval-741858d2e9162acf8d38-chunk-00027`

## 4. 일반화 규칙

### 붙임형 aspect 분리

질문 전체 문자열을 예외 처리하지 않고 한글 token의 끝에 붙은 일반 aspect suffix만 검색용 경계로 분리한다.

| Aspect family | 인식 표현 | Canonical aspect |
|---|---|---|
| procedure | 절차, 방법, 순서, 시행 | 절차 |
| purpose | 목적, 이유 | 목적 |
| preparation | 준비, 준비사항, 확인, 체크사항 | 준비 |
| cautions | 주의, 주의사항, 조심, 안전 | 주의 |

예를 들어 `진정절차`, `진정목적`, `진정준비사항`, `진정주의사항`은 각각 subject `진정`과 해당 aspect로 분리된다.

### 등록 문서 기반 canonical topic

질문 subject가 등록된 문서명/title에서 추출한 topic 하나와만 일치할 때 canonical topic을 부여한다. `간호`, `관리`, `치료`, `교육` 같은 topic role suffix는 subject alias 연결에만 사용한다.

- `진정` → 등록 topic `진정간호`가 유일할 때만 canonicalize
- 같은 base가 여러 등록 topic과 일치하면 추측하지 않음
- 등록 topic과 연결되지 않는 외부 subject는 hospital query로 승격하지 않음
- `out_of_scope` 판정은 canonicalization보다 우선

`도뇨관관리주의사항`을 `도뇨관관리 + cautions`로 처리하는 비진정 예시와, `진정간호`/`진정치료`가 함께 있을 때 bare `진정`을 추측하지 않는 테스트를 추가했다. 따라서 production 코드에는 Q001~Q006 질문 문자열, Q002 chunk ID 또는 진정간호 전용 예외가 없다.

### 요청형 표현과 임상 qualifier

`알려줘`, `알려주세요`, `에 대해`, `무엇인가요`, `뭐야`, `어떻게`, `진행해` 등은 canonical retrieval query에서 요청형 noise로 제외한다. 반면 `소아` 같은 임상 qualifier와 `사용`, `세척`, `투여` 같은 별도 action은 제거하지 않는다.

## 5. Q001~Q006 오프라인 결과

| Case | 기대 | kind / domain | Pre / Post | Selected evidence | 판정 |
|---|---|---|---|---:|---|
| Q001 목적 | answerable | purpose / hospital | supported / supported | 1 | PASS |
| Q002 절차 | answerable | procedure / hospital | supported / supported | 12 | PASS |
| Q003 사전 준비 | answerable | preparation / hospital | supported / supported | 3 | PASS |
| Q004 목적 변형 | answerable | purpose / hospital | supported / supported | 1 | PASS |
| Q005 주의 | answerable | cautions / hospital | supported / supported | 1 | PASS |
| Q006 화성 우주선 | abstain | fact / out_of_scope | domain_or_clarification / 미도달 | 0 | PASS |

Q006은 빈 evidence로 `generate()` 경계를 통과시켜도 Mock transport 호출이 0회였고 최종 `answerable=false`였다.

## 6. 새 표현 변형 결과

### Procedure

| 질문 | Canonical query | Pre/Post | Evidence |
|---|---|---|---:|
| 진정간호 절차는? | 진정간호 절차 | supported/supported | 12 |
| 진정 절차는? | 진정간호 절차 | supported/supported | 12 |
| 진정절차 알려줘 | 진정간호 절차 | supported/supported | 12 |
| 진정절차에 대해 알려줘 | 진정간호 절차 | supported/supported | 12 |
| 진정간호 방법 알려줘 | 진정간호 절차 | supported/supported | 11 |
| 진정은 어떻게 진행해? | 진정간호 절차 | supported/supported | 12 |
| 진정 시행 순서는? | 진정간호 절차 | supported/supported | 12 |

### Purpose / Preparation / Cautions

| Family | 질문 | Canonical query | Pre/Post | Evidence |
|---|---|---|---|---:|
| purpose | 진정목적 알려줘 | 진정간호 목적 | supported/supported | 1 |
| purpose | 진정 목적은? | 진정간호 목적 | supported/supported | 1 |
| purpose | 진정은 왜 시행해? | 진정간호 목적 | supported/supported | 1 |
| preparation | 진정준비사항 알려줘 | 진정간호 준비 | supported/supported | 2 |
| preparation | 진정 전 준비사항은? | 진정간호 준비 | supported/supported | 3 |
| preparation | 진정 전에 뭘 확인해? | 진정간호 준비 | supported/supported | 2 |
| cautions | 진정주의사항 알려줘 | 진정간호 주의 | supported/supported | 1 |
| cautions | 진정 주의사항은? | 진정간호 주의 | supported/supported | 1 |
| cautions | 진정할 때 조심할 점은? | 진정간호 주의 | supported/supported | 1 |

기존 purpose/preparation/cautions paraphrase 9개도 모두 canonical topic `진정간호`, 올바른 intent, pre/post `supported`를 유지했다.

## 7. 변경 범위

변경:

- `mvp/query.py`
  - 붙임형 aspect boundary normalization
  - 등록 문서 기반 request-scoped canonical topic
  - canonical retrieval query와 요청형 noise 제외
  - aspect family 분류 보강
- `mvp/library.py`
  - query/retrieval cache 분리를 위해 `SEARCH_VERSION=14`
- `tests/test_pilot_query_generalization.py`
  - 테스트 우선 UAT/표현 변형/일반화/모호성/Q006 회귀
- `tools/rag_topic_aspect_generalization_evaluate.py`
  - 원문을 저장하지 않는 오프라인 검수 report와 HTML 생성

비변경:

- BM25 tokenization·점수식·threshold
- Embedding model·threshold
- RRF와 reranker
- Context parent atomicity
- Evidence gate와 PromptCoverage/AnswerCoverage
- Facet-slot selection, reconstruction과 모든 citation validator
- Groq prompt/schema, retry, fallback과 web search

## 8. 테스트 및 정적 검사

- 테스트 우선 red run: `24 failed, 9 passed`
- 새 UAT/canonicalization 집중 테스트: 통과
- 관련 retrieval/evidence/Facet/Q006 회귀: `172 passed`
- 최종 전체 pytest: `394 passed, 1 skipped, 4 warnings`
- Ruff (`mvp`, `tests`, `tools`): 통과
- `git diff --check` (`mvp`, `docs`, `tests`, `tools`): 통과
- Code review: 임상 qualifier/action 보존, 다중 topic 모호성 fail-closed, Q006 우선 차단을 확인했으며 추가 조치가 필요한 결함 없음

Warning 4건은 기존 FastEmbed multilingual MiniLM pooling 기본값 안내다. Embedding 설정은 변경하지 않았다.

## 9. 산출물 및 작업 중지

최종 오프라인 검수 산출물:

`artifacts/2026-09-15_rag-topic-aspect-generalization-02/`

- `generalization_report.json`
- `review.html`

대표 29개 질문은 29/29 통과했다. Artifact에는 질문, query plan, evidence ID와 검증 metadata만 있으며 병원 원문 전체나 SourceUnit exact text는 저장하지 않았다. 같은 작업 중 먼저 만든 초기 디렉터리는 덮어쓰지 않았다.

실제 Groq 호출은 0회다. 웹앱 UAT에는 실행 중인 Streamlit 프로세스를 새 코드로 재시작한 뒤 같은 질문을 다시 입력해야 하며, 이번 작업에서는 서버 재기동이나 Live 생성 호출을 수행하지 않고 멈춘다.

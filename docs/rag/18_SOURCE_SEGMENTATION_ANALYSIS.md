# Q002 Source Sentence Segmentation 품질 분석

- 작성일: 2026-09-14
- 대상 저장소: `D:\보하 바탕화면\SCHAT\sch-guide-ai`
- 기준 문서: `docs/rag/17_COVERAGE_SEMANTICS_PLAN.md`
- 분석 대상: Q002 selected evidence 12 chunks, 현재 `source_sentences()` 출력 43 units
- 검수 HTML: `artifacts/2026-09-14_rag-source-segmentation-analysis/review.html`
- 실제 Groq 호출: 0회
- production 코드 수정: 0건
- 결론: **C. 일반화된 segmentation 개선과 selection/statement 한도 조정을 모두 별도 승인받아야 함**

## 1. 결론

현재 `source_sentences()`는 마침표로 끝나는 일반 문장과 다수의 번호 단계는 잘 보존한다. Q002의 핵심 action 문장 대부분은 완전한 unit으로 분리된다. 그러나 PDF 줄바꿈이 들어간 bullet에서 앞줄이 `)` 또는 조사·연결어 이외의 문자로 끝나면 continuation으로 인식하지 못해 하나의 의미 단위가 두 fragment로 갈라진다.

확인된 과분할은 세 쌍이다.

- `00025#1 + 00025#2`: 조건부 혈압 측정 행
- `00026#4 + 00026#5`: 대체 모니터를 이용한 맥박·산소포화도 기록 행
- `00027#3 + 00027#4`: 퇴실 기준 미충족 사유 기록 행

또한 heading, running header, caption과 단계 표지가 독립 source unit으로 반환된다. 이는 segmentation 함수가 구조를 보존하는 현재 역할상 곧바로 버그는 아니지만, Answer catalog에서는 `selectable=false`로 분리해야 한다. `00022#1`, `00026#1` 같은 짧은 orphan continuation은 chunk overlap/청킹 경계 문제이므로 `source_sentences()`만으로 안전하게 복원하지 말고 non-selectable 처리해야 한다.

현재 Q002 required gold 10개를 의미 그대로 표현하려면 15개 source-unit reference가 필요하다. 일반화된 wrapped-bullet 결합을 적용하면 `pediatric-after`의 3개가 2개로 줄어 전체 최소치는 14개다. 따라서 segmentation만 개선해도 max 10에는 도달하지 않는다. 반대로 limit만 늘리면 fragment를 답변 단위로 허용하게 되므로 안전하지 않다.

## 2. 분석 방법과 경계

다음 자료만 로컬에서 read-only로 사용했다.

- `artifacts/2026-09-13_rag-phase1-retrieval/q002_retrieval_funnel.json`
- `tests/fixtures/q002_gold_stages.json`
- 현재 `mvp/evidence.py::source_sentences()`
- 현재 `ProcedureCoverage` 및 group/branch 계산

Selected evidence 12 chunks나 원문을 수정하지 않았다. 각 chunk text를 현재 함수에 그대로 입력해 43개 unit을 얻고, 다음 기준으로 사람이 검수했다.

- 완전한 독립 문장 또는 의미가 완결된 표 행인가
- heading/header/caption/단계 표지뿐인가
- 앞뒤 unit과 결합해야 의미가 완전한가
- chunk overlap에서 생긴 고립 fragment인가
- Q002 gold stage가 요구하는 의미를 단독 또는 복수 unit으로 정확히 지지하는가

`selectable 예상`은 향후 Source Unit catalog의 분석용 예상값이다. production 판정 로직은 아직 구현하지 않았다.

## 3. 전체 43 units 검수 요약

| 분류 | unit 수 | 설명 |
|---|---:|---|
| 현재 그대로 selectable 예상 | 19 | 완전한 문장 또는 독립 표 행 |
| heading/header/caption/단계 표지 | 16 | 문맥에는 보존하되 단독 답변 근거로 선택 금지 |
| orphan continuation | 2 | chunk overlap/경계에서 남은 짧은 fragment |
| wrapped-line 과분할에 포함 | 6 | 3쌍이며 일반화 결합 후 3개 완전 unit이 됨 |
| 합계 | 43 | 분류는 상호 배타적으로 집계 |

일반화 결합 후에는 raw 43개가 40개가 되고, 예상 selectable은 19개에서 22개가 된다. 이는 답변에 22개를 모두 써야 한다는 뜻이 아니라 catalog가 선택 가능한 완전 원문 후보 22개를 갖는다는 뜻이다.

## 4. 집중 검토 결과

| Gold 관련 unit | 현재 상태 | 판정 |
|---|---|---|
| `00021#2` | 성인 투약·기록 완전 문장 | selectable |
| `00021#4` | 성인 15분 간격 모니터링 완전 문장 | selectable |
| `00022#2` | 성인 회복 모니터링 완전 단계 문장 | selectable |
| `00022#3` | 성인 입원실 이동·모니터링 완전 문장 | selectable |
| `00023#3` | 소아 진정 전 평가 완전 단계 문장 | selectable |
| `00023#5` | 소아 투약·기록 완전 문장 | selectable |
| `00024#2` | 소아 10분 간격 모니터링 완전 문장 | selectable |
| `00025#1` | 조건으로 끝나는 bullet 앞부분 | 단독 non-selectable, `#2`와 결합 필요 |
| `00025#2` | 주어·조건이 앞 unit에 있는 행동 뒷부분 | 단독 non-selectable, `#1`과 결합 필요 |
| `00025#3` | 소아 진정 후 회복 모니터링 완전 문장 | selectable |

`00021`, `00022`, `00023`, `00024`의 지정 unit은 현재 상태로도 완전하다. 문제의 핵심은 `00025#1/#2`다. 두 unit을 각각 선택하면 문장 fragment를 사용자에게 노출하고, 하나만 선택하면 조건 또는 행동이 빠진다.

## 5. 이상 유형별 분석

### 5.1 문장을 너무 잘게 쪼개는 경우

현재 continuation 정규식은 앞줄이 특정 조사·연결어(`의`, `을`, `경우`, `후`, `전` 등)로 끝날 때 주로 다음 줄을 붙인다. 앞줄이 닫는 괄호 `)`나 일반 명사로 끝나는 wrapped bullet은 결합되지 않는다.

- `00025#1/#2`: 닫는 괄호 뒤 줄바꿈
- `00026#4/#5`: `산소` 뒤 줄바꿈
- `00027#3/#4`: 조사 `에`가 continuation 목록에 없어 줄바꿈

세 사례 모두 다음 줄이 새 bullet/번호/heading으로 시작하지 않고, 합친 결과가 종결형 문장이 된다.

### 5.2 서로 붙여야 하는 표 행을 분리하는 경우

Q002 원문은 표처럼 보이는 목록 행이 PDF text에서 여러 줄로 래핑된다. `source_sentences()`는 newline을 기본 경계로 취급하므로 bullet 시작을 ‘한 행이 계속되는 신호’로 충분히 사용하지 못한다. 특히 `Ÿ` 문자가 현재 boundary bullet 집합에 명시돼 있지 않다.

### 5.3 Heading을 문장으로 보는 경우

다음 유형이 source unit으로 남는다.

- 문서/페이지 header: `간호실무지침`
- section heading: `6. 진정 절차`
- branch heading: `진정 기록지 작성 방법 [성인/소아]`
- 단계 표지: `② 진정 약물 투여`, `③ 진정 치료 중`, `④ 진정 치료 후`
- caption: `진정 중 서면 기록지 출력 방법`

이들은 source segmentation 결과에는 보존할 수 있지만 답변 문장 후보는 아니다. segmentation 함수에서 모두 삭제하면 section/branch 문맥을 잃을 수 있으므로, SourceUnit catalog builder의 eligibility 계층에서 non-selectable로 구분하는 편이 안전하다.

### 5.4 Bullet/번호/줄바꿈 문제

현재 번호 표지와 바로 뒤 action이 같은 chunk 안에서 이어지는 경우에는 일부가 잘 결합된다. 예를 들어 `00020#4`와 `00023#3`은 ‘① 진정 치료 전’과 다음 action이 하나의 완전 unit이다. 반면 단계 표지가 chunk 끝에 있고 다음 chunk에서 반복되는 경우에는 독립 heading unit이 생긴다. 이는 chunk-local 함수가 chunk 경계를 넘지 않는 한 자연스러운 결과다.

`00022#1`, `00026#1`의 `기록한다.`는 앞 chunk 내용의 overlap continuation으로 보인다. 이를 다음/이전 unit에 무조건 붙이면 중복 또는 잘못된 문장 결합이 생길 수 있으므로 generic segmentation rule로 복원하지 않는다.

## 6. 일반화 가능한 segmentation 개선안

Q002 ID나 진정간호 표현을 사용하지 않고 다음 규칙만 제안한다.

### 규칙 S1: Bullet marker 정규화

`-`, `•`, `●`, `▪`, ``, `*`, `※`뿐 아니라 PDF에서 흔히 추출되는 `Ÿ` 등 명시적 목록 marker를 내부 bullet category로 정규화한다. 원문 문자는 `exact_text`에서 보존한다.

### 규칙 S2: Wrapped bullet continuation

한 raw line이 bullet/list marker로 시작하고 문장 종결 부호로 끝나지 않으며, 다음 non-empty line이 새 bullet·번호·section heading·표 행 시작이 아니면 다음 줄을 결합한다. 다음 줄을 붙인 결과가 종결형이 될 때까지 같은 규칙을 반복할 수 있다.

이 규칙은 앞줄 마지막 단어를 의료 용어 목록으로 판단하지 않는다. 구조 marker와 종결 여부만 사용한다.

### 규칙 S3: 새 구조 경계 우선

다음 줄이 bullet, 원문 번호 단계, branch heading, 명시적 표 구분자 또는 caption pattern으로 시작하면 앞줄에 붙이지 않는다. `* 진정 평가 기록지...` 다음의 별도 `* ... 출력 방법` 같은 두 bullet이 합쳐지는 것을 방지한다.

### 규칙 S4: Orphan fragment는 결합하지 않음

`기록한다.`처럼 주제·대상·조건이 없는 짧은 종결형 unit은 catalog eligibility에서 non-selectable로 표시한다. 인접 chunk와의 자동 결합은 segmentation 범위를 넘어가며 기존 exact citation과 chunk ownership을 흐릴 수 있으므로 시행하지 않는다.

### 규칙 S5: Heading은 보존하되 선택 금지

heading/header/caption detection은 `source_sentences()`의 반환값을 삭제하는 방식이 아니라 SourceUnit `selectable` 판정으로 둔다. Prompt context와 branch/section 해석에는 남기고 모델 응답 ID allowlist에서는 제외한다.

## 7. 개선 전후 최소 source unit 계산

### 현재 segmentation

| Required gold stage | 필요한 unit 수 |
|---|---:|
| common-order | 1 |
| common-pre-assessment | 1 |
| common-explanation-consent | 1 |
| common-record-scope | 1 |
| adult-before | 1 |
| adult-during: 투약 + 모니터링 | 2 |
| adult-after: 회복 + 이동 | 2 |
| pediatric-before: 평가 + 투약 | 2 |
| pediatric-during | 1 |
| pediatric-after: 조건부 혈압 앞/뒤 fragment + 회복 | 3 |
| 합계 | **15** |

현재 `00025#1/#2`는 단독 완전 unit이 아니므로 숫자 15는 reference 개수일 뿐, 모두 selectable한 안전 답변을 만들 수 있다는 뜻도 아니다.

### S1~S3 개선 후

`00025#1/#2`가 하나의 완전 bullet row로 합쳐진다. 나머지 required gold unit은 이미 완전하므로 다음처럼 계산된다.

| Required gold stage | 필요한 unit 수 |
|---|---:|
| common 4 stages | 4 |
| adult before/during/after | 1 + 2 + 2 = 5 |
| pediatric before/during/after | 2 + 1 + 2 = 5 |
| 합계 | **14** |

이는 max 10을 목표로 역산한 값이 아니라 현재 gold label의 모든 결합 의미를 exact source units로 표현한 최소치다. 한 unit이 실제로 두 gold 의미를 모두 포함하는 경우만 중복 계산을 제거했다.

## 8. 대안 비교

| 기준 | A. Segmentation 유지 + limit 증가 | B. 일반화 segmentation 개선만 | C. 둘 다 |
|---|---|---|---|
| 의미적으로 완전한 unit | 실패. fragment가 남음 | 개선. wrapped bullet 복원 | 개선 |
| Q002 최소 required 표현 | 15 references이나 일부 non-selectable | 14 units | 14 units |
| max 10 충족 | limit를 15 이상으로 바꾸면 수치상 가능 | 불가능 | limit/statement cap을 최소 14로 별도 조정해야 가능 |
| Citation 안전성 | fragment citation 위험 | 높음 | 높음 |
| 일반화 가능성 | 낮음. 데이터 문제를 limit로 우회 | 높음 | 높음 |
| 변경 범위 | schema/Answer/UI limit | segmentation 및 eligibility | 양쪽 모두, 단계적 승인 필요 |
| 권고 | 기각 | 필요한 선행 개선이나 단독으로 불충분 | **권고** |

### A. Segmentation 수정 없이 selection limit 증가

`00025#1/#2` 같은 fragment를 그대로 선택하게 되므로 답변 문장 완전성 목표에 맞지 않는다. selection limit뿐 아니라 현재 `Answer.statements max_length=10`도 함께 바꿔야 한다. 단독 채택하지 않는다.

### B. Segmentation 일반화 개선

source unit 품질을 직접 고치며 Q002 외 PDF bullet에도 적용 가능하다. 반드시 필요한 선행 개선이다. 그러나 required minimum이 14이므로 max 10/Answer 10 statements 충돌은 남는다.

### C. Segmentation 개선 + limit 조정

S1~S3으로 완전 unit을 만든 뒤, 사람이 승인한 source-unit gold 최소치에 맞춰 selection limit과 public Answer statement cap을 함께 검토한다. 현재 계산상 required gold만 표현하는 최소치는 14다. supplemental unit까지 포함할지는 별도 정책이며 자동으로 한도를 더 늘리지 않는다.

## 9. 권고 검증 순서

후속 승인이 있다면 한 번에 production 변경을 합치지 않고 다음 순서로 검증한다.

1. 현재 43-unit fixture를 고정하고 S1~S3의 pure segmentation 테스트를 작성한다.
2. 세 wrapped pair만 각각 하나의 완전 unit으로 바뀌고 다른 완전 unit은 변하지 않는지 확인한다.
3. heading/header/caption/orphan eligibility를 별도 테스트한다.
4. Q002 source-unit gold mapping을 개선 후 position/fingerprint에 맞춰 사람이 검수한다.
5. required gold 최소 14가 재현되는지 확인한다.
6. 그 결과를 승인받은 뒤에만 selection limit과 `Answer.statements` cap 변경을 별도 설계한다.
7. Source Unit D안 구현과 Mock 검증은 그 다음 단계에서 진행한다.

Segmentation 변경으로 source-unit position이 바뀌므로 fixture는 단순 position만 신뢰하지 않고 `(chunk_id, exact_text fingerprint)` drift 검증을 함께 가져야 한다.

## 10. 비변경 확인과 승인 대기

이번 분석에서는 다음을 수행하지 않았다.

- `source_sentences()` 또는 production 코드 수정
- Q002 gold fixture 수정
- selected evidence 12 chunks 변경
- BM25, embedding, RRF, reranker 또는 validator 변경
- selection/statement limit 변경
- 실제 Groq 호출
- 기존 artifacts 덮어쓰기

권고는 C안이지만 이는 즉시 구현 승인이 아니다. 먼저 일반화된 wrapped-bullet segmentation 개선과 fixture 갱신 범위를 승인받고, 이후 최소 14개를 수용할 selection/Answer 계약 변경을 별도로 승인받아야 한다.


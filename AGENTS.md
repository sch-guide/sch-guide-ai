# SCHAT 프로젝트 규칙

## 프로젝트 목적

- 프로젝트 상세 요구사항은 `docs/00_기획/프로젝트_명세서.md`를 참조한다.
- `docs/`를 유일한 공식 문서 정본으로 사용한다.
- `docs_view/`는 렌더링·열람용 복사본이며 요구사항·설계·평가 판정의 기준으로 사용하지 않는다.
- 최종 목표는 병원 실무지침서를 기반으로 간호사가 자연어로 질문했을 때:
  - 관련 근거를 정확하게 검색하고
  - 지침 범위 안에서만 답변하며
  - 출처를 함께 제시하고
  - 근거 부족 시 안전하게 abstain하고
  - 다양한 표현에도 안정적으로 대응하며
  - 표/이미지/도식 근거까지 필요 시 활용할 수 있는
  실사용 가능한 SCHAT 웹앱을 완성하는 것이다.

---

## 개발 절차

- 복잡한 기능은 기본적으로 다음 순서로 진행한다.

  `분석 → 설계 → 사용자 승인 → 구현 → 테스트`

- 사용자 승인 전에는 실제 구현으로 넘어가지 않는다.

- 단, 사용자가 특정 작업 범위에 대해 다음과 같이 명시적으로 승인한 경우에는:
  - "중간 승인 없이 진행"
  - "끝까지 자동 진행"
  - "이 범위는 알아서 실행"

  해당 승인 범위 안에서는 추가 중간 승인 없이 구현·테스트·검증까지 진행할 수 있다.

- 자동 진행이 승인된 경우에도 아래 작업은 별도 승인 없이 진행하지 않는다.
  - 승인 범위를 벗어나는 production 구조 변경
  - 외부 API 또는 외부 provider 실제 호출
  - 병원 데이터 외부 전송
  - 안전 validator 완화
  - 기존 정상 기능 삭제
  - main branch 직접 수정
  - force push
  - 시스템 경로 수정/삭제
  - 병원 지침에 없는 임상정보 생성

- 단계별 계획 및 설계 산출물은 Markdown(`.md`)으로 남긴다.

- 사람이 직접 검수해야 하는 결과는 HTML로도 생성한다.

---

## 개발 단계

- 1단계: BM25 검색 성능 검증
- 2단계: RAG 챗봇 구현
- 3단계: Query generalization 및 Evidence 안정화
- 4단계: Retrieval baseline 비교 및 RAGAS 평가
- 5단계: Answer UX / Controlled generation
- 6단계: Table evidence / Table retrieval
- 7단계: Image / Diagram / Multimodal retrieval
- 8단계: Provider 비교 및 최종 production 안정화

- 각 단계는 이미 완료된 경우 반복하지 않는다.

- 기존 완료 결과가 있으면:
  - 코드
  - 테스트
  - `docs/`
  - `workspace/`
  - Superpowers progress

  를 먼저 확인하고, 마지막 미완료 단계부터 이어서 진행한다.

---

## 현재 Retrieval 원칙

- 외부 API를 사용하지 않는 standalone 기준선에서는 BM25가 가장 강하고 빠른 retriever다.
- Gemini Embedding 2 기반 ChromaDB-only는 Hit@10 0.9524로 개선됐지만 external evaluation-only이며 MRR·Recall·Precision은 Current Hybrid보다 낮다.
- ChromaDB 단독 검색은 현재 production 기본 검색기로 채택하지 않는다.
- 기존 production retrieval 구조는 유지한다.

현재 production retrieval:

`BM25 + semantic/vector retrieval + RRF + reranker`

- production retrieval 구조는 명시적 승인 없이 변경하지 않는다.
- ChromaDB는 evaluation-only 또는 보조 검색 실험에 사용할 수 있다.
- BM25, semantic, Hybrid, ChromaDB 중 어떤 방식을 채택할지는 반드시 실측 metric을 근거로 판단한다.
- 최신 기술이라는 이유만으로 특정 검색 방식을 채택하지 않는다.

---

## Generation 원칙

- 검증된 evidence 범위 안에서만 답변을 생성한다.
- 자연스러운 재서술은 허용하되 의미를 바꾸면 안 된다.

허용:
- 문장 합치기
- 반복 제거
- 제목/소제목 생성
- bullet 정리
- 단계별 구조화
- 비교표 생성
- 질문 의도에 맞는 요약

금지:
- 새로운 임상정보 추가
- 원문에 없는 숫자/시간/용량 추가
- 조건/금기/부정 의미 변경
- 근거 없는 해석
- clinical ontology 임의 추론
- branch/phase 임의 혼합

Generation 후 반드시 기존 validator를 다시 실행한다.

---

## Table / Image / Multimodal 원칙

### Table

- 표는 가능한 경우 OCR보다 parser 기반으로 구조화한다.
- 다음 metadata를 보존한다.
  - table_id
  - document_id
  - page
  - caption/title
  - row_index
  - column_index
  - header
  - cell_text
  - parent_table_id
  - bbox
  - fingerprint

- 표 검색 단위는 다음을 우선 검토한다.
  - table 전체
  - row 단위
  - header + row
  - key-value 구조

- header/cell 관계를 깨뜨리지 않는다.
- 원문에 없는 표 값을 생성하지 않는다.
- 숫자/단위는 기존 validator로 검증한다.

### Image / Diagram

- 장식 이미지는 검색 대상에서 제외한다.
- 임상 의미가 있는 다음 유형만 대상으로 한다.
  - flowchart
  - algorithm
  - decision tree
  - procedure diagram
  - clinical figure

- 이미지에서 보이지 않는 관계를 추론하지 않는다.
- 화살표/순서/숫자/단위가 불확실하면 `needs_human_review=true`로 처리한다.
- 승인되지 않은 image-dependent gold를 production 근거로 사용하지 않는다.

---

## UAT / Evaluation 원칙

- 개별 질문 하나를 통과시키기 위한 문자열 하드코딩을 금지한다.
- 실패는 반드시 유형별로 묶어서 분석한다.

예:
- temporal
- product-specific
- paraphrase
- procedure
- fact-specific
- branch-specific
- table
- image
- negative/out-of-scope

- Gold/reference는 retrieval 결과에서 자동 확정하지 않는다.
- 사람이 검수 가능한 ground truth를 사용한다.
- heading-only chunk는 gold로 사용하지 않는다.
- 필요 시:
  - primary gold
  - acceptable gold
  - multi-gold
  구조를 사용한다.

---

## Retrieval Metric 원칙

기본적으로 다음 metric을 사용한다.

- Hit@1
- Hit@3
- Hit@5
- Hit@10
- MRR
- Recall@1
- Recall@3
- Recall@5
- Recall@10
- Precision@1
- Precision@3
- Precision@5
- Precision@10

RAGAS retrieval metric:
- ID-based Context Precision
- ID-based Context Recall

LLM judge 기반 metric은 외부 provider 호출 승인이 없는 경우 실행하지 않는다.

Negative/out-of-scope 질문은 positive IR metric aggregate에 섞지 않는다.

---

## 안전 규칙

- 기존 정상 기능을 임의로 삭제하지 않는다.
- `C:\Windows`, `C:\Program Files` 등 시스템 경로를 사용자 승인 없이 수정하거나 삭제하지 않는다.
- 병원 지침에 근거가 없는 내용을 임의로 생성하지 않는다.
- 병원 원문 전체를 artifact에 저장하지 않는다.
- API key를 저장하지 않는다.
- Authorization header를 저장하지 않는다.
- raw provider response를 저장하지 않는다.
- 전체 prompt를 저장하지 않는다.
- 필요 이상의 exact SourceUnit text를 artifact에 저장하지 않는다.

반드시 유지:
- citation validator
- number validator
- unit validator
- time validator
- condition validator
- negation validator
- action validator
- branch/phase 검증
- source order
- AnswerCoverage
- `validate_answer()`
- Q006 zero-call 안전 경계
- parent atomicity

---

## 외부 Provider / API 규칙

- Groq, Gemini 또는 기타 외부 provider 호출은 사용자가 명시적으로 승인한 경우에만 실제 실행한다.
- 병원 데이터 외부 전송 정책이 확인되지 않은 상태에서는 실제 병원 근거를 외부 provider에 전송하지 않는다.
- provider 비교가 필요할 경우 동일 evidence, 동일 prompt, 동일 validator, 동일 question set을 사용한다.
- provider 비교 전에 retrieval baseline을 안정화한다.

---

## 산출물

- 평가·실험·검토 결과는 역할에 따라 `workspace/` 아래에 저장한다.
- BM25 과거 문서는 `workspace/과거작업/문서이력/bm25/`를 참조한다.
- RAG 과거 문서는 `workspace/과거작업/문서이력/rag/`를 참조한다.
- Superpowers 계획·진행 이력은 `workspace/과거작업/문서이력/superpowers/`를 사용한다.

### 문서 자동 현행화

- 코드, 평가 결과, 운영 상태, 안전 판정이 실질적으로 바뀌면 사용자의 별도 요청이 없어도 관련 `docs/` 정본을 함께 갱신한다.
- 비개발자가 이해해야 하는 변경은 `docs/07_쉬운_작업일지/`에도 날짜별로 반영한다.
- 문서 변경 후 `tools/build_docs_view.py`를 실행해 `docs_view/`를 다시 생성한다.
- 사용자용 문서의 비용은 원화(원)를 기본으로 표기한다. 외화로만 제공된 비용은 작업일 또는 가장 가까운 확인 가능 날짜의 환율로 환산하고, 환율 변동이 있으면 `약`으로 표시한다.
- 감사용 평가 JSON의 provider 원결제 통화 값은 임의로 바꾸지 않으며, 필요하면 사용자용 문서에서만 원화 환산값을 제공한다.
- 검증되지 않은 결과를 추측해 기록하지 않으며, 공식 tag·Gold·readiness·Production 채택 여부는 승인 없이 승격하지 않는다.
- 작업 범위상 문서를 갱신하지 못한 경우 완료 보고에 미반영 문서와 이유를 명시한다.

### Superpowers progress

장시간 작업은 각 PHASE 완료 시 다음을 기록한다.

경로:

`workspace/과거작업/문서이력/superpowers/progress/`

각 progress 문서에 포함:
- 완료 항목
- 변경 파일
- 테스트 결과
- 다음 단계
- pending 항목
- 중단 조건 여부

세션 중단, compact, restart가 발생하면:
- progress 문서를 먼저 읽고
- 이미 완료된 단계를 반복하지 않고
- 마지막 미완료 단계부터 이어서 진행한다.

---

## Git 규칙

- 기본 작업 브랜치는 현재 승인된 feature branch를 사용한다.
- `main`은 직접 수정하지 않는다.
- force push를 사용하지 않는다.
- 사용자 요청 없이 자동 commit/push하지 않는다.
- 작업 완료 후 commit 권고만 보고한다.
- `catalog.sqlite3` 등 기준 데이터 snapshot은 승인된 경우에만 Git 추적 대상으로 사용한다.
- Git 충돌 해결 시 기존 정상 기능을 우선 보존한다.

---

## 스킬 라우팅

### Superpowers

- 장시간·복합 작업의 주 워크플로우는 Superpowers를 우선 사용할 수 있다.
- Superpowers는 다음 절차를 담당한다.
  - brainstorming
  - planning
  - test-driven-development
  - executing-plans
  - verification

- Superpowers progress는 `workspace/과거작업/문서이력/superpowers/`에 기록한다.
- Superpowers와 SCHAT 전용 skill이 충돌하면 이 `AGENTS.md`의 규칙과 현재 사용자의 명시적 승인 범위를 우선한다.

### SCHAT 전용 스킬

- 아이디어 정리와 구현 전 요구사항 탐색은 `.agents/skills/brainstorm/SKILL.md`를 사용한다.
- 시스템 구조와 복잡한 기능 설계가 필요한 경우 architect 스킬 사용을 사용자에게 제안한다.
- architect 스킬은 사용자가 명시적으로 승인하거나 요청한 경우에만 사용한다.
- 구현 후 코드 품질 검수는 `.agents/skills/code-review/SKILL.md`를 사용한다.
- 오류 발생 시 원인 분석과 해결은 `.agents/skills/debug-error/SKILL.md`를 사용한다.
- 구현 후 테스트 작성 및 테스트 전략 수립은 `.agents/skills/write-tests/SKILL.md`를 사용한다.
- BM25 검색 성능 검증과 검색 실패 분석은 `.agents/skills/bm25-evaluation/SKILL.md`를 사용한다.

---

## 자동 진행 규칙

사용자가 특정 작업에서 다음을 명시적으로 승인한 경우:

- "중간 승인 없이 진행"
- "끝까지 자동 진행"
- "가능한 범위에서 알아서 실행"

해당 작업 범위 안에서는:
- 분석
- 설계
- 테스트
- 구현
- 평가
- 검증
- code review

까지 중간 승인 없이 진행할 수 있다.

단 아래 경우에는 즉시 중단하고 사용자에게 보고한다.

### 중단 조건

- 병원 데이터 외부 API 전송 필요
- 사용자 계정/서비스 승인 필요
- 표/이미지 임상 해석이 불확실
- gold 신뢰성 확보 불가
- validator 완화 필요
- dependency 충돌
- Git 데이터 손상 위험
- API key/병원 원문 유출 위험
- 승인 범위를 벗어나는 production 구조 변경 필요

중단 조건이 없으면 계획만 작성하고 멈추지 말고 계속 진행한다.

---

## 완료 전 검증

작업 완료를 주장하기 전에 반드시 확인한다.

- 관련 집중 테스트
- 전체 pytest
- Ruff
- `git diff --check`
- code review
- artifact security audit
- 기존 주요 UAT regression

완료 보고에는 반드시:
- 변경 내용
- 변경하지 않은 범위
- 테스트 결과
- 안전성 검증
- 남은 제한점
- 다음 권고 단계

를 포함한다.

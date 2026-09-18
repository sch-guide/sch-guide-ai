# SCHAT v1.0 완료 기준

현재 공식 안정 복구 기준점은 `v0.9`다. 아래 조건을 모두 검증하기 전에는 v1.0 tag를 만들거나 production controlled generation/image workflow를 공개하지 않는다.

## 1. Provider Live 승인과 비교

- 병원 evidence exact text 외부 전송 정책 승인
- Groq/Gemini 계정, 서비스, model ID와 데이터 보존·학습·지역 조건 승인
- 승인 case, provider별 최대 호출 수와 전송 byte/token 상한 고정
- 동일 SourceUnit evidence/schema를 사용한 provider별 단일 호출
- retry, fallback model, web/tool/file search 0회
- HTTP/finish/usage/latency와 안전한 validator metadata만 저장
- raw prompt, raw response, API key, Authorization과 SourceUnit text 미저장

## 2. Controlled generation 안전성

- Schema, SourceUnit ID, coverage, branch와 phase 검증 통과
- Citation을 서버가 SourceUnit에서 재생성
- Number, unit, time, condition, contraindication, negation과 action 검증 통과
- Unsupported synthesis와 semantic support 평가 완료
- 실패 시 재호출 없이 기존 verified extractive Answer로 fallback
- 기존 `validate_answer()`, AnswerCoverage와 selection limit 16 유지

## 3. TF027 image workflow

- `61_TF027_IMAGE_HUMAN_REVIEW_CHECKLIST.md`의 10개 항목 사람 검수 완료
- 화살표·순서·분기·숫자·단위에 불확실성 없음
- 두 번째 확인자를 포함한 production gold 승인
- 승인 전 `needs_human_review=true`, aggregate/production 제외

## 4. 운영 UAT

- `schat-v1-operational-uat-v1` 36개 case 실행
- 진정/수혈 text, table, mixed, follow-up와 negative 경계 통과
- 승인된 provider case는 각 provider 최대 1회
- Image pending case는 승인 전 provider/vision 0회
- Out-of-scope 4개는 pre-LLM abstain과 provider 0회
- 실제 Streamlit 화면에서 답변 구조, 항목별 citation과 근거 UI 검수

## 5. 회귀·보안·복구

- Production retrieval/embedding 구조 변경 없음 또는 별도 승인된 변경만 존재
- Sedation UAT, transfusion retrieval, Q006 zero-call, Facet-slot, parent atomicity와 table 회귀 통과
- Full pytest, Ruff, `git diff --check`, code review와 artifact security audit 통과
- 외부 전송 범위와 실제 호출 수가 승인 manifest와 정확히 일치
- 병원 원문 exact-match artifact, secret marker와 raw provider data 0건
- v0.9 tag 보존 확인 후 별도 사용자 승인으로만 v1.0 commit/tag/push

## 현재 판정

Offline provider harness, TF027 checklist와 36-case 운영 UAT fixture 준비는 완료했다. Provider Live, semantic support 검증, TF027 사람 검수와 실제 운영 UAT는 아직 완료 조건이 아니므로 v1.0은 **준비 후보**이며 공식 안정 버전은 계속 `v0.9`다.

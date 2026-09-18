# SCHAT 운영 Positive Gold 사람 검수 안내

- 기준 안정 버전: `v0.9`
- 현재 후보: `v1.0-rc1`
- 검수 대상: 운영 UAT positive 32문항
- 현재 사람 최종 승인: 2문항 (`UAT-S01`, `UAT-S02`)
- 자동 초안 대상: 나머지 provisional 30문항
- 자동 Gold 승인: 0건
- 외부 호출·전송: 0회
- Production retrieval/generation 변경: 0건

## 1. Gold가 무엇인가요?

Gold는 평가에서 사용할 **사람이 확인한 정답 기준**입니다. 질문에 답할 때 반드시
포함해야 하는 근거와 핵심 사실을 뜻합니다. 검색 결과 1위나 현재 앱의 답변이 Gold가
되는 것은 아닙니다. 간호사가 실제 지침 원문을 읽고 직접 승인해야 합니다.

## 2. 왜 사람이 검수하나요?

검색기는 질문과 비슷한 문장을 먼저 보여줄 수 있지만, 비슷하다는 것과 임상적으로
정답이라는 것은 다릅니다. 특히 숫자, 시간, 조건, 금기, 부정 표현과 순서는 검색
점수만으로 확정할 수 없습니다. 그래서 화면의 검색 순위는 후보를 찾는 참고 자료로만
사용하고, Gold 여부는 사람이 별도로 체크합니다.

## 3. 실행 방법

저장소 최상위 폴더에서 PowerShell을 열고 실행합니다.

```powershell
.\.venv\Scripts\streamlit.exe run tools\schat_gold_human_review_app.py
```

브라우저가 자동으로 열리지 않으면 터미널에 표시된 로컬 주소를 엽니다. 이 화면은
로컬 PC에서만 동작하며 provider나 외부 서버로 질문·근거를 보내지 않습니다.

## 4. 화면 사용 순서

1. 상단의 `남은 provisional case 자동 초안 생성`을 한 번 누릅니다.
2. 시스템은 이미 승인된 case를 건너뛰고 local retrieval과 exact evidence만 사용해 초안을
   만듭니다.
3. 상태 필터와 질문 선택 메뉴에서 검수할 case를 고릅니다.
4. `자동 Gold 초안`의 문서, Primary/Acceptable 후보, 핵심 사실, 숫자·단위·시간·조건을
   원문과 대조합니다.
5. 필요하면 `후보 근거 불러오기`를 눌러 문서명, 페이지, section, 근거 ID와 원문을
   확인합니다.
6. reviewer를 입력하고 다음 중 하나를 누릅니다.
   - `초안 그대로 승인`: 제안값을 1차 검수 결과로 사용
   - `수정 후 승인`: 화면에서 고친 값을 1차 검수 결과로 사용
   - `판단 보류`: 검수 중 상태로 저장
7. 최종 Gold로 확정할 때만 `최종 Gold 승인`을 사람이 직접 체크합니다.
8. 저장되면 다음 미검수 case로 자동 이동합니다. `이전 case`와 `다음 미검수 case`로
   이동할 수도 있습니다.

화면을 열거나 초안·후보를 만드는 것만으로는 Gold가 지정되지 않습니다. 초안 승인
버튼을 눌러도 최종 Gold 체크가 없으면 `reviewing` 상태이며 평가 aggregate에서
제외됩니다.

## 5. 자동 초안을 읽는 방법

자동 초안은 검색 1위를 정답으로 복사하지 않습니다. 질문과 근거의 직접성, 문서,
section, evidence type과 질문 의도를 함께 확인합니다. 다음 세 상태를 사용합니다.

- `초안 그대로 검토 가능`: 직접 근거와 최소 핵심 사실이 있으나 사람의 최종 확인은 필요
- `수정 권고`: 후보는 있으나 보조 근거·표·범위를 사람이 다듬어야 함
- `사람 확인 필요`: 직접 근거가 충분하지 않거나 이미지처럼 자동 판정하면 안 되는 case

숫자·단위·시간·조건·금기·부정·단계는 선택한 exact evidence에 실제 표현이 있는
경우에만 제안합니다. 빈칸은 정보가 없다는 뜻이 아니라 시스템이 안전하게 확정하지
않았다는 뜻입니다.

## 6. Primary와 Acceptable의 차이

- **Primary Gold Evidence**: 이 근거가 빠지면 정답으로 볼 수 없는 필수 근거입니다.
- **Acceptable Gold Evidence**: Primary는 아니지만 같은 질문에 대한 보조 근거로 인정할
  수 있습니다.

한 근거를 두 목록에 동시에 넣을 수 없습니다. 검색 순위는 어느 목록에도 자동으로
반영되지 않습니다.

## 7. 핵심 정답 사실 입력

각 입력란에는 한 항목을 한 줄로 적습니다.

- 필수 핵심 사실: 답변에 반드시 들어가야 하는 임상 사실
- 필수 숫자·단위·시간: 값과 표현이 보존되어야 하는 항목
- 필수 조건·금기·부정: 의미가 바뀌면 위험한 제한 조건
- 필수 단계/순서: 순서를 반드시 지켜야 하는 절차
- 표/이미지 필요 여부: 이 질문의 정답이 해당 evidence type에 의존하는지 명시

승인하려면 Primary 근거, 핵심 사실, 표/이미지 필요 여부를 모두 사람이 확인해야
합니다. 지침 원문을 복사해 대량 저장하기보다는 평가에 필요한 최소 핵심 사실만 짧게
기록합니다.

## 8. 승인 상태와 2차 검수

- `unreviewed`: 아직 검수하지 않음
- `reviewing`: 검수 중이거나 판단 보류
- `approved`: 최종 Gold로 승인
- `rejected`: 현재 case/label을 Gold로 사용하지 않음
- `needs_second_review`: 1차 확인은 끝났지만 2차 확인이 필요함

임상적으로 중요한 case는 `2차 검수 필수`를 체크합니다. 이 경우 2차 reviewer와 승인
기록 없이는 `final_gold_approved=true`로 저장할 수 없습니다. TF027 image case는 기존
사람 검수 checklist 완료를 별도로 체크하고 2차 reviewer까지 승인하지 않으면 최종
Gold로 저장할 수 없습니다.

## 9. 저장 위치와 안전 경계

자동 초안은 Git에서 제외되는 다음 로컬 경로에 저장됩니다.

`data/review/schat_v1_operational_gold_drafts.json`

이 파일은 `draft_only=true`, `final_gold_approved=false`만 허용합니다. 전체 후보 본문과
검색 점수는 저장하지 않으며, 사람이 대조할 최소 exact 핵심 사실만 로컬에 둡니다.
이미 최종 승인된 case는 초안 파일에도 포함되지 않습니다.

사람 검수 결과는 다음 별도 파일에 저장됩니다.

`tests/fixtures/schat_v1_operational_gold_reviewed.json`

원본 `schat_v1_operational_uat.json`과 `schat_v1_operational_gold.json`은 덮어쓰지
않습니다. Reviewed 파일에는 evidence ID와 사람이 작성한 최소 구조만 저장하며 다음은
저장하지 않습니다.

- 질문 문자열
- 후보 근거 원문
- 검색 순위·점수·검색 방식
- 전체 지침서/PDF
- API key, Authorization, 외부 응답

평가 집계에는 `review_status=approved`이면서 사람이
`final_gold_approved=true`로 확인한 case만 포함됩니다. 미검수, 검수 중, 2차 검수 대기
case는 자동으로 제외됩니다.

## 10. 검수 완료 기준

32개 positive case를 모두 검수하고, production 평가에 사용할 case는 최종 승인하며,
모호한 case는 반려 또는 사람 검수 대기로 분리해야 합니다. 필요한 critical fact 구조와
2차 검수도 완료되어야 합니다. 이 도구는 승인 판단을 자동으로 대신하지 않습니다.

# TF027 이미지/도식 사람 검수 안내

- 대상: `TF027` 수혈 workflow figure 후보
- 원본 확인 위치: `실무지침서_수혈간호.pdf` page 11
- 현재 상태: **needs_human_review=true**
- Production image Gold: **미승인**
- Multimodal aggregate: **제외**
- Vision/provider API 호출: **0회**

## 실행 방법

저장소 루트의 PowerShell에서 실행한다.

```powershell
.\.venv\Scripts\streamlit.exe run tools/tf027_human_review_app.py
```

브라우저가 자동으로 열리지 않으면 터미널에 표시된 로컬 주소를 연다. 화면 왼쪽에는
원본 page와 후보 bbox 오버레이가 표시되고, 오른쪽에는 10개 검수 항목이 표시된다.
오버레이는 메모리에서만 만들며 원본 PDF를 수정하거나 artifact에 복제하지 않는다.

## 사람이 확인할 10개 항목

각 항목의 상태를 `확인됨`, `불확실`, `해당 없음` 중 하나로 사람이 직접 선택한다.
검수하지 않은 항목은 `미검수`로 둔다. `불확실` 또는 `해당 없음`은 사유 메모가 필수다.

1. Figure 전체 경계와 잘림 여부
2. 시작 node와 종료 node
3. 각 node label
4. 화살표 방향과 node 연결 관계
5. Decision branch 조건과 경로
6. Workflow 단계 순서
7. 숫자·단위·시간·용량
8. Caption 및 nearby text와 figure의 관계
9. 흐림·가림·잘림·판독 불가 요소
10. Production image Gold 사용 가능 여부

화면의 후보 번호와 경계는 위치를 찾기 위한 참고 표시일 뿐 정답 순위가 아니다. 사람이
확인하지 않은 label, edge, branch, sequence, number, unit 또는 time은 입력하지 않는다.

## 구조화 입력

사람이 원본에서 확인한 값만 다음 필드에 한 줄씩 입력한다.

- `selected_figure_ids`
- `nodes`
- `edges`
- `branches`
- `sequence`
- `numbers`
- `units`
- `times`
- `caption_relation`
- `uncertainties`

빈 값은 시스템이 자동으로 채우지 않는다. 분기나 숫자·단위·시간이 실제로 없으면 해당
검수 항목을 `해당 없음`으로 선택하고 사람이 그 이유를 기록한다.

## 저장과 승인

원본 fixture인
`tests/fixtures/tf027_image_human_review_checklist.json`은 읽기 전용 기준으로 보존한다.
화면에서 저장 버튼을 누른 경우에만 별도 파일에 저장한다.

`tests/fixtures/tf027_image_human_review_reviewed.json`

별도 파일에는 figure ID, page, bbox, fingerprint, 사람이 작성한 구조화 값과 검수 상태만
기록한다. 이미지 픽셀, PDF 원문, provider payload는 저장하지 않는다.

Production image Gold 승인은 다음을 모두 만족할 때만 파생된다.

- 필수 검수 항목이 모두 `확인됨`
- 분기/숫자 항목이 없다면 `해당 없음`과 사유가 기록됨
- 대상 figure, node, edge와 순서가 구조화됨
- 남은 불확실성이 없음
- reviewer 1의 식별자·시간·승인이 있음
- 독립적인 reviewer 2의 식별자·시간·승인이 있음
- 사람이 최종 승인 요청을 명시함

하나라도 빠지면 `needs_human_review=true`, `production_gold_approved=false`,
`included_in_aggregate=false`를 유지한다. 승인 전에는 image-dependent Gold 생성,
image retrieval aggregate 포함 또는 production answerability 연결을 하지 않는다.

## 현재 진행 상태

사람의 실제 입력은 아직 없다.

- 확인됨: **0/10**
- 불확실: **0/10**
- 미검수: **10/10**
- 1차 검수: **미완료**
- 2차 검수: **미완료**
- Production image Gold: **미승인**

# BM25 + Gemini Baseline v1

## 평가 환경

- Retrieval: BM25
- LLM: Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite`)
- Label Set: 10문항
- Source: 실무지침서_수혈간호.pdf
- 실행 일시(기존 기록): 2026-09-17T07:55:52.365191+00:00
- 실제 검색 구현: BM25 + FAISS 벡터 검색 + RRF + 재정렬 + 주변 문맥 확장. 순수 BM25 단독 결과는 아님.
- 평가 대상: 저장된 최종 사용자 표시 답변. 검색 재실행·Gemini API 재호출·외부 의학 지식 추가 없음.

## 전체 결과

| 지표 | 결과 |
| --- | --- |
| Retrieval Hit Rate | 90% (9/10) |
| Retrieval Miss | TRF-004 |
| Average Answer Coverage | 0% (문항별 Coverage 산술평균) |
| Full Coverage | 0/10 |
| Partial Coverage | 0/10 |
| Zero Coverage | 10/10 |
| Critical Error | 0건 / 해당 문항 없음 |
| 필수항목 충족 | 0/41 |
| 저장 기록상 Gemini 호출 | 0/10 |
| LLM 호출 전 답변 거절 | 10/10 |

## 문항별 결과

| ID | Retrieval | Answer Coverage | Critical Error | 비고 |
| -- | --------- | --------------: | -------------- | -- |
| TRF-001 | Hit | 0% (0/6) | false (라벨 미정의) | 절차; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-002 | Hit | 0% (0/4) | false (라벨 미정의) | 조건/예외; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-003 | Hit | 0% (0/4) | false (라벨 미정의) | 검사/수치; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-004 | Miss | 0% (0/3) | false (라벨 미정의) | RBC 수령량·주입시간: 근거 p.2 누락, LLM 호출 전 차단 |
| TRF-005 | Hit | 0% (0/4) | false (라벨 미정의) | 환자안전; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-006 | Hit | 0% (0/4) | false (라벨 미정의) | 모니터링; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-007 | Hit | 0% (0/4) | false | 상황대처; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-008 | Hit | 0% (0/5) | false (라벨 미정의) | 조건/예외; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-009 | Hit | 0% (0/3) | false | 금기/주의; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |
| TRF-010 | Hit | 0% (0/4) | false (라벨 미정의) | 상황/업무절차; 페이지 Hit이나 LLM 호출 전 근거 완전성 검사에서 차단 |

## 판정 기준과 해석

모든 저장 답변은 **“등록된 지침서에서 확인할 수 없습니다.”**이다. 필수항목 41개 중 어느 항목도 의미상 설명하지 않으므로 모든 항목을 false로 판정했다. 문항별 세부 근거는 JSON의 `must_include_evaluation`에 기록했다. 검색 context에 정답이 있더라도 최종 답변에 없으면 충족으로 인정하지 않았다.

Critical Error 0건은 위험한 반대 지시가 관측되지 않았다는 뜻이다. TRF-007의 수혈 지속·미중단 지시와 TRF-009의 생리식염수 priming 허용 지시는 없었다. 나머지 8문항은 해당 오류 라벨이 비어 있다. 필수 설명 누락을 위험한 반대 지시로 계산하지 않았으며, 이 수치만으로 임상적 유용성이나 안전성이 확보됐다고 해석할 수 없다.

모든 문항의 `generation_trace`가 `llm_called=false`, `stage=before_llm`, `block_reason=pre_llm:incomplete_semantic_block`이다. 즉 불완전한 의미 단위의 근거를 감지한 사전 검사에서 생성이 차단됐다. 저장된 결과는 Gemini가 작성한 답변 10개가 아니라 서비스가 반환한 거절 안내 10개이다. **이번 0%는 서비스 최종 답변의 Coverage이며 Gemini 모델 자체의 생성 성능을 측정한 결과가 아니다.**

**TRF-004 분리 해석:** 기존 Retrieval Baseline에서 정답 페이지 p.2가 검색되지 않았다. 이번 답변 실행의 저장 context 페이지도 3, 5, 6, 9, 13으로 p.2가 없다. 검색 누락과 생성 전 차단이 함께 기록되어 있지만, Gemini를 호출하지 않았으므로 잘못된 답변을 생성했다고 판단할 수 없다. 나머지 9문항도 페이지 Hit 이후 같은 사유로 차단되어, 페이지 적중과 답변에 필요한 근거 완전성은 별도로 해석해야 한다.

## 질문 유형별 관찰

- 이번 10문항의 페이지 기준으로 수혈 전체 절차, 동의서 재작성 조건, 수혈 전 검사·주기, 환자 확인, 관찰 시점, 부작용 대처, 방사선 조사 조건, 필터 주의사항, 재불출 절차 질문에서는 정답 근거 페이지를 하나 이상 찾았다.
- RBC의 수령 unit·주입시간을 묻는 TRF-004에서는 정답 페이지가 누락됐다. 이 한 사례를 모든 수치형 질문의 실패로 일반화할 수는 없다. 검사 주기(TRF-003)와 관찰 시점(TRF-006)은 페이지 Hit였다.
- 페이지 기준 Hit가 90%였지만 최종 답변은 전부 거절됐다. 기록이 뒷받침하는 병목은 생성 전 근거 완전성 검사이며, 왜 모든 문항에서 불완전 판정이 발생했는지는 이번 저장 결과 채점만으로 확정하지 않는다.

## 보존 범위

원본 답변·라벨·검색 context·trace·환경정보는 유지하고 평가 필드만 추가했다. 원래 실행의 `scoring_performed=false`도 보존하며, 사후 채점 완료 여부는 `answer_quality_evaluation.evaluation_performed=true`로 별도 기록했다. 기존 Retrieval Baseline JSON과 Label Set은 수정하지 않았다. RAGAS 또는 합격/불합격 판정은 수행하지 않았다.

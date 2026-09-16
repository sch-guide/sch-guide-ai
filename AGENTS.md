# SCHAT 프로젝트 규칙

## 프로젝트 목적
- 프로젝트 상세 요구사항은 docs/PRD.md를 참조한다.

## 개발 절차
- 복잡한 기능은 반드시 분석 → 설계 → 사용자 승인 → 구현 → 테스트 순서로 진행한다.
- 사용자 승인 전에는 실제 구현으로 넘어가지 않는다.
- 단계별 계획 및 설계 산출물은 Markdown(.md)으로 남긴다.
- 사람이 직접 검수해야 하는 결과는 HTML로도 생성한다.

## 개발 단계
- 1단계: BM25 검색 성능 검증
- 2단계: RAG 챗봇 구현
- BM25 검증이 승인되기 전에는 RAG 구현으로 넘어가지 않는다.

## 산출물
- 작업 결과는 artifacts/YYYY-MM-DD_기능명/ 폴더에 저장한다.
- BM25 관련 문서는 docs/bm25/를 참조한다.
- RAG 관련 문서는 docs/rag/를 참조한다.

## 안전 규칙
- 기존 정상 기능을 임의로 삭제하지 않는다.
- C:\Windows, C:\Program Files 등 시스템 경로를 사용자 승인 없이 수정하거나 삭제하지 않는다.
- 병원 지침에 근거가 없는 내용을 임의로 생성하지 않는다.

## 스킬 라우팅
- 아이디어 정리와 구현 전 요구사항 탐색은 `.agents/skills/brainstorm/SKILL.md`를 사용한다.
- 시스템 구조와 복잡한 기능 설계가 필요한 경우 architect 스킬 사용을 사용자에게 제안한다.
- architect 스킬은 사용자가 명시적으로 승인하거나 요청한 경우에만 사용한다.
- 구현 후 코드 품질 검수는 `.agents/skills/code-review/SKILL.md`를 사용한다.
- 오류 발생 시 원인 분석과 해결은 `.agents/skills/debug-error/SKILL.md`를 사용한다.
- 구현 후 테스트 작성 및 테스트 전략 수립은 `.agents/skills/write-tests/SKILL.md`를 사용한다.
- BM25 검색 성능 검증과 검색 실패 분석은 `.agents/skills/bm25-evaluation/SKILL.md`를 사용한다.
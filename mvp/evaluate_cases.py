"""합성 자료만으로 10종 질문을 점검합니다. --live를 지정할 때만 Groq를 호출합니다.

실행: python -m mvp.evaluate_cases [--live] [--case exact]
실제 병원 지침서/계정/질문을 읽지 않으며 결과는 Git 제외 artifacts에 저장합니다.
"""

import argparse
import json
import time
from pathlib import Path

from mvp.ai import Quota, answer_text, generate, validate_answer
from mvp.library import Chunk, Embedder, LocalLibrary, bounded_embedding_question
from mvp.query import plan_query
from mvp.settings import ROOT, GuideError, load_settings


def corpus():
    # 의료 처방/시술을 제시하지 않는 검색 시험용 문서입니다.
    rows = [
        ('pcn', '가상교육안내.pdf', 1, 'PCN 교육', 'PCN irrigation 교육 방법: 용어 카드를 읽고 교육 확인표에 표시합니다.'),
        ('cre', '가상감염교육.pdf', 2, 'CRE 교육', 'CRE 격리 교육 방법: CRE 교육 안내문을 읽고 학습 확인표를 확인합니다.'),
        ('release', '가상감염교육.pdf', 3, 'CRE 교육', 'CRE 격리 교육 종료 기준: 학습 확인표 제출을 확인한 후 교육을 종료합니다.'),
        ('vre', '가상감염교육.pdf', 4, 'VRE 교육', 'VRE 교육은 장알균 용어 카드를 사용합니다. CRE 교육은 장내세균 용어 카드를 사용합니다.'),
        ('drug', '가상약물교육.pdf', 1, 'Vancomycin 교육', 'Vancomycin 교육 주의사항: 반코마이신 교육 카드를 실제 투약 지시로 사용하지 않습니다.'),
        ('materials', '가상교육안내.pdf', 2, '흉강천자 교육', 'thoracentesis 교육 준비물은 용어 카드와 교육 확인표입니다. 실제 시술 준비물에 관한 지침은 아닙니다.'),
        ('pcn-extra', '가상보충안내.pdf', 1, 'PCN 교육', 'PCN irrigation 교육을 마친 뒤 교육 설문을 작성합니다.'),
    ]
    parts = [Chunk(key, name, name, page, name, section, None, text, index, location=f'PDF {page}페이지')
             for index, (key, name, page, section, text) in enumerate(rows)]
    documents = [dict(id=name, document_name=name, title=name) for name in dict.fromkeys(c.document_id for c in parts)]
    return parts, documents


CASES = [
    ('exact', 'PCN irrigation 교육 방법을 알려줘', '', {'pcn'}),
    ('abbreviation', 'VRE 교육은 어떤 카드를 사용해?', '', {'vre'}),
    ('mixed', '반코마이신 Vancomycin 교육 주의사항은?', '', {'drug'}),
    ('typo', 'PCN irrigaton 교육 방법은?', '', {'pcn'}),
    ('materials', 'thoracentesis 교육 준비물 뭐야?', '', {'materials'}),
    ('procedure', 'CRE 격리 교육 어떻게 해?', '', {'cre'}),
    ('comparison', 'CRE 교육과 VRE 교육에서 쓰는 카드를 비교해줘', '', {'vre'}),
    ('synthesis', 'PCN irrigation 교육에 관한 여러 문서 내용을 종합해줘', '', {'pcn', 'pcn-extra'}),
    ('followup', '그럼 교육 종료 기준은?', 'CRE 격리 교육 어떻게 해?', {'release'}),
    ('absent', '화성 우주선의 궤도 계산 공식은?', '', set()),
]


def fixture_answer(hits, expected):
    selected = [h for h in hits if h.chunk.id in expected]
    return {'answerable': bool(selected), 'statements': [dict(text=h.chunk.text,
            evidence=[dict(chunk_id=h.chunk.id, quote=h.chunk.text)]) for h in selected]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--case', choices=[row[0] for row in CASES])
    args = parser.parse_args()
    model = Embedder()
    library = LocalLibrary()
    library.chunks, documents = corpus()
    library.vectors = model.encode([c.text for c in library.chunks])
    settings = load_settings()
    report = []
    for code, question, previous, expected in CASES:
        if args.case and args.case != code:
            continue
        start = time.perf_counter()
        plan = plan_query(question, previous=previous, documents=documents)
        vector = model.encode([bounded_embedding_question(plan.expanded, model)])[0]
        hits = library.search(plan.query, vector, [d['id'] for d in documents], .38, plan)
        entry = dict(case=code, question=question, query=plan.query, kind=plan.kind,
                     expected=sorted(expected), search_seconds=round(time.perf_counter()-start, 3),
                     hits=[dict(chunk_id=h.chunk.id, document=h.chunk.document_name, page=h.chunk.page,
                                text=h.chunk.text, similarity=round(h.similarity, 4), bm25=round(h.bm25_score, 4),
                                rrf=round(h.fusion_score, 4), rerank=round(h.rerank_score, 4)) for h in hits])
        entry['retrieval_pass'] = expected.issubset({h.chunk.id for h in hits}) if expected else not hits
        try:
            if args.live:
                # 실제 앱과 동일한 전체/사용자 한도를 사용하며 한도 오류를 자동 재시도하지 않습니다.
                answer, used = generate(settings, plan.query, hits, 'synthetic-evaluation', quota=Quota(), plan=plan)
            else:
                answer = validate_answer(json.dumps(fixture_answer(hits, expected), ensure_ascii=False), hits)
                used = hits
            entry.update(answer=answer.model_dump(), final_text=answer_text(answer),
                         sent_source_ids=[h.chunk.id for h in used], answer_mode='Groq' if args.live else 'fixture')
        except GuideError as exc:
            entry['error'] = str(exc)
        entry['total_seconds'] = round(time.perf_counter()-start, 3)
        report.append(entry)
        print(json.dumps({k: entry[k] for k in ('case', 'retrieval_pass', 'total_seconds')} |
                         {'answerable': entry.get('answer', {}).get('answerable'), 'error': entry.get('error')}, ensure_ascii=False), flush=True)
    destination = ROOT / 'artifacts' / ('rag-live-' + (args.case or 'all') + '.json' if args.live else 'rag-10-cases.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(Path(destination)), flush=True)


if __name__ == '__main__':
    main()

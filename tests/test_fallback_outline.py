from types import SimpleNamespace as NS
from mvp.answer_ui import fallback_outline


def make(text, section='수혈 절차'):
    return NS(text=text,evidence=[NS(chunk_id='c')]), {'c':NS(section=section,title='수혈간호')}


def test_steps_verbatim_order_and_duplicate_heading():
    s,chunks=make('수혈 절차\n수혈 처방\n확인 및 동의서 작성\nŸ 원문 1.\n↓\n혈액 신청 및 수령\nŸ 원문 2.\n↓\n혈액 신청 및 수령\nŸ 원문 2.')
    assert [x[0] for x in fallback_outline([s],chunks,True)]==['수혈 처방 확인 및 동의서 작성','혈액 신청 및 수령']


def test_nonprocedure_distinct_facts_preserved():
    s,chunks=make('Ÿ 15분 후 확인한다.\nŸ 15분 후 확인한다.\nŸ 30분마다 확인한다.\nŸ 시행하지 않는다.')
    assert [x[0] for x in fallback_outline([s],chunks,False)]==['15분 후 확인한다.','30분마다 확인한다.','시행하지 않는다.']


def test_many_steps_not_discarded():
    s,chunks=make('\n↓\n'.join(f'{i}단계 확인\nŸ 조건 {i} 확인한다.' for i in range(10)))
    assert len(fallback_outline([s],chunks,True))==10


def test_saved_transfusion_flow_uses_only_existing_steps():
    import json
    from pathlib import Path
    row=json.loads((Path(__file__).parents[1]/'evaluation/results/current_hybrid_groq_v5_completed.json').read_text(encoding='utf-8'))['results'][0]
    chunks={c['chunk_id']:NS(section=c['section_title'],title='') for c in row['retrieval_trace']['final_hits']}
    statements=[NS(text=c['content'],evidence=[NS(chunk_id=c['chunk_id'])]) for c in row['retrieved_contexts']]
    original=[s.text for s in statements]
    labels=[x[0] for x in fallback_outline(statements,chunks,True)]
    assert labels==['수혈 처방 확인 및 동의서 작성','수혈 전 검사 확인 및 시행',
                    '혈액신청 및 수령','수혈 전 혈액 확인 절차','수혈 직전 환자확인']
    assert [s.text for s in statements]==original

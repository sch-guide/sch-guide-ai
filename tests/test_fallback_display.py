from mvp.answer_ui import fallback_display


def test_artifacts_wrapping_and_order():
    raw='수혈 절차\n수혈 절차\nŸ   수혈 전,  15분 후\n  관찰한다.\n↓\nŸ 30분마다 확인한다.'
    assert fallback_display(raw, ('수혈 절차',)) == [
        '수혈 절차', '수혈 전, 15분 후 관찰한다.', '30분마다 확인한다.']


def test_clinical_symbols_conditions_and_repeated_instructions_preserved():
    raw='Ÿ 혈압 ↓ 시 중단한다.\nŸ 2~4시간, 1 unit.\nŸ 금기: 시행하지 않는다.\nŸ 금기: 시행하지 않는다.'
    assert fallback_display(raw)==['혈압 ↓ 시 중단한다.', '2~4시간, 1 unit.',
                                  '금기: 시행하지 않는다.', '금기: 시행하지 않는다.']
    assert fallback_display('↓ 혈압\n확인')==['↓ 혈압 확인']


def test_ui_keeps_original_quote_and_page():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('''
import streamlit as st
from mvp.grounded_answer import SourceAnswer, SourceStatement, SourceQuote
from mvp.library import Chunk, Hit
from mvp.answer_ui import render_answer
raw='Ÿ  수혈 전  15분 확인한다.\\n↓'
chunk=Chunk('c','d','guide.pdf',5,'','',None,raw,0)
answer=SourceAnswer(statements=[SourceStatement(text=raw,evidence=[SourceQuote(chunk_id='c',quote=raw)])])
def view(chunk,quotes,**kwargs):
    st.session_state['source_quote']=quotes[0]
render_answer(answer,[Hit(chunk,.9)],0,view)
assert answer.statements[0].text==raw and chunk.text==raw
assert answer.statements[0].evidence[0].quote==raw
''').run()
    assert not app.exception
    assert any('수혈 전 15분 확인한다' in x.value and 'Ÿ' not in x.value for x in app.markdown)
    button=next(b for b in app.button if b.key=='open_source_0_1')
    assert 'p.5' in button.label
    button.click().run()
    assert app.session_state['source_quote']=='Ÿ  수혈 전  15분 확인한다.\n↓'

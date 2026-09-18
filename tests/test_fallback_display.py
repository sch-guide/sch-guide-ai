from mvp.answer_ui import fallback_display, detail_display


def test_detail_title_and_bullet_boundaries():
    raw='수혈 처방\n확인 및\n동의서 작성\nŸ 의사는 혈액의 종류와 수량을 확인하고\n필요한 검사를 처방한다.\nŸ 수혈 동의서를 작성한다.\n↓'
    assert detail_display(raw)==[
        ('heading','수혈 처방 확인 및 동의서 작성'),
        ('bullet','의사는 혈액의 종류와 수량을 확인하고 필요한 검사를 처방한다.'),
        ('bullet','수혈 동의서를 작성한다.')]


def test_detail_preserves_clinical_symbols_and_prose_boundaries():
    assert detail_display('혈압 ↓ 시 중단한다.\n2~4시간 유지한다.\nŸ 금기:\n시행하지 않는다.\n\x00↓')==[
        ('paragraph','혈압 ↓ 시 중단한다.'),('paragraph','2~4시간 유지한다.'),
        ('bullet','금기: 시행하지 않는다.')]


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
    assert app.expander[0].label=='근거 원문 자세히 보기'
    assert any(e.label=='추출 원문 그대로 보기' for e in app.expander)
    assert any('출처:' in m.value and r'guide\.pdf' in m.value and r'p\.5' in m.value for m in app.markdown)
    assert not any(m.value.startswith('[[1]]') for m in app.markdown)
    assert any(x.value=='Ÿ  수혈 전  15분 확인한다.\n↓' for x in app.expander[0].text)
    assert any('수혈 전 15분 확인한다' in x.value and 'Ÿ' not in x.value for x in app.markdown)
    button=next(b for b in app.button if b.key=='open_source_0_1')
    assert 'p.5' in button.label
    button.click().run()
    assert app.session_state['source_quote']=='Ÿ  수혈 전  15분 확인한다.\n↓'

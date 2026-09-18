from types import SimpleNamespace
from unittest.mock import Mock
from dataclasses import replace
import pytest
from mvp import ai
from mvp.library import Chunk, Hit
from mvp.query import plan_query
from mvp.grounded_answer import recover_answer, evidence_only


def test_wrapper_passes_only_original_public_arguments(monkeypatch):
    from mvp import grounded_answer as g
    from mvp.settings import Settings
    received=[]
    def original_signature(settings, question, hits, user_id, quota=None, transport=None, plan=None, trace=None):
        received.append((quota,transport,plan,trace))
        return ai.Answer(answerable=False,statements=[]),hits
    monkeypatch.setattr(ai,'generate',original_signature)
    result,_=g.generate(Settings(),'수혈 활력징후 관찰',hits(),'u')
    assert not result.answerable and len(received)==1


def test_public_signature_and_internal_context_reset():
    import inspect
    from mvp import grounded_answer as g
    assert list(inspect.signature(ai.generate).parameters)==[
        'settings','question','hits','user_id','quota','transport','plan','trace']
    assert g._generation_context.get() is None


def test_capture_is_internal_and_reset_even_on_exception(monkeypatch):
    from mvp import grounded_answer as g
    capture={}
    def generate(settings,question,hits,user_id,quota=None,transport=None,plan=None,trace=None):
        assert g._generation_context.get()['capture'] is capture
        raise RuntimeError('mock failure')
    monkeypatch.setattr(ai,'generate',generate)
    with pytest.raises(RuntimeError):
        g._invoke(None,'q',[], 'u',None,None,None,{},capture=capture)
    assert g._generation_context.get() is None


def hits():
    return [Hit(Chunk('c','d','guide.pdf',5,'수혈','수혈',None,
        '수혈 전, 수혈 시작 후 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무를 관찰한다.',0),.9)]


def test_retry_success_and_single_attempt():
    expected=ai.Answer(answerable=False,statements=[])
    retry=Mock(return_value=(expected,hits()))
    result,used=recover_answer('수혈 활력징후 관찰',hits(),plan_query('수혈 활력징후 관찰'),retry,{})
    retry.assert_called_once()
    assert result.answerable and result.answer_kind=='evidence_only'


def test_insufficient_never_retries():
    retry=Mock()
    result,_=recover_answer('수혈 활력징후 관찰',[],plan_query('수혈 활력징후 관찰'),retry,{})
    assert not result.answerable
    retry.assert_not_called()


def test_verbatim_values_sources_and_incomplete_blocked():
    source=hits();plan=plan_query('수혈 활력징후 관찰')
    result,used=evidence_only(plan,source)
    assert result.answerable
    assert result.statements[0].text==source[0].chunk.text
    assert result.statements[0].evidence[0].quote==source[0].chunk.text
    assert used[0].chunk.page==5 and used[0].chunk.document_name=='guide.pdf'
    assert not evidence_only(plan,[replace(source[0],context_complete=False)])[0].answerable
    assert not evidence_only(plan,[replace(source[0],chunk=replace(source[0].chunk,page=None))])[0].answerable


def test_retry_valid_answer_returned():
    answer=ai.Answer(answerable=True,statements=[ai.Statement(text='원문',evidence=[ai.Evidence(chunk_id='c',quote='원문 근거')])])
    retry=Mock(return_value=(answer,hits()))
    assert recover_answer('수혈 활력징후 관찰',hits(),plan_query('수혈 활력징후 관찰'),retry,{})[0] is answer


@pytest.mark.parametrize('identifier',['TRF-001','TRF-006'])
def test_saved_final_fixture(identifier):
    import json
    from pathlib import Path
    data=json.loads((Path(__file__).parents[1]/'evaluation/results/current_hybrid_groq_v5_completed.json').read_text(encoding='utf-8'))
    row=next(r for r in data['results'] if r['question_id']==identifier)
    metadata={c['chunk_id']:c for c in row['retrieval_trace']['final_hits']}
    source=[]
    for c in row['retrieved_contexts']:
        m=metadata[c['chunk_id']]
        source.append(Hit(Chunk(c['chunk_id'],c['document_id'],c['document'],c['page'],'',
            m['section_title'],None,c['content'],m['index'],parent_id=m['parent_id']),
            c['similarity'],context_only=c['context_only'],context_complete=m['context_complete']))
    answer,used=evidence_only(plan_query(row['question']),source)
    assert answer.answerable
    assert [s.text for s in answer.statements]==[h.chunk.text for h in used]
    assert all(h.chunk.page and h.chunk.document_name for h in used)


@pytest.mark.parametrize('failures',[0,1,2,'unsafe'])
def test_web_generation_transport(failures):
    import json
    import httpx
    from mvp.grounded_answer import generate
    from mvp.settings import Settings
    settings=Settings(llm_provider='groq_free',llm_model='openai/gpt-oss-20b',
        llm_key='test-only',llm_approved=True,groq_free_confirmed=True)
    source=hits();calls=[]
    def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        answer=dict(answerable=True,statements=[dict(text=source[0].chunk.text,
            evidence=[dict(chunk_id='c',quote=source[0].chunk.text)])])
        if failures=='unsafe': answer['statements'][0]['text']=source[0].chunk.text.replace('15분','60분')
        return httpx.Response(200,json={'choices':[{'finish_reason':'length' if isinstance(failures,int) and len(calls)<=failures else 'stop',
            'message':{'content':json.dumps(answer,ensure_ascii=False)}}]})
    quota=Mock();quota.reserve.return_value='r'
    answer,used=generate(settings,'수혈 활력징후 관찰',source,'u',quota=quota,transport=httpx.MockTransport(handler))
    assert answer.answerable
    assert len(calls)==(1 if failures==0 else 2)
    assert quota.reserve.call_count==len(calls)
    assert getattr(answer,'answer_kind',None)==('evidence_only' if failures in (2,'unsafe') else None)
    if failures=='unsafe': assert answer.statements[0].text==source[0].chunk.text


def test_conflict_and_missing_aspect_do_not_retry(monkeypatch):
    from mvp import grounded_answer as g
    retry=Mock()
    monkeypatch.setattr(g,'explicit_conflicts',lambda _: [('a','b')])
    assert not g.recover_answer('수혈 활력징후 관찰',hits(),plan_query('수혈 활력징후 관찰'),retry,{})[0].answerable
    retry.assert_not_called()


def test_fallback_ui_label_and_page():
    from streamlit.testing.v1 import AppTest
    script='''
from mvp.grounded_answer import SourceAnswer, SourceStatement, SourceQuote
from mvp.library import Chunk, Hit
from mvp.answer_ui import render_answer
chunk=Chunk('c','d','guide.pdf',5,'','',None,'Source text.',0)
answer=SourceAnswer(statements=[SourceStatement(text=chunk.text,evidence=[SourceQuote(chunk_id='c',quote=chunk.text)])])
render_answer(answer,[Hit(chunk,.9)],0,lambda *args,**kwargs:None)
'''
    app=AppTest.from_string(script).run()
    assert not app.exception
    assert any('근거 기반 안내' in x.value for x in app.markdown)
    assert any('AI가 생성한 답변이 아니라' in x.value for x in app.caption)
    assert any('guide.pdf' in b.label and 'p.5' in b.label for b in app.button)

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from evaluation import run_current_hybrid_ragas as r


def test_mapping_and_empty_context():
    labels = [dict(id='a', question='Q', reference_answer='R')]
    rows = r.map_rows(labels, {'results': [dict(question_id='a', question='Q', retrieved_contexts=[])]})
    assert rows[0]['reference'] == 'R'
    assert rows[0]['metric_status']['context_recall'] == 'na'
    assert rows[0]['context_recall'] is None


def test_order_preserved_and_mismatch_rejected():
    labels = [dict(id='a', question='Q', reference_answer='R')]
    data = {'results': [dict(question_id='a', question='Q', retrieved_contexts=[{'content':'second'}, {'content':'first'}])]}
    assert r.map_rows(labels, data)[0]['retrieved_contexts'] == ['second', 'first']
    data['results'][0]['question'] = 'different'
    with pytest.raises(ValueError):
        r.map_rows(labels, data)


def test_checkpoint_resume_skips_completed_and_averages_only_valid(tmp_path):
    row = r.map_rows([dict(id='a', question='Q', reference_answer='R')],
        {'results':[dict(question_id='a', question='Q', retrieved_contexts=[{'content':'C'}])]})[0]
    report = {'results':[row]}
    calls = []
    async def score(name, sample):
        calls.append(name)
        if name == 'context_recall' and len(calls) == 2:
            raise RuntimeError('secret must not be stored')
        return .5
    output = tmp_path/'result.json'
    asyncio.run(r.evaluate(report, score, output))
    assert row['metric_status']['context_precision'] == 'completed'
    assert 'secret' not in output.read_text()
    asyncio.run(r.evaluate(report, score, output))
    assert calls == ['context_precision', 'context_recall', 'context_recall']
    assert r.mean(report, 'context_precision') == (.5, 1)


def test_retry_after_and_bounded_retry():
    exc = RuntimeError('private')
    exc.status_code = 429
    exc.response = SimpleNamespace(headers={'retry-after':'2'})
    delays=[]
    async def sleep(n): delays.append(n)
    async def fail(): raise exc
    with pytest.raises(RuntimeError):
        asyncio.run(r.with_retry(fail, sleep=sleep))
    assert delays == [2.0, 2.0]


def test_in_progress_is_not_replayed(tmp_path):
    report={'results':[{'metric_status':{'context_precision':'in_progress', 'context_recall':'na'}}]}
    score=Mock()
    asyncio.run(r.evaluate(report, score, tmp_path/'x.json'))
    score.assert_not_called()


def test_na_not_counted_as_zero():
    report={'results':[{'context_precision':None, 'metric_status':{'context_precision':'na'}},
                       {'context_precision':.8, 'metric_status':{'context_precision':'completed'}}]}
    assert r.mean(report, 'context_precision') == (.8, 1)


def test_http_date_retry_after():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    exc=RuntimeError()
    exc.status_code=429
    exc.response=SimpleNamespace(headers={'Retry-After':format_datetime(datetime.now(timezone.utc)+timedelta(seconds=30))})
    assert 28 <= r.retry_after(exc) <= 30


def test_real_ragas_litellm_adapter_with_mock_transport(monkeypatch):
    # Real SDK parsing and metrics, synthetic data, zero network/real credentials.
    monkeypatch.setenv('RAGAS_DO_NOT_TRACK', 'true')
    monkeypatch.setenv('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
    import dotenv
    import litellm
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda _: {'GUIDE_LLM_API_KEY':'test-only'})
    calls=[]
    async def fake(**kwargs):
        calls.append(kwargs)
        content = json.dumps({'reason':'supported', 'verdict':1, 'classifications':[
            {'statement':'Paris is in France.', 'reason':'supported', 'attributed':1}]})
        return litellm.ModelResponse(choices=[{'index':0,'finish_reason':'stop',
            'message':{'role':'assistant','content':content}}])
    monkeypatch.setattr(litellm, 'acompletion', fake)
    score=r.scorer()
    sample={'question':'Where is Paris?', 'reference':'Paris is in France.',
            'retrieved_contexts':['Paris is in France.']}
    assert asyncio.run(score('context_precision', sample)) == pytest.approx(1)
    assert asyncio.run(score('context_recall', sample)) == pytest.approx(1)
    assert len(calls) == 2
    assert all(c['model'] == r.MODEL and c['num_retries'] == 0 for c in calls)

import json

import pytest

from mvp.ai import validate_answer
from mvp.library import Chunk, Hit
from mvp.settings import GuideError


def attempt(text, *, quote='교육실 안내문을 읽습니다.', label='', trace=None):
    chunk = Chunk('source-1', 'doc-1', 'synthetic.pdf', 1, '', '', None,
                  '교육실 안내문을 읽습니다. 예약표를 확인합니다.', 0)
    raw = json.dumps({'answerable': True, 'statements': [
        {'text': '교육실 안내문을 읽습니다.', 'evidence': [
            {'chunk_id': 'source-1', 'quote': '교육실 안내문을 읽습니다.'}]},
        {'text': text, 'label': label, 'evidence': [
            {'chunk_id': 'source-1', 'quote': quote}]}]})
    return validate_answer(raw, [Hit(chunk, .9)], trace=trace)


def test_failed_sentence_index_and_evidence():
    trace = {}
    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        attempt('안내문을 읽습니다.', trace=trace)
    assert trace['validation_reason'] == 'unsupported sentence'
    assert trace['post_llm_validation_stage'] == 'sentence_matching'
    failure = trace['validation_failure']
    assert failure['statement_index'] == 2
    assert failure['sentence_index'] == 1
    assert failure['statement_text'] == '안내문을 읽습니다.'
    assert failure['sentence_text'] == '안내문을 읽습니다.'
    assert failure['failure_type'] == 'sentence_not_in_source'
    assert failure['evidence'][0]['chunk_id'] == 'source-1'


@pytest.mark.parametrize('text,quote,label,kind', [
    ('예약표를 확인합니다.', '교육실 안내문을 읽습니다.', '', 'sentence_not_in_quote'),
    ('교육실 안내문을 읽습니다.', '교육실 안내문을 읽습니다.', '안내사항', 'label_not_in_quote'),
    ('교육실 안내문을 읽습니다.', '없는 원문입니다.', '', 'citation_mismatch'),
])
def test_failure_types(text, quote, label, kind):
    trace = {}
    with pytest.raises(GuideError):
        attempt(text, quote=quote, label=label, trace=trace)
    assert trace['validation_failure']['failure_type'] == kind


@pytest.mark.parametrize('secret', [
    '환자명: 홍길동', '010-1234-5678', 'Authorization: Bearer secret-sentinel',
    'GEMINI_API_KEY=secret-sentinel', '홍길동 환자는 입원 중입니다.',
])
def test_sensitive_statement_is_not_retained(secret):
    trace = {}
    with pytest.raises(GuideError):
        attempt(secret, trace=trace)
    assert secret not in json.dumps(trace, ensure_ascii=False)
    assert trace['validation_failure']['statement_text'] is None


def test_result_and_error_are_unchanged_with_trace():
    assert attempt('예약표를 확인합니다.', quote='예약표를 확인합니다.').model_dump() == attempt(
        '예약표를 확인합니다.', quote='예약표를 확인합니다.', trace={}).model_dump()
    messages = []
    for trace in (None, {}):
        with pytest.raises(GuideError) as caught:
            attempt('안내문을 읽습니다.', trace=trace)
        messages.append(str(caught.value))
    assert messages[0] == messages[1]


def test_second_sentence_is_located():
    trace = {}
    with pytest.raises(GuideError):
        attempt('교육실 안내문을 읽습니다. 안내문을 읽습니다.', trace=trace)
    assert trace['validation_failure']['sentence_index'] == 2
    assert trace['validation_failure']['sentence_text'] == '안내문을 읽습니다.'


def test_file_credential_is_redacted(tmp_path, monkeypatch):
    from mvp import validation_trace
    (tmp_path / 'mvp').mkdir()
    (tmp_path / 'mvp' / '.env').write_text('GUIDE_LLM_API_KEY=privatevalue\n', encoding='utf-8')
    monkeypatch.setattr(validation_trace, 'ROOT', tmp_path)
    monkeypatch.delenv('GUIDE_LLM_API_KEY', raising=False)
    assert validation_trace.safe_text('privatevalue') is None


def test_quote_is_never_saved():
    trace = {}
    with pytest.raises(GuideError):
        attempt('안내문을 읽습니다.', quote='Authorization: Bearer secret-sentinel', trace=trace)
    assert 'secret-sentinel' not in json.dumps(trace)


def test_diagnostic_failure_does_not_change_rejection(monkeypatch):
    from mvp import validation_trace
    monkeypatch.setattr(validation_trace, 'safe_text', lambda value: 1 / 0)
    trace = {}
    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        attempt('안내문을 읽습니다.', trace=trace)
    assert trace['validation_reason'] == 'unsupported sentence'
    assert trace['post_llm_validation_stage'] == 'diagnostic_unavailable'

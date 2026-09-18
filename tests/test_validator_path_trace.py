import json
import pytest
from mvp.ai import validate_answer
from mvp.library import Chunk, Hit
from mvp.settings import GuideError

@pytest.mark.parametrize("source,text,label,exact,norm,reason", [
    ("안내문을 읽습니다.", "안내문을 읽습니다.", "", True, False, "passed"),
    ("(유무 관찰)", "유무를 관찰한다.", "", False, True, "passed"),
    ("(유무 관찰)", "유무를 관찰한다.", "새 label", False, True, "label_not_in_quote"),
    ("안내문을 읽습니다.", "없는 문장입니다.", "", False, False, "unsupported sentence"),
])
def test_real_path(source,text,label,exact,norm,reason):
    c=Chunk("one","doc","synthetic.pdf",1,"","",None,source,0)
    raw=json.dumps({"answerable":True,"statements":[{"text":text,"label":label,"evidence":[{"chunk_id":"one","quote":source}]}]})
    trace={}
    if reason=="passed": validate_answer(raw,[Hit(c,.9)],trace)
    else:
        with pytest.raises(GuideError): validate_answer(raw,[Hit(c,.9)],trace)
    assert trace["exact_match_passed"] is exact
    assert trace["normalization_passed"] is norm
    assert trace["final_validation_reason"]==reason
    if norm: assert trace["matched_source_text"]==[source]


def test_matched_source_does_not_leak_credentials():
    source = 'Authorization: Bearer secret-sentinel'
    c = Chunk('one', 'doc', 'synthetic.pdf', 1, '', '', None, source, 0)
    raw = json.dumps({'answerable': True, 'statements': [{'text': source,
        'evidence': [{'chunk_id': 'one', 'quote': source}]}]})
    trace = {}
    validate_answer(raw, [Hit(c, .9)], trace)
    assert trace['exact_match_passed'] is True
    assert trace['matched_source_text'] == [None]
    assert 'secret-sentinel' not in json.dumps(trace)

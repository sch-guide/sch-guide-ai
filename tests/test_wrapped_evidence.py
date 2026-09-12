"""병원 원문을 공개 저장소에 넣지 않고 실제 PDF의 줄바꿈/제목 구조를 재현합니다."""

import json
from dataclasses import replace

import httpx
import numpy as np
import pytest

from mvp.ai import Quota, answer_text, generate, validate_answer
from mvp.evidence import assess_evidence, source_sentences
from mvp.library import DIMENSIONS, NO_GUIDELINE, Chunk, Hit, LocalLibrary, clean, has_substantive_body
from mvp.query import plan_query
from mvp.settings import GuideError, Settings

WRAPPED = '교육 자료의 누락을 예방하기 위해 담당자가 학습자의\n상태를 확인하고 안내문을 읽도록 돕기 위함이다.'


def purpose():
    return Chunk('body', 'doc', '검색교육.pdf', 1, '검색교육', '1. 목적', None, '1. 목적\n' + WRAPPED, 1)


def raw_answer(text, quote=None):
    return json.dumps(dict(answerable=True, statements=[dict(text=text, evidence=[
        dict(chunk_id='body', quote=quote or text)])]), ensure_ascii=False)


def test_soft_wrap_is_one_complete_sentence_without_dropping_words():
    assert source_sentences('1. 목적\n' + WRAPPED) == ['1. 목적', clean(WRAPPED)]
    answer = validate_answer(raw_answer(clean(WRAPPED)), [Hit(purpose(), .58)])
    assert len(answer.statements) == 1 and answer.statements[0].text == clean(WRAPPED)
    assert answer.statements[0].evidence[0].quote == clean(WRAPPED)


@pytest.mark.parametrize('fragment', WRAPPED.splitlines())
def test_only_one_visual_line_cannot_be_used_as_a_complete_answer(fragment):
    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_answer(raw_answer(fragment, clean(WRAPPED)), [Hit(purpose(), .58)])


@pytest.mark.parametrize('text', [
    '교육 자료의 누락을 예방하기 위해\n\n담당자가 안내문을 읽는다.',
    '교육 자료의 누락을 예방하기 위해\n2. 주의사항\n담당자가 안내문을 읽는다.',
    '교육 자료의 누락을 예방하기 위해\n• 담당자가 안내문을 읽는다.',
    '교육 자료의 누락을 예방하기 위해\n항목 | 내용',
    '항목 | 담당자의\n교육 자료를 확인한다.',
])
def test_paragraph_heading_list_and_table_boundaries_remain_separate(text):
    assert clean(text) not in source_sentences(text)


def test_complete_sentences_numbers_units_and_negations_remain_intact():
    text = '승인된 경우 0.5 mg을\n기록하며 단위를 변경하지 않는다. 확인표는 생략하지 않는다.'
    assert source_sentences(text) == ['승인된 경우 0.5 mg을 기록하며 단위를 변경하지 않는다.', '확인표는 생략하지 않는다.']


def test_repeated_header_and_example_captions_are_not_body_evidence():
    chunk = replace(purpose(), text='교육안내\n[교육기록]\n예시 ②\n예시 ①', section='')
    assert not has_substantive_body(chunk)
    assert has_substantive_body(replace(chunk, text=chunk.text + '\n교육 안내문을 읽는다.'))


def test_heading_only_chunks_do_not_displace_real_purpose_or_enter_llm(tmp_path):
    source = purpose()
    heading = replace(source, id='heading', index=0, text='15. 검색교육', section='15. 검색교육')
    library, trace = LocalLibrary(), {}
    library.chunks = [heading, source]
    library.vectors = np.zeros((2, DIMENSIONS), dtype=np.float32)
    library.vectors[:, 0] = [1, .58]
    library.vectors[1, 1] = np.sqrt(1 - .58**2)
    vector = np.zeros(DIMENSIONS, dtype=np.float32)
    vector[0] = 1
    query, plan = '검색교육 목적에 대해 알려줘', plan_query('검색교육 목적에 대해 알려줘')
    hits = library.search(query, vector, ['doc'], .38, plan=plan, trace=trace)
    assert [h.chunk.id for h in hits] == ['body']
    assert trace['reranked_top5'][0]['chunk_id'] == 'body'
    assert next(r for r in trace['rerank_decisions'] if r['chunk_id'] == 'heading')['reason'] == 'heading_only_or_short_body'
    assert not assess_evidence(plan, [Hit(heading, .999)]).sufficient
    settings = Settings(llm_provider='internal', llm_url='https://example.invalid/v1', llm_model='fixture', llm_approved=True)
    def respond(request):
        sent = json.loads(request.content)['messages'][1]['content']
        assert '"chunk_id": "heading"' not in sent
        return httpx.Response(200, json={'choices': [{'message': {'content': raw_answer(clean(WRAPPED))}}]})
    generation = {}
    answer, _ = generate(settings, query, hits, 'fixture', Quota(tmp_path / 'quota.db'),
                         httpx.MockTransport(respond), plan=plan, trace=generation)
    assert answer_text(answer) == clean(WRAPPED) and answer_text(answer) != NO_GUIDELINE
    assert generation['citation_assessment'] == 'supported' and generation['block_reason'] is None

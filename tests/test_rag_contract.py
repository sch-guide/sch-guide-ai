"""사용자 요청의 실제 배포 경로 및 근거 부족/문장별 출처 계약을 검증합니다."""

import json
from dataclasses import asdict, replace
from uuid import UUID

import httpx
import numpy as np
import pytest

from mvp.ai import Quota, answer_text, generate, validate_answer
from mvp.cloud import StaffLibrary
from mvp.cloud_repository import CloudRepository
from mvp.context import expand_context
from mvp.documents import PdfDocument, PdfPage
from mvp.evidence import assess_evidence
from mvp.library import DIMENSIONS, NO_GUIDELINE, Chunk, Hit, LocalLibrary, make_chunks
from mvp.query import plan_query
from mvp.settings import GuideError, Settings


def chunk(identifier='c', text='PCN 교육 안내문을 확인합니다.', section='PCN 교육', document='doc', index=0):
    return Chunk(identifier, document, '가상지침.pdf', 3, '가상지침', section, None, text, index)


def configured():
    return Settings(llm_provider='internal', llm_url='https://example.invalid/v1',
                    llm_model='fixture', llm_approved=True)


def response(statements):
    return json.dumps({'answerable': True, 'statements': statements}, ensure_ascii=False)


def statement(text, source, quote=None):
    return {'text': text, 'evidence': [{'chunk_id': source.id, 'quote': quote or source.text}]}


@pytest.mark.parametrize('question', ['오늘 날씨 어때?', '간호사에게 주식 추천해줘', 'PCN 설명하고 로또 번호 알려줘'])
def test_out_of_scope_never_searches_or_calls_llm(question):
    plan = plan_query(question)
    assert plan.domain == 'out_of_scope'
    library = LocalLibrary()
    library.chunks = [chunk()]
    library.vectors = np.ones((1, DIMENSIONS), dtype=np.float32)
    assert library.search(question, library.vectors[0], ['doc'], .1, plan) == []
    result, _ = generate(Settings(), question, [Hit(chunk(), .99)], 'employee', plan=plan,
                         transport=httpx.MockTransport(lambda r: pytest.fail('LLM called')))
    assert answer_text(result) == NO_GUIDELINE


def test_fullwidth_question_normalization_and_offtopic_followup():
    assert plan_query('  ＰＣＮ　 세척  방법? ').entities == ('pcn',)
    assert plan_query('그럼 오늘 날씨는?', previous='PCN 교육 방법').domain == 'out_of_scope'


@pytest.mark.parametrize('question', ['PCN 세척량은?', 'PCN 교체 주기는?', 'PCN 제거 속도는?',
                                    'PCN 해제 기준은?', 'PCN 교육 방법은?'])
def test_medical_topic_without_requested_evidence_abstains_before_api(question):
    hits = [Hit(chunk(text='PCN 지침의 목적은 교육용 용어 안내입니다.'), .99)]
    assert not assess_evidence(plan_query(question), hits).sufficient
    result, _ = generate(configured(), question, hits, 'employee',
                         transport=httpx.MockTransport(lambda r: pytest.fail('LLM called')))
    assert answer_text(result) == '등록된 지침서에서 확인할 수 없습니다.'


def test_vector_similarity_and_title_alone_are_not_evidence():
    source = replace(chunk(text='교육실 배포 일정을 안내합니다.', section=''), title='진정간호', document_name='진정간호.pdf')
    assert not assess_evidence(plan_query('진정간호 투여량은?'), [Hit(source, .999)]).sufficient


def test_every_requested_aspect_required_before_and_after_budget(monkeypatch):
    hits = [Hit(chunk('amount', 'PCN 세척량은 가상값 5 mL입니다.'), .9),
            Hit(chunk('interval', 'PCN 세척 간격은 가상값 3시간입니다.', index=1), .8)]
    plan = plan_query('PCN 세척량과 간격은?')
    assert assess_evidence(plan, hits).sufficient
    monkeypatch.setattr('mvp.ai.prompt_messages', lambda *a, **k: ([], hits[:1]))
    result, _ = generate(configured(), plan.query, hits, 'employee', plan=plan,
                         transport=httpx.MockTransport(lambda r: pytest.fail('LLM called')))
    assert answer_text(result) == NO_GUIDELINE


def test_prompt_can_include_essential_source_beyond_five_hits(tmp_path):
    hits = [Hit(chunk(str(i), f'PCN 교육 확인표 {i}를 읽습니다.', index=i), .8) for i in range(5)]
    last = chunk('last', 'PCN 교육 준비물은 용어 카드입니다.', index=5)
    hits.append(Hit(last, .7))
    def handle(request):
        evidence = json.loads(json.loads(request.content)['messages'][1]['content'].split('Evidence (JSON):\n')[1])
        assert 'last' in {e['chunk_id'] for e in evidence}
        return httpx.Response(200, json={'choices': [{'message': {'content': response([statement(last.text, last)])}}]})
    result, _ = generate(configured(), 'PCN 교육 준비물은?', hits, 'employee',
                         Quota(tmp_path / 'quota.db'), httpx.MockTransport(handle))
    assert result.answerable


def test_multiple_sentences_are_split_and_cited_individually():
    a = chunk('a', 'PCN 교육 카드를 확인합니다.')
    b = chunk('b', 'PCN 교육 확인표를 작성합니다.')
    item = statement(a.text + ' ' + b.text, a)
    item['evidence'].append({'chunk_id': b.id, 'quote': b.text})
    result = validate_answer(response([item]), [Hit(a, .8), Hit(b, .8)])
    assert [s.text for s in result.statements] == [a.text, b.text]
    assert [[e.chunk_id for e in s.evidence] for s in result.statements] == [['a'], ['b']]


@pytest.mark.parametrize(('source', 'invented'), [
    ('PCN 교육 확인표를 작성하지 않습니다.', 'PCN 교육 확인표를 작성합니다.'),
    ('관리자가 승인한 경우 PCN 교육표를 배포합니다.', 'PCN 교육표를 배포합니다.'),
    ('PCN 교육 안내문을 읽습니다.', 'PCN은 안전하고 부작용이 없습니다.'),
    ('PCN 교육 안내문을 읽습니다.', 'PCN 교육 안내문을 읽습니다. 추가 검사도 필요합니다.'),
])
def test_real_citation_does_not_license_new_knowledge_or_removed_conditions(source, invented):
    part = chunk(text=source)
    with pytest.raises(GuideError, match='AI_EVIDENCE'):
        validate_answer(response([statement(invented, part)]), [Hit(part, .9)])


def test_ungrounded_llm_output_is_exact_abstention_and_counts_usage(tmp_path):
    source = chunk()
    payload = {'choices': [{'message': {'content': response([statement('PCN은 항상 안전합니다.', source)])}}],
               'usage': {'total_tokens': 42}}
    quota = Quota(tmp_path / 'quota.db')
    answer, _ = generate(configured(), 'PCN 교육 안내', [Hit(source, .9)], 'employee', quota,
                         httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    assert answer_text(answer) == NO_GUIDELINE
    assert quota.summary(configured())['tokens'] == 42


def test_llm_cannot_answer_other_aspect_even_with_true_quote(tmp_path):
    purpose = chunk('purpose', 'PCN 교육 목적은 용어 학습입니다.')
    material = chunk('material', 'PCN 교육 준비물은 용어 카드입니다.', index=1)
    payload = {'choices': [{'message': {'content': response([statement(purpose.text, purpose)])}}]}
    answer, _ = generate(configured(), 'PCN 교육 준비물은?', [Hit(purpose, .9), Hit(material, .8)],
                         'employee', Quota(tmp_path / 'quota.db'),
                         httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    assert answer_text(answer) == NO_GUIDELINE


def test_chunk_parent_ids_can_be_written_to_postgres_uuid_column():
    class Counter:
        def count(self, text):
            return len(text)
    _, chunks = make_chunks(PdfDocument('가상.pdf', (PdfPage(3, 'PCN 교육\n\n교육 카드를 확인합니다.'),)), Counter())
    assert all(str(UUID(c.parent_id)) == c.parent_id for c in chunks)
    assert all(c.document_name == '가상.pdf' and c.page == 3 for c in chunks)


def cloud_fixture():
    """실제 StaffLibrary.request + CloudRepository.search를 통과하는 모의 Supabase."""
    parts = [chunk(str(i), '다른 주제의 일반 교육 안내입니다.', section='일반', index=i) for i in range(45)]
    target = chunk('target', 'PCN 교육 준비물은 용어 카드입니다.', index=45)
    next_part = chunk('next', '교육 확인표를 생략하지 않습니다.', index=46, section='PCN 교육')
    parts.extend([target, next_part])
    state = {'parts': parts, 'revision': 'v1', 'active': True, 'source_reads': 0, 'requests': []}
    def handle(request):
        state['requests'].append(request)
        assert request.headers.get('authorization') == 'Bearer staff-test-token'
        path = request.url.path
        if path == '/auth/v1/user':
            return httpx.Response(200, json={'id': 'employee'})
        if path == '/rest/v1/guide_profiles':
            return httpx.Response(200, json=[{'user_id': 'employee', 'role': 'staff', 'active': state['active']}])
        if path == '/rest/v1/guide_documents':
            return httpx.Response(200, json=[{'id': 'doc', 'status': 'active', 'file_hash': 'hash',
                                             'indexed_at': state['revision']}])
        if path == '/rest/v1/guide_chunks':
            state['source_reads'] += 1
            assert 'embedding' not in request.url.params['select']
            return httpx.Response(200, json=[asdict(c) for c in state['parts']])
        if path == '/rest/v1/rpc/guide_search':
            payload = json.loads(request.content)
            assert payload['query_terms'] == [] and payload['document_ids'] == ['doc']
            return httpx.Response(200, json=[{**asdict(c), 'similarity': .99} for c in state['parts'][:40]])
        pytest.fail(f'Unexpected path {path}')
    auth = StaffLibrary(Settings(supabase_url='https://example.invalid', supabase_key='public-test'),
                        httpx.MockTransport(handle))
    auth.token, auth.user_id, auth.expires = 'staff-test-token', 'employee', float('inf')
    return CloudRepository(auth.settings, auth, None), state


def test_cloud_bm25_finds_sources_outside_dense_top40_and_reuses_then_invalidates_cache():
    repo, state = cloud_fixture()
    plan = plan_query('PCN 교육 준비물은?')
    def search():
        return repo.search(plan.query, np.zeros(DIMENSIONS), ['doc'], .38, plan=plan)
    hits = search()
    assert hits[0].chunk.id == 'target' and hits[0].bm25_score > 0 and hits[0].rerank_score > 0
    assert 'next' in {h.chunk.id for h in hits}
    search()
    assert state['source_reads'] == 1
    state['parts'] = state['parts'][:45]
    state['revision'] = 'v2'
    assert search() == []
    assert state['source_reads'] == 2
    state['active'] = False
    with pytest.raises(GuideError, match='ACCESS'):
        search()
    assert repo.auth._search_cache is None


def test_cloud_respects_plan_document_scope():
    repo, state = cloud_fixture()
    plan = replace(plan_query('PCN 교육'), document_ids=('another-document',))
    assert repo.search(plan.query, np.zeros(DIMENSIONS), ['doc'], .38, plan) == []
    assert not any(r.url.path == '/rest/v1/rpc/guide_search' for r in state['requests'])


def test_truncated_semantic_block_abstains_without_omitting_cautions():
    parts = [replace(chunk(str(i), f'PCN 교육 절차 {i}를 확인합니다.', index=i), parent_id='same-block')
             for i in range(7)]
    hits = expand_context('PCN 교육 절차', [Hit(parts[0], .9)], parts)
    assert hits and not all(h.context_complete for h in hits)
    assert not assess_evidence(plan_query('PCN 교육 절차'), hits).sufficient


def test_offtopic_chat_stops_before_loading_embedding_model(monkeypatch, tmp_path):
    import streamlit as st

    from tests.test_mvp_chat import ask, registered_app
    monkeypatch.setenv('GUIDE_MODE', 'local')
    monkeypatch.setenv('GUIDE_DATA_DIR', str(tmp_path / 'library'))
    monkeypatch.setenv('GUIDE_STORAGE_BACKEND', 'local')
    st.cache_resource.clear()
    monkeypatch.setattr('mvp.library.Embedder', lambda: pytest.fail('Embedding must not run'))
    monkeypatch.setattr('mvp.ai.generate', lambda *a, **k: pytest.fail('LLM must not run'))
    app = registered_app(monkeypatch, tmp_path)
    ask(app, '간호사에게 주식 추천해줘')
    assert not app.exception
    assert any(x.value == NO_GUIDELINE for x in app.markdown)

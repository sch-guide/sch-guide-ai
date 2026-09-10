"""2026-09 운영 고도화 회귀 테스트."""

import sqlite3
from io import BytesIO

import httpx
import numpy as np
import pytest
from docx import Document
from pglast import parse_sql

from mvp.ai import Quota, RateLimitError, generate
from mvp.auth import LocalAuth
from mvp.checklist_ui import matching_checklists
from mvp.library import DIMENSIONS, Hit
from mvp.medical_terms import ABBREVIATIONS, ALIASES
from mvp.repository import Repository
from mvp.settings import Settings
from mvp.storage import source_store
from tests.test_mvp_chat import part


class Model:
    def count(self, text):
        return len(text)

    def encode(self, texts):
        values = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        values[:, 0] = 1
        return values


def test_medical_terms_are_separate_and_bilingual():
    for key in ('pcn', 'foley', 'cre', 'cpe', 'vre', 'picc', 'vancomycin'):
        assert key in ALIASES
        assert any(any('가' <= char <= '힣' for char in term) for term in ALIASES[key])
    assert 'pcn' in ABBREVIATIONS and 'irrigation' in ' '.join(ALIASES['pcn'])


def test_only_procedure_questions_open_an_approved_checklist():
    entry = {'title': 'PCN 관리', 'keywords': ['PCN', '신루관'], 'items': [{'quote': '확인'}]}
    assert matching_checklists('PCN irrigation 방법을 알려줘', [entry]) == [entry]
    assert matching_checklists('PCN의 정의는?', [entry]) == []
    assert matching_checklists('Foley 관리 방법은?', [entry]) == []


def test_two_short_groq_reservations_do_not_immediately_wait(tmp_path):
    quota = Quota(tmp_path / 'quota.sqlite3')
    settings = Settings(llm_provider='groq_free', daily_limit=100, user_daily_limit=20)
    assert quota.reserve(settings, 'employee', 3500, now=100)
    assert quota.reserve(settings, 'employee', 3500, now=101)
    with pytest.raises(RateLimitError) as error:
        quota.reserve(settings, 'employee', 3500, now=102)
    assert 1 <= error.value.retry_after <= 60


def test_rejected_groq_request_releases_unreported_reservation(tmp_path):
    quota = Quota(tmp_path / 'quota.sqlite3')
    settings = Settings(llm_provider='groq_free', llm_key='test-key',
                        llm_model='openai/gpt-oss-20b', llm_approved=True,
                        groq_free_confirmed=True, daily_limit=100, user_daily_limit=20)
    with pytest.raises(RateLimitError):
        generate(settings, '교육실 확인 방법', [Hit(part(), .8)], 'employee', quota,
                 httpx.MockTransport(lambda request: httpx.Response(
                     429, headers={'x-ratelimit-reset-tokens': '2s'})))
    with sqlite3.connect(quota.path) as db:
        assert db.execute('select count(*) from reservations').fetchone()[0] == 0


def test_review_request_stores_only_hash_and_source_ids(tmp_path):
    settings = Settings(mode='local', data_dir=str(tmp_path / 'library'))
    auth = LocalAuth(settings.library_dir)
    auth.bootstrap('admin', 'synthetic-admin-password')
    repo = Repository(settings, auth, source_store(settings, auth))
    document = Document()
    document.add_paragraph('PCN 관리 전 확인 사항입니다.')
    data = BytesIO()
    document.save(data)
    doc_id = repo.register('synthetic.docx', data.getvalue(), Model())
    chunk_id = repo.source_chunks(doc_id)[0].id
    repo.request_review(auth.user_id, [doc_id], [chunk_id])
    with sqlite3.connect(repo.path) as db:
        row = db.execute('select user_hash,document_ids,chunk_ids,status from review_requests').fetchone()
    assert len(row[0]) == 64 and auth.user_id not in row[0]
    assert doc_id in row[1] and chunk_id in row[2] and row[3] == 'open'


def test_pgvector_migration_is_parseable_and_keeps_rls():
    sql = (pytest.importorskip('pathlib').Path(__file__).parents[1] /
           'mvp/migrations/20260910_operational_pgvector.sql').read_text(encoding='utf-8')
    assert len(parse_sql(sql)) >= 30
    lowered = sql.lower()
    assert 'enable row level security' in lowered
    assert 'service_role' not in lowered
    assert 'guide_request_review' in lowered and 'guide_reindex' in lowered

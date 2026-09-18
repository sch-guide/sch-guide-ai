"""Evaluation-only quota paths and read-only capacity reporting."""
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'evaluation' / '.runtime'


def resolve_path(path=None):
    value = path or os.environ.get('GUIDE_EVALUATION_USAGE_DB')
    target = Path(value) if value else RUNTIME / 'usage.sqlite3'
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    if not target.is_relative_to(RUNTIME.resolve()) or target.suffix != '.sqlite3':
        raise ValueError('Evaluation quota must be a .sqlite3 file inside evaluation/.runtime.')
    service = ROOT / 'data' / 'usage.sqlite3'
    if target.exists() and service.exists() and target.samefile(service):
        raise ValueError('Service quota cannot be used for evaluation.')
    return target


def preflight(settings, number_of_questions, path=None):
    target = resolve_path(path)
    calls = own = tokens = minute_tokens = 0
    if target.exists():
        with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as db:
            now = time.time()
            calls, tokens = db.execute(
                'select count(*),coalesce(sum(tokens),0) from reservations where at>=?',
                (now - 86400,)).fetchone()
            own = db.execute('select count(*) from reservations where at>=? and user_hash=?',
                (now - 86400, hashlib.sha256(b'bm25-baseline-v1').hexdigest())).fetchone()[0]
            minute_tokens = db.execute('select coalesce(sum(tokens),0) from reservations where at>?',
                (now - 60,)).fetchone()[0]
    remaining = min(settings.daily_limit - calls, settings.user_daily_limit - own)
    return dict(quota_db=str(target), exists=target.exists(), calls=calls, user_calls=own,
                tokens=tokens, minute_tokens=minute_tokens,
                daily_call_limit=settings.daily_limit, user_daily_call_limit=settings.user_daily_limit,
                remaining_calls=max(0, remaining), planned_questions=number_of_questions,
                ten_question_call_capacity=remaining >= number_of_questions,
                completion_guaranteed=False,
                note='Local call-count capacity only. Existing token/minute limits and provider limits still apply; concurrent usage may change capacity.')

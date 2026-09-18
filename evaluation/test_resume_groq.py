import json
from unittest.mock import Mock

import pytest

from evaluation import resume_groq as resume


def row(identifier, called=False, status='error', message='(AI_LIMIT_MINUTE)'):
    return dict(question_id=identifier, status=status,
                error=dict(type='RateLimitError', message=message),
                generation_trace=dict(llm_called=called))


def test_only_known_pre_call_minute_errors_selected():
    rows = [row('004'), row('001', True), row('007', status='no_hits'),
            row('005', message='(AI_RATE)'), row('006', called=None)]
    assert resume.target_ids({'results': rows}) == ['004']


def test_quota_waits_only_on_local_minute_limit():
    quota, sleep, emit = Mock(), Mock(), Mock()
    quota.reserve.side_effect = [resume.base.ai.RateLimitError('(AI_LIMIT_MINUTE)', 7), 'id']
    paced = resume.PacedQuota(quota, sleep=sleep, emit=emit)
    assert paced.reserve('settings', 'user', 100) == 'id'
    sleep.assert_called_once_with(7)
    emit.assert_called_once_with('waiting for evaluation quota 7')
    assert quota.reserve.call_count == 2
    quota.reserve.side_effect = resume.base.ai.RateLimitError('(AI_RATE)', 30)
    with pytest.raises(resume.base.ai.RateLimitError):
        paced.reserve('settings', 'user', 100)
    assert sleep.call_count == 1


def test_available_quota_does_not_wait():
    quota, sleep = Mock(), Mock()
    quota.reserve.return_value = 'id'
    assert resume.PacedQuota(quota, sleep=sleep).reserve(1, 2, 3) == 'id'
    sleep.assert_not_called()


def test_real_quota_uses_usage_timestamp_without_changing_limits(tmp_path, monkeypatch):
    import gc
    from mvp.settings import Settings
    clock = [1000.0]
    monkeypatch.setattr(resume.base.ai.time, 'time', lambda: clock[0])
    settings = Settings(llm_provider='groq_free')
    quota = resume.base.ai.Quota(tmp_path / 'usage.sqlite3')
    quota.reserve(settings, 'user', resume.base.ai.GROQ_MINUTE_TOKEN_BUDGET)
    clock[0] += 17
    delays = []

    def sleep(seconds):
        delays.append(seconds)
        clock[0] += seconds

    try:
        identifier = resume.PacedQuota(quota, sleep=sleep, emit=lambda _: None).reserve(settings, 'user', 1)
        assert identifier
        assert delays == [43]
        assert settings.user_daily_limit == 10
    finally:
        gc.collect()


def test_merge_preserves_original_and_never_repeats_existing_output(tmp_path):
    source = {'results': [row('001', True), row('004'), row('007', status='no_hits')]}
    original = json.dumps(source)
    output = tmp_path / 'completed.json'
    execute = Mock(return_value={'question_id': '004', 'status': 'abstained'})
    resume.execute_resume(source, output, execute)
    result = json.loads(output.read_text(encoding='utf-8'))
    execute.assert_called_once_with('004')
    assert result['original_results'] == source['results']
    assert [r['result_origin'] for r in result['results']] == ['original', 'resumed', 'original']
    assert json.dumps(source) == original
    with pytest.raises(FileExistsError):
        resume.execute_resume(source, output, execute)
    execute.assert_called_once()


def test_interrupted_attempt_cannot_be_automatically_replayed(tmp_path):
    output = tmp_path / 'completed.json'
    execute = Mock(side_effect=RuntimeError('interrupted'))
    with pytest.raises(RuntimeError):
        resume.execute_resume({'results': [row('004')]}, output, execute)
    assert json.loads(output.read_text())['in_progress_question_id'] == '004'
    with pytest.raises(FileExistsError):
        resume.execute_resume({'results': [row('004')]}, output, execute)
    execute.assert_called_once()

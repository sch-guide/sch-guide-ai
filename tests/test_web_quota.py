import gc
from unittest.mock import Mock
import pytest
from mvp.settings import Settings, GuideError
from mvp import web_quota


def test_local_creates_separate_db_and_keeps_limits(tmp_path,monkeypatch):
    monkeypatch.setattr(web_quota,'ROOT',tmp_path)
    production=tmp_path/'data/usage.sqlite3'
    production.parent.mkdir();production.write_bytes(b'untouched')
    settings=Settings(mode='local')
    q=web_quota.for_web(settings)
    assert q.path==tmp_path/'data/web_local_usage.sqlite3'
    assert q.path.exists()
    for _ in range(settings.user_daily_limit): q.reserve(settings,'test',1)
    with pytest.raises(GuideError,match='AI_LIMIT_CALLS'): q.reserve(settings,'test',1)
    assert production.read_bytes()==b'untouched'
    assert web_quota.for_web(settings).path==q.path
    gc.collect()


def test_staff_keeps_existing_default_without_touching_db(monkeypatch):
    constructor=Mock()
    monkeypatch.setattr(web_quota,'Quota',constructor)
    assert web_quota.for_web(Settings(mode='staff')) is None
    constructor.assert_not_called()

"""File settings tests; all keys are synthetic and API calls are replaced."""
import os
from unittest.mock import patch

import httpx

from mvp import settings
from mvp.test_gemini_connection import main


def configure_file(tmp_path, monkeypatch):
    (tmp_path / 'mvp').mkdir()
    (tmp_path / 'mvp' / '.env').write_text(
        'GEMINI_API_KEY=fake-file-key\nGUIDE_LLM_PROVIDER=gemini\n'
        'GUIDE_LLM_MODEL=gemini-3.1-flash-lite\nGUIDE_LLM_APPROVED=true\n',
        encoding='utf-8')
    monkeypatch.setattr(settings, 'ROOT', tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.chdir(other)


def test_file_key_loads_independently_of_cwd(tmp_path, monkeypatch):
    configure_file(tmp_path, monkeypatch)
    with patch.dict(os.environ, {}, clear=True):
        loaded = settings.load_settings(use_streamlit=False)
        assert loaded.llm_key == 'fake-file-key'
        assert loaded.llm_endpoint() == 'gemini'
        assert loaded.llm_model == 'gemini-3.1-flash-lite'


def test_process_environment_has_priority(tmp_path, monkeypatch):
    configure_file(tmp_path, monkeypatch)
    with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake-process-key'}, clear=True):
        assert settings.load_settings(use_streamlit=False).llm_key == 'fake-process-key'


def test_connection_test_loads_file_before_key_check(tmp_path, monkeypatch, capsys):
    configure_file(tmp_path, monkeypatch)
    response = httpx.Response(200, json={'choices': [
        {'finish_reason': 'stop', 'message': {'content': 'OK'}}]})
    with patch.dict(os.environ, {}, clear=True), patch(
        'mvp.test_gemini_connection.completion_response', return_value=response
    ):
        assert main() == 0
    assert capsys.readouterr().out == 'OK\n'

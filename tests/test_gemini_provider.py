"""Offline provider contract tests: no real API keys or network calls."""
import json
import os
import unittest
from unittest.mock import patch

from google.genai import errors, types

from mvp import ai
from mvp.gemini_provider import completion_response
from mvp.library import Chunk, Hit
from mvp.settings import GuideError, Settings, load_settings


class GeminiSettingsTests(unittest.TestCase):
    def test_environment_key_and_existing_guide_configuration(self):
        with patch.dict(os.environ, {'GUIDE_LLM_PROVIDER': 'gemini',
                                     'GUIDE_LLM_MODEL': 'gemini-3.1-flash-lite',
                                     'GUIDE_LLM_APPROVED': 'true',
                                     'GEMINI_API_KEY': 'test-only-key',
                                     'GUIDE_LLM_API_KEY': 'wrong-provider-key'}):
            settings = load_settings(use_streamlit=False)
        self.assertEqual(settings.llm_key, 'test-only-key')
        self.assertEqual(settings.llm_endpoint(), 'gemini')

    def test_missing_key_is_configuration_error(self):
        with self.assertRaisesRegex(GuideError, 'AI_SETUP'):
            Settings(llm_provider='gemini', llm_model='gemini-3.1-flash-lite',
                     llm_approved=True).llm_endpoint()

    def test_approval_is_still_required(self):
        with self.assertRaisesRegex(GuideError, 'AI_APPROVAL'):
            Settings(llm_provider='gemini', llm_model='gemini-3.1-flash-lite',
                     llm_key='test-only-key').llm_endpoint()


class GeminiAdapterTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(llm_provider='gemini', llm_model='gemini-3.1-flash-lite',
                                 llm_key='test-only-key', llm_approved=True)

    def response(self, text, reason='STOP'):
        return types.GenerateContentResponse(candidates=[types.Candidate(
            finish_reason=reason, content=types.Content(parts=[types.Part(text=text)]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=42))

    def test_sdk_preserves_prompt_config_and_maps_usage(self):
        with patch('google.genai.Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.models.generate_content.return_value = self.response('{"answerable":false,"statements":[]}')
            response = completion_response(self.settings,
                [{'role': 'system', 'content': ai.SYSTEM}, {'role': 'user', 'content': 'unchanged evidence'}],
                temperature=0, max_output_tokens=768)
            call = client.models.generate_content.call_args.kwargs
            self.assertEqual(call['config'].system_instruction, ai.SYSTEM)
            self.assertEqual(call['contents'][0].parts[0].text, 'unchanged evidence')
            self.assertEqual(call['config'].response_mime_type, 'application/json')
            self.assertEqual(call['config'].temperature, 0)
            self.assertEqual(call['config'].max_output_tokens, 768)
            self.assertEqual(call['model'], 'gemini-3.1-flash-lite')
            self.assertEqual(factory.call_args.kwargs['http_options'].retry_options.attempts, 1)
            self.assertEqual(response.json()['usage']['total_tokens'], 42)

    def test_truncated_response_is_not_accepted_as_complete(self):
        with patch('google.genai.Client') as factory:
            factory.return_value.__enter__.return_value.models.generate_content.return_value = self.response('{}', 'MAX_TOKENS')
            response = completion_response(self.settings, [], temperature=0, max_output_tokens=768)
        self.assertEqual(response.json()['choices'][0]['finish_reason'], 'incomplete')

    def test_api_error_does_not_expose_provider_body(self):
        with patch('google.genai.Client') as factory:
            factory.return_value.__enter__.return_value.models.generate_content.side_effect = errors.ClientError(
                429, {'error': {'message': 'SECRET_SENTINEL', 'code': 429}})
            response = completion_response(self.settings, [], temperature=0, max_output_tokens=768)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('SECRET_SENTINEL', response.text)

    def test_unexpected_exception_is_sanitized(self):
        with patch('google.genai.Client', side_effect=RuntimeError('SECRET_SENTINEL')):
            with self.assertRaises(GuideError) as caught:
                completion_response(self.settings, [], temperature=0, max_output_tokens=768)
        self.assertNotIn('SECRET_SENTINEL', str(caught.exception))

    def test_generate_reuses_existing_validation_and_quota(self):
        from unittest.mock import Mock
        chunk = Chunk('one', 'doc', 'synthetic.pdf', 1, '교육실', '', None,
                      '교육실 사용 전 예약 확인표를 확인합니다.', 0)
        raw = json.dumps({'answerable': True, 'statements': [{'text': chunk.text,
                         'evidence': [{'chunk_id': chunk.id, 'quote': chunk.text}]}]})
        quota = Mock()
        with patch('google.genai.Client') as factory, patch('mvp.ai.httpx.Client') as legacy:
            factory.return_value.__enter__.return_value.models.generate_content.return_value = self.response(raw)
            answer, hits = ai.generate(self.settings, '교육실 사용 전 무엇을 확인하나요?',
                                       [Hit(chunk, .9)], 'test', quota=quota)
        self.assertTrue(answer.answerable)
        self.assertEqual(answer.statements[0].text, chunk.text)
        quota.reserve.assert_called_once()
        quota.settle.assert_called_once_with(quota.reserve.return_value, {'total_tokens': 42})
        legacy.assert_not_called()

    def test_manual_test_skips_missing_key_without_calling_api(self):
        from mvp.test_gemini_connection import main
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}), \
             patch('mvp.test_gemini_connection.completion_response') as call, patch('builtins.print'):
            self.assertEqual(main(), 0)
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()

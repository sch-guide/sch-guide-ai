"""Offline orchestration checks; no Label Set retrieval or API calls."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evaluation import run_bm25_answer_baseline as runner


class AnswerBaselineTests(unittest.TestCase):
    def prepared(self, provider='gemini', model='gemini-3.1-flash-lite'):
        return (SimpleNamespace(llm_provider=provider, llm_model=model),
                [{'id': f'TRF-{i:03}'} for i in range(10, 0, -1)], object(),
                {'ready': True, 'blockers': []})

    def test_exact_question_order_and_model(self):
        with patch.object(runner.base, 'prepare', return_value=self.prepared()) as prepare:
            _, cases, _, meta = runner.prepare()
        self.assertEqual([c['id'] for c in cases], [f'TRF-{i:03}' for i in range(1, 11)])
        self.assertTrue(meta['ready'])
        self.assertEqual(meta['baseline_name'], 'BM25 + Gemini Answer Baseline v1')
        prepare.assert_called_once_with(output=runner.OUTPUT)

    def test_other_model_is_blocked_without_overriding_it(self):
        with patch.object(runner.base, 'prepare', return_value=self.prepared(model='other')):
            settings, _, _, meta = runner.prepare()
        self.assertFalse(meta['ready'])
        self.assertEqual(settings.llm_model, 'other')

    def test_other_provider_is_blocked(self):
        with patch.object(runner.base, 'prepare', return_value=self.prepared(provider='groq_free')):
            self.assertFalse(runner.prepare()[3]['ready'])

    def test_unexpected_id_is_rejected(self):
        prepared = self.prepared()
        prepared[1][0]['id'] = 'OTHER'
        with patch.object(runner.base, 'prepare', return_value=prepared):
            with self.assertRaises(ValueError):
                runner.prepare()

    def test_default_never_runs(self):
        with patch.object(runner, 'prepare', return_value=self.prepared()), \
             patch.object(runner.base, 'run_baseline') as run, patch('builtins.print'):
            self.assertEqual(runner.main([]), 0)
        run.assert_not_called()

    def test_blocked_run_never_runs(self):
        prepared = self.prepared()
        prepared[3].update(ready=False, blockers=['configuration'])
        with patch.object(runner, 'prepare', return_value=prepared), \
             patch.object(runner.base, 'run_baseline') as run, patch('builtins.print'):
            self.assertEqual(runner.main(['--run']), 2)
        run.assert_not_called()

    def test_explicit_run_uses_shared_runner_and_new_output(self):
        prepared = self.prepared()
        with patch.object(runner, 'prepare', return_value=prepared), \
             patch.object(runner.base, 'run_baseline', return_value=0) as run, patch('builtins.print'):
            self.assertEqual(runner.main(['--run']), 0)
        run.assert_called_once_with(*prepared, output=runner.OUTPUT)


if __name__ == '__main__':
    unittest.main()

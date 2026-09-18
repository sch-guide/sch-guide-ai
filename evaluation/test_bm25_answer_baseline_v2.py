"""v2 routing tests only: no search, model initialization, or API calls."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evaluation import run_bm25_answer_baseline_v2 as runner


class V2Tests(unittest.TestCase):
    def prepared(self):
        return (SimpleNamespace(llm_provider='gemini', llm_model='gemini-3.1-flash-lite'),
                [{'id': f'TRF-{i:03}'} for i in range(10, 0, -1)], object(),
                {'ready': True, 'blockers': []})

    def test_preparation_uses_new_output_and_branch(self):
        with patch.object(runner.base, 'prepare', return_value=self.prepared()) as prepare:
            _, cases, _, meta = runner.prepare()
        prepare.assert_called_once_with(output=runner.OUTPUT, expected_branch='lagom-bm25-context-fix')
        self.assertEqual(cases[0]['id'], 'TRF-001')
        self.assertEqual(meta['baseline_name'], 'BM25 + Gemini Answer Baseline v2')
        self.assertEqual(runner.OUTPUT.name, 'bm25_answer_baseline_v2.json')

    def test_default_does_not_execute(self):
        with patch.object(runner, 'prepare', return_value=self.prepared()), \
             patch.object(runner.base, 'run_baseline') as run, patch('builtins.print'):
            self.assertEqual(runner.main([]), 0)
        run.assert_not_called()

    def test_explicit_run_only_routes_to_v2(self):
        prepared = self.prepared()
        with patch.object(runner, 'prepare', return_value=prepared), \
             patch.object(runner.base, 'run_baseline', return_value=0) as run, patch('builtins.print'):
            self.assertEqual(runner.main(['--run']), 0)
        run.assert_called_once_with(*prepared, output=runner.OUTPUT)

    def test_wrong_model_is_blocked(self):
        prepared = self.prepared()
        prepared[0].llm_model = 'other'
        with patch.object(runner.base, 'prepare', return_value=prepared):
            self.assertFalse(runner.prepare()[3]['ready'])


if __name__ == '__main__':
    unittest.main()

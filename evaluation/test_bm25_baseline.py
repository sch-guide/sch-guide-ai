"""Offline safety tests. Never execute the real label questions or call an LLM."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from evaluation import run_bm25_baseline as baseline
from mvp.library import Chunk, Hit, LocalLibrary
from mvp.query import plan_query
from mvp.retrieval import search


def fixture():
    library = LocalLibrary()
    library.docs = [dict(id='target', document_name=baseline.TARGET),
                    dict(id='other', document_name='other.pdf')]
    library.chunks = [Chunk('one', 'target', baseline.TARGET, 1, '제목', '격리', None,
                            'CRE 격리 시 접촉주의를 적용하고 손 위생을 시행한다.', 0),
                      Chunk('two', 'other', 'other.pdf', 2, '제목', '', None,
                            '다른 문서의 본문입니다.', 0)]
    library.vectors = np.array([[1., 0.], [0., 1.]], dtype=np.float32)
    return library


class SafetyTests(unittest.TestCase):
    def test_scope_is_memory_only_and_preserves_vector_alignment(self):
        original = fixture()
        scoped = baseline.scope_library(original)
        self.assertEqual([c.id for c in scoped.chunks], ['one'])
        np.testing.assert_array_equal(scoped.vectors, original.vectors[:1])
        self.assertEqual(len(original.chunks), 2)
        self.assertEqual(len(original.docs), 2)

    def test_scoping_matches_original_service_search(self):
        original = fixture()
        scoped = baseline.scope_library(original)
        plan = plan_query('CRE 격리 주의사항', documents=scoped.docs)
        vector = np.array([1., 0.], dtype=np.float32)
        expected = search(original, plan.query, vector, ['target'], .38, plan=plan)
        actual = scoped.search(plan.query, vector, ['target'], .38, plan=plan)
        self.assertTrue(expected)
        self.assertEqual(actual, expected)

    def test_contexts_preserve_order_and_do_not_invent_scores(self):
        chunk = fixture().chunks[0]
        hits = [Hit(chunk, .8, bm25_score=2.4),
                Hit(replace(chunk, id='adjacent', page=None), .8,
                    bm25_score=0, context_only=True)]
        rows = baseline.context_rows(hits)
        self.assertEqual([r['rank'] for r in rows], [1, 2])
        self.assertEqual(rows[0]['bm25_score'], 2.4)
        self.assertIsNone(rows[1]['bm25_score'])
        self.assertIsNone(rows[1]['page'])

    def test_duplicate_target_is_rejected(self):
        library = fixture()
        library.docs.append(dict(id='duplicate', document_name=baseline.TARGET))
        with self.assertRaises(ValueError):
            baseline.scope_library(library)

    def test_labels_never_enter_generation_and_all_contexts_are_retained(self):
        scoped = baseline.scope_library(fixture())
        case = dict(id='SYNTHETIC-ONLY', category='test', question='CRE 격리 주의사항',
                    reference_answer='REFERENCE_SENTINEL', must_include=['MUST_SENTINEL'],
                    critical_error=['ERROR_SENTINEL'])
        model = Mock()
        model.count.return_value = 10
        model.encode.return_value = np.array([[1., 0.]], dtype=np.float32)
        quota = object()
        with patch.object(baseline.ai, 'generate', return_value=(
                baseline.ai.Answer(answerable=False, statements=[]), [])) as generate:
            row = baseline.run_case(case, SimpleNamespace(min_similarity=.38), scoped, model, quota)
        self.assertEqual(row['status'], 'abstained')
        self.assertTrue(row['retrieved_contexts'])
        self.assertEqual(row['reference_answer'], 'REFERENCE_SENTINEL')
        self.assertEqual(row['generation_selected_contexts'], [])
        self.assertEqual(generate.call_args.args[1], row['query_plan']['query'])
        self.assertIs(generate.call_args.kwargs['quota'], quota)
        self.assertNotIn('SENTINEL', repr(generate.call_args))
        self.assertNotIn('SENTINEL', repr(model.encode.call_args))
        self.assertEqual(len(row['retrieved_contexts']), len(generate.call_args.args[2]))

    def test_generation_failure_keeps_retrieved_context_and_null_answer(self):
        scoped = baseline.scope_library(fixture())
        case = dict(id='SYNTHETIC-ONLY', category='test', question='CRE 격리 주의사항',
                    reference_answer='NOT_AN_ANSWER', must_include=[], critical_error=[])
        model = Mock()
        model.count.return_value = 10
        model.encode.return_value = np.array([[1., 0.]], dtype=np.float32)
        with patch.object(baseline.ai, 'generate', side_effect=RuntimeError('sensitive provider text')):
            row = baseline.run_case(case, SimpleNamespace(min_similarity=.38), scoped, model, object())
        self.assertEqual(row['status'], 'error')
        self.assertIsNone(row['generated_answer'])
        self.assertTrue(row['retrieved_contexts'])
        self.assertNotIn('sensitive provider text', str(row))

    def test_default_command_never_executes_baseline(self):
        with patch.object(baseline, 'prepare', return_value=(None, [], None,
                          {'ready': False, 'blockers': ['disabled']})), \
             patch.object(baseline, 'run_baseline') as run, \
             patch('builtins.print'):
            self.assertEqual(baseline.main([]), 0)
        run.assert_not_called()

    def test_blocked_explicit_run_never_executes_baseline(self):
        with patch.object(baseline, 'prepare', return_value=(None, [], None,
                          {'ready': False, 'blockers': ['disabled']})), \
             patch.object(baseline, 'run_baseline') as run, \
             patch('builtins.print'):
            self.assertEqual(baseline.main(['--run']), 2)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

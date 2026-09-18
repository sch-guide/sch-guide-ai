import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from mvp.settings import Settings
from evaluation import evaluation_quota as q


class QuotaScopeTests(unittest.TestCase):
    def test_default_and_explicit_paths(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(q.resolve_path(), (q.RUNTIME / 'usage.sqlite3').resolve())
        with patch.dict('os.environ', {'GUIDE_EVALUATION_USAGE_DB': 'evaluation/.runtime/usage_v5_final.sqlite3'}):
            self.assertEqual(q.resolve_path().name, 'usage_v5_final.sqlite3')

    def test_existing_result_is_preserved_before_quota_or_model(self):
        from evaluation import run_bm25_baseline as base
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            path.write_text('preserved', encoding='utf-8')
            with patch.object(base, 'AREA', Path(folder)), patch.object(base.library, 'Embedder') as model, patch.object(base.ai, 'Quota') as quota:
                with self.assertRaises(ValueError):
                    base.run_baseline(Settings(), [], None, {'ready': True}, output=path)
                model.assert_not_called()
                quota.assert_not_called()
            self.assertEqual(path.read_text(encoding='utf-8'), 'preserved')

    def test_fresh_scope_is_read_only_and_ready(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(q, 'RUNTIME', Path(folder)):
            path = Path(folder) / 'fresh.sqlite3'
            result = q.preflight(Settings(), 10, path)
            self.assertTrue(result['ten_question_call_capacity'])
            self.assertEqual(result['calls'], 0)
            self.assertFalse(path.exists())

    def test_outside_runtime_rejected(self):
        with self.assertRaises(ValueError):
            q.resolve_path(q.ROOT / 'data' / 'usage.sqlite3')

    def test_usage_and_settings_preserved(self):
        from mvp.ai import Quota
        with tempfile.TemporaryDirectory() as folder, patch.object(q, 'RUNTIME', Path(folder)):
            path = Path(folder) / 'used.sqlite3'
            settings = Settings()
            quota = Quota(path)
            quota.reserve(settings, 'bm25-baseline-v1', 1)
            import gc
            gc.collect()  # Existing service Quota transaction contexts close on collection.
            before = path.read_bytes()
            result = q.preflight(settings, 10, path)
            self.assertEqual(result['user_calls'], 1)
            self.assertFalse(result['ten_question_call_capacity'])
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(settings.user_daily_limit, 10)


if __name__ == '__main__':
    unittest.main()

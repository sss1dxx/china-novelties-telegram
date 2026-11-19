import tempfile
import unittest
from parser import Store
from test_parser import product


class BackfillTests(unittest.TestCase):
    def test_restart_cap_uncertain_and_daily_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = folder + '/history.sqlite'
            store = Store(path)
            def item(n):
                return product(url=f'https://example.test/{n}', title=f'Robot {n}')
            first = store.reserve(item(1), [], '2026-11-19', 1, 2)
            store.finish(first, 'sent', [])
            second = store.reserve(item(2), [], '2026-11-19', 1, 2)
            store.finish(second, 'uncertain', [])
            store.db.close()
            store = Store(path)
            self.assertEqual(store.backfill_count(), 1)
            self.assertIsNone(store.reserve(item(3), [], '2026-11-20', 1, 2))
            self.assertTrue(store.duplicate(item(2)))
            self.assertIsNotNone(store.reserve(item(3), [], '2026-11-19', 1))
            self.assertIsNone(store.reserve(item(4), [], '2026-11-19', 1))
            store.db.close()

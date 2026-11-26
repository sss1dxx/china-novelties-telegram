"""A blocked source must produce a saved diagnostic and a failing run."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import parser


class SourceFailureTests(unittest.TestCase):
    def execute(self, sources, result, expected=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = dict(sources=sources, min_score=40, timezone='Europe/Moscow',
                          per_run=1, daily_limit=24, max_age_days=14)
            (root/'config.json').write_text(json.dumps(config))
            (root/'rights.json').write_text('{}')
            args = SimpleNamespace(config=str(root/'config.json'), rights=str(root/'rights.json'),
                                   db=str(root/'history.sqlite'), output=str(root/'preview'), send=False)
            with patch.dict(os.environ, {'GITHUB_ACTIONS':'false', 'GITHUB_STEP_SUMMARY':str(root/'summary')}), \
                 patch.object(parser, 'collect_urls', return_value=result), \
                 patch.object(parser, 'send') as send:
                if expected:
                    with self.assertRaisesRegex(RuntimeError, expected):
                        parser.run(args)
                else:
                    parser.run(args)
                send.assert_not_called()
            return json.loads((root/'preview/report.json').read_text()), (root/'summary').read_text()

    def test_blocked_source_fails_and_preserves_reason(self):
        report, summary = self.execute(
            [dict(name='kickstarter', enabled=True, terms_reviewed=True)],
            ([], [dict(page=1, reason='robots.txt HTTP 403; collection suspended')]),
            'All enabled sources unavailable')
        self.assertEqual(report['outcome'], 'source_unavailable')
        self.assertEqual(report['sent'], 0)
        self.assertIn('HTTP 403', summary)

    def test_valid_empty_catalog_is_not_blocked(self):
        report, _ = self.execute([dict(name='kickstarter', enabled=True, terms_reviewed=True)], ([], []))
        self.assertEqual(report['outcome'], 'no_eligible_products')

    def test_no_enabled_sources_fails(self):
        report, _ = self.execute([], ([], []), 'All enabled sources unavailable')
        self.assertEqual(report['outcome'], 'source_unavailable')

    def test_robots_403_stops_before_catalog(self):
        http = parser.HTTP()
        with patch.object(http, 'raw', side_effect=HTTPError('https://www.kickstarter.com/robots.txt',403,'Forbidden',{},None)) as raw:
            with self.assertRaisesRegex(ValueError, 'robots.txt HTTP 403'):
                http.get('https://www.kickstarter.com/discover/advanced')
            self.assertEqual(raw.call_count, 1)

if __name__ == '__main__':
    unittest.main()

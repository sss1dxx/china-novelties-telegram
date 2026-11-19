import unittest
from unittest.mock import patch
import parser as p
import setup_app as app

class MediaPolicyTests(unittest.TestCase):
    def test_direct_media_does_not_query_robots(self):
        class HTTP:
            def get(self,*args): raise AssertionError('page path used for media')
            def raw(self,url,limit):return url.encode(),'image/jpeg'
        with patch.object(p,'photo_bytes',side_effect=lambda b:(b,b.decode())):
            media,_=p.prepare_media({'images':['https://cdn.example/a.jpg','https://cdn.example/b.jpg']},{},HTTP())
            self.assertEqual(len(media),2)

    def test_pages_still_obey_robots(self):
        http=p.HTTP()
        with patch.object(http,'raw',return_value=(b'User-agent: *\nDisallow: /', 'text/plain')) as raw:
            with self.assertRaises(p.RobotsBlocked):http.get('https://example.com/products')
            self.assertEqual(raw.call_count,1)

    def test_update_does_not_require_telegram_credentials(self):
        app.validate({'update_only':True,'github_token':'test','repo':'china-novelties-telegram'})

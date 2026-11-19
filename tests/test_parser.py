import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch
import parser as p

URL = 'https://example.com/product/1'

def product(**kwargs):
    result = dict(url=URL, title='Умный робот', description='Робот для дома.', features=['Голосовое управление', 'Подключение по Wi-Fi'],
                  status='краудфандинг', language='ru', supporters=10, published='', price='', brand='', model='')
    result.update(kwargs)
    return result

class ParserTests(unittest.TestCase):
    def test_listing_rejects_navigation(self):
        links = p.discover('<a href="/item/123.html">x</a><a href="/user/1">no</a>', 'https://zhongchou.modian.com', r'^https://zhongchou\.modian\.com/item/\d+\.html$')
        self.assertEqual(links, ['https://zhongchou.modian.com/item/123.html'])

    def test_jsonld_uses_product_images_not_avatar(self):
        data = {'@type':'Product','name':'Smart robot','image':['https://img.example/a.jpg','https://img.example/b.jpg'],
                'offers':{'price':'99','priceCurrency':'USD','availability':'https://schema.org/InStock'}}
        result = p.product_from_html('<img src="avatar.jpg"><script type="application/ld+json">'+json.dumps(data)+'</script>', URL, {'name':'test'})
        self.assertEqual(len(result['images']), 2)
        self.assertEqual(result['price'], '99 USD')
        self.assertEqual(result['status'], 'доступен у поставщика')

    def test_modian_does_not_use_fundraising_as_price(self):
        page = '<h3>AI智能眼镜</h3><h3>已筹¥16,826</h3><li>此项目须在 2099-12-06 23:59 前达到 ¥1,000 的目标才可成功</li>'
        result = p.product_from_html(page, URL, {'name':'modian'})
        self.assertEqual(result['price'], '')
        self.assertEqual(result['status'], 'краудфандинг')

    def test_unknown_or_stale_supplier_novelty_rejected(self):
        self.assertEqual(p.score(product(title='Smart robot', status='доступен у поставщика')), 0)
        self.assertEqual(p.score(product(title='Smart robot', published='2001-01-01')), 0)
        self.assertEqual(p.score(product(title='Smart robot online course')), 0)
        self.assertGreater(p.score(product(title='Smart robot')), 40)

    def test_caption_limits_and_escaping(self):
        text = p.caption(product(title='Робот <новый>', description='А'*2000, features=['Голосовое управление','Подключение по Wi-Fi']))
        self.assertIn('&lt;новый&gt;', text)
        self.assertLess(len(p.html.unescape(p.re.sub('<[^>]+>', '', text)).encode('utf-16-le'))//2, 1025)

    def test_no_fabricated_features(self):
        with self.assertRaises(ValueError):
            p.caption(product(features=[]))

    def test_album_has_one_caption(self):
        method, fields = p.telegram_payload('@existing', 'hello', 3)
        self.assertEqual(method, 'sendMediaGroup')
        media = json.loads(fields['media'])
        self.assertEqual(sum('caption' in x for x in media), 1)
        self.assertEqual(len(media), 3)
        self.assertEqual(p.telegram_payload('@existing', 'hello', 1)[0], 'sendPhoto')

    def test_state_durable_dedupe_daily_cap_and_uncertain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = folder + '/history.sqlite'
            store = p.Store(path)
            a = product(brand='ACME', model='R1')
            ident = store.reserve(a, ['hash1'], '2026-11-19', 1)
            self.assertIsNotNone(ident)
            store.finish(ident, 'uncertain', {})
            store.db.close()
            store = p.Store(path)
            self.assertTrue(store.duplicate(product(url='https://other.test/p', brand='ACME', model='R1')))
            self.assertTrue(store.duplicate(product(title='Another robot'), ['hash1']))
            self.assertIsNone(store.reserve(product(url='https://other.test/2', title='Another robot'), [], '2026-11-19', 1))
            self.assertIsNotNone(store.reserve(product(url='https://other.test/2', title='Another robot'), [], '2026-11-20', 1))
            store.db.close()

    def test_canonical_retains_product_id(self):
        self.assertNotEqual(p.canonical('https://e.test/p?id=1'), p.canonical('https://e.test/p?id=2'))
        self.assertEqual(p.canonical('https://e.test/p?utm_source=x&id=1'), 'https://e.test/p?id=1')

    def test_no_photos_rejected(self):
        with self.assertRaises(ValueError):
            p.prepare_media(product(), {}, None)

    def test_single_photo_not_allowed_after_second_download_failure(self):
        class FakeHTTP:
            def raw(self, url, limit):
                if url == 'bad': raise OSError()
                return b'image', 'image/jpeg'
        with patch.object(p, 'photo_bytes', return_value=(b'jpeg', 'hash')):
            with self.assertRaises(ValueError):
                p.prepare_media(product(), dict(allowed=True, evidence='permission', checked_at='2026-11-19', image_urls=['good','bad'], only_one_exists=True), FakeHTTP())

    def test_automatic_gallery_without_permission_records(self):
        class FakeHTTP:
            def raw(self, url, limit): return url.encode(), 'image/jpeg'
        with patch.object(p, 'photo_bytes', side_effect=lambda b:(b,b.decode())):
            media, hashes = p.prepare_media(product(images=['front','side']), {}, FakeHTTP())
            self.assertEqual(media, [b'front', b'side'])
            single, _ = p.prepare_media(product(images=['sole']), {}, FakeHTTP())
            self.assertEqual(len(single),1)

    def test_actual_multipart_request_uses_uploads(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"ok":true,"result":[{"message_id":12},{"message_id":13}]}'
        with patch.object(p.request, 'urlopen', return_value=Response()) as call:
            result = p.send('TEST-TOKEN', '@existing', 'caption', [b'photo1', b'photo2'])
            self.assertEqual(result, [12,13])
            req = call.call_args.args[0]
            self.assertIn(b'attach://p1', req.data)
            self.assertIn(b'photo2', req.data)
            self.assertIn('sendMediaGroup', req.full_url)

if __name__ == '__main__': unittest.main()

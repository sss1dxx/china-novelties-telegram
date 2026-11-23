import datetime as dt
import html
import json
import time
import unittest
from parser import kickstarter_product, score, caption, collect_urls

URL='https://www.kickstarter.com/projects/test/laser'
class KickstarterTests(unittest.TestCase):
    def fixture(self, **changes):
        data=dict(name='LINEMETRIX: 3-in-1 Laser Measure', blurb='50m laser distance meter and cross-line laser guide.',
                  category={'slug':'technology/gadgets'}, urls={'web':{'project':URL}},
                  launched_at=int(time.time())-86400, deadline=int(time.time())+86400,
                  state='live', backers_count=3, photo={'full':'https://i.kickstarter.com/cover.jpg'})
        data.update(changes)
        return '<script>window.current_project = '+json.dumps(html.escape(json.dumps(data)))+';</script>'
    def product(self, **changes):
        return kickstarter_product(self.fixture(**changes),URL)
    def test_real_payload_and_caption(self):
        p=self.product(); self.assertGreater(score(p,14),40)
        text=caption(p);self.assertIn('LINEMETRIX',text);self.assertIn('Измеряет расстояние лазером',text)
        self.assertNotIn('Источник',text);self.assertTrue(text.endswith('@stepan_business'))
    def test_unknown_date_rejected(self):
        self.assertEqual(score(self.product(launched_at=None)),0)
    def test_old_date_rejected(self):
        self.assertEqual(score(self.product(launched_at=int(time.time())-15*86400)),0)
    def test_completed_campaign_rejected(self):
        self.assertEqual(score(self.product(state='successful')),0)
        self.assertEqual(score(self.product(deadline=int(time.time())-10)),0)
    def test_software_rejected(self):
        self.assertEqual(score(self.product(category={'slug':'technology/software'})),0)
    def test_newer_beats_popular(self):
        new=self.product(backers_count=0)
        old=self.product(launched_at=int(time.time())-4*86400,backers_count=999999)
        self.assertGreater(score(new),score(old))
    def test_gallery_excludes_avatar(self):
        s=self.fixture()+'<img src="https://i.kickstarter.com/avatar.jpg"><div class="description-container"><img data-src="https://i.kickstarter.com/product.jpg"></div>'
        p=kickstarter_product(s,URL);self.assertEqual(len(p['images']),2);self.assertNotIn('https://i.kickstarter.com/avatar.jpg',p['images'])
    def test_identity_required(self):
        with self.assertRaises(ValueError):kickstarter_product(self.fixture(),URL+'other')
    def test_json_pagination(self):
        class HTTP:
            def get(self,url):
                self.urls.append(url)
                return json.dumps({'projects':[{'urls':{'web':{'project':URL}}}]}).encode(),'application/json'
        http=HTTP();http.urls=[]
        urls,errors=collect_urls(http,dict(name='kickstarter',listing='https://www.kickstarter.com/discover/advanced?sort=newest',product_pattern=r'https://www\.kickstarter\.com/projects/[^/]+/[^/]+',max_pages=3))
        self.assertEqual(urls,[URL]);self.assertEqual(errors,[]);self.assertIn('page=2',http.urls[1]);self.assertIn('format=json',http.urls[0])
    def test_negated_fact_not_published(self):
        p=self.product(blurb='No laser distance meter. Without cross-line laser guide.')
        with self.assertRaises(ValueError):caption(p)

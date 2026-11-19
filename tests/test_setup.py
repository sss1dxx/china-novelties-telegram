import json
import unittest
import uuid
from unittest.mock import patch
import setup_app as app
import parser as p

class SetupTests(unittest.TestCase):
    def values(self):
        return dict(github_token='fake',bot_token='fake',channel='@example_channel',repo='news',public_ok=True,terms_ok=True)
    def test_valid_inputs(self): app.validate(self.values())
    def test_setup_needs_no_product_links_or_permissions(self):
        values=self.values()
        app.validate(values)
        self.assertNotIn('name="approved_urls"', app.PAGE)
        self.assertNotIn('name="evidence"', app.PAGE)
    def test_rejects_invalid_repository(self):
        values=self.values();values['repo']='../other'
        with self.assertRaises(app.SetupError):app.validate(values)
    def test_upload_never_contains_credentials(self):
        values=self.values();values['bot_token']=uuid.uuid4().hex;values['github_token']=uuid.uuid4().hex
        files=app.project_files(values)
        self.assertFalse(any(values['bot_token'] in content or values['github_token'] in content for content in files.values()))
        self.assertTrue(json.loads(files['config.json'])['sources'][0]['terms_reviewed'])
        self.assertEqual(json.loads(files['rights.json']), {})
    def test_bot_without_post_rights_rejected(self):
        responses=[{'ok':True,'result':{'id':1}}, {'ok':True,'result':{'id':-100123,'type':'channel'}}, {'ok':True,'result':{'status':'administrator','can_post_messages':False}}]
        with patch.object(app,'api',side_effect=responses):
            with self.assertRaises(app.SetupError):app.telegram_check('fake','@example_channel')
    def test_modian_real_observed_selectors(self):
        snippet='''<h3 class="title">AI智能眼镜｜800W拍摄+语音助手+实时翻译，超轻</h3>
        <h3 start_time="2026-11-14 19:00:00" end_time="2099-12-06 23:59:00"></h3>
        <span backer_count="123">55</span><li>此项目须在 2099-12-06 23:59 前达到目标</li>
        <img id="big_logo" src="http://p6.moimg.net/path/dst_project/main.jpg">
        <div class="back-detail"><p>F13 AI 眼镜｜800W 拍摄 + 通话 + 翻译，42g 超轻</p></div>
        <div class="back-img clearfix"><img data-original="https://p6.moimg.net/path/dst_project/side.jpg"></div>
        <img src="https://p6.moimg.net/path/dst_avatar/avatar.jpg">
        <script id="projectContentTemplate" type="text/template"><img src="https://p6.moimg.net/path/dst_project/detail.jpg"></script>'''
        result=p.product_from_html(snippet,'https://zhongchou.modian.com/item/123.html',{'name':'modian','language':'zh'})
        self.assertEqual(len(result['images']),3)
        self.assertTrue(all('dst_project' in u for u in result['images']))
        self.assertEqual(len(result['features']),3)
        self.assertTrue(result['description'])
        self.assertEqual(result['supporters'],55)
        self.assertEqual(result['published'],'2026-11-14 19:00:00')

import unittest
from unittest.mock import patch
import parser as p
from editorial import editorial_copy

class EditorialTests(unittest.TestCase):
    def glasses(self):
        return dict(title='AI智能眼镜｜800W拍摄+语音助手+实时翻译，超轻',description='F13 AI 眼镜｜800W 拍摄 + 通话 + 翻译，42g 超轻',
            features=['800W拍摄','语音助手','实时翻译'], language='zh',status='краудфандинг',url='https://example.com/p',price='')
    def test_reported_glasses_regression(self):
        with patch.object(p,'translate',side_effect=AssertionError('Known facts must not be machine translated')):
            text=p.caption(self.glasses())
        self.assertIn('F13 — умные очки с искусственным интеллектом',text)
        self.assertIn('камерой для съёмки',text)
        self.assertIn('перевод в реальном времени',text)
        self.assertNotIn('800',text)
        self.assertNotIn('F13 AI',text)
        self.assertNotIn('Источник',text)
        self.assertNotIn('https://',text)
        self.assertNotIn('🔥',text)
        self.assertNotIn('📦',text)
        self.assertTrue(text.endswith('Расчет поставки - @stepan_business'))
        self.assertEqual(text.count('🚀'),1)
    def test_no_invented_camera(self):
        product=self.glasses();product.update(title='AI智能眼镜｜语音助手+实时翻译',features=['语音助手','实时翻译'])
        title,body=editorial_copy(product,lambda *_:'unused')
        self.assertNotIn('камер',body)
    def test_mixed_translation_is_rejected(self):
        product=dict(title='Unknown device',features=['foo','bar'],language='en')
        with self.assertRaises(ValueError):editorial_copy(product,lambda *_:'Девайс Super Light Shoot')
    def test_negated_camera_not_claimed(self):
        product=self.glasses();product.update(title='AI智能眼镜｜无摄像头；语音助手；实时翻译',features=[])
        _,body=editorial_copy(product,lambda *_:'unused')
        self.assertNotIn('камер',body)

    def test_original_product_name_preserved(self):
        product=self.glasses();product['title']='MILLY POCKET AI机器人｜语音助手+实时翻译'
        title,_=editorial_copy(product,lambda *_:'unused')
        self.assertTrue(title.startswith('MILLY POCKET — '))
        self.assertNotIn('800W',title)

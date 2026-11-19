"""Conservative Russian copy from explicit product facts; no paid text API."""
import re

CONTACT = 'Расчет поставки - @stepan_business'

# Patterns describe stated capabilities, not assumptions based on product category.
FACTS = [
    (r'墨屏|电子墨水|电子纸', 'Экран на электронных чернилах.'),
    (r'OLED屏幕', 'Есть OLED-экран.'),
    (r'按键及旋钮|按键和旋钮', 'Управление кнопками и поворотной ручкой.'),
    (r'远程互动', 'Поддерживает дистанционное взаимодействие.'),
    (r'录音', 'Позволяет записывать звук.'),
    (r'语音转文字|录音转文字', 'Преобразует речь в текст.'),
    (r'心率监测|监测心率', 'Отслеживает частоту пульса.'),
    (r'睡眠监测|监测睡眠', 'Помогает отслеживать сон.'),

    (r'(?<!不支持)(?<!无)实时翻译', 'Поддерживает перевод в реальном времени.'),
    (r'(?<!无)(?<!不支持)语音助手', 'Есть голосовой помощник.'),
    (r'(?<!不支持)(?<!无法)(?<!无)(?:拍摄|摄像头)', 'Оснащены камерой для съёмки.'),
    (r'(?<!不支持)(?<!无法)通话', 'Поддерживают телефонные звонки.'),
    (r'(?<!不支持)(?<!无)语音控制', 'Поддерживает голосовое управление.'),
    (r'(?<!不支持)(?<!无)指纹识别', 'Предусмотрено распознавание отпечатка пальца.'),
    (r'(?<!不支持)(?<!无)无线充电', 'Поддерживает беспроводную зарядку.'),
    (r'(?<!不支持)(?<!无)远程控制', 'Доступно дистанционное управление.'),
    (r'(?<!不支持)(?<!无)自动清洁', 'Предусмотрена автоматическая очистка.'),
]
CATEGORIES = [
    (r'AI Agent桌面控制台', 'Настольная консоль для ИИ-агентов', 'console'),
    (r'远程互动.*小车|小车.*远程互动', 'Машинка для дистанционного взаимодействия', 'car'),
    (r'指纹授权', 'Сканер отпечатка пальца', 'fingerprint'),

    (r'(?:AI|智能).*眼镜|眼镜.*(?:AI|智能)', 'Умные очки', 'glasses'),
    (r'机器人', 'Робот', 'robot'),
    (r'智能.*戒指|智能.*指环', 'Умное кольцо', 'ring'),
    (r'录音笔', 'Карманный диктофон', 'recorder'),
    (r'投影仪', 'Проектор', 'projector'),
    (r'时钟', 'Электронные часы', 'clock'),
    (r'动捕手套', 'Перчатки для захвата движений', 'gloves'),
]


def normalized(text):
    return re.sub(r'[^а-яa-z0-9]', '', text.lower().replace('ё','е'))


def readable_russian(text):
    """Reject mixed-language fragments and known mistranslations, don't invent repairs."""
    if re.search(r'[\u4e00-\u9fff]|выстрел|super\s*light|\bshoot\b', text, re.I):
        return False
    without_terms = re.sub(r'\b(?:Wi-Fi|Bluetooth|USB(?:-[AC])?|HDMI|GPS|NFC|LED|OLED|LCD|4K|Full HD)\b', '', text, flags=re.I)
    return bool(re.search('[А-Яа-яЁё]', text)) and not re.search('[A-Za-z]', without_terms)


def safe_translate(text, language, translator):
    # Ambiguous Chinese W denotes pixels in some camera listings: never translate it as watts.
    if re.search(r'\d+\s*[wWＷ]\s*(?:拍摄|摄像|像素)', text):
        text = re.sub(r'\d+\s*[wWＷ]\s*(?:拍摄|摄像头?|像素)', 'камера для съёмки', text)
        if readable_russian(text):
            return text
        # Mixed Russian and Chinese is unsuitable input for the offline translator.
        raise ValueError('Ambiguous camera specification; no reliable Russian fact')
    result = translator(text, language)
    result = re.sub(r'\bAI\b', 'ИИ', result)
    if not readable_russian(result):
        raise ValueError('Mixed-language or unreliable translation rejected')
    return result.strip(' .;:')


def product_name(product):
    """Keep explicit brands/models; never treat camera specs or units as names."""
    explicit = ' '.join(str(product.get(k) or '').strip() for k in ('brand', 'model')).strip()
    if explicit:
        return explicit[:70]
    if product['title'].startswith('AI Console'):
        return 'AI Console'
    prefix = re.match(r'[A-Za-z0-9][A-Za-z0-9 ._-]*', product['title'])
    if prefix:
        words = prefix[0].strip().split()
        generic = {'ai','smart','glass','glasses','robot','camera','super','light'}
        words = [w for w in words if w.lower() not in generic and not re.fullmatch(r'\d+(?:w|g|hz|mah|v|mp|k)',w,re.I)]
        if words:
            return ' '.join(words)[:70]
    # Some listings put the model at the start of the product reward description.
    model = re.match(r'^\s*([A-Za-z]{1,8}[-]?\d{1,4}[A-Za-z]?)\b',product.get('description',''))
    return model[1] if model else ''


def editorial_copy(product, translator):
    source_title = product['title']
    # Ignore reward/shipping copy: it often describes an accessory rather than the product.
    evidence = [source_title] + list(product.get('features', []))
    category = next(((title, kind) for pattern, title, kind in CATEGORIES if re.search(pattern, source_title, re.I)), None)
    if category:
        title, kind = category
        if kind != 'console' and re.search(r'(?<![A-Za-z])AI(?![A-Za-z])|人工智能', source_title, re.I):
            title += ' с искусственным интеллектом'
    else:
        kind = ''
        if product['language'] != 'ru':
            raise ValueError('Unrecognized category; editorial mapping required')
        base = re.split(r'[｜|+＋]', source_title, maxsplit=1)[0].strip()
        title = safe_translate(base, product['language'], translator)
        if len(title) > 110:
            raise ValueError('Product name too verbose')
    name = product_name(product)
    if name and name.casefold() not in title.casefold():
        title = name + ' — ' + title[0].lower() + title[1:]
    facts = []
    for pattern, sentence in FACTS:
        # Avoid claiming a capability from a negative statement or comparison.
        matched = any(re.search(pattern, fragment) and not re.search(r'不支持|无法|不具备|没有|不含|无需|无摄像|无语音|无通话|不带|竞品|相比', fragment)
                      for field in evidence for fragment in re.split(r'[。；;\n]', field))
        if matched:
            if kind != 'glasses':
                sentence = sentence.replace('Оснащены камерой', 'Есть камера').replace('Поддерживают телефонные', 'Поддерживает телефонные')
            facts.append(sentence)
    if len(facts) < 2 and product['language'] == 'ru':
        for feature in product.get('features', []):
            if re.search(r'不支持|无法|不具备|没有|не поддерж|без камеры', feature, re.I):
                continue
            try:
                translated = safe_translate(feature, product['language'], translator)
            except ValueError:
                continue
            if not 5 <= len(translated) <= 180:
                continue
            sentence = translated.rstrip('.') + '.'
            if normalized(sentence) != normalized(title) and normalized(sentence) not in [normalized(x) for x in facts]:
                facts.append(sentence)
    if kind == 'gloves':
        resolution = re.search(r'分辨率\s*(\d+(?:\.\d+)?)°', source_title)
        frequency = re.search(r'传输频率\s*(\d+)\s*hz', source_title, re.I)
        if resolution:
            facts.append('Заявленное угловое разрешение — ' + resolution[1].replace('.', ',') + '°.')
        if frequency:
            facts.append('Частота передачи данных — ' + frequency[1] + ' Гц.')
    if kind == 'fingerprint' and 'Mac mini' in source_title and '指纹授权' in source_title:
        facts.append('Предназначен для авторизации по отпечатку пальца на Mac mini.')
    facts = list(dict.fromkeys(facts))
    if kind == 'glasses':
        facts = [x.replace('Поддерживает ', 'Поддерживают ') for x in facts]
        facts.sort(key=lambda x: 0 if 'камер' in x else 1)
    facts = facts[:3]
    if not facts:
        raise ValueError('Not enough reliable facts for a Russian post')
    # A single prose paragraph; no repeated raw description or feature header.
    return title, ' '.join(facts)

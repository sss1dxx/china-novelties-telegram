"""Conservative standalone collector: no LLM, paid API or ChatGPT dependency."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import io
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import time
import unicodedata
from urllib import request, parse, error, robotparser
from zoneinfo import ZoneInfo
from network import https_handler, tls_context
from editorial import editorial_copy, CONTACT

LOG = logging.getLogger('collector')
UA = 'ChinaNoveltiesBot/1.0'
RELEVANT = re.compile(r'robot|smart|gadget|sensor|wearable|projector|earbuds|camera|智能|机器人|眼镜|传感|投影|录音|动捕|时钟|小车|指纹|翻译|充电|\bAI\b', re.I)
EXCLUDE = re.compile(r'NFT|课程|电子书|小程序|软件订阅|博彩|成人用品|枪支|e-?book|online course|software subscription', re.I)


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def canonical(url):
    p = parse.urlsplit(url)
    # Product identifiers in query strings must be retained.
    query = [(k, v) for k, v in parse.parse_qsl(p.query) if not k.lower().startswith('utm_') and k not in ('spm', 'ref')]
    return parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, parse.urlencode(sorted(query)), ''))


def public_url(url):
    p = parse.urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Only public HTTPS URLs accepted')
    for addr in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(addr[4][0]).is_global:
            raise ValueError('Non-public network address')
    return url


class Redirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RobotsBlocked(ValueError):
    pass


class HTTP:
    def __init__(self):
        self.opener = request.build_opener(Redirect(), https_handler())
        self.robots = {}
        self.last = {}

    def raw(self, url, limit=5_000_000):
        public_url(url)
        host = parse.urlsplit(url).netloc
        time.sleep(max(0, 2 - (time.monotonic() - self.last.get(host, 0))))
        self.last[host] = time.monotonic()
        with self.opener.open(request.Request(url, headers={'User-Agent': UA}), timeout=25) as r:
            data = r.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Response exceeds limit')
            return data, r.headers.get('Content-Type', '')

    def get(self, url, limit=5_000_000):
        p = parse.urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            rules = robotparser.RobotFileParser()
            try:
                body, _ = self.raw(origin + '/robots.txt', 500_000)
                rules.parse(body.decode('utf-8', 'replace').splitlines())
            except error.HTTPError as e:
                if e.code == 404:
                    rules.parse([])
                else:
                    raise ValueError('robots.txt unavailable; collection suspended') from None
            self.robots[origin] = rules
        rules = self.robots[origin]
        if not rules.can_fetch(UA, url):
            raise RobotsBlocked('robots.txt forbids automated access: ' + p.hostname)
        delay = rules.crawl_delay(UA) or rules.crawl_delay('*') or 2
        if delay > 60:
            raise ValueError('Crawl delay exceeds run budget')
        time.sleep(max(0, delay - (time.monotonic() - self.last.get(p.netloc, 0))))
        return self.raw(url, limit)


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

    def text(self):
        return ' '.join(c.text() if isinstance(c, Node) else c for c in self.children).strip()


class DOM(HTMLParser):
    VOID = {'img', 'meta', 'link', 'input', 'br', 'hr', 'source', 'area', 'embed', 'wbr', 'col', 'base', 'param'}
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.current)
        self.current.children.append(n)
        if tag not in self.VOID:
            self.current = n

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        n = self.current
        while n.parent is not None:
            if n.tag == tag:
                self.current = n.parent
                break
            n = n.parent

    def handle_data(self, data):
        self.current.children.append(data)


def clean(text):
    return re.sub(r'\s+', ' ', html.unescape(str(text))).strip()


def json_objects(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from json_objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from json_objects(v)


def product_from_html(content, url, source):
    nodes = list(DOM(content).root.walk())
    meta = {n.attrs.get('property', n.attrs.get('name')): n.attrs.get('content', '') for n in nodes if n.tag == 'meta'}
    objects = []
    for n in nodes:
        if n.tag == 'script' and n.attrs.get('type') == 'application/ld+json':
            try:
                objects.extend(json_objects(json.loads(n.text())))
            except (ValueError, TypeError):
                pass
    data = next((o for o in objects if 'Product' in ([o.get('@type')] if isinstance(o.get('@type'), str) else o.get('@type', []))), {})
    title = data.get('name') or meta.get('og:title')
    if not title and source['name'] == 'modian':
        title = next((n.text() for n in nodes if n.tag in ('h1', 'h3') and n.text()), '')
    if not title:
        return None
    title = re.split(r'\s+-\s+摩点', str(title))[0]
    description = clean(data.get('description') or meta.get('og:description') or meta.get('description', ''))
    # Only semantic product images. Never harvest every image (avatars, banners, recommendations).
    images = data.get('image', [])
    images = images if isinstance(images, list) else [images]
    images = [x.get('url', x.get('contentUrl', '')) if isinstance(x, dict) else x for x in images]
    if meta.get('og:image'):
        images.insert(0, meta['og:image'])
    features = data.get('additionalProperty', [])
    features = features if isinstance(features, list) else [features]
    features = [clean(f"{x.get('name', '')}: {x.get('value', '')}") for x in features if isinstance(x, dict) and x.get('value')]
    offers = data.get('offers', {})
    offers = offers[0] if isinstance(offers, list) and offers else offers
    price = ''
    if isinstance(offers, dict) and offers.get('price') and offers.get('priceCurrency'):
        price = f"{offers['price']} {offers['priceCurrency']}"
    modian_date = ''
    modian_supporters = 0
    if source['name'] == 'modian':
        # Selectors observed on the real product page, 2026-11-19.
        for n in nodes:
            if n.attrs.get('start_time'):
                modian_date = n.attrs['start_time']
            if n.attrs.get('backer_count') and n.text().isdigit():
                modian_supporters = int(n.text())
            if n.tag == 'meta' and n.attrs.get('itemprop') == 'image':
                images.append(n.attrs.get('content', ''))
        details = [n for n in nodes if 'back-detail' in n.attrs.get('class', '').split() and RELEVANT.search(n.text())]
        if not description and details:
            description = clean(details[0].text())
        if not features:
            # Extract explicit enumerated claims, never generate product attributes.
            enumerated = re.split(r'[｜|]', title, maxsplit=1)
            if len(enumerated) == 2:
                features = [clean(x) for x in re.split(r'[+＋，,；;]', enumerated[1]) if len(clean(x)) > 1][:3]
        for n in nodes:
            if n.tag != 'img':
                continue
            ancestor = n.parent
            in_product = n.attrs.get('id') == 'big_logo'
            while ancestor:
                if ancestor.attrs.get('id') == 'projectDetail' or 'back-img' in ancestor.attrs.get('class', '').split():
                    in_product = True
                    break
                ancestor = ancestor.parent
            if in_product:
                image_url = n.attrs.get('data-original') or n.attrs.get('data-src') or n.attrs.get('src', '')
                if '/dst_project/' in image_url:
                    images.append(image_url.replace('http://p6.moimg.net/', 'https://p6.moimg.net/'))
        # Server-rendered project content can live in a text/template script.
        for template in nodes:
            if template.attrs.get('id') == 'projectContentTemplate':
                for n in DOM(template.text()).root.walk():
                    if n.tag == 'img' and '/dst_project/' in n.attrs.get('src', ''):
                        images.append(n.attrs['src'])
    text = '\n'.join(n.text() for n in nodes if n.tag in ('p', 'li', 'h3'))
    deadline = re.search(r'此项目须在\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', text)
    status = ''
    if source['name'] == 'modian' and deadline:
        end = dt.datetime.strptime(deadline[1], '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        if end > dt.datetime.now(dt.timezone.utc):
            status = 'краудфандинг'
    if source['name'] != 'modian' and isinstance(offers, dict) and str(offers.get('availability', '')).endswith('InStock'):
        status = 'доступен у поставщика'
    supporters = re.search(r'(\d+)\s*人\s*支持人数', text)
    videos = [parse.urljoin(url, n.attrs.get('src', '')) for n in nodes if n.tag in ('video', 'source') and '.mp4' in n.attrs.get('src', '')]
    return dict(url=canonical(url), source=source['name'], language=source.get('language', 'en'),
                title=clean(title), description=description, features=features[:3],
                images=list(dict.fromkeys(parse.urljoin(url, x) for x in images if isinstance(x, str) and x)),
                videos=videos, price=price, status=status, published=data.get('releaseDate') or meta.get('article:published_time', '') or modian_date,
                supporters=int(supporters[1]) if supporters else modian_supporters,
                brand=clean(data.get('brand', {}).get('name', '') if isinstance(data.get('brand'), dict) else data.get('brand', '')),
                model=clean(data.get('model') or data.get('mpn') or ''), discovered=utcnow())


def discover(content, base, pattern):
    return list(dict.fromkeys(canonical(parse.urljoin(base, n.attrs['href'])) for n in DOM(content).root.walk()
                             if n.tag == 'a' and n.attrs.get('href') and re.fullmatch(pattern, canonical(parse.urljoin(base, n.attrs['href'])))))


def score(p, max_age=45):
    if not RELEVANT.search(p['title']) or EXCLUDE.search(p['title']):
        return 0
    if not p.get('status'):
        return 0
    points = 25 + (20 if p['status'] in ('краудфандинг', 'до запуска') else 0)
    if p.get('published'):
        try:
            date = dt.datetime.fromisoformat(p['published'].replace('Z', '+00:00')).date()
            age = (dt.datetime.now(dt.timezone.utc).date() - date).days
            if age < -1 or age > max_age:
                return 0
            points += 20
        except ValueError:
            return 0
    elif p['status'] == 'доступен у поставщика':
        return 0  # First seen is not evidence of a new product.
    points += min(15, int(math.log1p(p.get('supporters', 0)) * 3))
    return points


def fingerprint(p):
    def norm(s):
        return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', s).lower())
    if p.get('brand') and p.get('model'):
        return 'model:' + norm(p['brand'] + ':' + p['model'])
    return 'title:' + norm(p['title'])


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS products(url TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, score INTEGER, seen TEXT);
        CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, url TEXT UNIQUE, fingerprint TEXT, day TEXT, status TEXT, response TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS media(hash TEXT PRIMARY KEY, delivery INTEGER);
        CREATE TABLE IF NOT EXISTS errors(at TEXT, source TEXT, kind TEXT);
        CREATE TABLE IF NOT EXISTS backfill(delivery INTEGER PRIMARY KEY);
        ''')
        self.db.commit()

    def put(self, p, points):
        self.db.execute('INSERT INTO products VALUES(?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET payload=excluded.payload,score=excluded.score',
                        (p['url'], fingerprint(p), json.dumps(p, ensure_ascii=False), points, utcnow()))
        self.db.commit()

    def duplicate(self, p, hashes=()):
        if self.db.execute('SELECT 1 FROM deliveries WHERE url=? OR fingerprint=?', (p['url'], fingerprint(p))).fetchone():
            return True
        return any(self.db.execute('SELECT 1 FROM media WHERE hash=?', (h,)).fetchone() for h in hashes)

    def backfill_count(self):
        return self.db.execute("SELECT COUNT(*) FROM backfill JOIN deliveries ON delivery=id WHERE status='sent'").fetchone()[0]

    def reserve(self, p, hashes, day, limit, backfill_target=0):
        with self.db:
            if self.duplicate(p, hashes):
                return None
            filling = backfill_target > 0
            if filling and self.db.execute("SELECT COUNT(*) FROM backfill JOIN deliveries ON delivery=id WHERE status IN ('sent','pending','uncertain')").fetchone()[0] >= backfill_target:
                return None
            if not filling and self.db.execute('SELECT COUNT(*) FROM deliveries WHERE day=? AND id NOT IN (SELECT delivery FROM backfill)', (day,)).fetchone()[0] >= limit:
                return None
            cur = self.db.execute('INSERT INTO deliveries(url,fingerprint,day,status,created) VALUES(?,?,?,?,?)',
                                  (p['url'], fingerprint(p), day, 'pending', utcnow()))
            if filling:
                self.db.execute('INSERT INTO backfill VALUES(?)', (cur.lastrowid,))
            for h in hashes:
                self.db.execute('INSERT OR IGNORE INTO media VALUES(?,?)', (h, cur.lastrowid))
            return cur.lastrowid

    def finish(self, ident, status, response):
        with self.db:
            self.db.execute('UPDATE deliveries SET status=?,response=? WHERE id=?', (status, json.dumps(response), ident))

    def failure(self, source, exc):
        # Exception text may contain tokens or signed URLs: store type only.
        with self.db:
            self.db.execute('INSERT INTO errors VALUES(?,?,?)', (utcnow(), source, type(exc).__name__))
        LOG.warning('%s: %s', source, type(exc).__name__)


def translate(text, language):
    if not text:
        return ''
    if language == 'ru':
        return text
    from argostranslate import translate as argos
    result = argos.translate(text[:1800], language, 'ru')
    if not result or not re.search('[А-Яа-яЁё]', result) or re.search('[\u4e00-\u9fff]', result):
        raise ValueError('Russian translation missing or incomplete')
    return clean(result)


def clip(text, units):
    result = ''
    for c in text:
        if len((result + c).encode('utf-16-le')) // 2 > units:
            break
        result += c
    return result


def caption(p):
    title, body = editorial_copy(p, translate)
    text = f'🚀 <b>{html.escape(title)}</b>\n\n{html.escape(body)}'
    text += f'\n\nСтатус: {html.escape(p["status"])}'
    if p.get('price'):
        text += '\nЦена: ' + html.escape(clip(p['price'], 65))
    text += '\n\n' + CONTACT
    plain = re.sub('<[^>]+>', '', text)
    if len(html.unescape(plain).encode('utf-16-le')) // 2 > 1024:
        raise ValueError('Caption exceeds Telegram limit')
    return text


def photo_bytes(body):
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(body)) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
        if min(w, h) < 320 or max(w, h) / min(w, h) > 5:
            raise ValueError('Image too small or a long infographic')
        im.thumbnail((2400, 2400))
        rgb = im.convert('RGB')
        # Exact normalized pixel hash catches identical images served from different URLs.
        digest = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
        out = io.BytesIO()
        rgb.save(out, 'JPEG', quality=88)
        return out.getvalue(), digest


def prepare_media(p, license_info, http):
    # Optional per-product media overrides; otherwise use the extracted product gallery.
    # This checks technical availability, not ownership or licensing.
    approved = list(dict.fromkeys(license_info.get('image_urls') or p.get('images', [])))
    if not approved:
        raise ValueError('No product photos found')
    media, hashes = [], []
    failed = 0
    failures = []
    for url in approved[:12]:
        try:
            # Known product-image URL: keep HTTPS, size limits and pacing,
            # but do not apply page-crawling robots rules to this direct download.
            body, mime = http.raw(url, 10_000_000)
            if not mime.lower().startswith('image/'):
                raise ValueError('Not an image response')
            data, digest = photo_bytes(body)
            if digest not in hashes:
                media.append(data)
                hashes.append(digest)
            if len(media) == 5:
                break
        except Exception as exc:
            failed += 1
            reason = str(exc) if isinstance(exc, RobotsBlocked) else type(exc).__name__
            failures.append(reason)
    if not media or (len(media) == 1 and (failed or len(approved) > 1)):
        raise ValueError('Insufficient usable product photos; ' + '; '.join(dict.fromkeys(failures or ['Gallery contains fewer than two usable images'])))
    return media, hashes


def telegram_payload(chat, text, count):
    if count == 1:
        return 'sendPhoto', {'chat_id': chat, 'caption': text, 'parse_mode': 'HTML', 'photo': 'attach://p0'}
    return 'sendMediaGroup', {'chat_id': chat, 'media': json.dumps([
        dict(type='photo', media=f'attach://p{i}', **({'caption': text, 'parse_mode': 'HTML'} if i == 0 else {})) for i in range(count)])}


def send(token, chat, text, media):
    method, fields = telegram_payload(chat, text, len(media))
    boundary = '----' + os.urandom(20).hex()
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for i, blob in enumerate(media):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="p{i}"; filename="photo{i}.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + blob + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    req = request.Request(f'https://api.telegram.org/bot{token}/{method}', data=b''.join(parts), headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    # No automatic retries: a lost HTTP response may follow a successful delivery.
    with request.urlopen(req, timeout=90, context=tls_context()) as r:
        result = json.load(r)
    if not result.get('ok'):
        raise RuntimeError('Telegram rejected request')
    messages = result['result'] if isinstance(result['result'], list) else [result['result']]
    return [m['message_id'] for m in messages]


def checkpoint(store):
    store.db.commit()
    if os.getenv('GITHUB_ACTIONS') == 'true':
        directory = os.environ.get('STATE_GIT_DIR')
        if not directory:
            raise RuntimeError('Persistent state directory required in Actions')
        subprocess.run(['git', '-C', directory, 'add', 'history.sqlite'], check=True, stdout=subprocess.DEVNULL)
        changed = subprocess.run(['git', '-C', directory, 'diff', '--cached', '--quiet']).returncode
        if changed:
            subprocess.run(['git', '-C', directory, 'commit', '-m', 'Persist collector state'], check=True, stdout=subprocess.DEVNULL)
        # Must succeed BEFORE sending a Telegram request.
        subprocess.run(['git', '-C', directory, 'push', 'origin', 'HEAD:parser-state'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(args):
    cfg = json.loads(Path(args.config).read_text())
    rights = json.loads(Path(args.rights).read_text())
    store = Store(args.db)
    http = HTTP()
    report = {'started': utcnow(), 'sources': [], 'candidates': [], 'sent': 0, 'attempted': 0, 'skipped': []}
    for source in cfg['sources']:
        if not source.get('enabled'):
            continue
        if not source.get('terms_reviewed'):
            report['sources'].append({'name': source['name'], 'status': 'terms_review_required'})
            continue
        try:
            body, _ = http.get(source['listing'])
            urls = discover(body.decode('utf-8', 'replace'), source['listing'], source['product_pattern'])
            report['sources'].append({'name': source['name'], 'found': len(urls), 'status': 'ok' if urls else 'empty_or_changed_markup'})
            for url in urls[:source.get('max_products', 20)]:
                try:
                    body, _ = http.get(url)
                    p = product_from_html(body.decode('utf-8', 'replace'), url, source)
                    if p:
                        points = score(p, cfg['max_age_days'])
                        store.put(p, points)
                except Exception as exc:
                    store.failure(source['name'], exc)
        except Exception as exc:
            store.failure(source['name'], exc)
            report['sources'].append({'name': source['name'], 'status': type(exc).__name__})
    rows = store.db.execute('SELECT payload FROM products WHERE score>=? ORDER BY score DESC, seen DESC', (cfg['min_score'],)).fetchall()
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if args.send and (not token or not chat):
        raise RuntimeError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID')
    day = dt.datetime.now(ZoneInfo(cfg['timezone'])).date().isoformat()
    target = int(cfg.get('backfill_target', 0))
    filling = store.db.execute('SELECT COUNT(*) FROM backfill').fetchone()[0] < target
    run_limit = cfg.get('backfill_per_run', 24) if filling else cfg['per_run']
    deadline = time.monotonic() + 900
    for (payload,) in rows:
        if time.monotonic() > deadline or (args.send and report['attempted'] >= run_limit):
            break
        p = json.loads(payload)
        if store.duplicate(p):
            continue
        # Refresh shortlisted products before publishing: no stale statuses/prices.
        try:
            source = next(s for s in cfg['sources'] if s['name'] == p['source'] and s.get('enabled') and s.get('terms_reviewed'))
            body, _ = http.get(p['url'])
            fresh = product_from_html(body.decode('utf-8', 'replace'), p['url'], source)
            if not fresh or score(fresh, cfg['max_age_days']) < cfg['min_score']:
                continue
            p = fresh
            permission = dict(rights.get(p['url'], {}))
            # Optional human-verified facts when HTML exposes too little structured data.
            facts = permission.get('verified_facts', {})
            if facts and permission.get('facts_evidence'):
                for key in ('description', 'features'):
                    if key in facts:
                        p[key] = facts[key]
            text = caption(p)
            media, hashes = prepare_media(p, permission, http)
            if store.duplicate(p, hashes):
                continue
            report['candidates'].append({'url': p['url'], 'caption': text, 'photos': len(media)})
            if args.send:
                if report['attempted'] >= run_limit:
                    break
                ident = store.reserve(p, hashes, day, cfg['daily_limit'], target if filling else 0)
                if ident is None:
                    continue
                checkpoint(store)
                report['attempted'] += 1
                try:
                    ids = send(token, chat, text, media)
                    store.finish(ident, 'sent', ids)
                    report['sent'] += 1
                except Exception as exc:
                    store.finish(ident, 'uncertain', {'error': type(exc).__name__})
                    store.failure('telegram', exc)
                checkpoint(store)
                if report['attempted'] < run_limit:
                    time.sleep(4)
            else:
                folder = Path(args.output) / hashlib.sha256(p['url'].encode()).hexdigest()[:12]
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'caption.html').write_text(text)
                for i, blob in enumerate(media):
                    (folder / f'{i+1}.jpg').write_bytes(blob)
        except subprocess.CalledProcessError:
            raise  # Never continue after a failed persistence checkpoint.
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            report['skipped'].append({'url': p['url'], 'reason': reason})
            store.failure(p['source'], exc)
    checkpoint(store)
    Path(args.output).mkdir(parents=True, exist_ok=True)
    report['backfill'] = {'target': target, 'sent': store.backfill_count(),
                          'remaining': max(0, target - store.backfill_count())}
    Path(args.output, 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    LOG.info('Eligible previews: %s; sent: %s', len(report['candidates']), report['sent'])


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
    LOG.setLevel(logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.json')
    ap.add_argument('--rights', default='rights.json')
    ap.add_argument('--db', default='state/history.sqlite')
    ap.add_argument('--output', default='preview')
    ap.add_argument('--send', action='store_true')
    try:
        run(ap.parse_args())
    except Exception as exc:
        LOG.error('Run stopped: %s', type(exc).__name__)
        raise SystemExit(1)

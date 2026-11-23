"""Connectivity/robots audit without bypassing website restrictions."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
from parser import HTTP, DOM, discover, utcnow, collect_urls

report = {'at': utcnow(), 'sources': []}
http = HTTP()
for source in json.loads(Path('config.json').read_text())['sources']:
    if not source.get('enabled'):
        continue
    row = {'source': source['name'], 'url': source['listing']}
    try:
        if source['name'] == 'kickstarter':
            urls, errors = collect_urls(http, dict(source, max_pages=1))
            row.update(product_links=len(urls), errors=errors, status='HTTP_OK' if urls else 'UNAVAILABLE')
            report['sources'].append(row)
            continue
        body, mime = http.get(source['listing'])
        content = body.decode('utf-8', 'replace')
        row.update(bytes=len(body), content_type=mime,
                   product_links=len(discover(content, source['listing'], source['product_pattern'])),
                   scripts=sum(n.tag == 'script' for n in DOM(content).root.walk()), status='HTTP_OK')
    except Exception as exc:
        row.update(status='UNAVAILABLE', reason=type(exc).__name__)
    report['sources'].append(row)
Path('preview').mkdir(exist_ok=True)
Path('preview/probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))

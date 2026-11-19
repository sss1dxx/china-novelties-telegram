import json
from collections import Counter
from pathlib import Path
report = json.loads(Path('preview/report.json').read_text())
if report.get('sent', 0) < 1:
    print('ACTIVATION BLOCKED: no real post was sent. Schedule remains disabled.')
    print('Sources:', json.dumps(report.get('sources', []), ensure_ascii=False))
    for reason, count in Counter(p['reason'] for p in report.get('skipped', [])).items():
        print(f'{count} product(s): {reason}')
    raise SystemExit(1)
print('First real Telegram publication confirmed.')
